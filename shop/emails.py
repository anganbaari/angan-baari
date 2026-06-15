from django.core.mail import send_mail
from django.conf import settings

def send_order_received_email(order):
    # Email to customer
    send_mail(
        subject='✅ Order Received — Angan Baari | आँगन बारी',
        message=f'''
नमस्ते {order.name}! 🌿

Thank you for ordering from Angan Baari (आँगन बारी)!

Your order has been received and is currently PENDING confirmation.
We will confirm your order shortly.

━━━━━━━━━━━━━━━━━━━━━━
📦 ORDER DETAILS
━━━━━━━━━━━━━━━━━━━━━━
Order No  : {order.order_number}
Product   : {order.product_interest}
Address   : {order.address}
Message   : {order.message or 'None'}
━━━━━━━━━━━━━━━━━━━━━━

📱 Questions? WhatsApp us:
https://wa.me/9779821025084

With love,
Angan Baari Team 🌱
Bhulka Danda, Rupandehi, Nepal
        ''',
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[order.email],
        fail_silently=False,
    )

    # Email to admin
    send_mail(
        subject=f'🆕 New Order from {order.name} — Angan Baari',
        message=f'''
New order received!

━━━━━━━━━━━━━━━━━━━━━━
📦 ORDER DETAILS
━━━━━━━━━━━━━━━━━━━━━━
Order No  : {order.order_number}
Customer  : {order.name}
Email     : {order.email}
Phone     : {order.phone}
Product   : {order.product_interest}
Address   : {order.address}
Message   : {order.message or 'None'}
Ordered at: {order.ordered_at}
━━━━━━━━━━━━━━━━━━━━━━

Go to admin panel to confirm:
http://127.0.0.1:8000/admin/shop/productorder/
        ''',
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[settings.ADMIN_EMAIL],
        fail_silently=False,
    )


def send_order_confirmed_email(order):
    # Email to customer
    send_mail(
        subject='🎉 Order Confirmed — Angan Baari | आँगन बारी',
        message=f'''
नमस्ते {order.name}! 🌿

Great news! Your order has been CONFIRMED and is on the way!

━━━━━━━━━━━━━━━━━━━━━━
📦 ORDER DETAILS
━━━━━━━━━━━━━━━━━━━━━━
Order No  : {order.order_number}
Product   : {order.product_interest}
Address   : {order.address}
Status    : ✅ Confirmed — On the way!
━━━━━━━━━━━━━━━━━━━━━━

Expected delivery: 2-3.5 hours after confirmation.

📱 Track your order via WhatsApp:
https://wa.me/9779821025084

With love,
Angan Baari Team 🌱
Bhulka Danda, Rupandehi, Nepal
        ''',
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[order.email],
        fail_silently=False,
    )


def send_order_delivered_email(order):
    # Email to customer
    send_mail(
        subject='🏡 Order Delivered — Angan Baari | आँगन बारी',
        message=f'''
नमस्ते {order.name}! 🌿

Your order has been DELIVERED! 

━━━━━━━━━━━━━━━━━━━━━━
📦 ORDER DETAILS
━━━━━━━━━━━━━━━━━━━━━━
Order No  : {order.order_number}
Product   : {order.product_interest}
Address   : {order.address}
Status    : 🏡 Delivered!
━━━━━━━━━━━━━━━━━━━━━━

Thank you for choosing Angan Baari!
We hope you enjoy your fresh organic products. 🌱

Please share your feedback via WhatsApp:
https://wa.me/9779821025084

With love,
Angan Baari Team 🌱
Bhulka Danda, Rupandehi, Nepal
        ''',
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[order.email],
        fail_silently=False,
    )

    # Email to admin
    send_mail(
        subject=f'✅ Order Delivered to {order.name} — Angan Baari',
        message=f'''
Order has been delivered!

━━━━━━━━━━━━━━━━━━━━━━
📦 DELIVERY DETAILS
━━━━━━━━━━━━━━━━━━━━━━
Order No  : {order.order_number}
Customer  : {order.name}
Email     : {order.email}
Phone     : {order.phone}
Product   : {order.product_interest}
Address   : {order.address}
━━━━━━━━━━━━━━━━━━━━━━

This order is now complete! ✅
        ''',
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[settings.ADMIN_EMAIL],
        fail_silently=False,
    )