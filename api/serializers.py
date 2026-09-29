from django.contrib.auth import authenticate
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.cache import cache
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from shop.models import (
    Category, Product, ProductVariant, InventoryMovement, POSSale, ProductOrder,
    Wishlist, Coupon,
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


class POSSaleReadSerializer(serializers.ModelSerializer):
    cashier_username = serializers.CharField(source='cashier.username', read_only=True)
    payment_method_display = serializers.CharField(source='get_payment_method_display', read_only=True)

    class Meta:
        model = POSSale
        fields = [
            'id', 'sale_number', 'cashier', 'cashier_username', 'payment_method',
            'payment_method_display', 'cart_snapshot', 'total_amount',
            'client_sale_id', 'created_at',
        ]


class POSSaleCreateSerializer(serializers.Serializer):
    """Input-shape validation only — the actual total computation, stock
    check and InventoryMovement writes happen in the view, exactly like
    pos_create_sale() in shop/views.py, since that's where product/variant
    lookups and server-side price recomputation have to happen anyway."""

    client_sale_id = serializers.CharField(max_length=64)
    payment_method = serializers.CharField()
    cart = serializers.ListField(child=serializers.DictField(), allow_empty=False)


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
