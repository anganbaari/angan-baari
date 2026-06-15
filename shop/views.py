from django.shortcuts import render, redirect
from .models import NewsletterSubscriber, ContactMessage, ProductOrder
from .emails import send_order_received_email
from django.core.mail import send_mail
from django.conf import settings
from django.http import JsonResponse

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
            # Email to admin
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

Reply to: {email}
                ''',
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[settings.ADMIN_EMAIL],
                fail_silently=True,
            )
            # Email to customer
            send_mail(
                subject='✅ Message Received — Angan Baari | आँगन बारी',
                message=f'''
नमस्ते {name}! 🌿

Thank you for contacting Angan Baari!

We have received your message and will get back to you within 12-36 hours.

━━━━━━━━━━━━━━━━━━━━━━
📩 YOUR MESSAGE
━━━━━━━━━━━━━━━━━━━━━━
Subject : {subject}
Message : {message}
━━━━━━━━━━━━━━━━━━━━━━

📱 For faster response WhatsApp us:
https://wa.me/9779821025084

With love,
Angan Baari Team 🌱
Bhulka Danda, Rupandehi, Nepal
                ''',
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[email],
                fail_silently=True,
            )
            return JsonResponse({'status': 'success'})
        return JsonResponse({'status': 'error', 'message': 'Please fill all required fields.'})
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
        send_order_received_email(order)
        return render(request, 'success.html', {'order': order})
    return redirect('home')

def error_404(request, exception):
    return render(request, '404.html', status=404)

def error_500(request):
    return render(request, '500.html', status=500)

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

                    ━━━━━━━━━━━━━━━━━━━━━━
                    📱 Order via WhatsApp:
                    https://wa.me/9779821025084
                    ━━━━━━━━━━━━━━━━━━━━━━
                    
                    With love,
                    Angan Baari Team 🌱
                    Bhulka Danda, Rupandehi, Nepal
                    ''',
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[email],
                    fail_silently=True,
                )
        return redirect('/?subscribed=1#newsletter')
    return redirect('home')