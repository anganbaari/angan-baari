import secrets
import uuid
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from rest_framework import generics, permissions, status
from rest_framework.authentication import SessionAuthentication, TokenAuthentication
from rest_framework.authtoken.models import Token
from rest_framework.response import Response
from rest_framework.views import APIView

from shop.emails import send_order_received_email, send_resend_email
from shop.models import Category, InventoryMovement, POSSale, Product, ProductOrder
from shop.views import create_inventory_movements_from_snapshot, create_order_inventory_movements

from .permissions import IsStaffUser
from .serializers import (
    CategorySerializer,
    InventoryMovementReadSerializer,
    LoginSerializer,
    OrderCreateSerializer,
    OrderDetailSerializer,
    OrderStatusSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    POSSaleCreateSerializer,
    POSSaleReadSerializer,
    ProductSerializer,
    SignupSerializer,
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
