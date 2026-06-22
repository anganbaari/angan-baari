import requests
from django.conf import settings


def send_telegram(message):
    """Send a Telegram notification to the admin."""
    try:
        token = settings.TELEGRAM_BOT_TOKEN
        chat_id = settings.TELEGRAM_CHAT_ID
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        requests.post(url, data={
            'chat_id': chat_id,
            'text': message,
            'parse_mode': 'HTML',
        }, timeout=5)
    except Exception:
        pass


def send_resend_email(to, subject, body):
    """Send an email via Resend API."""
    try:
        requests.post(
            'https://api.resend.com/emails',
            headers={
                'Authorization': f'Bearer {settings.RESEND_API_KEY}',
                'Content-Type': 'application/json',
            },
            json={
                'from': 'Angan Baari <orders@anganbaari.com>',
                'to': [to],
                'subject': subject,
                'text': body,
            },
            timeout=10,
        )
    except Exception:
        pass


def send_order_received_email(order):
    cancel_link = f"https://anganbaari.pythonanywhere.com/cancel/{order.cancel_token}/"

    # Telegram notification to admin
    send_telegram(f"""🆕 <b>New Order Received!</b>

━━━━━━━━━━━━━━━━━━━━
📦 <b>ORDER DETAILS</b>
━━━━━━━━━━━━━━━━━━━━
Order No  : <b>{order.order_number}</b>
Customer  : {order.name}
Phone     : {order.phone}
Product   : {order.product_interest}
Address   : {order.address}
Message   : {order.message or 'None'}
━━━━━━━━━━━━━━━━━━━━
🔗 Admin: https://anganbaari.pythonanywhere.com/admin/shop/productorder/""")

    # Email to customer
    if order.email:
        send_resend_email(
            to=order.email,
            subject='✅ Order Received — Angan Baari | आँगन बारी',
            body=f'''नमस्ते {order.name}! 🌿

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

⚠️ Need to cancel? You can cancel within 30 minutes:
{cancel_link}

📱 Questions? WhatsApp us:
https://wa.me/9779821025084

With love,
Angan Baari Team 🌱
Bhulka Danda, Rupandehi, Nepal'''
        )

    # Email to admin
    send_resend_email(
        to='anganbaari@gmail.com',
        subject=f'🆕 New Order from {order.name} — Angan Baari',
        body=f'''New order received!

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
https://anganbaari.pythonanywhere.com/admin/shop/productorder/'''
    )


def send_order_confirmed_email(order):
    send_telegram(f"""✅ <b>Order Confirmed!</b>

Order No  : <b>{order.order_number}</b>
Customer  : {order.name}
Phone     : {order.phone}
Product   : {order.product_interest}""")

    if order.email:
        send_resend_email(
            to=order.email,
            subject='🎉 Order Confirmed — Angan Baari | आँगन बारी',
            body=f'''नमस्ते {order.name}! 🌿

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
Bhulka Danda, Rupandehi, Nepal'''
        )


def send_order_delivered_email(order):
    send_telegram(f"""🏡 <b>Order Delivered!</b>

Order No  : <b>{order.order_number}</b>
Customer  : {order.name}
Phone     : {order.phone}
Product   : {order.product_interest}
Address   : {order.address}""")

    if order.email:
        send_resend_email(
            to=order.email,
            subject='🏡 Order Delivered — Angan Baari | आँगन बारी',
            body=f'''नमस्ते {order.name}! 🌿

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

Please share your feedback:
https://anganbaari.pythonanywhere.com

With love,
Angan Baari Team 🌱
Bhulka Danda, Rupandehi, Nepal'''
        )

    send_resend_email(
        to='anganbaari@gmail.com',
        subject=f'✅ Order Delivered to {order.name} — Angan Baari',
        body=f'''Order has been delivered!

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

This order is now complete! ✅'''
    )


def send_order_cancelled_email(order):
    send_telegram(f"""❌ <b>Order Cancelled!</b>

Order No  : <b>{order.order_number}</b>
Customer  : {order.name}
Phone     : {order.phone}
Product   : {order.product_interest}""")

    if order.email:
        send_resend_email(
            to=order.email,
            subject='❌ Order Cancelled — Angan Baari | आँगन बारी',
            body=f'''नमस्ते {order.name}! 🌿

Your order has been CANCELLED successfully.

━━━━━━━━━━━━━━━━━━━━━━
📦 CANCELLED ORDER
━━━━━━━━━━━━━━━━━━━━━━
Order No  : {order.order_number}
Product   : {order.product_interest}
Status    : ❌ Cancelled
━━━━━━━━━━━━━━━━━━━━━━

Want to place a new order?
https://anganbaari.pythonanywhere.com

📱 Questions? WhatsApp us:
https://wa.me/9779821025084

With love,
Angan Baari Team 🌱
Bhulka Danda, Rupandehi, Nepal'''
        )

    send_resend_email(
        to='anganbaari@gmail.com',
        subject=f'❌ Order Cancelled by {order.name} — Angan Baari',
        body=f'''Order has been cancelled by customer!

━━━━━━━━━━━━━━━━━━━━━━
❌ CANCELLED ORDER
━━━━━━━━━━━━━━━━━━━━━━
Order No  : {order.order_number}
Customer  : {order.name}
Email     : {order.email}
Phone     : {order.phone}
Product   : {order.product_interest}
━━━━━━━━━━━━━━━━━━━━━━'''
    )