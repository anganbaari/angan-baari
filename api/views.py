import secrets
import uuid
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import generics, permissions, status
from rest_framework.authentication import SessionAuthentication, TokenAuthentication
from rest_framework.authtoken.models import Token
from rest_framework.response import Response
from rest_framework.views import APIView

from shop.emails import send_order_cancelled_email, send_order_received_email, send_resend_email
from shop.models import (
    Category, CreditTransaction, Customer, InventoryMovement, POSSale, Product, ProductOrder,
    UserProfile, Wishlist, get_live_coupons,
)
from shop.views import (
    create_inventory_movements_from_snapshot,
    create_order_inventory_movements,
    create_pos_sale,
    format_weight,
    get_pos_operator,
    resolve_pos_coupon,
    POSSaleValidationError,
)

from .permissions import IsStaffUser
from .serializers import (
    CategorySerializer,
    ChangePasswordSerializer,
    CouponSerializer,
    CouponValidateSerializer,
    CreditRepaySerializer,
    CustomerCreateSerializer,
    CustomerSerializer,
    InventoryMovementReadSerializer,
    LoginSerializer,
    OrderCreateSerializer,
    OrderDetailSerializer,
    OrderHistorySerializer,
    OrderStatusSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    PosRestockSerializer,
    PosUnlockSerializer,
    POSSaleCreateSerializer,
    POSSaleReadSerializer,
    ProductOriginUpdateSerializer,
    ProductSerializer,
    ProductVariantSerializer,
    ProfileUpdateSerializer,
    SignupSerializer,
    WishlistItemSerializer,
    WishlistMoveToCartSerializer,
    WishlistSetVariantSerializer,
    WishlistToggleSerializer,
)


# ─── PUBLIC READ ────────────────────────────────────────────────

class ProductListView(generics.ListAPIView):
    serializer_class = ProductSerializer
    authentication_classes = []
    permission_classes = [permissions.AllowAny]

    def get_queryset(self):
        qs = Product.objects.select_related('category').prefetch_related('variants').order_by('name')
        category_id = self.request.query_params.get('category')
        if category_id:
            qs = qs.filter(category_id=category_id)
        return qs


class ProductDetailView(generics.RetrieveAPIView):
    queryset = Product.objects.select_related('category').prefetch_related('variants')
    serializer_class = ProductSerializer
    authentication_classes = []
    permission_classes = [permissions.AllowAny]


class ProductDetailBySlugView(generics.RetrieveAPIView):
    """Slug-based lookup, for frontends (e.g. the Next.js shop) that only
    know the slug, matching how the Django site's own product_detail view
    looks products up. Additive alongside ProductDetailView (by id) —
    neither replaces the other."""

    queryset = Product.objects.select_related('category').prefetch_related('variants')
    serializer_class = ProductSerializer
    authentication_classes = []
    permission_classes = [permissions.AllowAny]
    lookup_field = 'slug'
    lookup_url_kwarg = 'slug'


class CategoryListView(generics.ListAPIView):
    queryset = Category.objects.all()
    serializer_class = CategorySerializer
    authentication_classes = []
    permission_classes = [permissions.AllowAny]


# ─── STAFF-ONLY READ ────────────────────────────────────────────

class InventoryMovementListView(generics.ListAPIView):
    """Separate v1 GET-capable view onto the ledger. The original
    /api/inventory/movements/ (shop/api_urls.py) only supports POST, is
    token-authed for ABMS, and is untouched by this app."""

    serializer_class = InventoryMovementReadSerializer
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsStaffUser]

    def get_queryset(self):
        qs = InventoryMovement.objects.select_related('product', 'variant').order_by('-created_at')
        product_id = self.request.query_params.get('product')
        movement_type = self.request.query_params.get('movement_type')
        source = self.request.query_params.get('source')
        if product_id:
            qs = qs.filter(product_id=product_id)
        if movement_type:
            qs = qs.filter(movement_type=movement_type)
        if source:
            qs = qs.filter(source=source)
        return qs


# ─── POS SALES (staff-only list + create) ──────────────────────

class POSSaleView(generics.ListAPIView):
    """GET: sale history (staff-only). POST: create a sale via the API —
    delegates to create_pos_sale() (shop/views.py), shared with
    pos_create_sale(), the traditional view templates/pos.html actually
    calls. Same server-side total/VAT recomputation, same payments-sum
    validation, same client_sale_id idempotency either way."""

    queryset = POSSale.objects.select_related('cashier').order_by('-created_at')
    serializer_class = POSSaleReadSerializer
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsStaffUser]

    def post(self, request, *args, **kwargs):
        input_serializer = POSSaleCreateSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        data = input_serializer.validated_data

        customer = None
        customer_id = data.get('customer_id')
        if customer_id:
            try:
                customer = Customer.objects.get(id=customer_id)
            except Customer.DoesNotExist:
                return Response({'status': 'error', 'message': 'Customer not found.'}, status=400)

        try:
            operator_user = get_pos_operator(request)
            sale, created = create_pos_sale(
                client_sale_id=data['client_sale_id'],
                cart=data['cart'],
                payments=data['payments'],
                operator_user=operator_user,
                customer=customer,
                note_prefix='POS sale (API)',
                coupon_code=data.get('coupon_code'),
            )
        except POSSaleValidationError as e:
            return Response({'status': 'error', 'message': e.message}, status=e.status)

        return Response(
            {
                'status': 'ok', 'sale_number': sale.sale_number, 'total': str(sale.total_amount),
                'discount_amount': str(sale.discount_amount), 'round_off_amount': str(sale.round_off_amount),
            },
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class CouponValidateView(APIView):
    """POST /api/v1/pos/coupon/validate/ — live coupon preview for the POS
    screen: checks a code against the current cart subtotal and returns the
    discount without creating or changing anything (no used_count
    increment, no sale). The actual sale still re-validates the same
    coupon from scratch in create_pos_sale() via resolve_pos_coupon(),
    which is the only place a coupon is ever actually consumed — this view
    exists purely so staff see the discount before tapping Complete Sale."""

    authentication_classes = [SessionAuthentication]
    permission_classes = [IsStaffUser]

    def post(self, request, *args, **kwargs):
        serializer = CouponValidateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        coupon_obj, discount_amount, error = resolve_pos_coupon(data['code'], data['subtotal'])
        if error:
            return Response({'status': 'error', 'message': error}, status=400)
        if not coupon_obj:
            return Response({'status': 'error', 'message': 'Enter a coupon code.'}, status=400)

        return Response({
            'status': 'ok',
            'code': coupon_obj.code,
            'discount_amount': str(discount_amount),
        })


class PosOffersListView(APIView):
    """GET /api/v1/pos/offers/ — POS Phase D: live Offers (percent/fixed
    product discounts and combo bundles) for the offers picker, fetched
    on demand rather than baked into the initial /pos/ page payload, since
    a terminal can stay unlocked all day (see PosUnlockView) while an
    offer's is_live() window starts or ends mid-shift.

    A product/bundle item still being listed here doesn't guarantee it'll
    still be available by the time the cashier taps Complete Sale —
    resolve_pos_offer_discount()/resolve_pos_combo_lines() in
    shop/views.py are the actual source of truth, re-checked fresh at sale
    time, exactly like the coupon preview above. This is read-only and
    makes no changes."""

    authentication_classes = [SessionAuthentication]
    permission_classes = [IsStaffUser]

    def get(self, request, *args, **kwargs):
        from shop.models import Offer

        now = timezone.now()
        live_offers = Offer.objects.filter(is_active=True, start_date__lte=now, end_date__gte=now)

        discount_offers = []
        combo_offers = []

        for offer in live_offers:
            if offer.discount_type == 'combo':
                bundle_items = list(offer.bundle_items.select_related('product').all())
                if not bundle_items:
                    continue
                natural_total = offer.get_bundle_natural_total()
                if natural_total <= 0:
                    continue

                items = []
                for bi in bundle_items:
                    item = {
                        'bundle_item_id': bi.id,
                        'product_id': bi.product_id,
                        'product_name': bi.product.name,
                        'pricing_mode': bi.product.pricing_mode,
                        'quantity': str(bi.quantity),
                        'unit_label': 'kg' if bi.product.pricing_mode == 'fixed_weight' else (
                            bi.product.weight_unit_label if bi.product.pricing_mode == 'variable_weight' else ''
                        ),
                        'available': bi.product.is_available,
                    }
                    if bi.product.pricing_mode == 'fixed_weight':
                        item['available_variants'] = [
                            {'id': v.id, 'weight': str(v.weight), 'label': v.label, 'total_price': str(v.total_price())}
                            for v in bi.product.available_variants()
                        ]
                        item['available'] = bi.product.is_available and bool(item['available_variants'])
                    items.append(item)

                combo_offers.append({
                    'offer_id': offer.id,
                    'title': offer.title,
                    'combo_price': str(offer.combo_price or natural_total),
                    'natural_total': str(natural_total),
                    'items': items,
                    'fully_available': all(i['available'] for i in items),
                })
            else:
                for product in offer.get_products():
                    if not product.price or product.price <= 0:
                        continue
                    discount_offers.append({
                        'offer_id': offer.id,
                        'title': offer.title,
                        'product_id': product.id,
                        'product_name': product.name,
                        'pricing_mode': product.pricing_mode,
                        'original_price': str(product.price),
                        'discounted_price': str(offer.discounted_price(product.price)),
                        'discount_type': offer.discount_type,
                        'discount_value': str(offer.discount_value),
                        'available': product.is_available,
                    })

        return Response({'discount_offers': discount_offers, 'combo_offers': combo_offers})


class PosStockListView(APIView):
    """GET /api/v1/pos/stock/ — the POS stock screen's data: every non-
    fixed_weight product's current stock, low-stock threshold/flag, restock
    method, and last-restocked date, including disabled (is_available=False)
    products — unlike pos_view()'s embedded PRODUCTS blob, which is filtered
    to is_available=True only (see shop/views.py's pos_view()), this screen
    needs disabled products too so staff can see (and restock) something
    that's run all the way out.

    Reuses get_stock_table_rows() in shop/stock.py — the exact same function
    ProductAdmin's stock column uses — so the two can never show different
    numbers for the same product. Read-only, makes no changes."""

    authentication_classes = [SessionAuthentication]
    permission_classes = [IsStaffUser]

    def get(self, request, *args, **kwargs):
        from shop.stock import get_stock_table_rows

        return Response([
            {
                'product_id': row['product_id'],
                'name': row['name'],
                'current_stock': str(row['current_stock']),
                'low_stock_threshold': row['low_stock_threshold'],
                'is_low': row['is_low'],
                'origin': row['origin'],
                'restock_method': row['restock_method'],
                'last_restocked_at': row['last_restocked_at'].isoformat() if row['last_restocked_at'] else None,
                'is_available': row['is_available'],
            }
            for row in get_stock_table_rows()
        ])


class PosRestockView(APIView):
    """POST /api/v1/pos/restock/ — a direct operator-entered restock from
    the POS stock screen. Picks movement_type='harvest' if this restock's
    explicit origin is 'farm', 'purchase' otherwise (a separate concept
    from both 'harvest' and 'adjustment_add' — see InventoryMovement.
    MOVEMENT_TYPE_CHOICES). source='pos'. full_clean() is called explicitly
    inside transaction.atomic() — DRF's serializer validation does NOT call
    Model.clean() on its own, and this is a direct operator-entered
    movement (not a best-effort website write), so it gets the same strict
    treatment create_pos_sale()/create_inventory_movements_from_snapshot()
    already use for POS-originated movements.

    origin is a per-restock field on the request (PosRestockSerializer),
    deliberately NOT derived from product.origin: a normally farm-grown
    product is occasionally bought in when the farm has none that day, and
    the ledger needs to record what actually happened, not the product's
    usual default. The POS restock modal pre-selects product.origin so the
    common case is still one click. Changing the product's own default
    going forward is a separate action — see PosProductOriginUpdateView.

    Fixed-weight products (goats/chickens) are rejected here — they're
    restocked by adding a new ProductVariant row (a new animal), not by a
    plain quantity movement, same exclusion as the stock table itself."""

    authentication_classes = [SessionAuthentication]
    permission_classes = [IsStaffUser]

    def post(self, request, *args, **kwargs):
        from shop.models import Product

        serializer = PosRestockSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            product = Product.objects.get(id=data['product_id'])
        except Product.DoesNotExist:
            return Response({'status': 'error', 'message': 'Product not found.'}, status=404)

        if product.pricing_mode == 'fixed_weight':
            return Response(
                {'status': 'error', 'message': 'Fixed-weight products are restocked by adding a new animal, not here.'},
                status=400,
            )

        movement_type = 'harvest' if data['origin'] == 'farm' else 'purchase'

        try:
            with transaction.atomic():
                movement = InventoryMovement(
                    product=product, movement_type=movement_type, source='pos',
                    quantity=data['quantity'], note=data.get('note') or '',
                )
                movement.full_clean()
                movement.save()
        except DjangoValidationError as e:
            message = '; '.join(e.messages) if hasattr(e, 'messages') else str(e)
            return Response({'status': 'error', 'message': message}, status=400)

        # A restock crossing stock from <=0 back to positive may have just
        # flipped product.is_available via the existing sync_availability_
        # from_stock signal (shop/signals.py) -- refresh so the response
        # reflects that instead of the pre-save value still held in memory.
        product.refresh_from_db()
        current_stock = InventoryMovement.current_stock(product)
        return Response({
            'status': 'ok',
            'product_id': product.id,
            'current_stock': str(current_stock),
            'is_low': current_stock < product.low_stock_threshold,
            'is_available': product.is_available,
            'last_restocked_at': movement.created_at.isoformat(),
            'movement_type': movement_type,
        }, status=status.HTTP_201_CREATED)


class PosProductOriginUpdateView(APIView):
    """PATCH /api/v1/pos/products/<product_id>/origin/ — changes a
    product's own default origin going forward (the inline edit control
    next to "Restock Method" on the POS stock screen), separate from
    PosRestockView's per-restock origin override above: this is for when a
    product's actual long-term sourcing changes (you start growing
    something you used to buy, or vice versa), not for recording what
    happened on one specific restock."""

    authentication_classes = [SessionAuthentication]
    permission_classes = [IsStaffUser]

    def patch(self, request, *args, **kwargs):
        from shop.models import Product

        serializer = ProductOriginUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            product = Product.objects.get(id=kwargs['product_id'])
        except Product.DoesNotExist:
            return Response({'status': 'error', 'message': 'Product not found.'}, status=404)

        product.origin = serializer.validated_data['origin']
        product.save(update_fields=['origin'])
        return Response({
            'status': 'ok',
            'product_id': product.id,
            'origin': product.origin,
            'restock_method': 'Own farm' if product.origin == 'farm' else 'Outsourced',
        })


class PosUnlockView(APIView):
    """POST /api/v1/pos/unlock/ — POS Phase A: identifies which staff member
    is actually standing at a shared POS terminal right now, layered on top
    of (not instead of) the terminal's own is_staff session login, which
    stays active all day regardless of who's currently operating it.

    Lockout state lives in request.session — per terminal/browser session,
    not per user — matching that same "one login, many operators" model:
    the failed-attempt counter tracks bad guesses at THIS register, not
    against any particular staff account.
    """

    authentication_classes = [SessionAuthentication]
    permission_classes = [IsStaffUser]

    MAX_ATTEMPTS = 7
    LOCKOUT_SECONDS = 5 * 60

    def post(self, request, *args, **kwargs):
        serializer = PosUnlockSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        pin = serializer.validated_data['pin']

        lockout_until_raw = request.session.get('pos_lockout_until')
        if lockout_until_raw:
            lockout_until = parse_datetime(lockout_until_raw)
            now = timezone.now()
            if lockout_until and lockout_until > now:
                return Response(
                    {'error': 'locked_out', 'retry_after_seconds': int((lockout_until - now).total_seconds())},
                    status=status.HTTP_403_FORBIDDEN,
                )
            # Lockout has expired -- clear it so a fresh set of attempts can start.
            del request.session['pos_lockout_until']

        matched_profile = None
        for profile in UserProfile.objects.exclude(pin_hash='').filter(
            user__is_staff=True, user__is_active=True,
        ).select_related('user'):
            if profile.check_pin(pin):
                matched_profile = profile
                break

        if matched_profile:
            request.session['pos_failed_attempts'] = 0
            user = matched_profile.user
            # This is the one place operator identity gets established --
            # sale creation and credit repayment both read it back from the
            # session rather than trusting a client-supplied operator_id,
            # so a request can't just claim to be any staff member without
            # that person actually having entered their PIN on this
            # terminal/session.
            request.session['pos_operator_id'] = user.id
            request.session['pos_operator_unlocked_at'] = timezone.now().isoformat()
            return Response({
                'id': user.id,
                'name': user.get_full_name() or user.username,
                'role': matched_profile.role,
            })

        attempts = request.session.get('pos_failed_attempts', 0) + 1
        if attempts >= self.MAX_ATTEMPTS:
            request.session['pos_lockout_until'] = (
                timezone.now() + timedelta(seconds=self.LOCKOUT_SECONDS)
            ).isoformat()
            request.session['pos_failed_attempts'] = 0
            return Response({'error': 'invalid_pin'}, status=status.HTTP_401_UNAUTHORIZED)

        request.session['pos_failed_attempts'] = attempts
        return Response(
            {'error': 'invalid_pin', 'attempts_remaining': self.MAX_ATTEMPTS - attempts},
            status=status.HTTP_401_UNAUTHORIZED,
        )


class PosLockView(APIView):
    """POST /api/v1/pos/lock/ — clears the session's operator identity.
    What the frontend's idle timer calls to show the lock overlay; after
    this, sale creation and credit repayment on this session are rejected
    until someone unlocks again via a correct PIN. Doesn't touch the
    failed-attempt/lockout counters -- those are a separate concern from
    "who's currently identified as operating this terminal"."""

    authentication_classes = [SessionAuthentication]
    permission_classes = [IsStaffUser]

    def post(self, request, *args, **kwargs):
        request.session.pop('pos_operator_id', None)
        request.session.pop('pos_operator_unlocked_at', None)
        return Response({'status': 'ok'})


class CustomerLookupView(APIView):
    """GET /api/v1/pos/customers/lookup/?phone=<number> — the checkout-time
    lookup staff use to find a credit customer. phone isn't unique (shared
    family phones happen), so this returns every match as a list, never a
    single object or a 404 -- an empty list just means "no match," which is
    the frontend's cue to show its own create-new-customer form next."""

    authentication_classes = [SessionAuthentication]
    permission_classes = [IsStaffUser]

    def get(self, request, *args, **kwargs):
        phone = request.query_params.get('phone', '').strip()
        if not phone:
            return Response({'status': 'error', 'message': 'phone is required.'}, status=400)
        customers = Customer.objects.filter(phone=phone)
        return Response(CustomerSerializer(customers, many=True).data)


class CustomerListCreateView(APIView):
    """GET/POST /api/v1/pos/customers/.

    GET powers the Repay Credit screen's customer table (name, nickname,
    phone, address, outstanding balance, and their most recent repayment
    amount/date, or null if they've never repaid) — every customer, not
    phone-filtered like CustomerLookupView above (that one's for the
    payment panel's credit-sale lookup, a separate flow this doesn't
    touch). phone isn't unique (shared family phones), so the Repay Credit
    screen does its own live substring filtering over this list client-side
    rather than hitting the server per keystroke.

    POST creates a new credit customer — unchanged from before. A fresh
    customer always starts at balance 0 (CreditTransaction rows only ever
    get created by an actual sale/repayment, never by this view)."""

    authentication_classes = [SessionAuthentication]
    permission_classes = [IsStaffUser]

    def get(self, request, *args, **kwargs):
        data = []
        for customer in Customer.objects.all():
            # -id as a tiebreaker: two repayments recorded in the same
            # request-handling instant can share an identical created_at
            # (auto_now_add's resolution), and id is the one field that's
            # guaranteed to break the tie in actual insertion order.
            last_repayment = (
                customer.credit_transactions
                .filter(transaction_type='repayment')
                .order_by('-created_at', '-id')
                .first()
            )
            data.append({
                'id': customer.id,
                'name': customer.name,
                'nickname': customer.nickname,
                'phone': customer.phone,
                'address': customer.address,
                'outstanding_balance': str(customer.outstanding_balance()),
                'last_repaid_amount': str(last_repayment.amount) if last_repayment else None,
                'last_repaid_at': last_repayment.created_at.isoformat() if last_repayment else None,
            })
        return Response(data)

    def post(self, request, *args, **kwargs):
        serializer = CustomerCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        customer = Customer.objects.create(**serializer.validated_data)
        return Response(CustomerSerializer(customer).data, status=status.HTTP_201_CREATED)


class CreditRepayView(APIView):
    """POST /api/v1/pos/credit/repay/ — records a उधारो repayment. The
    operator comes from request.session (see get_pos_operator() /
    PosUnlockView), not necessarily request.user, same reasoning as sale
    creation — never a client-supplied operator_id. Deliberately does not
    cap amount at the customer's current balance -- an overpayment is a
    real business situation (rounding, the customer paying off more than
    they technically owe), not a client error, so it's recorded exactly
    as entered rather than silently clamped."""

    authentication_classes = [SessionAuthentication]
    permission_classes = [IsStaffUser]

    def post(self, request, *args, **kwargs):
        serializer = CreditRepaySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            customer = Customer.objects.get(id=data['customer_id'])
        except Customer.DoesNotExist:
            return Response({'status': 'error', 'message': 'Customer not found.'}, status=400)

        try:
            operator_user = get_pos_operator(request)
        except POSSaleValidationError as e:
            return Response({'status': 'error', 'message': e.message}, status=e.status)

        transaction_row = CreditTransaction.objects.create(
            customer=customer,
            amount=data['amount'],
            transaction_type='repayment',
            recorded_by=operator_user,
        )
        return Response(
            {
                'status': 'ok',
                'id': transaction_row.id,
                'outstanding_balance': str(customer.outstanding_balance()),
            },
            status=status.HTTP_201_CREATED,
        )


# ─── WEBSITE ORDERS (public, guest checkout) ───────────────────

class OrderCreateView(APIView):
    """Public — matches current checkout() behavior (no login required).
    Calls create_order_inventory_movements(), which itself calls
    create_inventory_movements_from_snapshot(strict=False): bad lines are
    silently skipped rather than losing the whole order, same as the
    website. Coupon/offer pricing isn't part of this v1 surface."""

    authentication_classes = []
    permission_classes = [permissions.AllowAny]

    def post(self, request, *args, **kwargs):
        input_serializer = OrderCreateSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        data = input_serializer.validated_data

        items_desc = []
        cart_snapshot = []
        for line in data['cart']:
            try:
                product = Product.objects.get(id=line.get('product_id'), is_available=True)
            except (Product.DoesNotExist, TypeError, ValueError):
                continue

            try:
                qty = max(1, int(line.get('qty') or 1))
            except (TypeError, ValueError):
                qty = 1
            weight = line.get('weight')
            variant_id = line.get('variant_id')

            cart_snapshot.append({
                'product_id': product.id, 'weight': weight, 'qty': qty, 'variant_id': variant_id,
            })
            items_desc.append(f"{product.name} ({weight}kg) x{qty}" if weight else f"{product.name} x{qty}")

        if not cart_snapshot:
            return Response({'status': 'error', 'message': 'No valid items in cart.'}, status=400)

        order = ProductOrder.objects.create(
            name=data['name'],
            email=data['email'],
            phone=data.get('phone', ''),
            address=data.get('address', ''),
            product_interest=', '.join(items_desc),
            message=data.get('message', ''),
            cart_snapshot=cart_snapshot,
        )
        create_order_inventory_movements(order, 'sale')

        try:
            send_order_received_email(order)
        except Exception:
            pass

        return Response(
            {'status': 'ok', 'order_number': order.order_number, 'id': order.id},
            status=status.HTTP_201_CREATED,
        )


class OrderDetailView(APIView):
    """Public. Returns the full order (name/email/phone/address/
    cart_snapshot) only when the caller proves ownership via
    ?token=<cancel_token> — the same secret already emailed to the
    customer on send_order_received_email and used by the existing
    /cancel/<token>/ link. Without a matching token, falls back to the
    status-only fields (order_number/status/ordered_at/product_interest),
    so a bare guessable integer id can never be used to enumerate other
    customers' PII."""

    authentication_classes = []
    permission_classes = [permissions.AllowAny]

    def get(self, request, pk, *args, **kwargs):
        try:
            order = ProductOrder.objects.get(pk=pk)
        except ProductOrder.DoesNotExist:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)

        token = request.query_params.get('token', '')
        if token and order.cancel_token and secrets.compare_digest(token, order.cancel_token):
            serializer = OrderDetailSerializer(order)
        else:
            serializer = OrderStatusSerializer(order)
        return Response(serializer.data)


# ─── AUTH (token-based) ─────────────────────────────────────────
# DRF's TokenAuthentication (rest_framework.authtoken) is already installed
# and is this project's existing pattern for API-boundary auth — it's what
# ABMS authenticates with at /api/inventory/movements/. Reusing it here
# (one token per user instead of one per integration) means these endpoints
# work cross-origin from the Vercel frontend over a plain Authorization
# header, with no session cookies involved at all — so none of the
# SESSION_COOKIE_SAMESITE/SECURE/CSRF_TRUSTED_ORIGINS changes drafted (and
# held back) during the Phase 2 guest-checkout work are needed. Those
# settings remain completely untouched.
#
# The traditional site's own session-based /account/ login is completely
# separate and unaffected: a user can be logged into the Django site via a
# session cookie AND hold a separate API token at the same time, same as
# any user with both a browser session and a personal access token.

def _serialize_user(user):
    return {
        'id': user.id,
        'name': user.get_full_name() or user.first_name,
        'email': user.email,
    }


class SignupView(APIView):
    """POST /api/v1/auth/signup/ — creates a real auth.User row via
    User.objects.create_user(), same as shop/auth_views.signup(): username
    is the email, name is split into first/last, and the same welcome email
    is sent (best-effort — send_resend_email already swallows its own
    errors). Returns a token immediately, same as the traditional flow logs
    the new user straight in rather than requiring a separate login step."""

    authentication_classes = []
    permission_classes = [permissions.AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = SignupSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        name = data['name'].strip()
        parts = name.split()
        first_name = parts[0] if parts else ''
        last_name = ' '.join(parts[1:]) if len(parts) > 1 else ''

        user = User.objects.create_user(
            username=data['email'],
            email=data['email'],
            password=data['password'],
            first_name=first_name,
            last_name=last_name,
        )
        token, _ = Token.objects.get_or_create(user=user)

        send_resend_email(
            to=data['email'],
            subject='🌿 Welcome to Angan Baari!',
            body=f'''नमस्ते {first_name}! 🌿

Welcome to Angan Baari (आँगन बारी)!

Your account has been created successfully.
You can now track your orders and shop easily.

🛒 Start shopping:
https://anganbaari.pythonanywhere.com/shop/

📱 Questions? WhatsApp us:
https://wa.me/9779821025084

With love,
Angan Baari Team 🌱
Bhulka Danda, Rupandehi, Nepal''',
        )

        return Response(
            {'token': token.key, 'user': _serialize_user(user)},
            status=status.HTTP_201_CREATED,
        )


class LoginView(APIView):
    """POST /api/v1/auth/login/ — same authenticate(username=email,
    password=...) call as shop/auth_views.login_view(). No "remember me"
    concept here (that's a session-expiry setting; tokens don't expire on
    their own) — the frontend decides how long to keep the token."""

    authentication_classes = []
    permission_classes = [permissions.AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data['user']
        token, _ = Token.objects.get_or_create(user=user)
        return Response({'token': token.key, 'user': _serialize_user(user)})


class LogoutView(APIView):
    """POST /api/v1/auth/logout/ — deletes the presented token so it can't
    be reused (unlike a session cookie, a token doesn't expire by itself on
    logout unless invalidated explicitly)."""

    authentication_classes = [TokenAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, *args, **kwargs):
        request.auth.delete()
        return Response({'status': 'ok'})


class PasswordResetRequestView(APIView):
    """POST /api/v1/auth/password-reset/ — same cache-based token scheme as
    shop/auth_views.forgot_password() (a uuid4 hex key in Django's cache,
    1-hour TTL under the same 'pwd_reset_<token>' key), so a token minted
    here can be completed via either this API or the traditional
    /account/reset-password/<token>/ page, and vice versa. Always responds
    the same way regardless of whether the email matched an account, same
    anti-enumeration behavior as the traditional view.

    The emailed link points at FRONTEND_BASE_URL (the Next.js app) rather
    than the traditional site's own reset page, since this endpoint exists
    for that frontend — see settings.py. That frontend's
    /reset-password/[token] route was built in the Phase 3 (Auth) frontend
    work and confirmed end-to-end against this endpoint."""

    authentication_classes = []
    permission_classes = [permissions.AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data['email']

        user = User.objects.filter(email__iexact=email).first()
        if user:
            token = uuid.uuid4().hex
            cache.set(f'pwd_reset_{token}', user.id, 3600)
            reset_link = f'{settings.FRONTEND_BASE_URL}/reset-password/{token}/'
            send_resend_email(
                to=email,
                subject='🔑 Reset Your Password — Angan Baari',
                body=f'''नमस्ते {user.first_name}! 🌿

You requested a password reset for your Angan Baari account.

Click the link below to reset your password:
{reset_link}

This link expires in 1 hour.

If you did not request this, please ignore this email.

Angan Baari Team 🌱''',
            )

        return Response({
            'status': 'ok',
            'message': 'If an account exists with that email, a reset link has been sent.',
        })


class PasswordResetConfirmView(APIView):
    """POST /api/v1/auth/password-reset-confirm/ — completes a reset
    started by either this API or the traditional forgot-password page
    (same cache key). Does not log the user in afterward (matching
    reset_password()'s own behavior of sending them back to log in)."""

    authentication_classes = []
    permission_classes = [permissions.AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        user = User.objects.get(id=data['user_id'])
        user.set_password(data['password'])
        user.save()
        cache.delete(f"pwd_reset_{data['token']}")

        return Response({'status': 'ok', 'message': 'Password reset successfully.'})


# ─── PROFILE PAGE (token-authenticated) ─────────────────────────
# Backs the Next.js Profile page (Phase 4). ProductOrder has no User FK, so
# "my orders" is matched by email the same way profile()'s own query does;
# Coupon has no per-user relation either, so /coupons/ is the same global
# get_live_coupons() list the public offers() page and the traditional
# profile page's "My Coupons" section already show everyone.

class ProfileOrderListView(generics.ListAPIView):
    """GET /api/v1/profile/orders/ -- same email-match ProductOrder query
    profile() uses, same ordering (newest first)."""

    serializer_class = OrderHistorySerializer
    authentication_classes = [TokenAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return ProductOrder.objects.filter(email=self.request.user.email).order_by('-ordered_at')


class OrderCancelView(APIView):
    """POST /api/v1/orders/<pk>/cancel/ -- ownership proven by the caller's
    token + email match (same scoping as ProfileOrderListView), not by the
    emailed cancel_token -- that flow stays at the existing
    /cancel/<token>/ page and OrderDetailView's ?token= gate, both untouched.
    A pk belonging to another user's order 404s, same as it not existing --
    this view never reveals whether the id exists at all to a non-owner.
    Mirrors cancel_order()'s two rejection states from cancel.html: already
    cancelled, and the 30-minute window expired."""

    authentication_classes = [TokenAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk, *args, **kwargs):
        order = get_object_or_404(ProductOrder, pk=pk, email=request.user.email)

        if order.status == 'cancelled':
            return Response(
                {'status': 'error', 'message': 'This order has already been cancelled.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not order.can_cancel():
            return Response(
                {'status': 'error', 'message': 'The 30-minute cancellation window for this order has expired.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        order.status = 'cancelled'
        order.save()
        create_order_inventory_movements(order, 'return')
        send_order_cancelled_email(order)

        return Response({'status': 'ok', 'order': OrderHistorySerializer(order).data})


class OrderReorderView(APIView):
    """POST /api/v1/orders/<pk>/reorder/ -- the traditional reorder() view
    adds straight into the session cart; the Next.js cart is client-side
    localStorage (lib/cart.ts), so this resolves the same per-line rules
    reorder() uses (skip a product that's gone/unavailable, always skip
    fixed_weight -- each listing is one unique animal, unlikely to still be
    around -- snap variable_weight to the nearest step) and hands back
    resolved product+weight+qty per line for the frontend to call its own
    addItem(product, {weight, qty}) with, instead of mutating a cart here."""

    authentication_classes = [TokenAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk, *args, **kwargs):
        order = get_object_or_404(ProductOrder, pk=pk, email=request.user.email)

        if not order.cart_snapshot:
            return Response(
                {
                    'status': 'error',
                    'message': "This order can't be reordered — it was placed before this feature existed.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        items = []
        skipped_count = 0

        for line in order.cart_snapshot:
            try:
                product = Product.objects.get(id=line.get('product_id'), is_available=True)
            except (Product.DoesNotExist, TypeError, ValueError):
                skipped_count += 1
                continue

            if product.pricing_mode == 'fixed_weight':
                skipped_count += 1
                continue

            try:
                qty = max(1, int(line.get('qty') or 1))
            except (TypeError, ValueError):
                qty = 1

            if product.pricing_mode == 'variable_weight':
                step = product.weight_step or '0.50'
                weight_str = format_weight(line.get('weight') or step, step)
                qty_to_add = 1  # each variable-weight line is one weight-slice, same as reorder()
            else:  # fixed_quantity
                weight_str = None
                qty_to_add = qty

            items.append({
                'product': ProductSerializer(product).data,
                'weight': weight_str,
                'qty': qty_to_add,
            })

        response = {'status': 'ok', 'items': items, 'skipped_count': skipped_count}
        if not items:
            response['message'] = 'None of the items from this order are available to reorder right now.'
        return Response(response)


class WishlistListView(generics.ListAPIView):
    """GET /api/v1/wishlist/ -- same Wishlist rows profile() passes to
    profile.html's wishlist section."""

    serializer_class = WishlistItemSerializer
    authentication_classes = [TokenAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Wishlist.objects.filter(user=self.request.user).select_related('product', 'variant')


class WishlistToggleView(APIView):
    """POST /api/v1/wishlist/toggle/ -- mirrors wishlist_toggle() exactly,
    with product_id/variant_id in the body instead of the URL path (this is
    a flat endpoint, not /wishlist/toggle/<id>/)."""

    authentication_classes = [TokenAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, *args, **kwargs):
        serializer = WishlistToggleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        product = get_object_or_404(Product, id=data['product_id'])
        existing = Wishlist.objects.filter(user=request.user, product=product).first()
        if existing:
            existing.delete()
            is_saved = False
        else:
            variant = None
            if product.pricing_mode == 'fixed_weight':
                variant_id = data.get('variant_id')
                if variant_id:
                    variant = product.variants.filter(id=variant_id, is_available=True).first()
                if not variant:
                    available = sorted(product.available_variants(), key=lambda v: v.total_price())
                    variant = available[0] if available else None
            Wishlist.objects.create(user=request.user, product=product, variant=variant)
            is_saved = True

        return Response({'status': 'ok', 'is_saved': is_saved})


class WishlistSetVariantView(APIView):
    """POST /api/v1/wishlist/set-variant/ -- mirrors wishlist_set_variant()
    exactly, including the 400 when the requested size is no longer
    available (and the 404 when this product was never wishlisted at all --
    wishlist_set_variant() uses get_object_or_404 the same way)."""

    authentication_classes = [TokenAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, *args, **kwargs):
        serializer = WishlistSetVariantSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        product = get_object_or_404(Product, id=data['product_id'])
        wishlist_item = get_object_or_404(Wishlist, user=request.user, product=product)

        variant = product.variants.filter(id=data['variant_id'], is_available=True).first()
        if not variant:
            return Response(
                {'status': 'error', 'message': 'That size is no longer available'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        wishlist_item.variant = variant
        wishlist_item.save(update_fields=['variant'])
        return Response({
            'status': 'ok',
            'weight': f"{variant.weight:.2f}",
            'price': str(variant.total_price()),
        })


class WishlistMoveToCartView(APIView):
    """POST /api/v1/wishlist/move-to-cart/ -- doesn't touch any server-side
    cart (the Next.js cart is client-side localStorage, see lib/cart.ts).
    Resolves which variant applies using the same fixed_weight defaulting
    resolve_cart_line() uses, then removes the wishlist row -- matching
    wishlist_move_to_cart()'s "claimed once it's in the cart" behavior --
    and leaves it to the frontend to call its own addItem(product,
    {variant}), which builds the identical cart line resolve_cart_line()
    would. variable_weight/fixed_quantity products get variant: null; the
    traditional wishlist UI never sends a weight/quantity for those modes
    either, so both sides default the same way (one step / one unit) inside
    addItem() itself. Also mirrors wishlist_move_to_cart()'s unconditional
    delete -- calling this for a product never actually wishlisted is a
    no-op on the Wishlist side, not a 404."""

    authentication_classes = [TokenAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, *args, **kwargs):
        serializer = WishlistMoveToCartSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        product = get_object_or_404(Product, id=data['product_id'])

        variant = None
        if product.pricing_mode == 'fixed_weight':
            variant_id = data.get('variant_id')
            if variant_id:
                variant = product.variants.filter(id=variant_id, is_available=True).first()
            if not variant:
                available = sorted(product.available_variants(), key=lambda v: v.total_price())
                variant = available[0] if available else None

        Wishlist.objects.filter(user=request.user, product=product).delete()

        return Response({
            'status': 'ok',
            'product': ProductSerializer(product).data,
            'variant': ProductVariantSerializer(variant).data if variant else None,
        })


class CouponListView(generics.ListAPIView):
    """GET /api/v1/coupons/ -- the same global get_live_coupons() list the
    public offers() page and the traditional profile page's "My Coupons"
    section already show every visitor/user identically; there is no
    per-user coupon relation to filter by (see Coupon model)."""

    serializer_class = CouponSerializer
    authentication_classes = [TokenAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return get_live_coupons()


class ProfileUpdateView(APIView):
    """PATCH /api/v1/profile/ -- matches edit_profile() exactly: only name
    and email are editable, and changing email also changes username since
    signup uses email as username (see SignupView / shop/auth_views.signup())."""

    authentication_classes = [TokenAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def patch(self, request, *args, **kwargs):
        serializer = ProfileUpdateSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        user = request.user
        parts = data['name'].split()
        user.first_name = parts[0]
        user.last_name = ' '.join(parts[1:]) if len(parts) > 1 else ''
        user.email = data['email']
        user.username = data['email']
        user.save()

        return Response({'status': 'ok', 'user': _serialize_user(user)})


class ChangePasswordView(APIView):
    """POST /api/v1/profile/change-password/ -- validation matches
    change_password()'s PasswordChangeForm (correct old password, new1==
    new2, Django's full password-validator chain). Doesn't rotate the
    caller's token: a DRF token isn't derived from the password hash (unlike
    a session, which update_session_auth_hash() has to specifically keep
    alive after a password change), so it stays valid on its own -- same
    "stay logged in" outcome, no extra step needed."""

    authentication_classes = [TokenAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, *args, **kwargs):
        serializer = ChangePasswordSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)

        user = request.user
        user.set_password(serializer.validated_data['new_password1'])
        user.save()

        return Response({'status': 'ok', 'message': 'Password changed successfully.'})
