from django.contrib import admin
from .models import ContactMessage, ProductOrder, NewsletterSubscriber
from .emails import (
    send_order_confirmed_email,
    send_order_delivered_email
)

@admin.register(ContactMessage)
class ContactAdmin(admin.ModelAdmin):
    list_display = ['name', 'email', 'subject', 'is_read', 'sent_at']
    list_filter = ['is_read']

@admin.register(ProductOrder)
class OrderAdmin(admin.ModelAdmin):
    list_display = ['name', 'email', 'product_interest', 'status', 'ordered_at']
    list_filter = ['status']
    list_editable = ['status']
    actions = ['mark_confirmed', 'mark_delivered']

    def mark_confirmed(self, request, queryset):
        for order in queryset:
            order.status = 'confirmed'
            order.save()
            send_order_confirmed_email(order)
        self.message_user(request, f'{queryset.count()} order(s) confirmed and emails sent!')
    mark_confirmed.short_description = '✅ Mark as Confirmed and send email'

    def mark_delivered(self, request, queryset):
        for order in queryset:
            order.status = 'delivered'
            order.save()
            send_order_delivered_email(order)
        self.message_user(request, f'{queryset.count()} order(s) marked delivered and emails sent!')
    mark_delivered.short_description = '🏡 Mark as Delivered and send email'

@admin.register(NewsletterSubscriber)
class NewsletterAdmin(admin.ModelAdmin):
    list_display = ['email', 'name', 'subscribed_at']