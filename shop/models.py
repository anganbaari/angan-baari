from django.db import models
import uuid


class Category(models.Model):
    name = models.CharField(max_length=100)
    parent = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='subcategories'
    )
    icon = models.CharField(max_length=50, blank=True, help_text='FontAwesome class e.g. fa-apple-alt')
    order = models.PositiveIntegerField(default=0, help_text='Display order (lower = first)')

    class Meta:
        ordering = ['order', 'name']
        verbose_name_plural = 'Categories'

    def __str__(self):
        if self.parent:
            return f"{self.parent.name} → {self.name}"
        return self.name

    def is_main(self):
        return self.parent is None


class Product(models.Model):
    name = models.CharField(max_length=200)
    slug = models.SlugField(unique=True)
    category = models.ForeignKey(
        Category,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='products'
    )
    description = models.TextField()
    detail_description = models.TextField(blank=True)
    season = models.CharField(max_length=100, blank=True)
    farming_method = models.CharField(max_length=200, blank=True)
    is_available = models.BooleanField(default=True)
    main_image = models.CharField(max_length=200, blank=True)
    image2 = models.CharField(max_length=200, blank=True)
    image3 = models.CharField(max_length=200, blank=True)
    image4 = models.CharField(max_length=200, blank=True)
    whatsapp_message = models.CharField(max_length=500, blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    price_unit = models.CharField(max_length=50, blank=True, default='per kg')
    created_at = models.DateTimeField(auto_now_add=True)

    PRICING_MODE_CHOICES = [
        ('variable_weight', 'Variable weight — customer picks the weight (fruits, loose pickle)'),
        ('fixed_quantity', 'Fixed quantity — sold per piece/dozen/jar, no weight (banana, jars)'),
        ('fixed_weight', 'Fixed weight, locked price — one specific animal (goat, chicken)'),
    ]
    pricing_mode = models.CharField(
        max_length=20, choices=PRICING_MODE_CHOICES, default='fixed_quantity',
        help_text='Controls how this product behaves in the cart.'
    )
    weight_step = models.DecimalField(
        max_digits=4, decimal_places=2, default=0.50,
        help_text='Only used for "Variable weight" products. The increment customers can '
                   'adjust the weight by, e.g. 0.50 for fruit (500g steps), 0.25 for pickle jars.'
    )
    fixed_weight = models.DecimalField(
        max_digits=6, decimal_places=2, null=True, blank=True,
        help_text='FALLBACK ONLY — used if you add no weight rows below. Once you add at least one '
                   'row in "Weight variants", that takes over and this field is ignored.'
    )
    weight_unit_label = models.CharField(
        max_length=20, default='kg',
        help_text='Unit shown next to the weight/quantity stepper for "Variable weight" products, '
                   'e.g. "kg" for fruit, "dozen" for banana. The price field is always per ONE of this unit.'
    )

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        from django.urls import reverse
        return reverse('product_detail', kwargs={'slug': self.slug})

    def is_variable_weight(self):
        return self.pricing_mode == 'variable_weight'

    def is_fixed_quantity(self):
        return self.pricing_mode == 'fixed_quantity'

    def is_fixed_weight(self):
        return self.pricing_mode == 'fixed_weight'

    def available_variants(self):
        return [v for v in self.variants.all() if v.is_available]

    def locked_total_price(self):
        """For fixed-weight products with NO variant rows added (fallback only):
        the locked total price (rate x product.fixed_weight)."""
        from decimal import Decimal
        if self.pricing_mode == 'fixed_weight' and self.fixed_weight:
            return round(Decimal(str(self.price)) * Decimal(str(self.fixed_weight)), 2)
        return self.price

    def starting_price(self):
        """For variable-weight products: what the SMALLEST purchasable amount
        actually costs (price-per-kg x weight_step), e.g. Rs.500/kg x 0.25kg
        step = Rs.125. This is what should be shown on shop/offer cards
        instead of the full per-kg rate, since a per-kg rate alone reads as
        much more expensive than what someone would actually pay."""
        from decimal import Decimal
        if self.pricing_mode == 'variable_weight' and self.weight_step:
            return round(Decimal(str(self.price)) * Decimal(str(self.weight_step)), 2)
        return self.price

    def starting_weight_label(self):
        """Human label for the smallest step, e.g. '250g', '1kg', or '0.5 dozen'."""
        if self.pricing_mode != 'variable_weight' or not self.weight_step:
            return None
        step = float(self.weight_step)
        unit = (self.weight_unit_label or 'kg').strip()
        if unit.lower() == 'kg' and step < 1:
            return f"{int(round(step * 1000))}g"
        return f"{step:g} {unit}"


class ProductVariant(models.Model):
    """One specific weight listing under a 'Fixed weight' product — e.g. one
    particular goat or chicken currently available. Lets you keep ONE Product
    record (name, photos, description, category) and just add or remove
    weight rows here as new animals become available or sell out, instead of
    creating a brand new product every time."""

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='variants')
    weight = models.DecimalField(max_digits=6, decimal_places=2, help_text='Actual weight of this specific animal in kg, e.g. 20')
    price_override = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        help_text='Leave blank to auto-calculate as product.price x weight. '
                   'Only set this if THIS particular animal is priced differently than the usual per-kg rate.'
    )
    label = models.CharField(max_length=100, blank=True, help_text='Optional note, e.g. "Male, ~1 year old"')
    is_available = models.BooleanField(default=True, help_text='Uncheck once this specific animal is sold, instead of deleting the row.')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['weight']

    def __str__(self):
        return f"{self.product.name} — {self.weight}kg"

    def total_price(self):
        from decimal import Decimal
        if self.price_override is not None:
            return self.price_override
        return round(Decimal(str(self.product.price)) * Decimal(str(self.weight)), 2)


class NewsletterSubscriber(models.Model):
    email = models.EmailField(unique=True)
    name = models.CharField(max_length=100, blank=True)
    subscribed_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.email


class ContactMessage(models.Model):
    name = models.CharField(max_length=200)
    email = models.EmailField()
    phone = models.CharField(max_length=20, blank=True)
    subject = models.CharField(max_length=300)
    message = models.TextField()
    is_read = models.BooleanField(default=False)
    sent_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} — {self.subject}"


class ProductOrder(models.Model):
    STATUS = [
        ('pending', 'Pending'),
        ('confirmed', 'Confirmed'),
        ('delivered', 'Delivered'),
        ('cancelled', 'Cancelled'),
    ]
    order_number = models.CharField(max_length=20, unique=True, blank=True)
    name = models.CharField(max_length=200)
    email = models.EmailField()
    phone = models.CharField(max_length=20)
    address = models.TextField()
    product_interest = models.CharField(max_length=300)
    message = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS, default='pending')
    ordered_at = models.DateTimeField(auto_now_add=True)
    cancel_token = models.CharField(max_length=64, blank=True)

    def save(self, *args, **kwargs):
        if not self.order_number:
            self.order_number = 'AB-' + uuid.uuid4().hex[:8].upper()
        if not self.cancel_token:
            self.cancel_token = uuid.uuid4().hex
        super().save(*args, **kwargs)

    def can_cancel(self):
        from django.utils import timezone
        from datetime import timedelta
        return (
            self.status == 'pending' and
            timezone.now() < self.ordered_at + timedelta(minutes=30)
        )

    def __str__(self):
        return f"{self.order_number} — {self.name}"


class Review(models.Model):
    RATING_CHOICES = [
        (1, '1 - Poor'),
        (2, '2 - Fair'),
        (3, '3 - Good'),
        (4, '4 - Very Good'),
        (5, '5 - Excellent'),
    ]
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='reviews')
    name = models.CharField(max_length=100)
    rating = models.IntegerField(choices=RATING_CHOICES)
    comment = models.TextField()
    is_approved = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} — {self.product.name} ({self.rating}★)"

    class Meta:
        ordering = ['-created_at']

class Wishlist(models.Model):
    user = models.ForeignKey('auth.User', on_delete=models.CASCADE, related_name='wishlist')
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'product')
        ordering = ['-added_at']

    def __str__(self):
        return f"{self.user.username} → {self.product.name}"        
    
# Add this to shop/models.py

class Offer(models.Model):
    DISCOUNT_TYPE = [
        ('percent', 'Percentage Off'),
        ('fixed', 'Fixed Amount Off'),
        ('combo', 'Combo Deal'),
    ]

    title = models.CharField(max_length=200, help_text='e.g. "Mango Mania Sale"')
    description = models.CharField(max_length=300, blank=True, help_text='Short tagline shown on badge/banner')
    discount_type = models.CharField(max_length=10, choices=DISCOUNT_TYPE, default='percent')
    discount_value = models.DecimalField(max_digits=10, decimal_places=2, help_text='e.g. 20 for 20% off, or 50 for Rs.50 off')
    combo_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, help_text='Only for combo deals - total bundle price')

    products = models.ManyToManyField(Product, blank=True, related_name='offers', help_text='Leave empty if applying to whole category')
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, blank=True, related_name='offers', help_text='Apply to all products in this category')

    start_date = models.DateTimeField()
    end_date = models.DateTimeField()
    is_active = models.BooleanField(default=True)
    banner_image = models.CharField(max_length=200, blank=True, help_text='Optional banner image path')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.title

    def is_live(self):
        from django.utils import timezone
        now = timezone.now()
        return self.is_active and self.start_date <= now <= self.end_date

    def get_products(self):
        if self.products.exists():
            return self.products.all()
        elif self.category:
            ids = [self.category.id]
            for sub in self.category.subcategories.all():
                ids.append(sub.id)
                for subsub in sub.subcategories.all():
                    ids.append(subsub.id)
            return Product.objects.filter(category__id__in=ids)
        return Product.objects.none()

    def discounted_price(self, original_price):
        from decimal import Decimal
        original_price = Decimal(str(original_price))
        if self.discount_type == 'percent':
            return round(original_price - (original_price * self.discount_value / 100), 2)
        elif self.discount_type == 'fixed':
            return max(round(original_price - self.discount_value, 2), Decimal('0'))
        return original_price

    def get_bundle_natural_total(self):
        """Sum of quantity x product price for all bundle items (only relevant for combo offers)."""
        from decimal import Decimal
        return sum([item.line_total() for item in self.bundle_items.all()], Decimal('0'))


class BundleItem(models.Model):
    """One line inside a combo/bundle Offer — a product plus how much of it
    is included (e.g. 1.9 kg Local Chicken, 2 kg Mango, 3 pcs Lemon).
    Price for this line is calculated automatically as quantity x product.price,
    which matters for products sold by weight (chicken, goat, veggies)."""

    offer = models.ForeignKey(Offer, on_delete=models.CASCADE, related_name='bundle_items')
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    quantity = models.DecimalField(
        max_digits=6, decimal_places=2, default=1,
        help_text='Amount of this product in the bundle. E.g. 1.9 for 1.9 kg chicken, 3 for 3 pieces of lemon.'
    )

    class Meta:
        ordering = ['id']

    def line_total(self):
        from decimal import Decimal
        if not self.product.price:
            return Decimal('0')
        return Decimal(str(self.quantity)) * Decimal(str(self.product.price))

    def __str__(self):
        return f"{self.quantity} x {self.product.name}"


class Coupon(models.Model):
    """Festival coupon codes (Dashain, Tihar, Holi, etc).
    Multiple coupons can be live at once, but only one is applied per order."""

    DISCOUNT_TYPE = [
        ('percent', 'Percentage Off'),
        ('fixed', 'Fixed Amount Off'),
    ]

    code = models.CharField(max_length=30, unique=True, help_text='e.g. DASHAIN25 — will be stored uppercase')
    festival_name = models.CharField(max_length=100, blank=True, help_text='e.g. Dashain, Tihar, Holi')
    description = models.CharField(max_length=200, blank=True, help_text='Shown next to the coupon code on the offers page')

    discount_type = models.CharField(max_length=10, choices=DISCOUNT_TYPE, default='percent')
    discount_value = models.DecimalField(max_digits=10, decimal_places=2, help_text='e.g. 25 for 25% off, or 200 for Rs.200 off')
    max_discount_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, help_text='Cap on discount for percentage coupons (optional)')
    min_order_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text='Minimum cart subtotal required to use this coupon')

    start_date = models.DateTimeField()
    end_date = models.DateTimeField()
    is_active = models.BooleanField(default=True)

    max_uses = models.PositiveIntegerField(null=True, blank=True, help_text='Leave blank for unlimited uses')
    used_count = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-start_date']

    def __str__(self):
        return f"{self.code} ({self.festival_name})" if self.festival_name else self.code

    def save(self, *args, **kwargs):
        if self.code:
            self.code = self.code.strip().upper()
        super().save(*args, **kwargs)

    def is_live(self):
        from django.utils import timezone
        now = timezone.now()
        if not self.is_active:
            return False
        if not (self.start_date <= now <= self.end_date):
            return False
        if self.max_uses is not None and self.used_count >= self.max_uses:
            return False
        return True

    def calculate_discount(self, subtotal):
        from decimal import Decimal
        subtotal = Decimal(str(subtotal))
        if subtotal < self.min_order_amount:
            return Decimal('0')
        if self.discount_type == 'percent':
            discount = subtotal * self.discount_value / 100
            if self.max_discount_amount:
                discount = min(discount, self.max_discount_amount)
        else:
            discount = self.discount_value
        return min(discount, subtotal)