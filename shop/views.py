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

def shop(request):
    from .models import Product, Category

    def get_all_ids(cat):
        """Get category ID + all descendant IDs"""
        ids = [cat.id]
        for sub in cat.subcategories.all():
            ids.append(sub.id)
            for subsub in sub.subcategories.all():
                ids.append(subsub.id)
        return ids

    def get_count(cat):
        return Product.objects.filter(category__id__in=get_all_ids(cat)).count()

    # Get selected category
    cat_id = request.GET.get('cat')
    selected_category = None
    products = Product.objects.all().order_by('name')

    if cat_id:
        try:
            selected_category = Category.objects.get(id=cat_id)
            products = products.filter(category__id__in=get_all_ids(selected_category))
        except Category.DoesNotExist:
            pass

    # Build 3-level tree
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

    return render(request, 'shop.html', {
        'products': products,
        'cat_tree': cat_tree,
        'selected_category': selected_category,
        'total_count': Product.objects.count(),
    })

# ─── CART SYSTEM ───────────────────────────────────────────────

def get_cart(request):
    return request.session.get('cart', {})

def save_cart(request, cart):
    request.session['cart'] = cart
    request.session.modified = True

def cart_count(request):
    cart = get_cart(request)
    return sum(item['qty'] for item in cart.values())

def add_to_cart(request, product_id):
    from .models import Product
    product = get_object_or_404(Product, id=product_id)
    cart = get_cart(request)
    pid = str(product_id)
    if pid in cart:
        cart[pid]['qty'] += 1
    else:
        cart[pid] = {
            'name': product.name,
            'price': str(product.price),
            'price_unit': product.price_unit,
            'image': product.main_image,
            'slug': product.slug,
            'qty': 1,
        }
    save_cart(request, cart)
    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return JsonResponse({'status': 'ok', 'count': cart_count(request)})
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

def cart_view(request):
    cart = get_cart(request)
    items = []
    total = 0
    for pid, item in cart.items():
        try:
            subtotal = float(item['price']) * item['qty']
        except:
            subtotal = 0
        total += subtotal
        items.append({**item, 'id': pid, 'subtotal': subtotal})
    return render(request, 'cart.html', {
        'items': items,
        'total': total,
        'count': cart_count(request),
    })

def checkout(request):
    cart = get_cart(request)
    if not cart:
        return redirect('shop')

    items = []
    total = 0
    for pid, item in cart.items():
        try:
            subtotal = float(item['price']) * item['qty']
        except:
            subtotal = 0
        total += subtotal
        items.append({**item, 'id': pid, 'subtotal': subtotal})

    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        email = request.POST.get('email', '').strip()
        phone = request.POST.get('phone', '').strip()
        address = request.POST.get('address', '').strip()
        message = request.POST.get('message', '').strip()

        # Build product interest string from cart
        product_list = ', '.join([f"{i['name']} x{i['qty']}" for i in items])

        order = ProductOrder.objects.create(
            name=name,
            email=email,
            phone=phone,
            address=address,
            product_interest=product_list,
            message=message,
        )
        try:
            send_order_received_email(order)
        except Exception:
            pass

        # Clear cart after order
        request.session['cart'] = {}
        request.session.modified = True

        return render(request, 'success.html', {'order': order})

    # Pre-fill form if logged in
    initial = {}
    if request.user.is_authenticated:
        initial['name'] = request.user.get_full_name()
        initial['email'] = request.user.email

    return render(request, 'checkout.html', {
        'items': items,
        'total': total,
        'initial': initial,
    })