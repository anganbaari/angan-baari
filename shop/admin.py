from django import forms
from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User
from django.db.models import Case, DecimalField, F, Sum, When
from django.urls import path
from django.http import HttpResponse
from django.shortcuts import redirect
from django.middleware.csrf import get_token
from django.utils.html import escape, format_html
from .models import ContactMessage, ProductOrder, NewsletterSubscriber, Product, Review, Category
from .models import Offer, Coupon, BundleItem, ProductVariant
from .emails import send_newsletter_campaign
from .models import InventoryMovement
from .models import (
    Offer, Coupon, BundleItem, ProductVariant, ProductSellingUnit, InventoryMovement, POSSale, UserProfile,
    BusinessSettings, CreditTransaction, Customer, POSSalePayment, RevenueTarget, CostEntry, FarmAsset,
    PurchaseBatch, POSSaleLine,
)
from .stock import get_stock_table_rows
from .utils import format_money


class MoneyDisplayWidget(forms.TextInput):
    """Shows the same value it's editing, minus a trailing '.00' -- '80.00'
    displays as '80' in the box. Submitting it back unchanged still
    round-trips correctly since Decimal('80') == Decimal('80.00') once
    Django re-quantizes on save (decimal_places=2 on the model field)."""
    def format_value(self, value):
        if value in (None, ''):
            return ''
        return format_money(value)


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


class ProductSellingUnitInline(admin.TabularInline):
    model = ProductSellingUnit
    extra = 1
    fields = ['name', 'price', 'quantity_in_base_units', 'is_default', 'is_available']


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ['name', 'category', 'price', 'price_unit', 'pricing_mode',
                     'weight_step', 'is_available', 'is_taxable', 'season', 'current_stock_display',
                     'stock_alert_display', 'origin']
    list_filter = ['category', 'is_available', 'is_taxable', 'pricing_mode', 'origin']
    list_editable = ['is_available', 'price', 'price_unit', 'origin']
    prepopulated_fields = {'slug': ('name',)}
    search_fields = ['name']
    inlines = [ProductVariantInline, ProductSellingUnitInline]

    def get_queryset(self, request):
        # Annotates a sortable stock figure so clicking the column header
        # actually works -- the DISPLAYED number/highlight in
        # stock_alert_display() still comes from get_stock_table_rows(),
        # the single shared source of truth the POS stock screen also uses,
        # so this annotation only ever drives sort order, never the values
        # shown. Meaningless for fixed_weight products (it sums across every
        # variant instead of per-animal) -- harmless since those rows always
        # display '—' in this column regardless of where they sort to.
        qs = super().get_queryset(request)
        return qs.annotate(
            _stock_sort=Sum(
                Case(
                    When(inventory_movements__movement_type__in=InventoryMovement.INCREASE_TYPES,
                         then=F('inventory_movements__quantity')),
                    default=-F('inventory_movements__quantity'),
                    output_field=DecimalField(max_digits=8, decimal_places=3),
                )
            )
        )

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        formfield = super().formfield_for_dbfield(db_field, request, **kwargs)
        if db_field.name == 'price':
            formfield.widget = MoneyDisplayWidget()
        return formfield

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
                'Optionally add rows in "Selling units" below to sell this product in more than '
                'one named unit at independent prices (e.g. eggs by the Piece AND by the Crate) — '
                'leave empty for the plain single-price behaviour above; most products need none. '
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
            'fields': ('is_available', 'origin')
        }),
        ('Tax (VAT)', {
            'fields': ('is_taxable',),
            'description': (
                'Dormant until Business Settings\' "VAT enabled" switch is turned on — until then, '
                'this has no effect on any sale. Most of this catalog (fresh produce, live animals, '
                'milk, eggs) is VAT-exempt by law; only check this for a processed/packaged item.'
            ),
        }),
    )

    def current_stock_display(self, obj):
        if obj.pricing_mode == 'fixed_weight':
            variants = obj.variants.all()
            if not variants:
                return '\u2014'
            available = sum(1 for v in variants if InventoryMovement.current_stock(obj, variant=v) > 0)
            return f'{available}/{variants.count()} animals available'
        stock = InventoryMovement.current_stock(obj)
        unit = obj.weight_unit_label if obj.pricing_mode == 'variable_weight' else 'pcs'
        return f'{stock} {unit}'
    current_stock_display.short_description = 'Current stock'

    def changelist_view(self, request, extra_context=None):
        # Computed once per page load (not once per row -- get_stock_table_
        # rows() itself already queries every non-fixed_weight product, so
        # calling it from stock_alert_display() directly would be quadratic)
        # and stashed on self rather than threaded through per-row via
        # list_display's (obj)-only call signature. Staff-only, low-traffic
        # admin page for a two-person team -- the Django-admin-common
        # tradeoff of caching read-only display data on self for the
        # duration of one changelist render is an acceptable one here.
        self._stock_by_product_id = {row['product_id']: row for row in get_stock_table_rows()}
        return super().changelist_view(request, extra_context)

    def stock_alert_display(self, obj):
        row = getattr(self, '_stock_by_product_id', {}).get(obj.id)
        if not row:  # fixed_weight products are excluded from get_stock_table_rows()
            return '\u2014'
        text = f"{row['current_stock']} (min {row['low_stock_threshold']})"
        if row['is_low']:
            return format_html(
                '<span style="background:#f8d7da;color:#842029;padding:2px 7px;'
                'border-radius:4px;font-weight:700;">{}</span>', text,
            )
        return text
    stock_alert_display.short_description = 'Stock alert'
    stock_alert_display.admin_order_field = '_stock_sort'


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
    fields = ['product', 'quantity', 'reference_weight']


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


@admin.register(InventoryMovement)
class InventoryMovementAdmin(admin.ModelAdmin):
    list_display = ['created_at', 'product', 'variant', 'movement_type', 'change_display', 'unit_cost', 'source', 'related_order']
    list_filter = ['movement_type', 'source', 'created_at']
    search_fields = ['product__name', 'note', 'related_order__order_number']
    autocomplete_fields = ['product']
    date_hierarchy = 'created_at'
    ordering = ['-created_at']
    readonly_fields = ['created_at', 'unit_cost']
    list_select_related = ['product', 'variant', 'related_order']

    def change_display(self, obj):
        sign = '+' if obj.movement_type in obj.INCREASE_TYPES else '\u2212'
        return f'{sign}{obj.quantity}'
    change_display.short_description = 'Change'


class PurchaseBatchAdminForm(forms.ModelForm):
    class Meta:
        model = PurchaseBatch
        fields = ['product', 'purchase_date', 'supplier', 'quantity', 'unit_price', 'transport_cost', 'other_direct_cost', 'note']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Outsourced products only -- a farm-grown product is costed
        # through ABMS cost centres instead (PurchaseBatch.clean() backs
        # this up server-side; this is just so the dropdown itself never
        # offers a wrong choice in the first place).
        self.fields['product'].queryset = Product.objects.filter(origin='sourced')


@admin.register(PurchaseBatch)
class PurchaseBatchAdmin(admin.ModelAdmin):
    """Add-only ledger: no change, no delete (PurchaseBatch.save()/
    delete() both raise on an existing row anyway -- these permission
    overrides are what actually hide the Change/Delete buttons). Void a
    mistaken batch with the "Void selected batches" action instead."""

    form = PurchaseBatchAdminForm
    list_display = [
        'purchase_date', 'product', 'supplier', 'quantity', 'unit_price',
        'transport_cost', 'landed_unit_cost', 'landed_total', 'is_void',
    ]
    list_filter = ['is_void', 'product']
    search_fields = ['product__name', 'supplier', 'note']
    date_hierarchy = 'purchase_date'
    ordering = ['-purchase_date', '-id']
    readonly_fields = ['landed_total', 'landed_unit_cost', 'movement', 'is_void', 'voided_at', 'created_by', 'created_at']
    actions = ['void_selected_batches']

    def save_model(self, request, obj, form, change):
        obj.created_by = request.user
        super().save_model(request, obj, form, change)

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.action(description='Void selected batches')
    def void_selected_batches(self, request, queryset):
        from django.db import transaction
        from django.utils import timezone

        voided, skipped = 0, []
        for batch in queryset.filter(is_void=False):
            current_stock = InventoryMovement.current_stock(batch.product)
            if current_stock < batch.quantity:
                skipped.append(
                    f'{batch} -- only {current_stock} in stock, needs {batch.quantity} to void'
                )
                continue
            with transaction.atomic():
                InventoryMovement.objects.create(
                    product=batch.product, movement_type='adjustment_remove', source='admin',
                    quantity=batch.quantity, unit_cost=batch.landed_unit_cost,
                    note=f'Void of purchase batch #{batch.pk}',
                )
                # Bypasses PurchaseBatch.save()'s append-only guard on
                # purpose -- a plain queryset update never calls save().
                PurchaseBatch.objects.filter(pk=batch.pk).update(is_void=True, voided_at=timezone.now())
            voided += 1

        if voided:
            self.message_user(request, f'{voided} batch(es) voided.')
        if skipped:
            self.message_user(
                request, 'Could not void (not enough stock left): ' + '; '.join(skipped), level=messages.WARNING,
            )


class POSSalePaymentInline(admin.TabularInline):
    model = POSSalePayment
    extra = 0
    readonly_fields = ['method', 'amount']
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


class POSSaleLineInline(admin.TabularInline):
    """Read-only -- written by create_pos_sale() itself, see POSSaleLine's
    own docstring. Existing sales predating this model have no lines."""

    model = POSSaleLine
    extra = 0
    readonly_fields = ['product', 'variant', 'quantity', 'line_total', 'list_line_total', 'unit_cost']
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(POSSale)
class POSSaleAdmin(admin.ModelAdmin):
    list_display = ['sale_number', 'cashier', 'customer', 'payment_method', 'is_wholesale', 'coupon', 'discount_amount', 'round_off_amount', 'total_amount', 'created_at']
    list_filter = ['payment_method', 'is_wholesale', 'cashier', 'coupon', 'created_at']
    search_fields = ['sale_number']
    date_hierarchy = 'created_at'
    ordering = ['-created_at']
    inlines = [POSSalePaymentInline, POSSaleLineInline]
    readonly_fields = [
        'sale_number', 'cashier', 'customer', 'payment_method', 'is_wholesale', 'cart_snapshot', 'total_amount',
        'client_sale_id', 'taxable_value', 'exempt_value', 'vat_amount', 'coupon', 'discount_amount',
        'round_off_amount', 'created_at',
    ]

    def has_add_permission(self, request):
        # Sales are created by the POS screen itself, never by hand in admin —
        # a manually-created "sale" here wouldn't actually move any inventory.
        return False


class UserProfileInlineForm(forms.ModelForm):
    """Swaps the raw pin_hash field for a plain 4-digit PIN input — the
    admin should never show or accept a hash directly. Left blank, the
    existing PIN (if any) is kept as-is; there's no way to display it back
    since only the hash is ever stored, so this field always renders empty
    regardless of whether a PIN is already set."""

    pin = forms.CharField(
        label='PIN', required=False,
        widget=forms.TextInput(attrs={'placeholder': '••••', 'autocomplete': 'off', 'inputmode': 'numeric', 'maxlength': 4}),
        help_text='4 digits. Leave blank to keep the current PIN unchanged.',
    )

    class Meta:
        model = UserProfile
        fields = ['role', 'staff_number']

    def clean_pin(self):
        pin = self.cleaned_data.get('pin', '').strip()
        if not pin:
            return pin
        if not (pin.isdigit() and len(pin) == 4):
            raise forms.ValidationError('PIN must be exactly 4 digits.')

        # pin_hash is hashed at rest, so there's no query that can check this
        # directly -- has to walk every other profile's hash and re-derive
        # whether the submitted raw PIN matches it, same as a login check.
        # Goes through UserProfile.check_pin() rather than hashing here
        # directly, so this form never needs to know the hash format.
        others = UserProfile.objects.exclude(pin_hash='')
        if self.instance.pk:
            others = others.exclude(pk=self.instance.pk)
        for other in others:
            if other.check_pin(pin):
                raise forms.ValidationError('This PIN is already in use by another staff member.')

        return pin

    def save(self, commit=True):
        instance = super().save(commit=False)
        pin = self.cleaned_data.get('pin')
        if pin:
            instance.set_pin(pin)
        if commit:
            instance.save()
        return instance


class UserProfileInline(admin.StackedInline):
    model = UserProfile
    form = UserProfileInlineForm
    can_delete = False
    verbose_name_plural = 'POS profile (role & PIN)'
    fields = ['role', 'staff_number', 'pin']


class UserAdmin(BaseUserAdmin):
    inlines = [UserProfileInline]


admin.site.unregister(User)
admin.site.register(User, UserAdmin)


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ['name', 'nickname', 'phone', 'outstanding_balance_display', 'created_at']
    search_fields = ['name', 'nickname', 'phone']
    ordering = ['name']

    def outstanding_balance_display(self, obj):
        return f'Rs. {obj.outstanding_balance():.2f}'
    outstanding_balance_display.short_description = 'Outstanding balance'


@admin.register(CreditTransaction)
class CreditTransactionAdmin(admin.ModelAdmin):
    """Viewable for reconciliation only — matches InventoryMovement's own
    append-only-ledger treatment: no add, no edit, no delete from here."""

    list_display = ['customer', 'transaction_type', 'amount', 'related_pos_sale', 'recorded_by', 'created_at']
    list_filter = ['transaction_type', 'created_at']
    search_fields = ['customer__name', 'customer__phone', 'related_pos_sale__sale_number']
    date_hierarchy = 'created_at'
    ordering = ['-created_at']
    readonly_fields = ['customer', 'amount', 'transaction_type', 'related_pos_sale', 'recorded_by', 'created_at']

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(CostEntry)
class CostEntryAdmin(admin.ModelAdmin):
    """Synced from ABMS (see /api/costs/sync/) — viewable for reconciliation
    only, same no-add/no-edit/no-delete treatment as CreditTransactionAdmin
    above. The model's own save()/delete() overrides already enforce this
    at the ORM level too; these permission overrides are what actually
    hide the Add/Change/Delete buttons in the admin UI."""

    list_display = ['date', 'amount', 'entry_type', 'cost_centre', 'category', 'is_shared', 'entered_by_email', 'received_at']
    list_filter = ['entry_type', 'cost_centre', 'category', 'is_shared']
    search_fields = ['abms_id', 'cost_centre', 'category', 'supplier', 'note', 'entered_by_email']
    date_hierarchy = 'date'
    ordering = ['-date', '-received_at']

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(FarmAsset)
class FarmAssetAdmin(admin.ModelAdmin):
    """Synced from ABMS (see /api/costs/sync/) — read-only list, no delete.
    ABMS is the source of truth for assets; Django is a read mirror plus
    sync target, not a second place to hand-edit them."""

    list_display = ['name', 'asset_category', 'cost_centre', 'purchase_date', 'cost', 'annual_depreciation_display', 'status', 'disposed_date']
    list_filter = ['asset_category', 'cost_centre', 'status']
    search_fields = ['abms_id', 'name', 'cost_centre']
    ordering = ['-purchase_date']

    def annual_depreciation_display(self, obj):
        return format_money(obj.annual_depreciation())
    annual_depreciation_display.short_description = 'Annual depreciation'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(BusinessSettings)
class BusinessSettingsAdmin(admin.ModelAdmin):
    """The one editable toggle — everything else about this model is
    singleton plumbing (see BusinessSettings.save()/get_solo())."""

    list_display = ['is_vat_enabled']

    def has_add_permission(self, request):
        # Exactly one row should ever exist; get_solo() creates it lazily,
        # so there's nothing to "add" from here.
        return not BusinessSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(RevenueTarget)
class RevenueTargetAdmin(admin.ModelAdmin):
    """Entered in plain A.D. dates deliberately -- see the model's own
    docstring. The Reports dashboard (/dashboard/) reads this and displays
    everything in B.S."""

    list_display = ['name', 'amount', 'start_date', 'end_date', 'is_active']
    list_filter = ['is_active']
    list_editable = ['is_active']
    ordering = ['-start_date']