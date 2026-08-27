from django.contrib import admin, messages
from django.urls import path
from django.http import HttpResponse
from django.shortcuts import redirect
from django.middleware.csrf import get_token
from django.utils.html import escape
from .models import ContactMessage, ProductOrder, NewsletterSubscriber, Product, Review, Category
from .models import Offer, Coupon, BundleItem, ProductVariant
from .emails import send_newsletter_campaign


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


class ProductVariantInline(admin.TabularInline):
    model = ProductVariant
    extra = 1
    fields = ['weight', 'price_override', 'label', 'is_available']


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ['name', 'category', 'price', 'price_unit', 'pricing_mode', 'weight_step', 'is_available', 'season']
    list_filter = ['category', 'is_available', 'pricing_mode']
    list_editable = ['is_available', 'price', 'price_unit']
    prepopulated_fields = {'slug': ('name',)}
    search_fields = ['name']
    inlines = [ProductVariantInline]

    fieldsets = (
        (None, {
            'fields': ('name', 'slug', 'category', 'description', 'detail_description')
        }),
        ('Pricing & cart behaviour', {
            'fields': ('price', 'price_unit', 'pricing_mode', 'weight_step', 'weight_unit_label', 'fixed_weight'),
            'description': (
                'pricing_mode controls how this product behaves in the cart: '
                '<b>Variable weight</b> — customer picks the amount in steps of "weight_step", labeled '
                'with "weight_unit_label" (e.g. 0.50 "kg" for fruit, 0.25 "kg" for pickle jars, 0.5 "dozen" for banana). '
                '<b>Fixed quantity</b> — plain quantity stepper, no weight (jars, eggs by piece). '
                'weight_step/weight_unit_label/fixed_weight are all ignored for this mode. '
                '<b>Fixed weight</b> — one specific animal (goat/chicken): scroll down to '
                '"Weight variants" below and add a row for each size/animal currently available '
                '(e.g. 15kg, 20kg) — no need to create a new product for each one. The old '
                '"fixed_weight" field above is only used as a fallback if you add NO rows below.'
            ),
        }),
        ('Images & details', {
            'fields': ('main_image', 'image2', 'image3', 'image4', 'season', 'farming_method', 'whatsapp_message')
        }),
        ('Availability', {
            'fields': ('is_available',)
        }),
    )


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
    list_display = ['email', 'name', 'is_subscribed', 'subscribed_at']
    list_filter = ['is_subscribed']
    search_fields = ['email', 'name']
    actions = ['send_campaign_action']

    def send_campaign_action(self, request, queryset):
        """Shows a small compose form for the selected subscribers. Submitting
        it hits process_campaign below, which sends via Resend's batch API."""
        ids = ','.join(str(pk) for pk in queryset.values_list('id', flat=True))
        csrf_token = get_token(request)
        return HttpResponse(f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>Send Newsletter Campaign</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
  body {{ font-family: -apple-system, sans-serif; background:#f4f4f4; padding:40px 16px; }}
  .box {{ max-width:640px; margin:0 auto; background:#fff; border-radius:8px; padding:32px;
          box-shadow:0 2px 10px rgba(0,0,0,0.08); }}
  h1 {{ font-size:1.3rem; margin-top:0; }}
  label {{ display:block; margin:16px 0 6px; font-weight:600; font-size:0.9rem; }}
  input[type=text], textarea {{ width:100%; padding:10px; border:1px solid #ccc; border-radius:6px;
          font-size:0.95rem; box-sizing:border-box; font-family:inherit; }}
  textarea {{ min-height:220px; }}
  button {{ margin-top:20px; padding:12px 24px; background:#1a2f1e; color:#fff; border:none;
          border-radius:6px; font-size:0.95rem; cursor:pointer; }}
  button:hover {{ background:#2d4a32; }}
  .count {{ color:#666; font-size:0.85rem; }}
  .hint {{ color:#888; font-size:0.8rem; margin-top:6px; }}
</style></head>
<body>
  <div class="box">
    <h1>📧 Send Newsletter Campaign</h1>
    <p class="count">Sending to {queryset.count()} selected subscriber(s).</p>
    <form method="post" action="/admin/shop/newslettersubscriber/send-campaign/">
      <input type="hidden" name="csrfmiddlewaretoken" value="{csrf_token}">
      <input type="hidden" name="subscriber_ids" value="{escape(ids)}">
      <label>Subject</label>
      <input type="text" name="subject" required placeholder="e.g. Mango season is here! 🥭">
      <label>Message</label>
      <textarea name="body" required placeholder="Write your update here."></textarea>
      <p class="hint">A greeting, WhatsApp link, and working unsubscribe link are added to every email automatically — no need to write those yourself.</p>
      <button type="submit">Send Campaign</button>
    </form>
  </div>
</body></html>""")
    send_campaign_action.short_description = '📧 Send Newsletter Campaign to Selected'

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path('send-campaign/', self.admin_site.admin_view(self.process_campaign),
                 name='newsletter_send_campaign'),
        ]
        return custom + urls

    def process_campaign(self, request):
        if request.method != 'POST':
            return redirect('admin:shop_newslettersubscriber_changelist')

        subject = request.POST.get('subject', '').strip()
        body = request.POST.get('body', '').strip()
        ids_raw = request.POST.get('subscriber_ids', '')
        ids = [int(i) for i in ids_raw.split(',') if i.strip().isdigit()]

        if not subject or not body or not ids:
            self.message_user(request, 'Subject, message, and at least one subscriber are required.', level=messages.ERROR)
            return redirect('admin:shop_newslettersubscriber_changelist')

        subscribers = NewsletterSubscriber.objects.filter(id__in=ids, is_subscribed=True)
        skipped = len(ids) - subscribers.count()
        sent_count = send_newsletter_campaign(subject, body, subscribers)

        if skipped:
            self.message_user(request, f'Campaign sent to {sent_count} subscriber(s)! ({skipped} selected were already unsubscribed and were skipped.)')
        else:
            self.message_user(request, f'Campaign sent to {sent_count} subscriber(s)! 🎉')
        return redirect('admin:shop_newslettersubscriber_changelist')


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ('name', 'product', 'rating', 'is_approved', 'created_at')
    list_filter = ('is_approved', 'rating', 'product')
    list_editable = ('is_approved',)
    search_fields = ('name', 'comment', 'product__name')
    ordering = ('-created_at',)

class BundleItemInline(admin.TabularInline):
    model = BundleItem
    extra = 1
    autocomplete_fields = ['product']
    fields = ['product', 'quantity']


@admin.register(Offer)
class OfferAdmin(admin.ModelAdmin):
    list_display = ['title', 'discount_type', 'discount_value', 'category', 'start_date', 'end_date', 'is_active', 'live_status', 'bundle_total_display']
    list_filter = ['discount_type', 'is_active', 'category']
    list_editable = ['is_active']
    filter_horizontal = ['products']
    search_fields = ['title']
    date_hierarchy = 'start_date'
    inlines = [BundleItemInline]

    def live_status(self, obj):
        return '🟢 Live' if obj.is_live() else '🔴 Not Live'
    live_status.short_description = 'Status'

    def bundle_total_display(self, obj):
        if obj.discount_type != 'combo':
            return '—'
        total = obj.get_bundle_natural_total()
        return f'Rs. {total:.0f}' if total else '—'
    bundle_total_display.short_description = 'Bundle Natural Total'


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