from django.conf import settings


def storefront_url(request):
    """Exposes STOREFRONT_URL to every template, so a template can check
    `{% if STOREFRONT_URL %}` directly without a view having to pass it
    explicitly. The actual link-building (Shop/Cart/Checkout/Login/Signup/
    Profile/product mapping) lives in the {% storefront_link %} tag
    (shop/templatetags/storefront_tags.py), not here -- this just makes the
    raw setting available."""
    return {'STOREFRONT_URL': settings.STOREFRONT_URL}
