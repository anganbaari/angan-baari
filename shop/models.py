from django.db import models
import uuid
from .imagekit_storage import ImageKitStorage


class UserProfile(models.Model):
    """POS role + PIN-unlock identity for a staff account. Separate from the
    Django login that stays active on a shared POS terminal all day — the PIN
    answers "who is actually standing at the register right now", not "is
    this browser session allowed on /pos/ at all" (that's still is_staff).

    role currently only labels who someone is; it doesn't gate anything by
    itself yet (POS Phase A is staff-roles + PIN unlock only — refunds/
    discount limits/reports come in later phases and will check this field
    then). pin_hash is never set directly — always go through set_pin(), same
    reasoning as never hand-hashing a login password."""

    user = models.OneToOneField('auth.User', on_delete=models.CASCADE, related_name='profile')
    ROLE_CHOICES = [('admin', 'Admin'), ('manager', 'Manager'), ('cashier', 'Cashier')]
    role = models.CharField(max_length=10, choices=ROLE_CHOICES, default='cashier')
    pin_hash = models.CharField(max_length=128, blank=True, help_text='Set via set_pin() — never store or edit this as plaintext.')
    staff_number = models.CharField(
        max_length=10, blank=True,
        help_text='Shown on the POS header once this person unlocks (e.g. "NIKESH 001") — '
                   'deliberately separate from both the PIN and the DB id, since neither of '
                   'those should ever be displayed on screen. Set once per staff member.'
    )

    def set_pin(self, raw_pin: str):
        """Fast HMAC-SHA256 + per-profile salt, deliberately NOT Django's own
        password hashers (PBKDF2 at 600k+ iterations). A 4-digit PIN's real
        protection is the 7-attempt/5-minute lockout in PosUnlockView, not
        hash cost -- a slow hash here only adds latency (this was the
        ~4-second PIN-unlock delay on PythonAnywhere's free-tier CPU,
        multiplied by every staff profile PosUnlockView has to check).
        Stored as 'salt$digest' (hex), both derived from HMAC with
        settings.PIN_HASH_SECRET (falling back to SECRET_KEY) as the pepper.
        This is a different format from Django's own hashers, so existing
        PINs set before this change won't verify -- staff re-set their PIN
        once via the admin form after this deploys."""
        import hashlib
        import hmac
        import os
        from django.conf import settings

        salt = os.urandom(16).hex()
        pepper = getattr(settings, 'PIN_HASH_SECRET', None) or settings.SECRET_KEY
        digest = hmac.new((pepper + salt).encode(), raw_pin.encode(), hashlib.sha256).hexdigest()
        self.pin_hash = f'{salt}${digest}'

    def check_pin(self, raw_pin: str) -> bool:
        import hashlib
        import hmac
        from django.conf import settings

        if not self.pin_hash or '$' not in self.pin_hash:
            return False
        salt, digest = self.pin_hash.split('$', 1)
        pepper = getattr(settings, 'PIN_HASH_SECRET', None) or settings.SECRET_KEY
        expected = hmac.new((pepper + salt).encode(), raw_pin.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, digest)

    def __str__(self):
        return f"{self.user.username} ({self.get_role_display()})"


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
    is_taxable = models.BooleanField(
        default=False,
        help_text='VAT scaffolding — dormant until BusinessSettings.is_vat_enabled is '
                   'switched on. Most of this catalog (fresh produce, live animals, milk, '
                   'eggs) is VAT-exempt under Nepali law; only check this for a '
                   'processed/packaged item once VAT registration actually happens.'
    )
    low_stock_threshold = models.PositiveIntegerField(
        default=5,
        help_text='When current stock drops to this level or below, the product is flagged '
                   'on the POS stock-alert table and triggers a one-time Telegram notification '
                   'the moment stock crosses down past this line (see shop/signals.py). Not '
                   'used for "Fixed weight" products (goats/chickens) -- those are tracked '
                   'per-animal, not by a stock count.'
    )
    main_image = models.ImageField(storage=ImageKitStorage(), upload_to='products/', blank=True)
    image2 = models.ImageField(storage=ImageKitStorage(), upload_to='products/', blank=True)
    image3 = models.ImageField(storage=ImageKitStorage(), upload_to='products/', blank=True)
    image4 = models.ImageField(storage=ImageKitStorage(), upload_to='products/', blank=True)
    whatsapp_message = models.CharField(max_length=500, blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    price_unit = models.CharField(max_length=50, blank=True, default='per kg')
    created_at = models.DateTimeField(auto_now_add=True)

    ORIGIN_CHOICES = [
        ('farm', 'Grown/Raised on our farm'),
        ('sourced', 'Sourced from other Nepali producers'),
    ]
    origin = models.CharField(
        max_length=10, choices=ORIGIN_CHOICES, default='farm',
        help_text='Controls the "Our Farm" / "Sourced in Nepal" label shown on the '
                   'shop and product pages. Defaults to farm-grown — flip individual '
                   'products (e.g. Apple, Kiwi) to "sourced" if they aren\'t actually '
                   'grown here.'
    )

    barcode = models.CharField(
        max_length=64, unique=True, null=True, blank=True,
        help_text='For packaged goods only (jars, bottles). Scan a blank label '
                   'with your barcode scanner into this field once, print that '
                   'same code onto your product label, and the POS will recognize '
                   'it at checkout. Leave blank for anything sold by weight/variant.'
    )

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
        help_text='Only used for "Variable weight" products. The POS keypad\'s +/- nudge '
                   'increment while "Kg" is selected, e.g. 0.50 for fruit (500g steps), '
                   '0.25 for pickle jars.'
    )
    WEIGHT_ENTRY_MODE_CHOICES = [
        ('stepper', 'Stepper — POS keypad starts on Kg'),
        ('exact', 'Exact — POS keypad starts on Gram'),
    ]
    weight_entry_mode = models.CharField(
        max_length=10, choices=WEIGHT_ENTRY_MODE_CHOICES, default='stepper',
        help_text='Only used for "Variable weight" products. Both modes use the same POS '
                   'keypad (a Kg/Gram unit switcher with +/- nudges) -- this only picks '
                   'which unit starts selected when the modal opens. "Exact" starts on '
                   'Gram, for produce typically weighed on a scale rather than bought in '
                   'round step sizes (coriander, dragon fruit, watermelon, papaya, '
                   'cauliflower). "Stepper" starts on Kg.'
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

    def available_variant_count(self):
        return len(self.available_variants())

    def available_selling_units(self):
        return [u for u in self.selling_units.all() if u.is_available]

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


class ProductSellingUnit(models.Model):
    """A named way ONE 'Fixed quantity' product can be sold, each with its
    own independently-set price -- e.g. eggs sold both by the Piece and by
    the Crate (~30 eggs), where the crate price is a bulk discount, NOT
    simply 30x the piece price. Each unit's price is entered directly here,
    never derived from another unit. Analogous to ProductVariant for
    fixed_weight products: lets one Product record carry several selling
    units instead of needing a separate Product per unit.

    Only meaningful for pricing_mode='fixed_quantity'. A fixed_quantity
    product with NO rows here behaves exactly as it did before this model
    existed (its own price/price_unit fields, plain quantity stepper) --
    this is additive, the same fallback relationship product.fixed_weight
    already has with ProductVariant."""

    UNIT_NAME_CHOICES = [
        ('piece', 'Piece'),
        ('kg', 'Kg'),
        ('gram', 'Gram'),
        ('dozen', 'Dozen'),
        ('liter', 'Liter'),
        ('milliliter', 'Milliliter'),
        ('crate', 'Crate'),
    ]

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='selling_units')
    name = models.CharField(max_length=20, choices=UNIT_NAME_CHOICES)
    price = models.DecimalField(
        max_digits=10, decimal_places=2,
        help_text="This unit's own price, e.g. Rs. 20 for Piece, Rs. 550 for Crate. "
                   "Entered directly, never calculated by multiplying another unit's "
                   "price -- bulk units are usually discounted, not a flat multiple."
    )
    quantity_in_base_units = models.DecimalField(
        max_digits=8, decimal_places=2, default=1,
        help_text="How many of this product's own stock-counting unit ONE of this "
                   "selling unit equals -- e.g. 1 for Piece, 30 for a Crate of eggs. "
                   "Only used to deduct the correct amount from inventory when this "
                   "unit is sold; never used to calculate price."
    )
    is_default = models.BooleanField(
        default=False,
        help_text='Pre-selected in the POS keypad modal when this product has more than one unit.'
    )
    is_available = models.BooleanField(
        default=True,
        help_text='Uncheck to stop offering this unit at POS without deleting its price history.'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['id']
        unique_together = [('product', 'name')]

    def __str__(self):
        return f"{self.product.name} — {self.get_name_display()} (Rs. {self.price})"


class NewsletterSubscriber(models.Model):
    email = models.EmailField(unique=True)
    name = models.CharField(max_length=100, blank=True)
    subscribed_at = models.DateTimeField(auto_now_add=True)
    is_subscribed = models.BooleanField(
        default=True,
        help_text='Automatically unchecked when the subscriber clicks the unsubscribe link in an email.'
    )
    unsubscribe_token = models.CharField(max_length=64, blank=True)

    def save(self, *args, **kwargs):
        if not self.unsubscribe_token:
            self.unsubscribe_token = uuid.uuid4().hex
        super().save(*args, **kwargs)

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
    cart_snapshot = models.JSONField(
        null=True, blank=True,
        help_text='Structured line items (product id, weight, qty) captured at checkout time, '
                   'used by the "Reorder" button. Orders placed before this field existed have '
                   'no snapshot, so they won\'t show a working Reorder button.'
    )

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
    variant = models.ForeignKey(
        'ProductVariant', null=True, blank=True, on_delete=models.SET_NULL, related_name='wishlisted_by',
        help_text='Only used for "Fixed weight" products (goat/chicken) — which specific size/animal '
                   'was picked. Left blank for every other pricing mode.'
    )
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
    banner_image = models.ImageField(storage=ImageKitStorage(), upload_to='offers/', blank=True, help_text='Optional banner image')
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
    reference_weight = models.DecimalField(
        max_digits=6, decimal_places=2, null=True, blank=True,
        help_text='For a fixed-weight bundle item: the animal weight this combo\'s '
                   'advertised price assumes. Picking a variant at or below this weight '
                   'costs exactly the combo price; picking a heavier one adds the real '
                   'price difference. Only meaningful when the bundled product is '
                   '"Fixed weight". Leave blank to auto-fill with the cheapest currently '
                   'available animal\'s weight when this row is first saved -- once set '
                   '(auto-filled or typed in), it stays fixed and is never recomputed as '
                   'stock changes.'
    )

    class Meta:
        ordering = ['id']

    def save(self, *args, **kwargs):
        # Auto-fill once, on whichever save first leaves this blank with a
        # fixed_weight product attached -- never re-derived afterward, which
        # is the whole point of a reference weight: it's what the owner's
        # advertised combo price assumes, not a floating "today's cheapest".
        if self.reference_weight is None and self.product_id and self.product.pricing_mode == 'fixed_weight':
            available = self.product.available_variants()
            if available:
                self.reference_weight = min(available, key=lambda v: v.total_price()).weight
        super().save(*args, **kwargs)

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


def get_live_coupons():
    """Every currently-live coupon (active, within date range, under its use
    cap) — shared by the public offers page, the traditional profile page's
    "My Coupons" section, and the /api/v1/coupons/ endpoint, so all three
    stay in sync instead of re-implementing the same filter three times."""
    from django.utils import timezone
    now = timezone.now()
    candidates = Coupon.objects.filter(is_active=True, start_date__lte=now, end_date__gte=now)
    return [c for c in candidates if c.is_live()]


class InventoryMovement(models.Model):
    """A single stock change for a product — the append-only ledger that
    inventory is derived from, instead of one editable 'stock' number.
    Current stock is always the sum of this table's rows for a product
    (or one specific variant, for fixed-weight animals), so every change —
    a harvest coming in, a sale going out, spoiled produce — stays visible
    and auditable instead of collapsing into a single overwritten figure.

    Quantity is always entered as a POSITIVE number, in whatever unit the
    product already uses (kg for variable-weight, pieces for fixed-quantity,
    or the variant's own weight for a fixed-weight animal). The movement
    type below fixes the direction, so there's no sign-entry mistake."""

    MOVEMENT_TYPE_CHOICES = [
        ('harvest', 'Harvest — new stock in from the farm'),
        ('purchase', 'Purchase — new stock bought in from an outside supplier'),
        ('sale', 'Sale — stock out, sold (website order or shop POS)'),
        ('waste', 'Waste — spoiled/bad stock, not sellable'),
        ('return', 'Return — stock back in, e.g. a cancelled order'),
        ('adjustment_add', 'Adjustment (add) — stock count found higher than recorded'),
        ('adjustment_remove', 'Adjustment (remove) — stock count found lower than recorded'),
    ]

    SOURCE_CHOICES = [
        ('admin', 'Entered manually in Django admin'),
        ('abms', 'Pushed from ABMS (farm app)'),
        ('website', 'Website order'),
        ('pos', 'Shop POS'),
    ]

    # Movement types that ADD to stock; every other type subtracts.
    INCREASE_TYPES = {'harvest', 'purchase', 'return', 'adjustment_add'}

    product = models.ForeignKey(
        'Product', on_delete=models.CASCADE, related_name='inventory_movements'
    )
    variant = models.ForeignKey(
        'ProductVariant', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='inventory_movements',
        help_text='Only used for "Fixed weight" products (goat/chicken) — which '
                   'specific animal this movement is about. Left blank for every '
                   'other pricing mode.'
    )
    movement_type = models.CharField(max_length=20, choices=MOVEMENT_TYPE_CHOICES)
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default='admin')
    quantity = models.DecimalField(
        max_digits=8, decimal_places=3,
        help_text='Always a POSITIVE number, in the product\'s own unit. The '
                   'movement type above decides whether it adds to or removes '
                   'from stock — you never need to enter a minus sign. 3 decimal '
                   'places so a single-gram scale reading (e.g. 0.335kg from a '
                   '335g reading) can be recorded exactly, not just the nearest 10g.'
    )
    related_order = models.ForeignKey(
        'ProductOrder', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='inventory_movements',
        help_text='Only set for "sale"/"return" movements that came from a website order.'
    )
    related_pos_sale = models.ForeignKey(
        'POSSale', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='inventory_movements',
        help_text='Only set for "sale" movements that came from the shop POS.'
    )
    note = models.CharField(
        max_length=300, blank=True,
        help_text='e.g. "rain damage", "sold to walk-in customer at farm shop"'
    )
    unit_cost = models.DecimalField(
        max_digits=12, decimal_places=4, null=True, blank=True,
        help_text="Landed cost per product unit for THIS movement — only ever set for "
                   "origin='sourced' products (see PurchaseBatch). Auto-filled on first "
                   "save from weighted_average_cost() for sale/waste/adjustment_remove "
                   "rows when not explicitly provided; a purchase row sets it explicitly "
                   "(its own landed unit cost). Always null for farm-grown products — "
                   "those are costed through ABMS cost centres instead."
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        sign = '+' if self.movement_type in self.INCREASE_TYPES else '\u2212'
        return f"{sign}{self.quantity} {self.product.name} ({self.get_movement_type_display()})"

    def signed_quantity(self):
        """Quantity as it actually applies to stock: positive for
        harvest/return/adjustment_add, negative for everything else."""
        from decimal import Decimal
        qty = Decimal(str(self.quantity))
        return qty if self.movement_type in self.INCREASE_TYPES else -qty

    def clean(self):
        """Two checks before a movement is allowed to save:
        1. Fixed-weight products (goat/chicken) are individual animals sold
           live, not butchered/by weight — every movement on one must be
           exactly 1, regardless of that animal's own weight.
        2. Stock can never go negative — a sale/waste/adjustment_remove
           can't remove more than is actually available for that exact
           product+variant right now (this is what stops the same animal,
           or the same kg of produce, from being "sold" twice)."""
        from django.core.exceptions import ValidationError
        from decimal import Decimal

        if self.product_id and self.product.pricing_mode == 'fixed_weight' and self.quantity != 1:
            raise ValidationError({
                'quantity': 'Fixed-weight products (goat/chicken) are tracked one '
                            'animal at a time — quantity must be 1 for these movements.'
            })

        if self.product_id and self.movement_type not in self.INCREASE_TYPES:
            existing = InventoryMovement.objects.filter(product=self.product, variant=self.variant)
            if self.pk:
                existing = existing.exclude(pk=self.pk)
            available = sum((m.signed_quantity() for m in existing), Decimal('0'))
            if available - Decimal(str(self.quantity)) < 0:
                # "ProductName: ..." (+ weight, for a specific animal) so a
                # stock-conflict message is actually useful to a human
                # reading it later -- e.g. in the POS offline queue panel,
                # where this is the one detail staff need to find which
                # line item in an already-queued sale is the problem.
                who = self.product.name
                if self.variant_id:
                    who += f' ({self.variant.weight}kg)'
                raise ValidationError({
                    'quantity': f'{who} — only {available} currently available, '
                                f'cannot remove {self.quantity}.'
                })

    @classmethod
    def current_stock(cls, product, variant=None):
        """Current stock for a product, or for one specific variant
        (fixed-weight animals) — derived from the ledger, never stored
        directly. Pass variant=None for products with no variants."""
        from decimal import Decimal
        qs = cls.objects.filter(product=product, variant=variant)
        return sum((m.signed_quantity() for m in qs), Decimal('0'))

    @classmethod
    def weighted_average_cost(cls, product):
        """Moving-average landed cost for an origin='sourced' product,
        derived fresh from the ledger every call — never stored as a
        running number anywhere. Only rows that already HAVE a unit_cost
        count at all (so a harvest/adjustment_add/return row with no
        unit_cost set — the normal case for those types — simply doesn't
        skew the average either way):

          value_in / qty_in   -- unit_cost-bearing rows whose movement_type
                                  is in INCREASE_TYPES (purchases, and any
                                  return that happens to carry a unit_cost)
          value_out / qty_out -- every other unit_cost-bearing row (sale,
                                  waste, adjustment_remove — including a
                                  void's compensating adjustment_remove)

        stock_qty = qty_in - qty_out; if stock_qty > 0, the average is
        stock_value / stock_qty. If stock_qty is 0 or negative (nothing
        left, or the ledger is otherwise exhausted), there's no current
        average to blend into — fall back to the most recent purchase
        row's own unit_cost, or None if this product has never been
        purchased at all.

        Returns a Decimal, or None if there's nothing to base a cost on."""
        from decimal import Decimal
        from django.db.models import Sum, Q, F, DecimalField

        rows = cls.objects.filter(product=product, unit_cost__isnull=False)
        increase = cls.INCREASE_TYPES
        line_value = F('quantity') * F('unit_cost')
        agg = rows.aggregate(
            value_in=Sum(line_value, filter=Q(movement_type__in=increase), output_field=DecimalField(max_digits=16, decimal_places=4)),
            value_out=Sum(line_value, filter=~Q(movement_type__in=increase), output_field=DecimalField(max_digits=16, decimal_places=4)),
            qty_in=Sum('quantity', filter=Q(movement_type__in=increase)),
            qty_out=Sum('quantity', filter=~Q(movement_type__in=increase)),
        )
        value_in = agg['value_in'] or Decimal('0')
        value_out = agg['value_out'] or Decimal('0')
        qty_in = agg['qty_in'] or Decimal('0')
        qty_out = agg['qty_out'] or Decimal('0')

        stock_value = value_in - value_out
        stock_qty = qty_in - qty_out
        if stock_qty > 0:
            return stock_value / stock_qty

        last_purchase = rows.filter(movement_type='purchase').order_by('-created_at', '-id').first()
        return last_purchase.unit_cost if last_purchase else None

    def save(self, *args, **kwargs):
        # Only on a movement's FIRST save, and only when the caller hasn't
        # already set unit_cost explicitly (a PurchaseBatch always does —
        # its own landed unit cost, which must never be overwritten by
        # this). Farm-grown products are costed through ABMS cost centres
        # instead (see CLAUDE.md) and must never get a unit_cost here.
        if self.pk is None and self.unit_cost is None and self.product_id:
            if self.product.origin == 'sourced' and self.movement_type in ('sale', 'waste', 'adjustment_remove'):
                self.unit_cost = InventoryMovement.weighted_average_cost(self.product)
        super().save(*args, **kwargs)


class PurchaseBatch(models.Model):
    """One purchase of an outsourced (origin='sourced') product from a
    supplier — the cost basis InventoryMovement.weighted_average_cost()
    blends into the moving average. Farm-grown products are costed
    through ABMS cost centres instead (see CLAUDE.md) — never create one
    of these for an origin='farm' product; clean() enforces that.

    Append-only like InventoryMovement itself: a mistake is corrected via
    the "Void selected batches" admin action (a compensating
    adjustment_remove movement, see PurchaseBatchAdmin), never by editing
    or deleting this row — save()/delete() both raise once a row exists,
    EXCEPT the void action itself, which deliberately bypasses save() via
    a plain queryset .update() so it can still flip is_void/voided_at."""

    product = models.ForeignKey('Product', on_delete=models.PROTECT, related_name='purchase_batches')
    purchase_date = models.DateField()
    supplier = models.CharField(max_length=200, blank=True)
    quantity = models.DecimalField(max_digits=10, decimal_places=2)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    transport_cost = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    other_direct_cost = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    note = models.CharField(max_length=300, blank=True)
    landed_total = models.DecimalField(max_digits=12, decimal_places=2, editable=False)
    landed_unit_cost = models.DecimalField(max_digits=12, decimal_places=4, editable=False)
    movement = models.OneToOneField(
        'InventoryMovement', null=True, blank=True, on_delete=models.PROTECT, related_name='purchase_batch',
    )
    is_void = models.BooleanField(default=False)
    voided_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey('auth.User', null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-purchase_date', '-id']

    def __str__(self):
        return f"Batch #{self.pk or '?'} — {self.product.name}: {self.quantity} @ Rs {self.unit_price}"

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.product_id and self.product.origin != 'sourced':
            raise ValidationError({
                'product': "Purchase batches are only for outsourced products (origin='sourced') "
                           "— farm-grown products are costed through ABMS cost centres instead."
            })
        if self.quantity is not None and self.quantity <= 0:
            raise ValidationError({'quantity': 'Quantity must be greater than 0.'})
        if self.unit_price is not None and self.unit_price < 0:
            raise ValidationError({'unit_price': 'Unit price cannot be negative.'})

    def save(self, *args, **kwargs):
        from decimal import Decimal
        from django.db import transaction

        is_new = self.pk is None
        if not is_new:
            raise ValueError(
                'PurchaseBatch is append-only and cannot be edited once saved — '
                'use the "Void selected batches" admin action instead.'
            )

        self.landed_total = (
            self.quantity * self.unit_price + (self.transport_cost or 0) + (self.other_direct_cost or 0)
        ).quantize(Decimal('0.01'))
        self.landed_unit_cost = (self.landed_total / self.quantity).quantize(Decimal('0.0001'))

        with transaction.atomic():
            # Two saves, both part of this one creation call (not a later
            # edit): the first INSERT gets us a real pk to reference in the
            # movement's note and in the OneToOne link itself.
            super().save(*args, **kwargs)
            movement = InventoryMovement.objects.create(
                product=self.product, movement_type='purchase', source='admin',
                quantity=self.quantity, unit_cost=self.landed_unit_cost,
                note=f'Purchase batch #{self.pk}',
            )
            self.movement = movement
            super().save(update_fields=['movement'])

    def delete(self, *args, **kwargs):
        raise ValueError('PurchaseBatch cannot be deleted — use the "Void selected batches" admin action instead.')


class CostEntry(models.Model):
    """A single line of the farm cost ledger, mirrored 1:1 from ABMS's
    Firestore `costEntries` collection (one document per entry there too —
    see that app's js/costs.js). This is the data foundation for the future
    Django P&L feature; nothing here computes revenue or profit.

    Append-only: a mistake already recorded in ABMS is corrected there with
    a reversal document (negative amount, entry_type='reversal',
    reversal_of=<original abms_id>), which syncs here as an ordinary new
    row — this table is never edited or deleted from once a row lands,
    enforced below in save()/delete() (stricter than InventoryMovement's
    own real behavior, which has no such override — see CLAUDE.md)."""

    ENTRY_TYPE_CHOICES = [('cost', 'Cost'), ('reversal', 'Reversal')]
    ALLOCATION_RULE_CHOICES = [('equal', 'Equal split'), ('manual', 'Manual percentages')]

    abms_id = models.CharField(
        max_length=64, unique=True, db_index=True,
        help_text="The ABMS Firestore document id (e.g. 'c_xxxxxxxx') — the idempotency "
                   "key for syncing: the same id arriving twice is a no-op, never a duplicate row."
    )
    date = models.DateField(help_text='AD date of the cost, as recorded in ABMS.')
    amount = models.DecimalField(
        max_digits=12, decimal_places=2,
        help_text='Signed: positive for an ordinary cost, negative for a reversal.'
    )
    entry_type = models.CharField(max_length=10, choices=ENTRY_TYPE_CHOICES, default='cost')
    reversal_of = models.CharField(
        max_length=64, blank=True,
        help_text='abms_id of the original CostEntry this reverses. Only set when entry_type=reversal.'
    )
    cost_centre = models.CharField(
        max_length=60, db_index=True,
        help_text="e.g. 'crop:mango', 'tree:lychee', 'livestock:goat', 'livestock:chicken', "
                   "'bees', 'vermi', 'water', 'shared'."
    )
    category = models.CharField(max_length=60)
    qty = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    unit = models.CharField(max_length=20, blank=True)
    supplier = models.CharField(max_length=200, blank=True)
    note = models.TextField(blank=True)
    is_shared = models.BooleanField(default=False)
    allocation_rule = models.CharField(max_length=10, choices=ALLOCATION_RULE_CHOICES, blank=True)
    allocation_manual = models.JSONField(
        null=True, blank=True,
        help_text="{'crop:mango': 40, 'livestock:goat': 60} — only set when allocation_rule='manual'. Must sum to 100."
    )
    entered_by_email = models.CharField(max_length=254, blank=True)
    abms_created_at = models.DateTimeField(
        null=True, blank=True, help_text='When ABMS itself recorded this entry (its serverTimestamp).'
    )
    received_at = models.DateTimeField(auto_now_add=True, help_text='When this row was synced into Django.')

    class Meta:
        ordering = ['-date', '-received_at']
        indexes = [
            models.Index(fields=['date']),
            models.Index(fields=['cost_centre', 'date']),
        ]
        verbose_name_plural = 'Cost entries'

    def __str__(self):
        sign = '+' if self.amount >= 0 else ''
        return f"{sign}{self.amount} {self.category} ({self.cost_centre}) {self.date}"

    def save(self, *args, **kwargs):
        if self.pk and CostEntry.objects.filter(pk=self.pk).exists():
            raise ValueError('CostEntry is append-only and cannot be updated once saved.')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError('CostEntry is append-only and cannot be deleted.')


class FarmAsset(models.Model):
    """A piece of farm machinery/equipment/infrastructure, mirrored from
    ABMS's Firestore `assets` collection. Unlike CostEntry this IS mutable —
    ABMS lets an asset's status/disposed_date (and a few other fields) be
    updated after creation, and the sync endpoint applies the same updates
    here. Depreciation is shown read-only in both ABMS and this admin; the
    real P&L calculation is a later feature, not built here."""

    STATUS_CHOICES = [('active', 'Active'), ('disposed', 'Disposed')]

    abms_id = models.CharField(max_length=64, unique=True, db_index=True)
    name = models.CharField(max_length=200)
    asset_category = models.CharField(max_length=60)
    purchase_date = models.DateField()
    cost = models.DecimalField(max_digits=12, decimal_places=2)
    life_years = models.PositiveSmallIntegerField()
    salvage_value = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    cost_centre = models.CharField(max_length=60)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='active')
    disposed_date = models.DateField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-purchase_date']

    def __str__(self):
        return f"{self.name} ({self.get_status_display()})"

    def annual_depreciation(self):
        """Straight-line — display only, same as ABMS. (cost - salvage) / life_years."""
        from decimal import Decimal
        return (self.cost - self.salvage_value) / Decimal(self.life_years)


# Shared base for the two payment-method choice lists below: a single
# POSSalePayment line is always one concrete method (+ 'credit'); a POSSale
# itself additionally needs 'split' as a derived summary value for when a
# sale has 2+ payment lines with different methods. Defined once so the two
# lists can't quietly drift apart.
_BASE_PAYMENT_METHODS = [
    ('cash', 'Cash'),
    ('esewa', 'eSewa'),
    ('khalti', 'Khalti'),
    ('bank_transfer', 'Bank Transfer'),
]


class Customer(models.Model):
    """A credit (उधारो) customer. Phone is the staff lookup key at
    checkout — deliberately not unique (shared family phones happen), so
    the lookup endpoint returns every match and lets staff pick."""

    name = models.CharField(max_length=200)
    nickname = models.CharField(max_length=100, blank=True)
    phone = models.CharField(max_length=20, db_index=True)
    address = models.TextField(help_text='Required -- needed to actually find a उधारो customer in person if a debt goes unpaid.')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.phone})"

    def outstanding_balance(self):
        """Never stored — always credit_sale total minus repayment total,
        computed fresh so there's exactly one place this math can happen.
        Quantized to 2dp explicitly: SQLite's Sum() doesn't reliably
        preserve DecimalField's declared scale (e.g. Decimal('200.00') -
        Decimal('0') can come back as Decimal('200')), which would
        otherwise make this value's string formatting inconsistent
        depending on which rows happen to be summed."""
        from decimal import Decimal
        from django.db.models import Q, Sum

        agg = self.credit_transactions.aggregate(
            credit_sales=Sum('amount', filter=Q(transaction_type='credit_sale')),
            repayments=Sum('amount', filter=Q(transaction_type='repayment')),
        )
        balance = (agg['credit_sales'] or Decimal('0')) - (agg['repayments'] or Decimal('0'))
        return balance.quantize(Decimal('0.01'))


class POSSale(models.Model):
    PAYMENT_METHOD_CHOICES = _BASE_PAYMENT_METHODS + [
        ('credit', 'Credit (उधारो)'),
        ('split', 'Split Payment'),
    ]

    sale_number = models.CharField(max_length=20, unique=True, editable=False)
    cashier = models.ForeignKey('auth.User', on_delete=models.PROTECT, related_name='pos_sales')
    customer = models.ForeignKey(
        Customer, on_delete=models.PROTECT, null=True, blank=True, related_name='pos_sales',
        help_text='Only set when at least one payment line on this sale is credit.'
    )
    payment_method = models.CharField(
        max_length=20, choices=PAYMENT_METHOD_CHOICES,
        help_text="Derived, not user-entered: the sale's one payment method, or "
                   "'split' when it has more than one payment line. See the "
                   "payments related set for the actual breakdown."
    )
    cart_snapshot = models.JSONField(
        help_text='Same shape as ProductOrder.cart_snapshot: '
                   '[{product_id, weight, qty, variant_id}, ...]'
    )
    total_amount = models.DecimalField(max_digits=10, decimal_places=2)
    client_sale_id = models.CharField(
        max_length=64, unique=True, null=True, blank=True,
        help_text='A UUID generated on the POS device itself, before the sale is '
                   'ever sent to the server. If the same ID arrives twice — a '
                   'double-tap on Complete Sale, a retried request after a dropped '
                   'connection, or a queued offline sale being synced — the second '
                   'attempt returns the original result instead of creating a '
                   'second sale. Null only for sales that predate this field.'
    )

    # VAT scaffolding (POS Phase B) — dormant while BusinessSettings.
    # is_vat_enabled is False: exempt_value == total_amount, the other two
    # stay zero, regardless of any product's is_taxable flag. Stored (not
    # recomputed later) so a receipt still shows the correct split even if
    # a product's is_taxable flag or the VAT toggle itself changes afterward.
    # NOTE: taxable_value/exempt_value are recorded AFTER any coupon discount
    # below is applied (see create_pos_sale()) — they reflect the sale's
    # actual tax basis, not the pre-discount cart total.
    taxable_value = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    exempt_value = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    vat_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    # POS Phase C: coupon discounts. Reuses the same Coupon model the
    # website checkout uses (festival codes like DASHAIN25) rather than a
    # parallel POS-only discount system — one place discount rules live,
    # one used_count counter, one "My Coupons" list.
    #
    # Offer-style automatic per-product/combo discounts (POS Phase D) are a
    # separate mechanism, not reflected on this field: a discounted single
    # product is just an ordinary cart line priced lower (its discount is
    # baked into that line's own price in cart_snapshot, not tracked here),
    # and a combo is several ordinary cart lines sharing a combo_instance_id
    # in cart_snapshot. See resolve_pos_offer_discount()/
    # resolve_pos_combo_lines() in shop/views.py — this field/discount_amount
    # are specifically about the one coupon code a sale can have, which is
    # independent of and stacks with any offer-priced lines in the same cart.
    coupon = models.ForeignKey(
        Coupon, on_delete=models.SET_NULL, null=True, blank=True, related_name='pos_sales',
        help_text='The coupon code applied to this sale, if any.',
    )
    discount_amount = models.DecimalField(
        max_digits=10, decimal_places=2, default=0,
        help_text='Amount knocked off the pre-tax subtotal by the coupon above. Zero when no coupon was used.',
    )

    # Standard retail cash rounding: nobody pays paisa, so the grand total is
    # rounded to the nearest whole rupee (round-half-up) and the difference
    # is recorded here rather than silently absorbed -- same bookkeeping
    # spirit as discount_amount. taxable_value/exempt_value/vat_amount above
    # stay exactly as computed (unrounded); only total_amount and this field
    # change, so the detailed breakdown still adds up precisely on its own
    # and this is the one place the rounding difference lives.
    round_off_amount = models.DecimalField(
        max_digits=4, decimal_places=2, default=0,
        help_text='required_total rounded to the nearest rupee, minus required_total itself -- '
                   'e.g. -0.37 if a Rs. 222.37 total was rounded down to Rs. 222. Always between '
                   '-0.99 and +0.99.'
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def save(self, *args, **kwargs):
        if not self.sale_number:
            import uuid
            self.sale_number = 'POS-' + uuid.uuid4().hex[:8].upper()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.sale_number} — Rs. {self.total_amount} ({self.get_payment_method_display()})"


class POSSalePayment(models.Model):
    """One payment line on a sale. A sale has 1+ of these; their amounts
    must sum to POSSale.total_amount (enforced in the sale-creation view,
    not here, since that's where the rest of the cart/total validation
    already happens)."""

    METHOD_CHOICES = _BASE_PAYMENT_METHODS + [('credit', 'Credit (उधारो)')]

    sale = models.ForeignKey(POSSale, on_delete=models.CASCADE, related_name='payments')
    method = models.CharField(max_length=20, choices=METHOD_CHOICES)
    amount = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        ordering = ['id']

    def __str__(self):
        return f"{self.sale.sale_number} — {self.get_method_display()} Rs. {self.amount}"


class POSSaleLine(models.Model):
    """One line of a completed POS sale — written going forward only from
    create_pos_sale() (shop/views.py), inside the same transaction.atomic()
    as the sale itself. Existing POSSale rows are NOT backfilled; a sale
    created before this model existed simply has no lines (see CLAUDE.md).

    line_total is the gross line amount BEFORE the sale-level coupon
    discount (POSSale.discount_amount) — i.e. this line's own contribution
    to create_pos_sale()'s pre-discount `total`, not a share of the final
    discounted/VAT'd/rounded POSSale.total_amount. unit_cost mirrors the
    InventoryMovement this line produced: null for a farm-origin product
    (not costed here — see ABMS cost centres), set for a sourced one."""

    sale = models.ForeignKey(POSSale, on_delete=models.CASCADE, related_name='lines')
    product = models.ForeignKey('Product', on_delete=models.PROTECT, related_name='pos_sale_lines')
    variant = models.ForeignKey(
        'ProductVariant', null=True, blank=True, on_delete=models.SET_NULL, related_name='pos_sale_lines',
    )
    quantity = models.DecimalField(
        max_digits=10, decimal_places=3,
        help_text="In the product's own base unit — same meaning as InventoryMovement.quantity for this line."
    )
    line_total = models.DecimalField(max_digits=12, decimal_places=2)
    unit_cost = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)

    class Meta:
        ordering = ['id']

    def __str__(self):
        return f"{self.sale.sale_number} — {self.product.name} x{self.quantity}"


class CreditTransaction(models.Model):
    """Append-only credit (उधारो) ledger — same philosophy as
    InventoryMovement: rows are never edited or deleted, and a customer's
    balance is always derived from this table (Customer.outstanding_balance()),
    never stored."""

    TRANSACTION_TYPE_CHOICES = [
        ('credit_sale', 'Credit Sale'),
        ('repayment', 'Repayment'),
    ]

    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name='credit_transactions')
    amount = models.DecimalField(
        max_digits=10, decimal_places=2,
        help_text='Always positive — transaction_type decides the direction.'
    )
    transaction_type = models.CharField(max_length=20, choices=TRANSACTION_TYPE_CHOICES)
    related_pos_sale = models.ForeignKey(
        POSSale, on_delete=models.SET_NULL, null=True, blank=True, related_name='credit_transactions',
        help_text='Set for credit_sale rows; left blank for a standalone repayment.'
    )
    recorded_by = models.ForeignKey(
        'auth.User', on_delete=models.PROTECT, related_name='recorded_credit_transactions',
        help_text="The staff member who actually took this payment/recorded this sale — the "
                  "PIN-unlock 'current operator', not necessarily whoever the terminal is logged in as."
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.customer.name} — {self.get_transaction_type_display()} Rs. {self.amount}"


class BusinessSettings(models.Model):
    """Singleton — exactly one row, always pk=1. is_vat_enabled is the one
    dormant switch for POS Phase B's VAT scaffolding; flipping it changes
    how future sales compute their taxable/exempt/VAT split, never
    retroactively (see POSSale.taxable_value/exempt_value/vat_amount,
    stored per-sale at creation time)."""

    is_vat_enabled = models.BooleanField(default=False)

    class Meta:
        verbose_name = 'Business Settings'
        verbose_name_plural = 'Business Settings'

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        pass

    @classmethod
    def get_solo(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def __str__(self):
        return 'Business Settings'


class RevenueTarget(models.Model):
    """A revenue goal for the Reports dashboard's sidebar Target card
    (shop/reports.py's get_target_progress()). Entered in plain A.D. dates
    here in admin — the owner picks whatever period they mean (a Nepali
    fiscal year, a calendar year, a single month) by just setting
    start_date/end_date; the dashboard itself displays everything in B.S.,
    but there's no reason to make the admin form itself BS-only for a
    field two people type into a handful of times a year.

    "Achieved" against this target is always POS sales only (never
    online orders), for the same reason revenue is POS-only everywhere
    else on this dashboard: ProductOrder has no stored price. See
    shop/reports.py's module docstring.
    """

    name = models.CharField(max_length=100, help_text='e.g. "FY 2083/84" or "2026 Goal" — shown on the dashboard as-is.')
    start_date = models.DateField()
    end_date = models.DateField()
    amount = models.DecimalField(max_digits=12, decimal_places=2, help_text='Target revenue in Rs.')
    is_active = models.BooleanField(
        default=True,
        help_text='Only active targets are ever shown on the dashboard. If more than one active '
                   "target's period contains today, the dashboard prefers that one; otherwise it "
                   'shows the most recently started active target.'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-start_date']

    def __str__(self):
        return f'{self.name} (Rs {self.amount:,.0f}, {self.start_date} to {self.end_date})'