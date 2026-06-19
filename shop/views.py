from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse
from django.core.mail import send_mail
from django.conf import settings
from .models import NewsletterSubscriber, ContactMessage, ProductOrder
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
    return render(request, 'product_detail.html', {
        'product': product,
        'related_products': related_products,
    })