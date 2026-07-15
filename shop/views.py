from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse
from django.core.mail import send_mail
from django.conf import settings
from .models import NewsletterSubscriber, ContactMessage, ProductOrder, Review
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
    return render(request, 'product_detail.html', {
        'product': product,
        'related_products': related_products,
        'reviews': reviews,
        'review_count': review_count,
        'avg_rating': avg_rating,
        'ratings': Review.RATING_CHOICES,
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


# ─── CART SYSTEM ───────────────────────────────────────────────

def get_cart(request):
    return request.session.get('cart', {})

def save_cart(request, cart):
    request.session['cart'] = cart
    request.session.modified = True


# ─── WEIGHT HELPERS ─────────────────────────────────────────────
# Products whose price_unit mentions "kg" are sold by weight: Product.price
# is treated as the price PER KG, and the customer picks how many kg they
# want (500g, 1kg, 1.5kg, 2.5kg, or a custom amount). Everything else
# (price_unit like "per piece", "per bunch", etc.) keeps the old
# quantity-only behaviour untouched.

def is_weighted_product(product):
    return 'kg' in (product.price_unit or '').lower()

def format_weight(raw):
    """Normalize any incoming weight value to a 2-decimal string, e.g.
    '1', '1.5', '2.50' -> '1.00', '1.50', '2.50'. Falls back to 1.00 kg
    on bad input so a cart line is never silently dropped."""
    from decimal import Decimal, InvalidOperation
    try:
        weight = Decimal(str(raw))
        if weight <= 0:
            weight = Decimal('1')
        return f"{weight:.2f}"
    except (InvalidOperation, TypeError, ValueError):
        return '1.00'

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
        price = float(item['price'])
        qty = item['qty']
        weight = float(item['weight']) if item.get('weight') else 1
        return price * qty * weight
    except (KeyError, TypeError, ValueError):
        return 0


def cart_count(request):
    cart = get_cart(request)
    return sum(item['qty'] for item in cart.values())

def cart_total(request):
    cart = get_cart(request)
    return sum(line_subtotal(item) for item in cart.values())

def is_ajax(request):
    return request.headers.get('x-requested-with') == 'XMLHttpRequest'

def add_to_cart(request, product_id):
    from .models import Product
    product = get_object_or_404(Product, id=product_id)
    cart = get_cart(request)

    weighted = is_weighted_product(product)
    weight_str = format_weight(request.POST.get('weight', '1')) if weighted else None
    line_key = make_line_key(product_id, weight_str)

    if line_key in cart:
        cart[line_key]['qty'] += 1
    else:
        cart[line_key] = {
            'product_id': product_id,
            'name': product.name,
            'price': str(product.price),
            'price_unit': product.price_unit,
            'image': product.main_image,
            'slug': product.slug,
            'qty': 1,
            'weight': weight_str,
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
        })
    return redirect(request.META.get('HTTP_REFERER', '/shop/'))

def remove_from_cart(request, line_key):
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

def update_cart(request, line_key):
    cart = get_cart(request)
    qty = int(request.POST.get('qty', 1))
    removed = False
    subtotal = 0
    if qty <= 0:
        if line_key in cart:
            del cart[line_key]
        removed = True
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
    product_id, _old_weight = parse_line_key(line_key)
    new_weight_str = format_weight(request.POST.get('weight', '1'))
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
        if key in cart:
            cart[key]['qty'] += 1
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
    for line_key, item in cart.items():
        subtotal = line_subtotal(item)
        total += subtotal
        items.append({**item, 'id': line_key, 'subtotal': subtotal})

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

    return render(request, 'cart.html', {
        'items': items,
        'total': total,
        'count': cart_count(request),
        'saved_items': saved_items,
        'recommended_products': recommended_products,
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
    for line_key, item in cart.items():
        item_subtotal = line_subtotal(item)
        subtotal += item_subtotal
        if item.get('is_offer'):
            has_offer_items = True
        items.append({**item, 'id': line_key, 'subtotal': item_subtotal})

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

        order = ProductOrder.objects.create(
            name=name,
            email=email,
            phone=phone,
            address=address,
            product_interest=product_list,
            message=message,
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

    return render(request, 'shop.html', {
        'products': products,
        'cat_tree': cat_tree,
        'selected_category': selected_category,
        'total_count': Product.objects.count(),
        'saved_ids': saved_ids,
        'has_active_offers': has_active_offers,
    })


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
    """Add to cart with a discounted price from offers page."""
    from .models import Product
    product = get_object_or_404(Product, id=product_id)
    cart = get_cart(request)

    weighted = is_weighted_product(product)
    weight_str = format_weight(request.POST.get('weight', '1')) if weighted else None
    line_key = make_line_key(product_id, weight_str)

    offer_price = request.POST.get('offer_price', None)
    price_to_use = offer_price if offer_price else str(product.price)

    if line_key in cart:
        cart[line_key]['qty'] += 1
        if offer_price and float(offer_price) < float(cart[line_key]['price']):
            cart[line_key]['price'] = price_to_use
            cart[line_key]['original_price'] = str(product.price)
            cart[line_key]['is_offer'] = True
    else:
        cart[line_key] = {
            'product_id': product_id,
            'name': product.name,
            'price': price_to_use,
            'original_price': str(product.price),
            'price_unit': product.price_unit,
            'image': product.main_image,
            'slug': product.slug,
            'qty': 1,
            'weight': weight_str,
            'is_offer': bool(offer_price),
        }
    save_cart(request, cart)
    return redirect(request.META.get('HTTP_REFERER', '/shop/'))