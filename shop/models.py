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

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        from django.urls import reverse
        return reverse('product_detail', kwargs={'slug': self.slug})


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