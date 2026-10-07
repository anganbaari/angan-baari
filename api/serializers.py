from decimal import Decimal

from django.contrib.auth import authenticate
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.cache import cache
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from shop.models import (
    Category, Product, ProductVariant, InventoryMovement, POSSale, ProductOrder,
    Wishlist, Coupon, Customer, CreditTransaction, CostEntry,
)


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ['id', 'name', 'parent', 'icon', 'order']


class ProductVariantSerializer(serializers.ModelSerializer):
    total_price = serializers.SerializerMethodField()

    class Meta:
        model = ProductVariant
        fields = ['id', 'weight', 'price_override', 'label', 'is_available', 'total_price']

    def get_total_price(self, obj):
        return str(obj.total_price())


class ProductSerializer(serializers.ModelSerializer):
    category = CategorySerializer(read_only=True)
    variants = serializers.SerializerMethodField()
    starting_price = serializers.SerializerMethodField()
    starting_weight_label = serializers.SerializerMethodField()
    locked_total_price = serializers.SerializerMethodField()
    main_image = serializers.SerializerMethodField()
    images = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = [
            'id', 'name', 'slug', 'category', 'description', 'detail_description',
            'season', 'farming_method', 'is_available', 'main_image', 'images',
            'price', 'price_unit', 'origin', 'barcode', 'pricing_mode',
            'weight_step', 'fixed_weight', 'weight_unit_label',
            'variants', 'starting_price', 'starting_weight_label',
            'locked_total_price', 'created_at',
        ]

    def get_variants(self, obj):
        # Only fixed_weight products (goat/chicken) carry variant rows —
        # matches Product.available_variants(), only-available-ones,
        # same as what the public shop page and POS screen show.
        if obj.pricing_mode != 'fixed_weight':
            return []
        return ProductVariantSerializer(obj.available_variants(), many=True).data

    def get_starting_price(self, obj):
        value = obj.starting_price()
        return str(value) if value is not None else None

    def get_starting_weight_label(self, obj):
        return obj.starting_weight_label()

    def get_locked_total_price(self, obj):
        value = obj.locked_total_price()
        return str(value) if value is not None else None

    def get_main_image(self, obj):
        return obj.main_image.url if obj.main_image else None

    def get_images(self, obj):
        """Every photo this product has, in gallery order.

        There is no ProductImage/M2M model — Product carries four flat
        ImageFields (main_image, image2, image3, image4, all blank=True),
        and product_detail.html builds its thumb grid + lightbox from
        exactly these four, in this order, skipping the blank ones. This
        mirrors that, so a frontend gallery can consume `images` wholesale.

        main_image is included as images[0] (same as the reference page,
        whose first thumbnail IS the main image) — so don't render
        main_image separately on top of this list. `main_image` is kept as
        its own field for existing consumers.
        """
        return [
            field.url
            for field in (obj.main_image, obj.image2, obj.image3, obj.image4)
            if field
        ]


class InventoryMovementReadSerializer(serializers.ModelSerializer):
    """Read-only v1 view onto the ledger — separate from
    shop.api_serializers.InventoryMovementSerializer, which is the ABMS
    write-path serializer at /api/inventory/movements/ and is untouched."""

    product_name = serializers.CharField(source='product.name', read_only=True)
    variant_label = serializers.SerializerMethodField()
    signed_quantity = serializers.SerializerMethodField()

    class Meta:
        model = InventoryMovement
        fields = [
            'id', 'product', 'product_name', 'variant', 'variant_label',
            'movement_type', 'source', 'quantity', 'signed_quantity',
            'related_order', 'related_pos_sale', 'note', 'created_at',
        ]

    def get_variant_label(self, obj):
        return str(obj.variant) if obj.variant_id else None

    def get_signed_quantity(self, obj):
        return str(obj.signed_quantity())


class CostEntryReadSerializer(serializers.ModelSerializer):
    """Read-only v1 view onto the cost ledger synced from ABMS (see
    shop.costs_views.CostSyncView, /api/costs/sync/, which is the write
    path and is untouched here) — for the future P&L dashboard."""

    class Meta:
        model = CostEntry
        fields = [
            'id', 'abms_id', 'date', 'amount', 'entry_type', 'reversal_of',
            'cost_centre', 'category', 'qty', 'unit', 'supplier', 'note',
            'is_shared', 'allocation_rule', 'allocation_manual',
            'entered_by_email', 'abms_created_at', 'received_at',
        ]


class POSSaleReadSerializer(serializers.ModelSerializer):
    cashier_username = serializers.CharField(source='cashier.username', read_only=True)
    payment_method_display = serializers.CharField(source='get_payment_method_display', read_only=True)

    coupon_code = serializers.CharField(source='coupon.code', read_only=True, default=None)

    class Meta:
        model = POSSale
        fields = [
            'id', 'sale_number', 'cashier', 'cashier_username', 'payment_method',
            'payment_method_display', 'cart_snapshot', 'total_amount',
            'client_sale_id', 'created_at', 'coupon_code', 'discount_amount', 'round_off_amount',
        ]


class POSSaleCreateSerializer(serializers.Serializer):
    """Input-shape validation only — the actual total/VAT computation,
    payments-sum check, and InventoryMovement writes happen in
    create_pos_sale() (shop/views.py), since that's where product/variant
    lookups and server-side price recomputation have to happen anyway.

    No general operator_id field here deliberately: the operator normally
    comes from request.session (set only by a verified PIN on POST
    /pos/unlock/, see get_pos_operator() in shop/views.py), never from
    client-supplied request data — otherwise any request could just claim
    to be any staff member without that person actually entering their PIN.

    queued_operator_id is the one narrow, deliberate exception: see
    get_pos_operator()'s docstring. It's only ever populated by
    static/js/pos-offline-queue.js replaying a sale that was queued while
    offline (pos.html captures currentOperator.id into the payload at the
    moment it's queued, not at replay time) — a live online sale never
    sends it, and still relies on request.session exactly as before.

    payments/cart are left as loose dicts (not nested serializers) to
    match this file's existing cart-shape convention — real validation of
    each line happens in create_pos_sale() either way."""

    client_sale_id = serializers.CharField(max_length=64)
    customer_id = serializers.IntegerField(required=False, allow_null=True)
    cart = serializers.ListField(child=serializers.DictField(), allow_empty=False)
    payments = serializers.ListField(child=serializers.DictField(), allow_empty=False)
    coupon_code = serializers.CharField(max_length=30, required=False, allow_null=True, allow_blank=True)
    queued_operator_id = serializers.IntegerField(required=False, allow_null=True)


class PosUnlockSerializer(serializers.Serializer):
    """Shape validation only — a non-4-digit PIN can never match any
    check_pin() anyway, but rejecting it here keeps a malformed/huge value
    from being hashed-compared against every staff profile for nothing."""

    pin = serializers.RegexField(regex=r'^\d{4}$', error_messages={'invalid': 'PIN must be exactly 4 digits.'})


class OrderCreateSerializer(serializers.Serializer):
    """Input-shape validation only, mirroring checkout()'s POST fields plus
    an explicit cart line list (this API has no session cart to read from)."""

    name = serializers.CharField(max_length=200)
    email = serializers.EmailField()
    phone = serializers.CharField(max_length=20, required=False, allow_blank=True, default='')
    address = serializers.CharField(required=False, allow_blank=True, default='')
    message = serializers.CharField(required=False, allow_blank=True, default='')
    cart = serializers.ListField(child=serializers.DictField(), allow_empty=False)


class OrderStatusSerializer(serializers.ModelSerializer):
    """The no-proof-of-ownership default — excludes name/email/phone/
    address/cancel_token, since a plain integer id alone is guessable and
    would otherwise let anyone enumerate other customers' orders."""

    class Meta:
        model = ProductOrder
        fields = ['id', 'order_number', 'status', 'ordered_at', 'product_interest']


class OrderDetailSerializer(serializers.ModelSerializer):
    """Full order detail — only returned once the caller has already proved
    ownership by presenting the order's own cancel_token (the same secret
    already emailed to the customer on send_order_received_email, and used
    today by the /cancel/<token>/ link). cancel_token itself is never
    echoed back here."""

    can_cancel = serializers.SerializerMethodField()

    class Meta:
        model = ProductOrder
        fields = [
            'id', 'order_number', 'name', 'email', 'phone', 'address',
            'product_interest', 'message', 'status', 'ordered_at',
            'cart_snapshot', 'can_cancel',
        ]

    def get_can_cancel(self, obj):
        return obj.can_cancel()


# ─── AUTH (token-based, cross-origin) ──────────────────────────
# Same underlying user system as shop/auth_views.py (signup/login_view/
# forgot_password/reset_password) — same auth.User table, username=email,
# same create_user()/authenticate() calls, same cache-based reset-token
# scheme (a token minted here is interchangeable with one minted by the
# traditional /account/ views, and vice versa). This is a second, additive
# entry point onto that one user system, not a parallel one. It differs
# from the traditional views in one deliberate way: password strength is
# checked with Django's own validate_password() (all 4
# AUTH_PASSWORD_VALIDATORS already configured in settings.py), not the
# traditional signup/reset views' plain len(password) < 8 check — that gap
# already exists there today and isn't touched by this addition.

class SignupSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=150)
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)
    confirm_password = serializers.CharField(write_only=True)

    def validate_email(self, value):
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError('An account with this email already exists.')
        return value

    def validate(self, data):
        if data['password'] != data['confirm_password']:
            raise serializers.ValidationError({'confirm_password': ['Passwords do not match.']})
        try:
            validate_password(data['password'])
        except DjangoValidationError as e:
            raise serializers.ValidationError({'password': e.messages})
        return data


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)

    def validate(self, data):
        user = authenticate(username=data['email'], password=data['password'])
        if not user:
            raise serializers.ValidationError('Invalid email or password.')
        data['user'] = user
        return data


class PasswordResetRequestSerializer(serializers.Serializer):
    """Shape validation only — deliberately never raises for an unknown
    email (the view always responds the same way either way), matching
    forgot_password()'s own anti-enumeration behavior exactly."""

    email = serializers.EmailField()


class PasswordResetConfirmSerializer(serializers.Serializer):
    token = serializers.CharField()
    password = serializers.CharField(write_only=True)
    confirm_password = serializers.CharField(write_only=True)

    def validate(self, data):
        user_id = cache.get(f"pwd_reset_{data['token']}")
        if not user_id:
            raise serializers.ValidationError(
                {'token': ['This reset link is invalid or has expired.']}
            )
        if data['password'] != data['confirm_password']:
            raise serializers.ValidationError({'confirm_password': ['Passwords do not match.']})
        try:
            validate_password(data['password'])
        except DjangoValidationError as e:
            raise serializers.ValidationError({'password': e.messages})
        data['user_id'] = user_id
        return data


# ─── PROFILE PAGE (token-authenticated) ─────────────────────────
# Backs the Next.js Profile page (Phase 4). Every endpoint here reuses the
# same data/logic the traditional profile.html + shop/auth_views.py +
# shop/views.py already use — see the Phase 4 scoping report for the
# investigation this is built from. ProductOrder has no User FK (orders are
# matched to the logged-in user by email, same as profile()'s own query),
# and Coupon has no per-user relation either (this is the same global
# active-coupon list get_live_coupons() already returns to the public
# offers() page and the traditional profile page's "My Coupons" section).

class OrderHistorySerializer(serializers.ModelSerializer):
    """One row of 'my orders' — deliberately excludes cancel_token (cancel
    ownership here is proven by the caller's auth token + email match, not
    by presenting the token, unlike the anonymous OrderDetailSerializer
    flow) and excludes name/phone (already known to the logged-in caller)."""

    status_display = serializers.CharField(source='get_status_display', read_only=True)
    can_cancel = serializers.SerializerMethodField()
    has_cart_snapshot = serializers.SerializerMethodField()

    class Meta:
        model = ProductOrder
        fields = [
            'id', 'order_number', 'ordered_at', 'status', 'status_display',
            'product_interest', 'address', 'can_cancel', 'has_cart_snapshot',
        ]

    def get_can_cancel(self, obj):
        return obj.can_cancel()

    def get_has_cart_snapshot(self, obj):
        return bool(obj.cart_snapshot)


class WishlistItemSerializer(serializers.ModelSerializer):
    """Reuses ProductSerializer wholesale (including its `variants` field,
    already scoped to available_variants() for fixed_weight products) so the
    frontend has everything needed to reproduce profile.html's wishlist
    card — including the size/animal dropdown — without a second request."""

    product = ProductSerializer(read_only=True)
    variant = ProductVariantSerializer(read_only=True)

    class Meta:
        model = Wishlist
        fields = ['id', 'product', 'variant', 'added_at']


class CouponSerializer(serializers.ModelSerializer):
    """Only the fields profile.html's coupon card and the public offers page
    actually display — not start_date/end_date/max_uses/used_count, which
    are backend-only bookkeeping."""

    class Meta:
        model = Coupon
        fields = [
            'id', 'code', 'festival_name', 'description',
            'discount_type', 'discount_value', 'max_discount_amount', 'min_order_amount',
        ]


class ProfileUpdateSerializer(serializers.Serializer):
    """Matches edit_profile() exactly: only name and email are editable,
    email must be unique account-wide (excluding the caller's own row)."""

    name = serializers.CharField(max_length=150)
    email = serializers.EmailField()

    def validate_email(self, value):
        request = self.context['request']
        if User.objects.filter(email=value).exclude(id=request.user.id).exists():
            raise serializers.ValidationError('Another account already uses that email.')
        return value


class ChangePasswordSerializer(serializers.Serializer):
    """Same validation change_password()'s PasswordChangeForm performs:
    correct old password, new1==new2, and Django's full password-validator
    chain (unlike signup/reset, change_password() already ran through
    validate_password()-equivalent checks via PasswordChangeForm, so there's
    no gap to preserve here)."""

    old_password = serializers.CharField(write_only=True)
    new_password1 = serializers.CharField(write_only=True)
    new_password2 = serializers.CharField(write_only=True)

    def validate_old_password(self, value):
        user = self.context['request'].user
        if not user.check_password(value):
            raise serializers.ValidationError('Your old password was entered incorrectly.')
        return value

    def validate(self, data):
        if data['new_password1'] != data['new_password2']:
            raise serializers.ValidationError({'new_password2': ["The two password fields didn't match."]})
        try:
            validate_password(data['new_password1'], user=self.context['request'].user)
        except DjangoValidationError as e:
            raise serializers.ValidationError({'new_password1': e.messages})
        return data


class WishlistToggleSerializer(serializers.Serializer):
    product_id = serializers.IntegerField()
    variant_id = serializers.IntegerField(required=False, allow_null=True)


class WishlistSetVariantSerializer(serializers.Serializer):
    product_id = serializers.IntegerField()
    variant_id = serializers.IntegerField()


class WishlistMoveToCartSerializer(serializers.Serializer):
    product_id = serializers.IntegerField()
    variant_id = serializers.IntegerField(required=False, allow_null=True)


# ─── POS Phase A/B: customer + credit (उधारो) ledger ────────────

class CustomerSerializer(serializers.ModelSerializer):
    """outstanding_balance is always computed (Customer.outstanding_balance()),
    never a stored field — see that method for why."""

    outstanding_balance = serializers.SerializerMethodField()

    class Meta:
        model = Customer
        fields = ['id', 'name', 'nickname', 'phone', 'address', 'outstanding_balance']

    def get_outstanding_balance(self, obj):
        return str(obj.outstanding_balance())


class CustomerCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=200)
    nickname = serializers.CharField(max_length=100, required=False, allow_blank=True, default='')
    phone = serializers.CharField(max_length=20)
    address = serializers.CharField()


class PosQueueFailureAlertSerializer(serializers.Serializer):
    """Input-shape validation for the offline-queue failure alert endpoint
    -- see PosQueueFailureAlertView's docstring (api/views.py) for why this
    exists. `payload` is the exact sale body the queue tried to replay
    (cart/payments/etc.), passed through as-is just to summarize in the
    Telegram message -- not re-validated or acted on here."""

    client_sale_id = serializers.CharField(max_length=64)
    error = serializers.CharField(allow_blank=True, required=False, default='')
    payload = serializers.DictField()


class PosQueueDismissedReportSerializer(serializers.Serializer):
    """Input-shape validation for the dismissed-queue-entry report endpoint
    -- see PosQueueDismissedReportView's docstring (api/views.py). Unlike
    PosQueueFailureAlertSerializer above, nothing here is re-validated or
    acted on either (this view only ever sends a Telegram message, never
    writes anything to the database), so there's no risk in trusting these
    fields as display text -- `status`/`last_error`/`queued_at` exist
    purely to tell a human reading the report what state this entry was
    stuck in and when it was originally rung up."""

    client_sale_id = serializers.CharField(max_length=64)
    payload = serializers.DictField()
    status = serializers.CharField(max_length=20)
    last_error = serializers.CharField(allow_blank=True, required=False, default='')
    queued_at = serializers.CharField(allow_blank=True, required=False, default='')


class CouponValidateSerializer(serializers.Serializer):
    """Input-shape validation for the POS coupon-preview endpoint. The
    actual coupon rules (is_live, min_order_amount, discount calculation)
    live in shop.views.resolve_pos_coupon() — same function create_pos_sale()
    uses to re-validate at checkout — not here."""

    code = serializers.CharField(max_length=30)
    subtotal = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal('0'))


class PosRestockSerializer(serializers.Serializer):
    """Input-shape validation for the POS restock endpoint. quantity gets
    the model's own 3-decimal-place precision (InventoryMovement.quantity)
    so a scale-weighed restock isn't forced to the nearest 10g. The actual
    business rules (which movement_type, full_clean()'s own checks) live in
    PosRestockView/InventoryMovement.clean() -- not here.

    origin is explicit per restock, not derived from product.origin -- a
    normally-farm-grown product (e.g. chilly) is occasionally bought in
    when the farm has none that day, and the ledger needs to record what
    actually happened, not just the product's usual default. The POS
    restock modal pre-selects product.origin so the common case stays one
    click, but staff can override it for this one restock."""

    product_id = serializers.IntegerField()
    quantity = serializers.DecimalField(max_digits=8, decimal_places=3, min_value=Decimal('0.001'))
    origin = serializers.ChoiceField(choices=Product.ORIGIN_CHOICES)
    note = serializers.CharField(max_length=300, required=False, allow_blank=True, default='')


class ProductOriginUpdateSerializer(serializers.Serializer):
    """Input-shape validation for patching a product's own default origin
    from the POS stock screen -- separate from PosRestockSerializer's
    per-restock origin override above: this changes what the product's
    sourcing normally is going forward, not just one restock event."""

    origin = serializers.ChoiceField(choices=Product.ORIGIN_CHOICES)


class CreditRepaySerializer(serializers.Serializer):
    """Shape validation only. amount > 0 is enforced here; deliberately NOT
    capped at the customer's current balance -- a repayment larger than
    what's owed is a real situation (rounding, the customer overpaying),
    not a client error, so it's recorded as entered rather than clamped.

    No operator_id: same reasoning as POSSaleCreateSerializer -- the
    operator comes from request.session (get_pos_operator()), never from
    client-supplied request data."""

    customer_id = serializers.IntegerField()
    amount = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal('0.01'))
