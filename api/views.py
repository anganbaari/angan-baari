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
    Category, InventoryMovement, POSSale, Product, ProductOrder, UserProfile, Wishlist, get_live_coupons,
)
from shop.views import (
    create_inventory_movements_from_snapshot,
    create_order_inventory_movements,
    format_weight,
)

from .permissions import IsStaffUser
from .serializers import (
    CategorySerializer,
    ChangePasswordSerializer,
    CouponSerializer,
    InventoryMovementReadSerializer,
    LoginSerializer,
    OrderCreateSerializer,
    OrderDetailSerializer,
    OrderHistorySerializer,
    OrderStatusSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    PosUnlockSerializer,
    POSSaleCreateSerializer,
    POSSaleReadSerializer,
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
    same server-side total recomputation, same
    create_inventory_movements_from_snapshot(strict=True) call inside
    transaction.atomic(), and the same client_sale_id idempotency as
    pos_create_sale() in shop/views.py, which templates/pos.html still
    calls directly and unchanged."""

    queryset = POSSale.objects.select_related('cashier').order_by('-created_at')
    serializer_class = POSSaleReadSerializer
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsStaffUser]

    def post(self, request, *args, **kwargs):
        input_serializer = POSSaleCreateSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        data = input_serializer.validated_data

        client_sale_id = data['client_sale_id']

        existing = POSSale.objects.filter(client_sale_id=client_sale_id).first()
        if existing:
            return Response({
                'status': 'ok',
                'sale_number': existing.sale_number,
                'total': str(existing.total_amount),
            })

        cart = data['cart']
        payment_method = data['payment_method']
        if payment_method not in dict(POSSale.PAYMENT_METHOD_CHOICES):
            return Response({'status': 'error', 'message': 'Choose a payment method.'}, status=400)

        total = Decimal('0')
        cart_snapshot = []
        try:
            for line in cart:
                product = Product.objects.get(id=line.get('product_id'), is_available=True)
                qty = int(line.get('qty', 1) or 1)
                weight = line.get('weight')
                variant_id = line.get('variant_id')

                if product.pricing_mode == 'fixed_weight':
                    variant = (
                        product.variants.filter(id=variant_id, is_available=True).first()
                        if variant_id else None
                    )
                    if not variant:
                        return Response(
                            {'status': 'error', 'message': f'{product.name}: that animal is no longer available.'},
                            status=400,
                        )
                    line_total = variant.total_price()
                elif product.pricing_mode == 'variable_weight':
                    line_total = Decimal(str(product.price)) * Decimal(str(weight or 0))
                else:
                    line_total = Decimal(str(product.price)) * qty

                total += line_total
                cart_snapshot.append({
                    'product_id': product.id, 'weight': weight, 'qty': qty, 'variant_id': variant_id,
                })
        except (Product.DoesNotExist, InvalidOperation, TypeError, ValueError):
            return Response({'status': 'error', 'message': 'One of the items in this cart is no longer valid.'}, status=400)

        try:
            with transaction.atomic():
                sale = POSSale.objects.create(
                    client_sale_id=client_sale_id,
                    cashier=request.user,
                    payment_method=payment_method,
                    cart_snapshot=cart_snapshot,
                    total_amount=total,
                )
                create_inventory_movements_from_snapshot(
                    cart_snapshot, 'sale', source='pos',
                    related_pos_sale=sale, note=f"POS sale {sale.sale_number} (API)",
                    strict=True,
                )
        except DjangoValidationError as e:
            message = '; '.join(e.messages) if hasattr(e, 'messages') else str(e)
            return Response({'status': 'error', 'message': f'Not enough stock: {message}'}, status=409)
        except Exception:
            return Response(
                {'status': 'error', 'message': 'Could not complete this sale — nothing was charged or recorded. Please try again.'},
                status=400,
            )

        return Response({'status': 'ok', 'sale_number': sale.sale_number, 'total': str(total)}, status=status.HTTP_201_CREATED)


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
