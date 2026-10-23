"""add_to_cart_offer() (shop/views.py) -- security fix: the discounted price
is now always recomputed server-side from a currently-live Offer, never
trusted from the client's posted `offer_price`. Kept separate from
shop/tests.py for the same reason tests_bs_calendar.py/tests_crop_batches.py
are.
"""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from shop.models import Category, Offer, Product


class AddToCartOfferSecurityTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('shopper', password='pw')
        self.client.login(username='shopper', password='pw')

        self.category = Category.objects.create(name='Offer Security Test', order=1)
        self.product = Product.objects.create(
            name='Offer Security Mango', slug='offer-security-mango', category=self.category,
            description='test', price=Decimal('100.00'), pricing_mode='fixed_quantity',
        )
        self.other_product = Product.objects.create(
            name='Offer Security Honey', slug='offer-security-honey', category=self.category,
            description='test', price=Decimal('500.00'), pricing_mode='fixed_quantity',
        )

    def _post(self, product, offer_price):
        return self.client.post(
            reverse('add_to_cart_offer', args=[product.id]),
            {'offer_price': offer_price},
        )

    def test_tampered_offer_price_is_ignored_server_price_used(self):
        """A live 20%-off offer on the product means the real price is
        Rs. 80 -- posting a tampered offer_price of Rs. 1 must not reach
        the cart."""
        Offer.objects.create(
            title='20% Off Mango', discount_type='percent', discount_value=Decimal('20'),
            start_date=timezone.now() - timedelta(hours=1),
            end_date=timezone.now() + timedelta(hours=1),
        ).products.add(self.product)

        self._post(self.product, offer_price='1')

        cart = self.client.session['cart']
        line = cart[str(self.product.id)]
        self.assertEqual(line['price'], str(Decimal('80.00')))
        self.assertNotEqual(line['price'], '1')

    def test_expired_offer_is_rejected(self):
        Offer.objects.create(
            title='Expired Mango Deal', discount_type='percent', discount_value=Decimal('50'),
            start_date=timezone.now() - timedelta(days=2),
            end_date=timezone.now() - timedelta(days=1),
        ).products.add(self.product)

        response = self._post(self.product, offer_price='1')

        self.assertNotEqual(response.status_code, 500)
        cart = self.client.session.get('cart', {})
        self.assertNotIn(str(self.product.id), cart)

    def test_inactive_offer_is_rejected(self):
        Offer.objects.create(
            title='Disabled Mango Deal', discount_type='percent', discount_value=Decimal('50'),
            is_active=False,
            start_date=timezone.now() - timedelta(hours=1),
            end_date=timezone.now() + timedelta(hours=1),
        ).products.add(self.product)

        self._post(self.product, offer_price='1')

        cart = self.client.session.get('cart', {})
        self.assertNotIn(str(self.product.id), cart)

    def test_product_outside_the_offer_is_rejected(self):
        """The live offer only covers `other_product` -- posting for
        `self.product` (not part of it) must be rejected, not silently
        given the other product's discount."""
        Offer.objects.create(
            title='Honey Only Deal', discount_type='percent', discount_value=Decimal('50'),
            start_date=timezone.now() - timedelta(hours=1),
            end_date=timezone.now() + timedelta(hours=1),
        ).products.add(self.other_product)

        self._post(self.product, offer_price='1')

        cart = self.client.session.get('cart', {})
        self.assertNotIn(str(self.product.id), cart)

    def test_ajax_rejection_returns_json_error_without_raising(self):
        Offer.objects.create(
            title='Expired AJAX Deal', discount_type='percent', discount_value=Decimal('50'),
            start_date=timezone.now() - timedelta(days=2),
            end_date=timezone.now() - timedelta(days=1),
        ).products.add(self.product)

        response = self.client.post(
            reverse('add_to_cart_offer', args=[self.product.id]),
            {'offer_price': '1'},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['status'], 'error')
