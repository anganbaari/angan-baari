from django.db import models
import uuid

class Product(models.Model):
    CATEGORY = [
        ('fruits', 'Fruits'),
        ('vegetables', 'Vegetables'),
        ('honey', 'Honey'),
        ('animals', 'Animals'),
        ('pickles', 'Pickles'),
    ]
    name = models.CharField(max_length=200)
    slug = models.SlugField(unique=True)
    category = models.CharField(max_length=50, choices=CATEGORY)
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