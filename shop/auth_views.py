import uuid
from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib import messages
from django.core.cache import cache
from .models import ProductOrder, Wishlist, Coupon
from .emails import send_resend_email


def signup(request):
    if request.user.is_authenticated:
        return redirect('shop')
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        email = request.POST.get('email', '').strip()
        password = request.POST.get('password', '').strip()
        confirm = request.POST.get('confirm_password', '').strip()

        if not all([name, email, password, confirm]):
            messages.error(request, 'Please fill in all fields.')
        elif password != confirm:
            messages.error(request, 'Passwords do not match.')
        elif len(password) < 8:
            messages.error(request, 'Password must be at least 8 characters.')
        elif User.objects.filter(email=email).exists():
            messages.error(request, 'An account with this email already exists.')
        else:
            first_name = name.split()[0]
            last_name = ' '.join(name.split()[1:]) if len(name.split()) > 1 else ''
            user = User.objects.create_user(
                username=email,
                email=email,
                password=password,
                first_name=first_name,
                last_name=last_name,
            )
            login(request, user)
            # Send welcome email
            try:
                send_resend_email(
                    to=email,
                    subject='🌿 Welcome to Angan Baari!',
                    body=f'''नमस्ते {name}! 🌿

Welcome to Angan Baari (आँगन बारी)!

Your account has been created successfully.
You can now track your orders and shop easily.

🛒 Start shopping:
https://anganbaari.pythonanywhere.com/shop/

📱 Questions? WhatsApp us:
https://wa.me/9779821025084

With love,
Angan Baari Team 🌱
Bhulka Danda, Rupandehi, Nepal'''
                )
            except Exception:
                pass
            messages.success(request, f'Welcome, {first_name}! Your account has been created.')
            return redirect('shop')

    return render(request, 'auth/signup.html')


def login_view(request):
    if request.user.is_authenticated:
        return redirect('shop')
    if request.method == 'POST':
        email = request.POST.get('email', '').strip()
        password = request.POST.get('password', '').strip()
        user = authenticate(request, username=email, password=password)
        if user:
            login(request, user)
            # "Remember me" — unchecked means the session ends when the
            # browser closes; checked keeps Django's normal long-lived session.
            if not request.POST.get('remember_me'):
                request.session.set_expiry(0)
            next_url = request.GET.get('next', '/shop/')
            return redirect(next_url)
        else:
            messages.error(request, 'Invalid email or password.')
    return render(request, 'auth/login.html')


def logout_view(request):
    logout(request)
    return redirect('/')


@login_required
def profile(request):
    orders = ProductOrder.objects.filter(email=request.user.email).order_by('-ordered_at')
    wishlist_items = Wishlist.objects.filter(user=request.user).select_related('product')
    live_coupons = [c for c in Coupon.objects.filter(is_active=True) if c.is_live()]
    return render(request, 'auth/profile.html', {
        'orders': orders,
        'wishlist_items': wishlist_items,
        'live_coupons': live_coupons,
    })


@login_required
def edit_profile(request):
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        email = request.POST.get('email', '').strip()

        if not name or not email:
            messages.error(request, 'Name and email cannot be empty.')
        elif User.objects.filter(email=email).exclude(id=request.user.id).exists():
            messages.error(request, 'Another account already uses that email.')
        else:
            parts = name.split()
            request.user.first_name = parts[0]
            request.user.last_name = ' '.join(parts[1:]) if len(parts) > 1 else ''
            # Changing email also changes username, since signup uses email as username.
            request.user.email = email
            request.user.username = email
            request.user.save()
            messages.success(request, 'Profile updated successfully.')
        return redirect('profile')
    return redirect('profile')


@login_required
def change_password(request):
    if request.method == 'POST':
        form = PasswordChangeForm(user=request.user, data=request.POST)
        if form.is_valid():
            user = form.save()
            # Keeps the user logged in after the password hash changes,
            # instead of forcing them to log back in immediately.
            update_session_auth_hash(request, user)
            messages.success(request, 'Password changed successfully.')
        else:
            # Surface the first validation error (wrong old password, passwords
            # don't match, too weak/common, etc.) rather than a generic message.
            for field_errors in form.errors.values():
                for error in field_errors:
                    messages.error(request, error)
                break
        return redirect('profile')
    return redirect('profile')


def forgot_password(request):
    if request.method == 'POST':
        email = request.POST.get('email', '').strip()
        try:
            user = User.objects.get(email=email)
            token = uuid.uuid4().hex
            cache.set(f'pwd_reset_{token}', user.id, 3600)  # 1 hour expiry
            reset_link = f'https://anganbaari.pythonanywhere.com/account/reset-password/{token}/'
            send_resend_email(
                to=email,
                subject='🔑 Reset Your Password — Angan Baari',
                body=f'''नमस्ते {user.first_name}! 🌿

You requested a password reset for your Angan Baari account.

Click the link below to reset your password:
{reset_link}

This link expires in 1 hour.

If you did not request this, please ignore this email.

Angan Baari Team 🌱'''
            )
        except User.DoesNotExist:
            pass
        # Always show success to prevent email enumeration
        messages.success(request, 'If an account exists with that email, a reset link has been sent.')
        return redirect('forgot_password')
    return render(request, 'auth/forgot_password.html')


def reset_password(request, token):
    user_id = cache.get(f'pwd_reset_{token}')
    if not user_id:
        messages.error(request, 'This reset link is invalid or has expired.')
        return redirect('forgot_password')

    if request.method == 'POST':
        password = request.POST.get('password', '').strip()
        confirm = request.POST.get('confirm_password', '').strip()
        if password != confirm:
            messages.error(request, 'Passwords do not match.')
        elif len(password) < 8:
            messages.error(request, 'Password must be at least 8 characters.')
        else:
            user = User.objects.get(id=user_id)
            user.set_password(password)
            user.save()
            cache.delete(f'pwd_reset_{token}')
            messages.success(request, 'Password reset successfully! Please login.')
            return redirect('login')
    return render(request, 'auth/reset_password.html', {'token': token})