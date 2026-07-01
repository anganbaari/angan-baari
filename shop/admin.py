from django.contrib import admin
from .models import ContactMessage, ProductOrder, NewsletterSubscriber, Product, Review, Category
from .models import Offer, Coupon


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'parent', 'icon', 'order', 'product_count']
    list_editable = ['order', 'icon']
    list_filter = ['parent']
    search_fields = ['name']
    ordering = ['order', 'name']

    def product_count(self, obj):
        return obj.products.count()
    product_count.short_description = '# Products'


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ['name', 'category', 'price', 'price_unit', 'is_available', 'season']
    list_filter = ['category', 'is_available']
    list_editable = ['is_available', 'price', 'price_unit']
    prepopulated_fields = {'slug': ('name',)}
    search_fields = ['name']


@admin.register(ContactMessage)
class ContactAdmin(admin.ModelAdmin):
    list_display = ['name', 'email', 'subject', 'is_read', 'sent_at']
    list_filter = ['is_read']


@admin.register(ProductOrder)
class OrderAdmin(admin.ModelAdmin):
    list_display = ['order_number', 'name', 'email', 'product_interest', 'status', 'ordered_at']
    list_filter = ['status']
    list_editable = ['status']
    actions = ['mark_confirmed', 'mark_delivered']

    def mark_confirmed(self, request, queryset):
        for order in queryset:
            order.status = 'confirmed'
            order.save()
            try:
                from .emails import send_order_confirmed_email
                send_order_confirmed_email(order)
            except Exception:
                pass
        self.message_user(request, f'{queryset.count()} order(s) confirmed!')
    mark_confirmed.short_description = '✅ Mark as Confirmed'

    def mark_delivered(self, request, queryset):
        for order in queryset:
            order.status = 'delivered'
            order.save()
            try:
                from .emails import send_order_delivered_email
                send_order_delivered_email(order)
            except Exception:
                pass
        self.message_user(request, f'{queryset.count()} order(s) delivered!')
    mark_delivered.short_description = '🏡 Mark as Delivered'


@admin.register(NewsletterSubscriber)
class NewsletterAdmin(admin.ModelAdmin):
    list_display = ['email', 'name', 'subscribed_at']


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ('name', 'product', 'rating', 'is_approved', 'created_at')
    list_filter = ('is_approved', 'rating', 'product')
    list_editable = ('is_approved',)
    search_fields = ('name', 'comment', 'product__name')
    ordering = ('-created_at',)

@admin.register(Offer)
class OfferAdmin(admin.ModelAdmin):
    list_display = ['title', 'discount_type', 'discount_value', 'category', 'start_date', 'end_date', 'is_active', 'live_status']
    list_filter = ['discount_type', 'is_active', 'category']
    list_editable = ['is_active']
    filter_horizontal = ['products']
    search_fields = ['title']
    date_hierarchy = 'start_date'
 
    def live_status(self, obj):
        return '🟢 Live' if obj.is_live() else '🔴 Not Live'
    live_status.short_description = 'Status'


@admin.register(Coupon)
class CouponAdmin(admin.ModelAdmin):
    list_display = ['code', 'festival_name', 'discount_type', 'discount_value', 'min_order_amount', 'start_date', 'end_date', 'is_active', 'used_count', 'live_status']
    list_filter = ['is_active', 'discount_type']
    list_editable = ['is_active']
    search_fields = ['code', 'festival_name']
    date_hierarchy = 'start_date'
    readonly_fields = ['used_count', 'created_at']

    def live_status(self, obj):
        return '🟢 Live' if obj.is_live() else '🔴 Not Live'
    live_status.short_description = 'Status'