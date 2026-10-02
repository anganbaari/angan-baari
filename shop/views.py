from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse, HttpResponse
from django.utils.html import escape
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.admin.views.decorators import staff_member_required
from django.urls import reverse
from django.contrib import messages
from .models import NewsletterSubscriber, ContactMessage, ProductOrder, Review, Wishlist
from .emails import (
    send_order_received_email,
    send_order_cancelled_email,
    send_resend_email,
)

def home(request):
    from .models import Product
    featured_products = Product.objects.filter(
        is_available=True
    ).exclude(main_image='').order_by('?')[:6]
    return render(request, 'index.html', {'featured_products': featured_products})

def contact(request):
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        email = request.POST.get('email', '').strip()
        phone = request.POST.get('phone', '').strip()
        subject = request.POST.get('subject', 'General Inquiry').strip()
        message = request.POST.get('message', '').strip()

        if name and email and message:
            ContactMessage.objects.create(
                name=name,
                email=email,
                phone=phone,
                subject=subject,
                message=message,
            )
            send_resend_email(
                to=settings.ADMIN_EMAIL,
                subject=f'📩 New Message from {name} — Angan Baari',
                body=f'''
New contact message received!

━━━━━━━━━━━━━━━━━━━━━━
📩 MESSAGE DETAILS
━━━━━━━━━━━━━━━━━━━━━━
Name    : {name}
Email   : {email}
Phone   : {phone or 'Not provided'}
Subject : {subject}
Message : {message}
━━━━━━━━━━━━━━━━━━━━━━
                ''',
            )
            send_resend_email(
                to=email,
                subject='✅ Message Received — Angan Baari',
                body=f'''
नमस्ते {name}! 🌿

Thank you for contacting Angan Baari!
We will get back to you within 12-36 hours.

Subject : {subject}
Message : {message}

📱 WhatsApp: https://wa.me/9779821025084

Angan Baari Team 🌱
                ''',
            )
            return JsonResponse({'status': 'success'})
        return JsonResponse({'status': 'error'})
    return redirect('home')

def place_order(request):
    if request.method == 'POST':
        order = ProductOrder.objects.create(
            name=request.POST.get('name'),
            email=request.POST.get('customer-email'),
            phone=request.POST.get('phone', ''),
            address=request.POST.get('delivery-address', ''),
            product_interest=request.POST.get('product', ''),
            message=request.POST.get('message', ''),
        )
        try:
            send_order_received_email(order)
        except Exception:
            pass
        return render(request, 'success.html', {'order': order})
    return redirect('home')

def cancel_order(request, token):
    order = get_object_or_404(ProductOrder, cancel_token=token)

    if order.status == 'cancelled':
        return render(request, 'cancel.html', {
            'order': order,
            'already_cancelled': True
        })

    if not order.can_cancel():
        return render(request, 'cancel.html', {
            'order': order,
            'expired': True
        })

    if request.method == 'POST':
        order.status = 'cancelled'
        order.save()
        create_order_inventory_movements(order, 'return')
        send_order_cancelled_email(order)
        return render(request, 'cancel.html', {
            'order': order,
            'cancelled': True
        })

    return render(request, 'cancel.html', {'order': order})


@login_required
def reorder(request, order_id):
    """Re-add a past order's items to the current cart, using a structured
    snapshot saved at checkout time. Orders placed before this field existed
    have no snapshot and can't be reordered. Fixed-weight items (goat/chicken)
    are always skipped since each listing is one unique animal — the exact
    one from a past order is very unlikely to still be available."""
    from .models import Product
    order = get_object_or_404(ProductOrder, id=order_id, email=request.user.email)

    if not order.cart_snapshot:
        messages.error(request, "This order can't be reordered — it was placed before this feature existed.")
        return redirect('profile')

    cart = get_cart(request)
    added_count = 0
    skipped_count = 0

    for line in order.cart_snapshot:
        product_id = line.get('product_id')
        try:
            product = Product.objects.get(id=product_id, is_available=True)
        except Product.DoesNotExist:
            skipped_count += 1
            continue

        if product.pricing_mode == 'fixed_weight':
            skipped_count += 1
            continue

        try:
            qty = max(1, int(line.get('qty') or 1))
        except (TypeError, ValueError):
            qty = 1

        if product.pricing_mode == 'variable_weight':
            step = product.weight_step or '0.50'
            weight_str = format_weight(line.get('weight') or step, step)
            line_key = make_line_key(product.id, weight_str)
            qty_to_add = 1  # each variable-weight line represents one weight-slice, same as add_to_cart
            weight_for_cart = weight_str
            weight_step_for_cart = str(product.weight_step)
            weight_unit_for_cart = product.weight_unit_label
        else:  # fixed_quantity
            line_key = make_line_key(product.id, None)
            qty_to_add = qty
            weight_for_cart = None
            weight_step_for_cart = None
            weight_unit_for_cart = None

        if line_key in cart and isinstance(cart[line_key], dict):
            cart[line_key]['qty'] = int(cart[line_key].get('qty', 0) or 0) + qty_to_add
        else:
            cart[line_key] = {
                'product_id': product.id,
                'name': product.name,
                'price': str(product.price),
                'price_unit': product.price_unit,
                'image': product.main_image.url if product.main_image else '',
                'slug': product.slug,
                'qty': qty_to_add,
                'weight': weight_for_cart,
                'pricing_mode': product.pricing_mode,
                'weight_step': weight_step_for_cart,
                'weight_unit_label': weight_unit_for_cart,
            }
        added_count += 1

    save_cart(request, cart)

    if added_count and skipped_count:
        messages.success(request, f"Added {added_count} item(s) to your cart. {skipped_count} item(s) from this order are no longer available.")
    elif added_count:
        messages.success(request, f"Added {added_count} item(s) to your cart.")
    else:
        messages.error(request, "None of the items from this order are available to reorder right now.")

    return redirect('cart')


def newsletter_signup(request):
    if request.method == 'POST':
        email = request.POST.get('email', '').strip()
        name = request.POST.get('name', '').strip()
        if email:
            subscriber, created = NewsletterSubscriber.objects.get_or_create(email=email)
            if created:
                subscriber.name = name
                subscriber.save()
                send_resend_email(
                    to=email,
                    subject='🌿 Welcome to Angan Baari Newsletter!',
                    body=f'''
नमस्ते {name or 'valued customer'}! 🌿

Thank you for subscribing to Angan Baari newsletter!

You will now receive updates about:
🌱 New seasonal products
🍯 Fresh honey harvests
🥭 Fruit availability
🎉 Special offers and discounts

📱 Order via WhatsApp:
https://wa.me/9779821025084

Angan Baari Team 🌱
                    ''',
                )
        return redirect('/?subscribed=1#newsletter')
    return redirect('home')

def newsletter_unsubscribe(request, token):
    """Shows a confirm/cancel step first — visiting the link alone (e.g. an
    email client pre-fetching links, or a misclick) does NOT unsubscribe
    anyone. Only actually unsubscribes once the person clicks "Yes" and the
    form is submitted via POST."""
    subscriber = get_object_or_404(NewsletterSubscriber, unsubscribe_token=token)

    if request.method == 'POST':
        if subscriber.is_subscribed:
            subscriber.is_subscribed = False
            subscriber.save()

        return HttpResponse(f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>Unsubscribed — Angan Baari</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
  body {{ font-family: -apple-system, sans-serif; background:#1a2f1e; color:#fff;
          display:flex; align-items:center; justify-content:center; min-height:100vh;
          margin:0; padding:24px; text-align:center; }}
  .card {{ max-width:420px; }}
  h1 {{ font-size:1.5rem; margin-bottom:12px; }}
  p {{ color:rgba(255,255,255,0.7); line-height:1.6; }}
  a {{ color:#c9a84c; }}
</style></head>
<body>
  <div class="card">
    <h1>You've been unsubscribed 🌿</h1>
    <p>{escape(subscriber.email)} will no longer receive newsletter emails from Angan Baari.</p>
    <p>Changed your mind? You can always resubscribe from our <a href="/">website</a>.</p>
  </div>
</body></html>""")

    # GET — show the confirm/cancel step, nothing is changed yet
    from django.middleware.csrf import get_token
    csrf_token = get_token(request)

    return HttpResponse(f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>Unsubscribe? — Angan Baari</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
  body {{ font-family: -apple-system, sans-serif; background:#1a2f1e; color:#fff;
          display:flex; align-items:center; justify-content:center; min-height:100vh;
          margin:0; padding:24px; text-align:center; }}
  .card {{ max-width:420px; }}
  h1 {{ font-size:1.5rem; margin-bottom:12px; }}
  p {{ color:rgba(255,255,255,0.7); line-height:1.6; margin-bottom:28px; }}
  .btn-row {{ display:flex; gap:12px; justify-content:center; flex-wrap:wrap; }}
  button, a.btn {{ padding:12px 24px; border-radius:50px; border:none; font-size:0.92rem;
          font-weight:700; cursor:pointer; text-decoration:none; display:inline-block;
          font-family:inherit; }}
  .btn-yes {{ background:#c9a84c; color:#1a2f1e; }}
  .btn-yes:hover {{ background:#e8cc84; }}
  .btn-no {{ background:transparent; color:#fff; border:1px solid rgba(255,255,255,0.25) !important; }}
  .btn-no:hover {{ background:rgba(255,255,255,0.08); }}
</style></head>
<body>
  <div class="card">
    <h1>Are you sure? 🌿</h1>
    <p>Do you want to stop receiving newsletter emails from Angan Baari at<br><strong>{escape(subscriber.email)}</strong>?</p>
    <div class="btn-row">
      <form method="post" style="margin:0;">
        <input type="hidden" name="csrfmiddlewaretoken" value="{csrf_token}">
        <button type="submit" class="btn-yes">Yes, unsubscribe</button>
      </form>
      <a href="/" class="btn btn-no">No, keep me subscribed</a>
    </div>
  </div>
</body></html>""")


def error_404(request, exception):
    return render(request, '404.html', status=404)

def error_500(request):
    return render(request, '500.html', status=500)

def product_detail(request, slug):
    from .models import Product
    product = get_object_or_404(Product, slug=slug)
    related_products = Product.objects.filter(
        category=product.category
    ).exclude(id=product.id)[:3]
    reviews = product.reviews.filter(is_approved=True)
    review_count = reviews.count()
    avg_rating = round(sum(r.rating for r in reviews) / review_count, 1) if review_count else None

    variants = []
    if product.pricing_mode == 'fixed_weight':
        variants = product.variants.filter(is_available=True).order_by('weight')

    is_wishlisted = False
    if request.user.is_authenticated:
        is_wishlisted = Wishlist.objects.filter(user=request.user, product=product).exists()

    return render(request, 'product_detail.html', {
        'product': product,
        'related_products': related_products,
        'reviews': reviews,
        'review_count': review_count,
        'avg_rating': avg_rating,
        'ratings': Review.RATING_CHOICES,
        'variants': variants,
        'is_wishlisted': is_wishlisted,
    })

def submit_review(request, slug):
    from .models import Product
    product = get_object_or_404(Product, slug=slug)
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        rating = request.POST.get('rating', '').strip()
        comment = request.POST.get('comment', '').strip()
        if name and rating and comment:
            Review.objects.create(
                product=product,
                name=name,
                rating=int(rating),
                comment=comment,
                is_approved=False,  # needs admin approval
            )
            return redirect(product.get_absolute_url() + '?reviewed=1')
    return redirect(product.get_absolute_url())


@login_required
def wishlist_toggle(request, product_id):
    """Add/remove a product from the logged-in user's wishlist. Login is
    required since Wishlist rows are tied to a real user account — there's
    no session-based wishlist for anonymous visitors (unlike the cart)."""
    from .models import Product
    product = get_object_or_404(Product, id=product_id)
    existing = Wishlist.objects.filter(user=request.user, product=product).first()
    if existing:
        existing.delete()
        is_saved = False
    else:
        variant = None
        if product.pricing_mode == 'fixed_weight':
            variant_id = request.POST.get('variant_id')
            if variant_id:
                variant = product.variants.filter(id=variant_id, is_available=True).first()
            if not variant:
                available = sorted(product.available_variants(), key=lambda v: v.total_price())
                variant = available[0] if available else None
        Wishlist.objects.create(user=request.user, product=product, variant=variant)
        is_saved = True

    if is_ajax(request):
        return JsonResponse({'status': 'ok', 'is_saved': is_saved})
    return redirect(request.META.get('HTTP_REFERER', '/shop/'))


@login_required
def wishlist_set_variant(request, product_id):
    """Change which size/animal a fixed-weight wishlist item points to —
    e.g. switching a wishlisted goat from the 15kg listing to the 20kg one."""
    from .models import Product
    product = get_object_or_404(Product, id=product_id)
    wishlist_item = get_object_or_404(Wishlist, user=request.user, product=product)

    variant_id = request.POST.get('variant_id')
    variant = product.variants.filter(id=variant_id, is_available=True).first()
    if not variant:
        return JsonResponse({'status': 'error', 'message': 'That size is no longer available'}, status=400)

    wishlist_item.variant = variant
    wishlist_item.save(update_fields=['variant'])
    return JsonResponse({
        'status': 'ok',
        'weight': f"{variant.weight:.2f}",
        'price': str(variant.total_price()),
    })


@login_required
def wishlist_move_to_cart(request, product_id):
    """Add a wishlist item to the cart, then remove it from the wishlist —
    a saved item is 'claimed' once it's actually in the cart, matching how
    most shopping wishlists behave. For fixed_weight products, uses whichever
    variant was picked (or currently selected in the dropdown)."""
    from .models import Product
    product = get_object_or_404(Product, id=product_id)

    line_key, weight_str, qty_to_add, fixed_total = resolve_cart_line(request, product)
    mode = product.pricing_mode
    price_to_store = str(fixed_total) if fixed_total is not None else str(product.price)

    cart = get_cart(request)
    if line_key in cart:
        if mode != 'fixed_weight':
            cart[line_key]['qty'] = int(cart[line_key].get('qty', 0) or 0) + qty_to_add
    else:
        cart[line_key] = {
            'product_id': product.id,
            'name': product.name,
            'price': price_to_store,
            'price_unit': '(fixed price)' if mode == 'fixed_weight' else product.price_unit,
            'image': product.main_image.url if product.main_image else '',
            'slug': product.slug,
            'qty': 1 if mode == 'fixed_weight' else qty_to_add,
            'weight': weight_str,
            'pricing_mode': mode,
            'weight_step': str(product.weight_step) if mode == 'variable_weight' else None,
            'weight_unit_label': product.weight_unit_label if mode == 'variable_weight' else None,
        }
    save_cart(request, cart)

    Wishlist.objects.filter(user=request.user, product=product).delete()

    if is_ajax(request):
        return JsonResponse({'status': 'ok', 'count': cart_count(request)})
    return redirect('cart')


# ─── CART SYSTEM ───────────────────────────────────────────────

def get_cart(request):
    return request.session.get('cart', {})

def save_cart(request, cart):
    request.session['cart'] = cart
    request.session.modified = True


# ─── PRICING MODE HELPERS ────────────────────────────────────────
# Every product has an explicit pricing_mode set in admin:
#   variable_weight  -> customer picks the weight, snapped to product.weight_step
#                        (e.g. 0.50 for fruit, 0.25 for pickle jars). Price = rate x weight.
#   fixed_quantity   -> plain quantity stepper, no weight at all (banana/dozen, jars).
#   fixed_weight     -> the product's actual weight is fixed by admin (product.fixed_weight,
#                        e.g. 20kg goat). Customer cannot change qty or weight — one click
#                        adds it, price is locked at rate x fixed_weight.

def format_weight(raw, step='0.50'):
    """Snap any incoming weight value to the nearest multiple of `step` and
    return it as a 2-decimal string, e.g. with step=0.50: '1.2' -> '1.00',
    '1.3' -> '1.50'. Falls back to one step on bad/zero/negative input so a
    cart line is never silently dropped."""
    from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
    try:
        step_dec = Decimal(str(step)) if step else Decimal('0.50')
        if step_dec <= 0:
            step_dec = Decimal('0.50')
    except (InvalidOperation, TypeError, ValueError):
        step_dec = Decimal('0.50')
    try:
        weight = Decimal(str(raw))
        if weight <= 0:
            weight = step_dec
        snapped = (weight / step_dec).to_integral_value(rounding=ROUND_HALF_UP) * step_dec
        if snapped <= 0:
            snapped = step_dec
        return f"{snapped:.2f}"
    except (InvalidOperation, TypeError, ValueError, ZeroDivisionError):
        return f"{step_dec:.2f}"


def resolve_cart_line(request, product):
    """Given the POST data and a product, figure out how this add-to-cart
    should behave based on the product's pricing_mode. Returns
    (line_key, weight_str, qty_to_add, fixed_total_price).
    fixed_total_price is only set (not None) for fixed_weight products —
    it's the locked total for the SPECIFIC animal/variant chosen, already
    computed, so the cart never has to multiply price x weight for these."""
    mode = product.pricing_mode

    if mode == 'variable_weight':
        step = product.weight_step or '0.50'
        weight_str = format_weight(request.POST.get('weight', step), step)
        line_key = make_line_key(product.id, weight_str)
        return line_key, weight_str, 1, None

    if mode == 'fixed_weight':
        variant = None
        variant_id = request.POST.get('variant_id')
        if variant_id:
            variant = product.variants.filter(id=variant_id, is_available=True).first()
        if not variant:
            # No variant chosen (e.g. one-click add from the shop grid) —
            # default to the cheapest available size.
            variant = sorted(product.available_variants(), key=lambda v: v.total_price())[:1]
            variant = variant[0] if variant else None

        if variant:
            weight_str = f"{variant.weight:.2f}"
            fixed_total = variant.total_price()
            line_key = f"{product.id}_v{variant.id}"
        elif product.fixed_weight:
            # Fallback for a fixed_weight product with no variant rows added yet.
            from decimal import Decimal
            weight_str = f"{Decimal(str(product.fixed_weight)):.2f}"
            fixed_total = round(Decimal(str(product.price)) * Decimal(str(product.fixed_weight)), 2)
            line_key = make_line_key(product.id, weight_str)
        else:
            weight_str = None
            fixed_total = product.price
            line_key = make_line_key(product.id, None)
        return line_key, weight_str, 1, fixed_total

    # fixed_quantity (also the safe default for legacy/unset products)
    try:
        qty_to_add = max(1, int(request.POST.get('quantity', 1)))
    except (TypeError, ValueError):
        qty_to_add = 1
    line_key = make_line_key(product.id, None)
    return line_key, None, qty_to_add, None

def make_line_key(product_id, weight_str):
    """Weighted lines get a composite key so the same product at two
    different weights becomes two separate cart lines. Non-weighted
    lines keep the plain product id, unchanged from before."""
    if weight_str:
        return f"{product_id}_{weight_str}"
    return str(product_id)

def parse_line_key(line_key):
    """Returns (product_id_str, weight_str_or_None)."""
    if '_' in line_key:
        pid, weight_str = line_key.split('_', 1)
        return pid, weight_str
    return line_key, None

def extract_variant_id(line_key):
    """For fixed_weight cart lines, line_key looks like '{product_id}_v{variant_id}'
    (see resolve_cart_line above). Returns the variant_id as int, or None for
    every other line shape (variable_weight/fixed_quantity lines never have
    a variant at all)."""
    if '_v' in line_key:
        try:
            return int(line_key.rsplit('_v', 1)[1])
        except (ValueError, IndexError):
            return None
    return None

def create_order_inventory_movements(order, movement_type):
    """Create one InventoryMovement per line in this order's cart_snapshot —
    'sale' when an order is placed, 'return' when one is cancelled."""
    create_inventory_movements_from_snapshot(
        order.cart_snapshot, movement_type, source='website',
        related_order=order, note=f"Order {order.order_number}",
    )

def create_inventory_movements_from_snapshot(cart_snapshot, movement_type, source,
                                               related_order=None, related_pos_sale=None, note=None,
                                               strict=False):
    """Shared by website checkout/cancellation and the shop POS — creates one
    InventoryMovement per cart line.

    strict=False (the default, used by the website): never raises. A bad
    line is silently skipped rather than losing a customer's whole order
    over one problem line — matching this file's existing email
    try/except pattern.

    strict=True (used by the POS): re-raises any failure, AND calls
    full_clean() on each movement so the model's own oversell/quantity
    rules are actually enforced — skipped everywhere else in this file,
    since full_clean() isn't called automatically on save(). The caller is
    expected to wrap this in transaction.atomic() so a failure on any line
    rolls back the whole sale rather than leaving a partial one. This is a
    deliberate difference from the website: an in-person sale hasn't been
    promised to anyone yet, so blocking it here (and letting the cashier
    handle it face to face) is more correct than silently allowing an
    oversell the way we accept for an already-placed online order.
    """
    from decimal import Decimal
    from .models import Product, InventoryMovement
    if not cart_snapshot:
        return
    for line in cart_snapshot:
        try:
            product = Product.objects.get(id=line.get('product_id'))
            qty = int(line.get('qty', 0) or 0)
            weight = line.get('weight')
            variant_id = line.get('variant_id')
            variant = None

            if product.pricing_mode == 'fixed_weight':
                quantity = Decimal('1')  # enforced shape — one animal per movement
                if variant_id:
                    variant = product.variants.filter(id=variant_id).first()
            elif product.pricing_mode == 'variable_weight':
                quantity = Decimal(str(qty)) * Decimal(str(weight or 0))
            else:  # fixed_quantity
                quantity = Decimal(str(qty))
                unit_id = line.get('unit_id')
                if unit_id:
                    unit = product.selling_units.filter(id=unit_id).first()
                    if unit:
                        # e.g. 1 Crate of eggs removes 30 pieces from stock —
                        # inventory is always tracked in the product's own
                        # base counting unit, never in whatever unit was sold.
                        quantity *= Decimal(str(unit.quantity_in_base_units))

            if quantity <= 0:
                continue

            movement = InventoryMovement(
                product=product,
                variant=variant,
                movement_type=movement_type,
                source=source,
                quantity=quantity,
                related_order=related_order,
                related_pos_sale=related_pos_sale,
                note=note,
            )
            if strict:
                movement.full_clean()
            movement.save()
        except Exception:
            if strict:
                raise
            continue


class POSSaleValidationError(Exception):
    """Raised by create_pos_sale() for any business-rule failure (bad cart
    line, payments not matching the required total, missing customer for a
    credit line, insufficient stock). Callers translate .message/.status
    into their own response shape (JsonResponse for the traditional POS
    view, DRF Response for the API one) rather than each re-implementing
    the same validation."""

    def __init__(self, message, status=400):
        self.message = message
        self.status = status
        super().__init__(message)


VAT_RATE = 0.13  # 13% — Nepal's standard VAT rate. A constant, not a
                 # literal, so the one place this ever needs to change is
                 # here, not scattered through every total computation.


def get_pos_operator(request):
    """Resolves the currently-unlocked operator from session state that
    PosUnlockView sets on a successful PIN match — never from client
    input (request.data), which would let any request just claim to be
    any staff member without that person actually entering their PIN on
    this terminal/session.

    Raises POSSaleValidationError(status=403) if the terminal was never
    unlocked, has since been locked (PosLockView), or the identified
    profile's user is no longer active/staff (e.g. deactivated after the
    unlock). Shared by sale creation and credit repayment — both callers
    catch POSSaleValidationError the same way regardless of which one
    raised it.
    """
    from .models import UserProfile

    operator_id = request.session.get('pos_operator_id')
    if not operator_id:
        raise POSSaleValidationError('Terminal is locked — enter a PIN first.', status=403)
    try:
        return UserProfile.objects.select_related('user').get(
            user_id=operator_id, user__is_staff=True, user__is_active=True,
        ).user
    except UserProfile.DoesNotExist:
        raise POSSaleValidationError('Terminal is locked — enter a PIN first.', status=403)


def resolve_pos_coupon(code, subtotal):
    """Validate a coupon code against a cart subtotal — same rules as the
    website checkout's coupon handling (Coupon.is_live(), min_order_amount,
    Coupon.calculate_discount()), factored out here so both the live
    /pos/coupon/validate/ preview and create_pos_sale()'s own server-side
    recheck use exactly one implementation.

    `subtotal` must already exclude any offer/combo-discounted lines (see
    the coupon_eligible_subtotal callers compute in create_pos_sale() and
    pos.html's couponEligibleSubtotal()) — a coupon never discounts a line
    that's already discounted by an Offer, so this function never sees
    those lines' value at all, not even to apply min_order_amount against.

    Returns (coupon_obj_or_None, discount_amount, error_message_or_None).
    A blank/whitespace code returns (None, Decimal('0'), None) — "no coupon"
    is not an error. discount_amount is always quantized to 2dp so it lines
    up exactly with the 2dp totals the rest of the sale math uses.
    """
    from decimal import Decimal, ROUND_HALF_UP
    from .models import Coupon

    code = (code or '').strip().upper()
    if not code:
        return None, Decimal('0'), None

    try:
        coupon_obj = Coupon.objects.get(code=code)
    except Coupon.DoesNotExist:
        return None, Decimal('0'), 'Invalid coupon code.'

    if not coupon_obj.is_live():
        return None, Decimal('0'), 'This coupon has expired or is no longer active.'

    subtotal = Decimal(str(subtotal))
    if subtotal < coupon_obj.min_order_amount:
        return None, Decimal('0'), f'Minimum order of Rs. {coupon_obj.min_order_amount:.0f} required for this coupon.'

    discount_amount = coupon_obj.calculate_discount(subtotal).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    return coupon_obj, discount_amount, None


def resolve_pos_offer_discount(offer_id, product, base_price):
    """Re-validates a single-product (percent/fixed) Offer server-side and
    returns the discounted price for one unit of `base_price` — never trusts
    a client-sent discounted price, same principle as resolve_pos_coupon().

    `base_price` is whatever this cart line's own per-unit price would
    normally be (product.price, a ProductSellingUnit's price, or a
    fixed_weight variant's total_price) — Offer.discounted_price() just does
    percent/fixed arithmetic on whatever Decimal it's given, so this works
    the same way regardless of pricing_mode.

    Raises POSSaleValidationError if the offer doesn't exist, isn't live,
    is a combo (those go through resolve_pos_combo_lines() instead), or
    doesn't actually include this product.
    """
    from .models import Offer

    try:
        offer = Offer.objects.get(id=offer_id)
    except (Offer.DoesNotExist, ValueError, TypeError):
        raise POSSaleValidationError('That offer no longer exists.')
    if offer.discount_type == 'combo':
        raise POSSaleValidationError(f'"{offer.title}" is a combo deal, not a per-product discount.')
    if not offer.is_live():
        raise POSSaleValidationError(f'"{offer.title}" is no longer available.')
    if product not in offer.get_products():
        raise POSSaleValidationError(f'{product.name} is not part of "{offer.title}".')
    return offer.discounted_price(base_price)


def resolve_combo_reference_price(bundle_item, available_variants):
    """For a fixed_weight BundleItem, the price a chosen animal is compared
    against to work out the combo upcharge (see BundleItem.reference_weight).

    Looks for the variant matching reference_weight among `available_variants`
    (today's actually-available animals for this product) and uses its price.
    If none currently matches that exact weight -- animals come and go, the
    one originally priced at the reference weight may be long sold -- falls
    back to the cheapest currently-available variant's price instead of
    erroring out. Returns None if there are no available variants at all
    (caller's problem: nothing can be priced or sold either way).
    """
    if not available_variants:
        return None
    if bundle_item.reference_weight is not None:
        match = next((v for v in available_variants if v.weight == bundle_item.reference_weight), None)
        if match is not None:
            return match.total_price()
    return min(v.total_price() for v in available_variants)


def resolve_pos_combo_lines(cart):
    """Validates every combo-offer group in a cart and returns the
    authoritative, server-computed price for each of their lines — a
    client-sent combo line price is never trusted, same principle as
    resolve_pos_coupon()/resolve_pos_offer_discount().

    A combo "instance" is a set of cart lines sharing the same
    combo_instance_id (a UUID the POS screen generates client-side when the
    cashier taps a combo in the offers picker — one per Offer.bundle_items
    row, exactly like how one real animal/weight/unit becomes one ordinary
    cart line everywhere else in this file). This function:
      1. Groups lines by combo_instance_id.
      2. Re-fetches the combo Offer fresh and confirms it's still live.
      3. Confirms the submitted lines are exactly the offer's current
         BundleItem set (by product_id) — not stale, not tampered with.
      4. Prices each line as its proportional share of combo_price, using
         each BundleItem's *declared* quantity x product.price (not
         whichever specific fixed_weight variant the cashier happened to
         pick) as the apportionment weight — so the money math stays
         deterministic regardless of which particular animal was in stock
         that day. The last line in each group absorbs the rounding
         remainder so the group's lines sum to combo_price exactly.
      5. For a fixed_weight slot (goat/chicken), adds an upcharge on top of
         that share when the cashier picked a heavier/pricier animal than
         BundleItem.reference_weight assumes — see
         resolve_combo_reference_price(). Never a discount: picking at or
         below the reference weight costs exactly the plain share, picking
         above it adds the real price difference. This upcharge is a pure
         add-on, computed after and independent of the remainder-absorption
         above, so the group's *plain* shares still sum to combo_price
         exactly regardless of which line(s) carry an upcharge.

    Returns {cart_index: Decimal(authoritative_price), ...} covering every
    line that belongs to a combo. Raises POSSaleValidationError if any
    combo group is incomplete, stale, or no longer live. Stock deduction
    for these lines is untouched — each one is still a completely ordinary
    product/variant/unit line as far as create_inventory_movements_from_
    snapshot() is concerned; only the price is special-cased here.
    """
    from decimal import Decimal, ROUND_HALF_UP
    from .models import Offer

    groups = {}
    for index, line in enumerate(cart):
        combo_instance_id = line.get('combo_instance_id')
        if combo_instance_id:
            groups.setdefault(combo_instance_id, []).append((index, line))

    line_prices = {}
    for combo_instance_id, entries in groups.items():
        offer_id = entries[0][1].get('offer_id')
        try:
            offer = Offer.objects.get(id=offer_id, discount_type='combo')
        except (Offer.DoesNotExist, ValueError, TypeError):
            raise POSSaleValidationError('One of the combo offers in this cart no longer exists.')
        if not offer.is_live():
            raise POSSaleValidationError(f'"{offer.title}" is no longer available.')

        bundle_items = list(offer.bundle_items.select_related('product').all())
        natural_total = offer.get_bundle_natural_total()
        if not bundle_items or natural_total <= 0:
            raise POSSaleValidationError(f'"{offer.title}" is not configured correctly.')
        if len(entries) != len(bundle_items):
            raise POSSaleValidationError(
                f'"{offer.title}" in the cart doesn\'t match the current bundle contents — remove and re-add it.'
            )

        # Match each cart line to exactly one BundleItem by product, consuming
        # each BundleItem at most once (handles a product appearing twice in
        # one bundle) rather than assuming cart order matches bundle order.
        remaining = list(bundle_items)
        allocations = []
        for index, line in entries:
            match = next((bi for bi in remaining if bi.product_id == line.get('product_id')), None)
            if not match:
                raise POSSaleValidationError(
                    f'"{offer.title}" in the cart doesn\'t match the current bundle contents — remove and re-add it.'
                )
            remaining.remove(match)
            allocations.append((index, match))

        combo_price = offer.combo_price or natural_total
        running_total = Decimal('0')
        for position, (index, bundle_item) in enumerate(allocations):
            # running_total only ever tracks these plain proportional shares
            # (pre-upcharge) -- a fixed_weight upcharge below is layered on
            # top afterward, never folded into the remainder math, so the
            # last line's share still correctly sums the GROUP to combo_price
            # regardless of which line(s) carry an upcharge.
            if position == len(allocations) - 1:
                price = combo_price - running_total  # last line absorbs the rounding remainder
            else:
                share = (bundle_item.line_total() / natural_total) * combo_price
                price = share.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                running_total += price

            if bundle_item.product.pricing_mode == 'fixed_weight':
                line = cart[index]
                variant_id = line.get('variant_id')
                variant = (
                    bundle_item.product.variants.filter(id=variant_id, is_available=True).first()
                    if variant_id else None
                )
                if not variant:
                    raise POSSaleValidationError(f'{bundle_item.product.name}: that animal is no longer available.')
                available = list(bundle_item.product.available_variants())
                reference_price = resolve_combo_reference_price(bundle_item, available)
                if reference_price is None:
                    raise POSSaleValidationError(
                        f'{bundle_item.product.name}: no animals currently available for this combo.'
                    )
                upcharge = variant.total_price() - reference_price
                if upcharge > 0:
                    price += upcharge

            line_prices[index] = price

    return line_prices


def create_pos_sale(*, client_sale_id, cart, payments, operator_user, customer=None,
                     source='pos', note_prefix='POS sale', coupon_code=None):
    """Called by POSSaleView.post() (api/views.py) — the sole entry point
    for completing a POS sale since templates/pos.html's completeSaleBtn
    switched to POST /api/v1/sales/ (POS-PWA Phase 1); the earlier plain-
    Django-view wrapper (pos_create_sale, at /pos/sale/) was removed once
    nothing called it anymore. Computes the cart total (and, once
    BusinessSettings.is_vat_enabled is switched on, the taxable/exempt/VAT
    split), validates the payment lines against it, and — all inside one
    atomic transaction — creates the POSSale, one POSSalePayment per
    payment line, a CreditTransaction for any credit line, and the same
    InventoryMovement rows website checkout writes.

    cashier is set from operator_user, not necessarily whoever's session
    is logged into the terminal — same reasoning as the PIN-unlock work:
    the terminal login can stay active all day while different staff take
    turns operating the register, and operator_user is whoever the PIN
    identified as actually running this specific sale.

    Idempotent on client_sale_id: if a sale with this id already exists,
    it's returned immediately with no re-validation and nothing new
    created — same behavior this already had before payments/VAT existed.

    coupon_code (POS Phase C) is resolved fresh here via resolve_pos_coupon(),
    never trusted from a client-supplied discount_amount — same reasoning
    as the rest of this function's server-side recomputation. See
    resolve_pos_coupon() for the coupon rules themselves and
    pos_validate_coupon() for the pre-checkout preview that uses the same
    function.

    Offers (POS Phase D) live on individual cart lines, not as a function
    parameter, since (unlike one coupon per sale) a cart can mix several
    offer-discounted lines and several combo instances at once:
      - A line with offer_id but no combo_instance_id is a single discounted
        product — resolve_pos_offer_discount() re-derives its price.
      - A group of lines sharing a combo_instance_id is one combo instance —
        resolve_pos_combo_lines() validates the whole group against the
        offer's current BundleItem set and prices every line in it. Stock
        validation/deduction is unaffected either way; offers only ever
        override price.

    Returns (sale, created) — created is False on the idempotent-replay
    path, so callers that distinguish 200 vs 201 (the API path) still can.
    """
    from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
    from django.core.exceptions import ValidationError
    from django.db import transaction
    from .models import BusinessSettings, CreditTransaction, POSSale, POSSalePayment, Product

    existing = POSSale.objects.filter(client_sale_id=client_sale_id).first()
    if existing:
        return existing, False

    if not cart:
        raise POSSaleValidationError('Cart is empty.')
    if not payments:
        raise POSSaleValidationError('At least one payment line is required.')

    valid_methods = dict(POSSalePayment.METHOD_CHOICES)
    for line in payments:
        if line.get('method') not in valid_methods:
            raise POSSaleValidationError('Choose a valid payment method for every payment line.')
        try:
            if Decimal(str(line.get('amount'))) <= 0:
                raise POSSaleValidationError('Each payment amount must be greater than zero.')
        except (InvalidOperation, TypeError, ValueError):
            raise POSSaleValidationError('Each payment amount must be a valid number.')

    has_credit_line = any(line['method'] == 'credit' for line in payments)
    if has_credit_line and not customer:
        raise POSSaleValidationError('A customer is required for a credit payment.')

    settings_row = BusinessSettings.get_solo()

    # POS Phase D: offers. A combo group's lines are priced entirely by
    # resolve_pos_combo_lines() below (apportioned shares of combo_price);
    # a single-product offer line just gets its usual per-pricing-mode price
    # discounted via resolve_pos_offer_discount(). Either way, stock
    # validation/deduction for every line stays completely ordinary — offers
    # only ever override the money, never the product/variant/unit logic.
    combo_line_prices = resolve_pos_combo_lines(cart)

    total = Decimal('0')
    # Separate from `total` -- a coupon only ever discounts lines that
    # AREN'T already offer/combo-discounted (see resolve_pos_coupon() call
    # below), so this only accumulates plain, undiscounted lines.
    coupon_eligible_subtotal = Decimal('0')
    taxable_value = Decimal('0')
    exempt_value = Decimal('0')
    cart_snapshot = []
    try:
        for index, line in enumerate(cart):
            product = Product.objects.get(id=line.get('product_id'), is_available=True)
            qty = int(line.get('qty', 1) or 1)
            weight = line.get('weight')
            variant_id = line.get('variant_id')
            unit_id = line.get('unit_id')
            unit = None
            offer_id = line.get('offer_id')
            combo_instance_id = line.get('combo_instance_id')
            # A combo line's price is fixed by resolve_pos_combo_lines() --
            # offer_id is still recorded on it (set alongside combo_instance_id
            # by the POS screen) but never re-applied as a separate discount.
            is_single_offer_line = bool(offer_id) and not combo_instance_id

            if product.pricing_mode == 'fixed_weight':
                variant = product.variants.filter(id=variant_id, is_available=True).first() if variant_id else None
                if not variant:
                    raise POSSaleValidationError(f'{product.name}: that animal is no longer available.')
                line_total = variant.total_price()
                if is_single_offer_line:
                    line_total = resolve_pos_offer_discount(offer_id, product, line_total)
            elif product.pricing_mode == 'variable_weight':
                unit_price = Decimal(str(product.price))
                if is_single_offer_line:
                    unit_price = resolve_pos_offer_discount(offer_id, product, unit_price)
                line_total = unit_price * Decimal(str(weight or 0))
            else:
                if unit_id:
                    unit = product.selling_units.filter(id=unit_id, is_available=True).first()
                    if not unit:
                        raise POSSaleValidationError(f'{product.name}: that selling unit is no longer available.')
                    unit_price = unit.price
                else:
                    unit_price = Decimal(str(product.price))
                if is_single_offer_line:
                    unit_price = resolve_pos_offer_discount(offer_id, product, unit_price)
                line_total = unit_price * qty

            if combo_instance_id:
                line_total = combo_line_prices[index]

            total += line_total
            if not offer_id and not combo_instance_id:
                coupon_eligible_subtotal += line_total
            if settings_row.is_vat_enabled and product.is_taxable:
                taxable_value += line_total
            else:
                exempt_value += line_total

            cart_snapshot.append({
                'product_id': product.id, 'weight': weight, 'qty': qty, 'variant_id': variant_id,
                'unit_id': unit.id if unit else None,
                'unit_name': unit.get_name_display() if unit else None,
                'offer_id': offer_id, 'combo_instance_id': combo_instance_id,
            })
    except (Product.DoesNotExist, InvalidOperation, TypeError, ValueError):
        raise POSSaleValidationError('One of the items in this cart is no longer valid.')

    # POS Phase C: coupon discount. Resolved against the pre-tax subtotal of
    # only the non-offer, non-combo lines (coupon_eligible_subtotal) -- a
    # coupon never discounts a line that's already offer/combo-discounted,
    # same reasoning as a combo's fixed bundle price never being touched
    # by anything else. An invalid/expired/no-longer-qualifying code is a
    # hard error here (unlike checkout(), which just shows an error banner
    # and proceeds at full price): the POS operator already told the
    # customer a discount applies, so silently dropping it would complete a
    # sale for more than what was rung up on the screen. The one exception
    # is a cart with nothing left for the coupon to discount at all (every
    # line is offer/combo priced) -- that's not an invalid code, there's
    # just nothing eligible, so the sale completes at full (undiscounted)
    # price instead of being blocked. In the normal UI flow this can't
    # actually happen (pos.html's Apply button already refuses to apply a
    # coupon to an all-offer cart with its own clear toast, the same
    # restricted-subtotal rule mirrored client-side), so reaching this
    # branch server-side means a stale or direct API request, not a
    # cashier action to explain.
    if coupon_code and coupon_code.strip() and coupon_eligible_subtotal <= 0:
        coupon_obj, discount_amount, coupon_error = None, Decimal('0'), None
    else:
        coupon_obj, discount_amount, coupon_error = resolve_pos_coupon(coupon_code, coupon_eligible_subtotal)
    if coupon_error:
        raise POSSaleValidationError(coupon_error)
    discounted_subtotal = total - discount_amount

    if settings_row.is_vat_enabled:
        # Spread the discount across taxable/exempt in the same proportion
        # the undiscounted cart had, then compute VAT on the now-smaller
        # taxable amount — a coupon reduces the tax bill too, not just the
        # sticker price.
        ratio = (taxable_value / total) if total > 0 else Decimal('0')
        taxable_value = (discounted_subtotal * ratio).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        exempt_value = discounted_subtotal - taxable_value
        vat_amount = (taxable_value * Decimal(str(VAT_RATE))).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        required_total = exempt_value + taxable_value + vat_amount
    else:
        # Dormant scaffolding: regardless of any product's is_taxable flag,
        # nothing is taxable while VAT itself is off.
        taxable_value = Decimal('0')
        vat_amount = Decimal('0')
        exempt_value = discounted_subtotal
        required_total = discounted_subtotal

    # Standard retail cash rounding: nobody pays paisa, so the grand total
    # itself is rounded to the nearest rupee (round-half-up, not Python's
    # bare round() which does banker's rounding and would round e.g. 222.50
    # down to 222 instead of up to 223). taxable_value/exempt_value/
    # vat_amount above are left exactly as computed -- only required_total
    # (what payments must actually match) and the stored round_off_amount
    # change, so the detailed tax breakdown still adds up precisely on its
    # own and the rounding difference lives in exactly one place.
    rounded_total = required_total.quantize(Decimal('1'), rounding=ROUND_HALF_UP)
    round_off_amount = (rounded_total - required_total).quantize(Decimal('0.01'))
    required_total = rounded_total

    payments_sum = sum((Decimal(str(line['amount'])) for line in payments), Decimal('0'))
    if payments_sum != required_total:
        raise POSSaleValidationError(
            f'Payments (Rs. {payments_sum}) do not match the sale total (Rs. {required_total}).'
        )

    payment_method = payments[0]['method'] if len(payments) == 1 else 'split'

    try:
        with transaction.atomic():
            sale = POSSale.objects.create(
                client_sale_id=client_sale_id,
                cashier=operator_user,
                customer=customer,
                payment_method=payment_method,
                cart_snapshot=cart_snapshot,
                total_amount=required_total,
                taxable_value=taxable_value,
                exempt_value=exempt_value,
                vat_amount=vat_amount,
                coupon=coupon_obj,
                discount_amount=discount_amount,
                round_off_amount=round_off_amount,
            )
            if coupon_obj:
                coupon_obj.used_count += 1
                coupon_obj.save(update_fields=['used_count'])
            for line in payments:
                POSSalePayment.objects.create(
                    sale=sale, method=line['method'], amount=Decimal(str(line['amount'])),
                )
                if line['method'] == 'credit':
                    CreditTransaction.objects.create(
                        customer=customer,
                        amount=Decimal(str(line['amount'])),
                        transaction_type='credit_sale',
                        related_pos_sale=sale,
                        recorded_by=operator_user,
                    )
            create_inventory_movements_from_snapshot(
                cart_snapshot, 'sale', source=source,
                related_pos_sale=sale, note=f"{note_prefix} {sale.sale_number}",
                strict=True,
            )
    except ValidationError as e:
        message = '; '.join(e.messages) if hasattr(e, 'messages') else str(e)
        raise POSSaleValidationError(f'Not enough stock: {message}', status=409)
    except POSSaleValidationError:
        raise
    except Exception:
        raise POSSaleValidationError(
            'Could not complete this sale — nothing was charged or recorded. Please try again.'
        )

    return sale, True


def pos_service_worker(request):
    """Serves static/js/pos-sw.js at /pos/service-worker.js -- NOT through
    Django's normal static-files pipeline, and deliberately not staff-only
    (a service worker request has no session cookie context worth gating,
    and browsers won't send credentials for it anyway).

    This has to be a real view under /pos/ rather than a plain static file
    under /static/js/, because a service worker's effective scope can
    never exceed the directory its own script URL lives in unless the
    response sends a Service-Worker-Allowed header -- and PythonAnywhere's
    static-file mapping (the Web tab UI) has no way to attach a custom
    header to one specific file. Reading the source directly from
    static/js/pos-sw.js (not STATIC_ROOT/collectstatic output) means this
    works identically in local dev and in production without depending on
    collectstatic having been run for this one file.
    """
    from pathlib import Path
    from django.conf import settings
    from django.http import Http404, HttpResponse

    sw_path = Path(settings.BASE_DIR) / 'static' / 'js' / 'pos-sw.js'
    try:
        content = sw_path.read_text(encoding='utf-8')
    except FileNotFoundError:
        raise Http404()

    response = HttpResponse(content, content_type='application/javascript')
    response['Service-Worker-Allowed'] = '/pos/'
    response['Cache-Control'] = 'no-cache'  # staff should get SW updates on next load, not a stale cached copy
    return response


@staff_member_required
def pos_view(request):
    """The shop POS screen. Staff-only (Django's own is_staff flag — same
    login as admin, no separate token/auth system needed)."""
    import json
    from .models import BusinessSettings, Category, Product, POSSale

    def top_level_category(category):
        # The POS category row shows broad groups (Fruits, Vegetables, ...),
        # not the finer subcategories the shop site uses (Mangoes, Lychee,
        # ... under Fruits) -- walk up to whichever ancestor has no parent.
        while category.parent_id:
            category = category.parent
        return category

    products = Product.objects.filter(is_available=True).select_related('category', 'category__parent')
    products_data = []
    for p in products:
        entry = {
            'id': p.id,
            'name': p.name,
            'category': top_level_category(p.category).name if p.category else 'Other',
            'pricing_mode': p.pricing_mode,
            'price': str(p.price) if p.price is not None else None,
            'price_unit': p.price_unit or '',
            'weight_step': str(p.weight_step) if p.weight_step else None,
            'weight_unit_label': p.weight_unit_label or 'kg',
            'weight_entry_mode': p.weight_entry_mode,
            'barcode': p.barcode or '',
            'image': p.main_image.url if p.main_image else '',
            # POS Phase B: lets the frontend mirror create_pos_sale()'s
            # taxable/exempt split for a live tax-box preview while
            # shopping, before any POSSale row exists to read it from.
            'is_taxable': p.is_taxable,
        }
        if p.pricing_mode == 'fixed_weight':
            entry['variants'] = [
                {
                    'id': v.id,
                    'weight': str(v.weight),
                    'label': v.label or '',
                    'total_price': str(v.total_price()),
                }
                for v in p.available_variants()
            ]
        if p.pricing_mode == 'fixed_quantity':
            units = p.available_selling_units()
            if units:
                entry['units'] = [
                    {
                        'id': u.id,
                        'name': u.name,
                        'label': u.get_name_display(),
                        'price': str(u.price),
                        'is_default': u.is_default,
                    }
                    for u in units
                ]
        products_data.append(entry)

    # Every top-level category a product could belong to (via itself or any
    # subcategory), independent of whether anything in it is currently in
    # stock — a category must never disappear from the row just because
    # it's temporarily sold out. (Previously derived from products_data
    # itself, which only ever contained is_available=True products, so an
    # out-of-stock category would silently vanish entirely; it also used
    # to list subcategories like "Mangoes" alongside top-level ones like
    # "Fruits" instead of grouping under it, which doesn't match the POS
    # screen's flat category row.)
    categories_with_products = Category.objects.filter(products__isnull=False).select_related('parent').distinct()
    top_level_ids_with_products = {top_level_category(c).id for c in categories_with_products}
    categories = list(
        Category.objects.filter(parent__isnull=True, id__in=top_level_ids_with_products)
        .order_by('order', 'name').values_list('name', flat=True)
    )
    if Product.objects.filter(category__isnull=True).exists():
        categories.append('Other')

    vat_settings = BusinessSettings.get_solo()

    return render(request, 'pos.html', {
        'products_json': json.dumps(products_data),
        'categories': categories,
        'categories_json': json.dumps(categories),
        'payment_methods': POSSale.PAYMENT_METHOD_CHOICES,
        'is_vat_enabled': vat_settings.is_vat_enabled,
        'vat_rate': VAT_RATE,
    })


def line_subtotal(item):
    try:
        if not isinstance(item, dict):
            return 0
        price = float(item.get('price', 0) or 0)
        qty = int(item.get('qty', 0) or 0)
        if item.get('pricing_mode') == 'fixed_weight':
            # price is already the locked TOTAL for this specific animal —
            # weight here is informational only, not a multiplier.
            return price * qty
        weight = float(item['weight']) if item.get('weight') else 1
        return price * qty * weight
    except (KeyError, TypeError, ValueError):
        return 0


def cart_count(request):
    # Defensive: a single malformed/stale session cart line (leftover from
    # an older cart schema) must never take down cart_count, since it's
    # called on almost every cart action. Any line that doesn't look right
    # is just skipped instead of crashing the whole request.
    cart = get_cart(request)
    total = 0
    for item in cart.values():
        if not isinstance(item, dict):
            continue
        try:
            total += int(item.get('qty', 0) or 0)
        except (TypeError, ValueError):
            continue
    return total

def cart_total(request):
    cart = get_cart(request)
    return sum(line_subtotal(item) for item in cart.values())

def is_ajax(request):
    return request.headers.get('x-requested-with') == 'XMLHttpRequest'

def add_to_cart(request, product_id):
    from .models import Product
    product = get_object_or_404(Product, id=product_id)
    cart = get_cart(request)

    line_key, weight_str, qty_to_add, fixed_total = resolve_cart_line(request, product)
    mode = product.pricing_mode
    price_to_store = str(fixed_total) if fixed_total is not None else str(product.price)

    if line_key in cart:
        # Fixed-weight items are a single unique animal/listing — clicking
        # "Add to Cart" again on the same one shouldn't stack quantity.
        if mode != 'fixed_weight':
            cart[line_key]['qty'] = int(cart[line_key].get('qty', 0) or 0) + qty_to_add
    else:
        cart[line_key] = {
            'product_id': product_id,
            'name': product.name,
            'price': price_to_store,
            'price_unit': '(fixed price)' if mode == 'fixed_weight' else product.price_unit,
            'image': product.main_image.url if product.main_image else '',
            'slug': product.slug,
            'qty': 1 if mode == 'fixed_weight' else qty_to_add,
            'weight': weight_str,
            'pricing_mode': mode,
            'weight_step': str(product.weight_step) if mode == 'variable_weight' else None,
            'weight_unit_label': product.weight_unit_label if mode == 'variable_weight' else None,
        }
    save_cart(request, cart)
    if is_ajax(request):
        return JsonResponse({
            'status': 'ok',
            'count': cart_count(request),
            'line_key': line_key,
            'weight': weight_str,
            'qty': cart[line_key]['qty'],
            'subtotal': line_subtotal(cart[line_key]),
            'pricing_mode': mode,
            'weight_step': str(product.weight_step) if mode == 'variable_weight' else None,
            'weight_unit_label': product.weight_unit_label if mode == 'variable_weight' else None,
        })
    return redirect(request.META.get('HTTP_REFERER', '/shop/'))

def remove_from_cart(request, key):
    line_key = key
    cart = get_cart(request)
    if line_key in cart:
        del cart[line_key]
        save_cart(request, cart)
    if is_ajax(request):
        return JsonResponse({
            'status': 'ok',
            'removed_id': line_key,
            'cart_total': cart_total(request),
            'cart_count': cart_count(request),
        })
    return redirect('cart')

def update_cart(request, key):
    line_key = key
    cart = get_cart(request)
    try:
        qty = int(request.POST.get('qty', 1))
    except (TypeError, ValueError):
        qty = 1  # malformed input (e.g. stray "NaN") should never 500 the request

    existing = cart.get(line_key)
    is_locked = isinstance(existing, dict) and existing.get('pricing_mode') == 'fixed_weight'

    removed = False
    subtotal = 0
    if qty <= 0:
        if line_key in cart:
            del cart[line_key]
        removed = True
    elif is_locked:
        # Fixed-weight lines (goat/chicken) can't have their quantity changed —
        # only removed entirely (handled by the qty<=0 branch above).
        subtotal = line_subtotal(existing)
        qty = existing.get('qty', 1)
    else:
        if line_key in cart:
            cart[line_key]['qty'] = qty
            subtotal = line_subtotal(cart[line_key])
    save_cart(request, cart)
    if is_ajax(request):
        return JsonResponse({
            'status': 'ok',
            'removed': removed,
            'qty': qty if not removed else 0,
            'subtotal': subtotal,
            'cart_total': cart_total(request),
            'cart_count': cart_count(request),
        })
    return redirect('cart')

def update_cart_weight(request, line_key):
    """Change the weight on an existing weighted cart line. Because weight
    is part of the line's identity, this re-keys the line — and if another
    line already exists at that new weight, merges quantities into it
    rather than creating a duplicate row."""
    cart = get_cart(request)
    if line_key not in cart:
        if is_ajax(request):
            return JsonResponse({'status': 'error', 'message': 'Line not found'}, status=404)
        return redirect('cart')

    item = cart[line_key]
    if isinstance(item, dict) and item.get('pricing_mode') == 'fixed_weight':
        # Weight is locked for these — nothing to do.
        if is_ajax(request):
            return JsonResponse({'status': 'error', 'message': 'Weight is fixed for this item'}, status=400)
        return redirect('cart')

    from .models import Product
    product_id, _old_weight = parse_line_key(line_key)
    step = item.get('weight_step') if isinstance(item, dict) else None
    if not step:
        try:
            step = Product.objects.get(id=product_id).weight_step
        except Product.DoesNotExist:
            step = '0.50'
    new_weight_str = format_weight(request.POST.get('weight', step), step)
    new_line_key = make_line_key(product_id, new_weight_str)
    merged = False

    if new_line_key != line_key:
        if new_line_key in cart:
            cart[new_line_key]['qty'] += item['qty']
            del cart[line_key]
            merged = True
        else:
            item['weight'] = new_weight_str
            cart[new_line_key] = item
            del cart[line_key]

    save_cart(request, cart)
    final_item = cart[new_line_key]
    if is_ajax(request):
        return JsonResponse({
            'status': 'ok',
            'old_line_key': line_key,
            'new_line_key': new_line_key,
            'merged': merged,
            'qty': final_item['qty'],
            'weight': final_item['weight'],
            'subtotal': line_subtotal(final_item),
            'cart_total': cart_total(request),
            'cart_count': cart_count(request),
        })
    return redirect('cart')


# ─── SAVE FOR LATER (session-based, no login needed) ───────────

def get_saved(request):
    return request.session.get('saved', {})

def save_saved(request, saved):
    request.session['saved'] = saved
    request.session.modified = True

def toggle_save_for_later(request, key):
    """`key` is either a plain product id (saving straight from a shop/product
    card, no weight involved) or a composite line_key (saving a weighted
    line from the cart, e.g. '14_1.50') — parse_line_key() handles both."""
    from .models import Product
    product_id, weight_str = parse_line_key(key)
    product = get_object_or_404(Product, id=product_id)
    cart = get_cart(request)
    saved = get_saved(request)

    if key in saved:
        del saved[key]
        is_saved = False
    else:
        existing_cart_item = cart.get(key) if isinstance(cart.get(key), dict) else {}
        if key in cart:
            del cart[key]
            save_cart(request, cart)
        saved[key] = {
            'product_id': product_id,
            'name': product.name,
            'price': str(product.price),
            'price_unit': product.price_unit,
            'image': product.main_image.url if product.main_image else '',
            'slug': product.slug,
            'weight': weight_str,
            'pricing_mode': existing_cart_item.get('pricing_mode', product.pricing_mode),
            'weight_step': existing_cart_item.get('weight_step'),
            'weight_unit_label': existing_cart_item.get('weight_unit_label'),
        }
        is_saved = True

    save_saved(request, saved)

    if is_ajax(request):
        return JsonResponse({
            'status': 'ok',
            'saved': is_saved,
            'removed_from_cart': key not in cart,
            'cart_total': cart_total(request),
            'cart_count': cart_count(request),
            'item': saved.get(key),
        })
    return redirect(request.META.get('HTTP_REFERER', '/shop/'))

def move_to_cart(request, key):
    saved = get_saved(request)
    cart = get_cart(request)
    item_data = None
    if key in saved:
        item = saved.pop(key)
        if key in cart and isinstance(cart[key], dict):
            cart[key]['qty'] = int(cart[key].get('qty', 0) or 0) + 1
        else:
            cart[key] = {**item, 'qty': 1}
        item_data = cart[key]
        save_saved(request, saved)
        save_cart(request, cart)
    if is_ajax(request):
        return JsonResponse({
            'status': 'ok',
            'moved_id': key,
            'item': item_data,
            'cart_total': cart_total(request),
            'cart_count': cart_count(request),
        })
    return redirect('cart')

def remove_saved(request, key):
    saved = get_saved(request)
    if key in saved:
        del saved[key]
        save_saved(request, saved)
    if is_ajax(request):
        return JsonResponse({'status': 'ok', 'removed_id': key})
    return redirect('cart')


def cart_view(request):
    from .models import Product
    import random
    cart = get_cart(request)
    saved = get_saved(request)

    items = []
    total = 0
    cleaned_a_bad_line = False
    for line_key, item in list(cart.items()):
        if not isinstance(item, dict):
            # Stale/corrupted line from an older cart schema — drop it
            # instead of crashing the whole cart page.
            del cart[line_key]
            cleaned_a_bad_line = True
            continue
        subtotal = line_subtotal(item)
        total += subtotal
        items.append({**item, 'id': line_key, 'subtotal': subtotal})
    if cleaned_a_bad_line:
        save_cart(request, cart)

    saved_items = [{**item, 'id': key} for key, item in saved.items()]

    # Recommendations — exclude products already in cart/saved (weighted
    # lines have a composite key like "14_1.50", so pull the real
    # product id back out via parse_line_key before excluding).
    excluded_ids = set()
    for key in list(cart.keys()) + list(saved.keys()):
        pid, _weight = parse_line_key(key)
        if pid.isdigit():
            excluded_ids.add(int(pid))
    all_products = list(Product.objects.filter(is_available=True).exclude(id__in=excluded_ids))
    recommended_products = random.sample(all_products, min(6, len(all_products)))

    wishlist_items = []
    if request.user.is_authenticated:
        wishlist_items = Wishlist.objects.filter(user=request.user).select_related('product')

    return render(request, 'cart.html', {
        'items': items,
        'total': total,
        'count': cart_count(request),
        'saved_items': saved_items,
        'recommended_products': recommended_products,
        'wishlist_items': wishlist_items,
    })


def checkout(request):
    from .models import Coupon, Product
    from decimal import Decimal
    cart = get_cart(request)
    if not cart:
        return redirect('shop')

    items = []
    subtotal = 0
    has_offer_items = False
    cleaned_a_bad_line = False
    for line_key, item in list(cart.items()):
        if not isinstance(item, dict):
            del cart[line_key]
            cleaned_a_bad_line = True
            continue
        item_subtotal = line_subtotal(item)
        subtotal += item_subtotal
        if item.get('is_offer'):
            has_offer_items = True
        items.append({**item, 'id': line_key, 'subtotal': item_subtotal})
    if cleaned_a_bad_line:
        save_cart(request, cart)

    # ── Coupon handling ──────────────────────────────────────
    # Coupon can arrive via ?coupon=CODE (Apply button) or the hidden
    # field carried through on the POST (Place Order button).
    coupon_code = (request.GET.get('coupon') or request.POST.get('coupon_code') or '').strip().upper()
    coupon_applied = False
    coupon_error = None
    discount_amount = Decimal('0')
    coupon_obj = None

    if coupon_code:
        try:
            coupon_obj = Coupon.objects.get(code=coupon_code)
            if not coupon_obj.is_live():
                coupon_error = "This coupon has expired or is no longer active."
            elif Decimal(str(subtotal)) < coupon_obj.min_order_amount:
                coupon_error = f"Minimum order of Rs. {coupon_obj.min_order_amount:.0f} required for this coupon."
            else:
                discount_amount = coupon_obj.calculate_discount(subtotal)
                coupon_applied = True
        except Coupon.DoesNotExist:
            coupon_error = "Invalid coupon code."

    total = float(Decimal(str(subtotal)) - discount_amount)

    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        email = request.POST.get('email', '').strip()
        phone = request.POST.get('phone', '').strip()
        address = request.POST.get('address', '').strip()
        message = request.POST.get('message', '').strip()

        product_list = ', '.join([
            f"{i['name']} ({i['weight']}kg) x{i['qty']}" if i.get('weight') else f"{i['name']} x{i['qty']}"
            for i in items
        ])
        if coupon_applied:
            product_list += f" | Coupon: {coupon_code} (-Rs.{discount_amount:.0f})"

        # Structured snapshot for the "Reorder" button — just enough to
        # re-add the same lines later (product + weight + qty), not the
        # historical price, since a reorder should use CURRENT pricing.
        cart_snapshot = [
            {
                'product_id': i.get('product_id'),
                'weight': i.get('weight'),
                'qty': i.get('qty'),
                'variant_id': extract_variant_id(i.get('id', '')),
            }
            for i in items if i.get('product_id')
        ]

        order = ProductOrder.objects.create(
            name=name,
            email=email,
            phone=phone,
            address=address,
            product_interest=product_list,
            message=message,
            cart_snapshot=cart_snapshot,
        )

        create_order_inventory_movements(order, 'sale')

        if coupon_applied and coupon_obj:
            coupon_obj.used_count += 1
            coupon_obj.save(update_fields=['used_count'])

        try:
            send_order_received_email(order)
        except Exception:
            pass

        request.session['cart'] = {}
        request.session.modified = True

        return render(request, 'success.html', {'order': order})

    initial = {}
    if request.user.is_authenticated:
        initial['name'] = request.user.get_full_name()
        initial['email'] = request.user.email

    # Simple recommendations: products not already in the cart
    cart_ids = set()
    for key in cart.keys():
        pid, _weight = parse_line_key(key)
        if pid.isdigit():
            cart_ids.add(int(pid))
    recommended_products = Product.objects.exclude(id__in=cart_ids).order_by('?')[:8]

    return render(request, 'checkout.html', {
        'items': items,
        'subtotal': subtotal,
        'total': total,
        'initial': initial,
        'coupon_code': coupon_code,
        'coupon_applied': coupon_applied,
        'coupon_error': coupon_error,
        'discount_amount': discount_amount,
        'has_offer_items': has_offer_items,
        'recommended_products': recommended_products,
    })


# ─── SHOP PAGE ───────────────────────────────────────────────

def shop(request):
    from .models import Product, Category, Offer

    def get_all_ids(cat):
        ids = [cat.id]
        for sub in cat.subcategories.all():
            ids.append(sub.id)
            for subsub in sub.subcategories.all():
                ids.append(subsub.id)
        return ids

    def get_count(cat):
        return Product.objects.filter(category__id__in=get_all_ids(cat)).count()

    cat_id = request.GET.get('cat')
    selected_category = None
    products = Product.objects.all().order_by('name')

    if cat_id:
        try:
            selected_category = Category.objects.get(id=cat_id)
            products = products.filter(category__id__in=get_all_ids(selected_category))
        except Category.DoesNotExist:
            pass

    main_categories = Category.objects.filter(parent=None).prefetch_related(
        'subcategories__subcategories'
    )

    cat_tree = []
    for cat in main_categories:
        subs = []
        for sub in cat.subcategories.all():
            subsubs = [{'obj': ss, 'count': ss.products.count()}
                       for ss in sub.subcategories.all()]
            subs.append({'obj': sub, 'count': get_count(sub), 'children': subsubs})
        cat_tree.append({'obj': cat, 'count': get_count(cat), 'children': subs})

    # Underlying product ids only (composite weight keys like "14_1.50"
    # would never match `product.id in saved_ids` on the shop template).
    saved_ids = []
    for key in get_saved(request).keys():
        pid, _weight = parse_line_key(key)
        if pid.isdigit():
            saved_ids.append(int(pid))

    # Check for active offers
    from django.utils import timezone
    now = timezone.now()
    has_active_offers = Offer.objects.filter(
        is_active=True, start_date__lte=now, end_date__gte=now
    ).exists()

    # For fixed-weight products (goat/chicken), each Product can now have
    # several weight rows (ProductVariant) instead of needing a separate
    # Product per size. Attach a price range computed from those rows so the
    # shop card can show "Rs. 9,750–13,000 · 2 sizes available".
    products = products.prefetch_related('variants')
    for p in products:
        p.variant_count = 0
        if p.pricing_mode == 'fixed_weight':
            avail = p.available_variants()
            if avail:
                prices = [v.total_price() for v in avail]
                p.variant_count = len(avail)
                p.price_range_low = min(prices)
                p.price_range_high = max(prices)

    wishlist_ids = []
    if request.user.is_authenticated:
        wishlist_ids = list(Wishlist.objects.filter(user=request.user).values_list('product_id', flat=True))

    return render(request, 'shop.html', {
        'products': products,
        'cat_tree': cat_tree,
        'selected_category': selected_category,
        'total_count': Product.objects.count(),
        'saved_ids': saved_ids,
        'wishlist_ids': wishlist_ids,
        'has_active_offers': has_active_offers,
    })


@login_required(login_url='login')
def offers(request):
    from .models import Offer, get_live_coupons
    from django.utils import timezone
    now = timezone.now()

    live_offers = Offer.objects.filter(
        is_active=True, start_date__lte=now, end_date__gte=now
    ).exclude(discount_type='combo')

    combo_offers = Offer.objects.filter(
        is_active=True, start_date__lte=now, end_date__gte=now, discount_type='combo'
    )

    # Festival coupons currently live (e.g. Dashain, Tihar)
    live_coupons = get_live_coupons()

    discount_offers = []
    for offer in live_offers:
        for product in offer.get_products():
            if not product.price or product.price <= 0:
                continue
            offer_price = offer.discounted_price(product.price)
            if offer.discount_type == 'percent':
                discount_percent = int(offer.discount_value)
            else:
                # fixed amount -> compute equivalent percent for the ribbon
                discount_percent = int(round((float(offer.discount_value) / float(product.price)) * 100))
            discount_offers.append({
                'id': product.id,
                'name': product.name,
                'slug': product.slug,
                'main_image': product.main_image,
                'original_price': product.price,
                'offer_price': offer_price,
                'price_unit': product.price_unit,
                'discount_percent': discount_percent,
                'is_available': product.is_available,
            })

    bundle_deals = []
    for offer in combo_offers:
        bundle_items = list(offer.bundle_items.select_related('product').all())
        if not bundle_items:
            continue

        original_total = offer.get_bundle_natural_total()
        bundle_price = offer.combo_price or original_total
        savings_percent = 0
        if original_total > 0:
            savings_percent = int(round((1 - (float(bundle_price) / float(original_total))) * 100))

        items = []
        for bi in bundle_items:
            unit_label = (bi.product.price_unit or '').replace('per ', '').strip() or 'unit'
            qty_str = f"{float(bi.quantity):g} {unit_label}"
            items.append({'name': bi.product.name, 'qty': qty_str})

        whatsapp_msg = f"Hello Angan Baari! I want to order the {offer.title} Bundle."

        bundle_deals.append({
            'title': offer.title,
            'subtitle': offer.description,
            'badge_label': '🎁 Combo Deal',
            'items': items,
            'bundle_price': bundle_price,
            'original_price': original_total,
            'savings_percent': savings_percent,
            'whatsapp_message': whatsapp_msg,
        })

    return render(request, 'offers.html', {
        'discount_offers': discount_offers,
        'bundle_deals': bundle_deals,
        'live_coupons': live_coupons,
    })

def add_to_cart_offer(request, product_id):
    """Add to cart with a discounted price from offers page. Offer pricing is
    a login-only perk — regular (non-offer) shopping stays open to everyone."""
    if not request.user.is_authenticated:
        return redirect(f"{reverse('login')}?next={reverse('offers')}")

    from .models import Product
    product = get_object_or_404(Product, id=product_id)
    cart = get_cart(request)

    line_key, weight_str, qty_to_add, fixed_total = resolve_cart_line(request, product)
    mode = product.pricing_mode
    base_price = str(fixed_total) if fixed_total is not None else str(product.price)

    offer_price = request.POST.get('offer_price', None)
    price_to_use = offer_price if offer_price else base_price

    if line_key in cart:
        if mode != 'fixed_weight':
            cart[line_key]['qty'] = int(cart[line_key].get('qty', 0) or 0) + qty_to_add
        try:
            if offer_price and float(offer_price) < float(cart[line_key].get('price', 0) or 0):
                cart[line_key]['price'] = price_to_use
                cart[line_key]['original_price'] = base_price
                cart[line_key]['is_offer'] = True
        except (TypeError, ValueError):
            pass
    else:
        cart[line_key] = {
            'product_id': product_id,
            'name': product.name,
            'price': price_to_use,
            'original_price': base_price,
            'price_unit': '(fixed price)' if mode == 'fixed_weight' else product.price_unit,
            'image': product.main_image.url if product.main_image else '',
            'slug': product.slug,
            'qty': 1 if mode == 'fixed_weight' else qty_to_add,
            'weight': weight_str,
            'is_offer': bool(offer_price),
            'pricing_mode': mode,
            'weight_step': str(product.weight_step) if mode == 'variable_weight' else None,
            'weight_unit_label': product.weight_unit_label if mode == 'variable_weight' else None,
        }
    save_cart(request, cart)
    return redirect(request.META.get('HTTP_REFERER', '/shop/'))