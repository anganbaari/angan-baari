from rest_framework import serializers

from shop.models import (
    Category, Product, ProductVariant, InventoryMovement, POSSale, ProductOrder,
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
