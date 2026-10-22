import re

from django import template
from django.conf import settings
from django.urls import reverse

register = template.Library()

# Kinds that take no extra argument -- {% storefront_link 'cart' %} etc.
# Maps to (Django url name, Vercel path).
_SIMPLE_LINKS = {
    'shop': ('shop', '/'),
    'cart': ('cart', '/cart'),
    'checkout': ('checkout', '/checkout'),
    'login': ('login', '/login'),
    'signup': ('signup', '/signup'),
    'profile': ('profile', '/profile'),
}


@register.simple_tag
def storefront_link(kind, arg=None):
    """{% storefront_link 'shop' %}, {% storefront_link 'category' cat.id %},
    {% storefront_link 'product' product.slug %}, or one of the other
    _SIMPLE_LINKS kinds above.

    Returns the Next.js storefront's own URL when STOREFRONT_URL is set
    (Phase 1 of the storefront links switch -- see that setting's own
    comment in farmsite/settings.py), otherwise the existing Django page's
    URL, completely unchanged from before this tag existed. Deliberately
    does NOT cover Offers, Home/logo, or About/Contact -- those stay on
    Django regardless of STOREFRONT_URL (the Next.js shop has no /offers
    route yet), so templates keep using {% url %} for those directly.
    """
    base = settings.STOREFRONT_URL

    if kind == 'category':
        if not base:
            return reverse('shop') + f'?cat={arg}'
        return f'{base}/?cat={arg}'

    if kind == 'product':
        if not base:
            return reverse('product_detail', kwargs={'slug': arg})
        return f'{base}/product/{arg}'

    if kind in _SIMPLE_LINKS:
        url_name, storefront_path = _SIMPLE_LINKS[kind]
        if not base:
            return reverse(url_name)
        return base + storefront_path

    raise ValueError(f'storefront_link: unknown kind {kind!r}')


@register.filter
def first_sentence(text):
    """The first sentence of a block of text (up to and including the
    first '.', '!' or '?'), or the whole text unchanged if none of those
    appear. Used for the database-driven home-page cards' short-line
    fallback when Product.tagline is blank: {{ product.description|
    first_sentence|truncatechars:90 }} (templates/index.html) -- chained
    with Django's own built-in truncatechars filter for the "~90
    characters, ellipsis" part, since there's no single built-in filter
    that does both steps at once."""
    if not text:
        return text
    match = re.search(r'[^.!?]*[.!?]', text.strip())
    return match.group(0).strip() if match else text.strip()
