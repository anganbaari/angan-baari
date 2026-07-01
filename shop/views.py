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
    return render(request, 'index.html')

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

def cart_count(request):
    cart = get_cart(request)
    return sum(item['qty'] for item in cart.values())

# Add this view to shop/views.py (after add_to_cart)

def add_to_cart_offer(request, product_id):
    """Add to cart with a discounted price from offers page."""
    from .models import Product
    product = get_object_or_404(Product, id=product_id)
    cart = get_cart(request)
    pid = str(product_id)
    
    # Use discounted price if provided, otherwise original
    offer_price = request.POST.get('offer_price', None)
    price_to_use = offer_price if offer_price else str(product.price)
    
    if pid in cart:
        cart[pid]['qty'] += 1
        # Update to discounted price if it's lower
        if offer_price and float(offer_price) < float(cart[pid]['price']):
            cart[pid]['price'] = price_to_use
            cart[pid]['original_price'] = str(product.price)
            cart[pid]['is_offer'] = True
    else:
        cart[pid] = {
            'name': product.name,
            'price': price_to_use,
            'original_price': str(product.price),
            'price_unit': product.price_unit,
            'image': product.main_image,
            'slug': product.slug,
            'qty': 1,
            'is_offer': bool(offer_price),
        }
    save_cart(request, cart)
    return redirect(request.META.get('HTTP_REFERER', '/shop/'))

def remove_from_cart(request, product_id):
    cart = get_cart(request)
    pid = str(product_id)
    if pid in cart:
        del cart[pid]
        save_cart(request, cart)
    return redirect('cart')

def update_cart(request, product_id):
    cart = get_cart(request)
    pid = str(product_id)
    qty = int(request.POST.get('qty', 1))
    if qty <= 0:
        if pid in cart:
            del cart[pid]
    else:
        if pid in cart:
            cart[pid]['qty'] = qty
    save_cart(request, cart)
    return redirect('cart')


# ─── SAVE FOR LATER (session-based, no login needed) ───────────

def get_saved(request):
    return request.session.get('saved', {})

def save_saved(request, saved):
    request.session['saved'] = saved
    request.session.modified = True

def toggle_save_for_later(request, product_id):
    from .models import Product
    product = get_object_or_404(Product, id=product_id)
    cart = get_cart(request)
    saved = get_saved(request)
    pid = str(product_id)

    if pid in saved:
        del saved[pid]
        is_saved = False
    else:
        if pid in cart:
            del cart[pid]
            save_cart(request, cart)
        saved[pid] = {
            'name': product.name,
            'price': str(product.price),
            'price_unit': product.price_unit,
            'image': product.main_image,
            'slug': product.slug,
        }
        is_saved = True

    save_saved(request, saved)

    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return JsonResponse({'status': 'ok', 'saved': is_saved})
    return redirect(request.META.get('HTTP_REFERER', '/shop/'))

def move_to_cart(request, product_id):
    saved = get_saved(request)
    cart = get_cart(request)
    pid = str(product_id)
    if pid in saved:
        item = saved.pop(pid)
        if pid in cart:
            cart[pid]['qty'] += 1
        else:
            cart[pid] = {**item, 'qty': 1}
        save_saved(request, saved)
        save_cart(request, cart)
    return redirect('cart')

def remove_saved(request, product_id):
    saved = get_saved(request)
    pid = str(product_id)
    if pid in saved:
        del saved[pid]
        save_saved(request, saved)
    return redirect('cart')


def cart_view(request):
    cart = get_cart(request)
    saved = get_saved(request)

    items = []
    total = 0
    for pid, item in cart.items():
        try:
            subtotal = float(item['price']) * item['qty']
        except Exception:
            subtotal = 0
        total += subtotal
        items.append({**item, 'id': pid, 'subtotal': subtotal})

    saved_items = [{**item, 'id': pid} for pid, item in saved.items()]

    return render(request, 'cart.html', {
        'items': items,
        'total': total,
        'count': cart_count(request),
        'saved_items': saved_items,
    })


def checkout(request):
    from .models import Coupon, Product
    from decimal import Decimal
    cart = get_cart(request)
    if not cart:
        return redirect('shop')

    items = []
    subtotal = 0
    for pid, item in cart.items():
        try:
            item_subtotal = float(item['price']) * item['qty']
        except Exception:
            item_subtotal = 0
        subtotal += item_subtotal
        items.append({**item, 'id': pid, 'subtotal': item_subtotal})

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

        product_list = ', '.join([f"{i['name']} x{i['qty']}" for i in items])
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
    cart_ids = [int(pid) for pid in cart.keys()]
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
 
    saved_ids = list(get_saved(request).keys())
 
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
        products = list(offer.get_products())
        if not products:
            continue
        original_total = sum([p.price for p in products if p.price])
        bundle_price = offer.combo_price or original_total
        savings_percent = 0
        if original_total > 0:
            savings_percent = int(round((1 - (float(bundle_price) / float(original_total))) * 100))

        items = [{'name': p.name, 'qty': p.price_unit or '1 unit'} for p in products]
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