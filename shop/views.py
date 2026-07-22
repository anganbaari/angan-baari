from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse
from django.core.mail import send_mail
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.urls import reverse
from django.contrib import messages
from .models import NewsletterSubscriber, ContactMessage, ProductOrder, Review, Wishlist
from .emails import (
    send_order_received_email,
    send_order_cancelled_email,
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
            send_mail(
                subject=f'📩 New Message from {name} — Angan Baari',
                message=f'''
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
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[settings.ADMIN_EMAIL],
                fail_silently=True,
            )
            send_mail(
                subject='✅ Message Received — Angan Baari',
                message=f'''
नमस्ते {name}! 🌿

Thank you for contacting Angan Baari!
We will get back to you within 12-36 hours.

Subject : {subject}
Message : {message}

📱 WhatsApp: https://wa.me/9779821025084

Angan Baari Team 🌱
                ''',
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[email],
                fail_silently=True,
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
                'image': product.main_image,
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
                send_mail(
                    subject='🌿 Welcome to Angan Baari Newsletter!',
                    message=f'''
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
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[email],
                    fail_silently=True,
                )
        return redirect('/?subscribed=1#newsletter')
    return redirect('home')

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
        Wishlist.objects.create(user=request.user, product=product)
        is_saved = True

    if is_ajax(request):
        return JsonResponse({'status': 'ok', 'is_saved': is_saved})
    return redirect(request.META.get('HTTP_REFERER', '/shop/'))


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
            'image': product.main_image,
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
            'image': product.main_image,
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
    from .models import Offer, Coupon
    from django.utils import timezone
    now = timezone.now()

    live_offers = Offer.objects.filter(
        is_active=True, start_date__lte=now, end_date__gte=now
    ).exclude(discount_type='combo')

    combo_offers = Offer.objects.filter(
        is_active=True, start_date__lte=now, end_date__gte=now, discount_type='combo'
    )

    # Festival coupons currently live (e.g. Dashain, Tihar)
    candidate_coupons = Coupon.objects.filter(
        is_active=True, start_date__lte=now, end_date__gte=now
    )
    live_coupons = [c for c in candidate_coupons if c.is_live()]

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
            'image': product.main_image,
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