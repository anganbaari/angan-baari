"""GET /api/v1/offers/, POST /api/v1/cart/quote/, and offer pricing on
POST /api/v1/orders/ -- additive offers-on-the-API feature. Kept separate
from api/tests.py for the same reason tests_reports.py/tests_refunds.py
are. Offer price resolution itself (quote_cart_line()/
resolve_automatic_offer(), shop/views.py) reuses price_pos_cart_line(),
so POS/refund behaviour is untouched -- covered by the existing POS/
refund test files, not re-tested here.
"""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from shop.models import Category, Offer, Product


class OffersPricingTestsBase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.category = Category.objects.create(name='Offers API Test', order=1)
        self.product = Product.objects.create(
            name='Offers API Mango', slug='offers-api-mango', category=self.category,
            description='test', price=Decimal('100.00'), pricing_mode='fixed_quantity',
        )
        self.user = User.objects.create_user(username='offers-api@example.com', email='offers-api@example.com', password='pw12345')
        self.token = Token.objects.create(user=self.user)

    def make_live_offer(self, discount_value=Decimal('20'), product=None, category=None,
                         is_active=True, start_delta=-1, end_delta=1):
        offer = Offer.objects.create(
            title='API Test Offer', discount_type='percent', discount_value=discount_value,
            is_active=is_active,
            start_date=timezone.now() + timedelta(hours=start_delta),
            end_date=timezone.now() + timedelta(hours=end_delta),
            category=category,
        )
        if product is not None:
            offer.products.add(product)
        return offer

    def auth(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {self.token.key}')


class CartQuoteTests(OffersPricingTestsBase):
    def _quote(self):
        return self.client.post(
            reverse('v1_cart_quote'),
            {'lines': [{'product_id': self.product.id, 'quantity': 1}]},
            format='json',
        )

    def test_guest_gets_normal_price_and_login_required_flag(self):
        self.make_live_offer(product=self.product)

        response = self._quote()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        line = data['lines'][0]
        self.assertEqual(line['final_price'], '100.00')
        self.assertIsNone(line['offer_id'])
        self.assertTrue(data['login_required_for_offers'])
        self.assertFalse(data['offers_applied'])

    def test_authenticated_gets_offer_price(self):
        offer = self.make_live_offer(discount_value=Decimal('20'), product=self.product)
        self.auth()

        response = self._quote()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        line = data['lines'][0]
        self.assertEqual(line['final_price'], '80.00')
        self.assertEqual(line['offer_id'], offer.id)
        self.assertTrue(data['offers_applied'])
        self.assertEqual(data['total_discount'], '20.00')

    def test_expired_offer_is_ignored(self):
        self.make_live_offer(product=self.product, start_delta=-48, end_delta=-24)
        self.auth()

        response = self._quote()

        line = response.json()['lines'][0]
        self.assertEqual(line['final_price'], '100.00')
        self.assertIsNone(line['offer_id'])

    def test_inactive_offer_is_ignored(self):
        self.make_live_offer(product=self.product, is_active=False)
        self.auth()

        response = self._quote()

        line = response.json()['lines'][0]
        self.assertEqual(line['final_price'], '100.00')
        self.assertIsNone(line['offer_id'])

    def test_category_offer_applies(self):
        offer = self.make_live_offer(discount_value=Decimal('10'), category=self.category)
        self.auth()

        response = self._quote()

        line = response.json()['lines'][0]
        self.assertEqual(line['final_price'], '90.00')
        self.assertEqual(line['offer_id'], offer.id)

    def test_tampered_client_price_is_ignored(self):
        """No price field exists on a quote line at all -- posting one
        must have no effect either way."""
        self.make_live_offer(product=self.product)
        self.auth()

        response = self.client.post(
            reverse('v1_cart_quote'),
            {'lines': [{'product_id': self.product.id, 'quantity': 1, 'price': '1'}]},
            format='json',
        )
        line = response.json()['lines'][0]
        self.assertEqual(line['final_price'], '80.00')

    def test_invalid_token_is_treated_as_guest_not_rejected(self):
        self.client.credentials(HTTP_AUTHORIZATION='Token not-a-real-token')

        response = self._quote()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()['lines'][0]['final_price'], '100.00')


class OffersListTests(OffersPricingTestsBase):
    def test_lists_only_live_offers(self):
        live = self.make_live_offer(product=self.product)
        self.make_live_offer(product=self.product, start_delta=-48, end_delta=-24)  # expired
        self.make_live_offer(product=self.product, is_active=False)  # inactive

        response = self.client.get(reverse('v1_offers_list'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        offer_ids = {line['offer_id'] for line in data['discount_offers']}
        self.assertEqual(offer_ids, {live.id})

    def test_combo_bundle_is_display_only(self):
        combo = Offer.objects.create(
            title='API Test Combo', discount_type='combo', discount_value=Decimal('0'),
            combo_price=Decimal('500.00'),
            start_date=timezone.now() - timedelta(hours=1), end_date=timezone.now() + timedelta(hours=1),
        )
        from shop.models import BundleItem
        BundleItem.objects.create(offer=combo, product=self.product, quantity=Decimal('2'))

        response = self.client.get(reverse('v1_offers_list'))

        bundle = response.json()['combo_bundles'][0]
        self.assertEqual(bundle['combo_price'], '500.00')
        self.assertIn('whatsapp_message', bundle)
        self.assertNotIn('add_to_cart', bundle)
        self.assertEqual(bundle['items'][0]['unit'], 'kg')  # self.product's price_unit defaults to 'per kg'

    def test_combo_item_unit_for_fixed_weight_product_is_kg(self):
        animal = Product.objects.create(
            name='API Test Goat', slug='api-test-goat', category=self.category,
            description='test', price=Decimal('0'), pricing_mode='fixed_weight', price_unit='per piece',
        )
        combo = Offer.objects.create(
            title='API Test Animal Combo', discount_type='combo', discount_value=Decimal('0'),
            combo_price=Decimal('5000.00'),
            start_date=timezone.now() - timedelta(hours=1), end_date=timezone.now() + timedelta(hours=1),
        )
        from shop.models import BundleItem
        BundleItem.objects.create(offer=combo, product=animal, quantity=Decimal('25.00'))

        response = self.client.get(reverse('v1_offers_list'))

        bundle = next(b for b in response.json()['combo_bundles'] if b['id'] == combo.id)
        self.assertEqual(bundle['items'][0]['unit'], 'kg')  # weight, regardless of this product's own price_unit

    def test_public_no_auth_required(self):
        response = self.client.get(reverse('v1_offers_list'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)


class OrderOfferPricingTests(OffersPricingTestsBase):
    def _order_payload(self, **extra):
        payload = {
            'name': 'Offers API Buyer', 'email': 'buyer-offers-api@example.com',
            'cart': [{'product_id': self.product.id, 'qty': 1, 'price': '1'}],
        }
        payload.update(extra)
        return payload

    def test_authenticated_order_records_offer_price_and_ignores_client_price(self):
        offer = self.make_live_offer(discount_value=Decimal('25'), product=self.product)
        self.auth()

        response = self.client.post(reverse('v1_order_create'), self._order_payload(), format='json')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        from shop.models import ProductOrder
        order = ProductOrder.objects.get(id=response.json()['id'])
        line = order.cart_snapshot[0]
        self.assertEqual(line['final_price'], '75.00')
        self.assertEqual(line['offer_id'], offer.id)
        self.assertNotEqual(line['final_price'], '1')

    def test_guest_order_unchanged_and_gets_normal_price(self):
        self.make_live_offer(product=self.product)

        response = self.client.post(reverse('v1_order_create'), self._order_payload(), format='json')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        from shop.models import ProductOrder
        order = ProductOrder.objects.get(id=response.json()['id'])
        line = order.cart_snapshot[0]
        self.assertEqual(line['final_price'], '100.00')
        self.assertIsNone(line['offer_id'])

    def test_invalid_token_on_order_is_treated_as_guest(self):
        self.make_live_offer(product=self.product)
        self.client.credentials(HTTP_AUTHORIZATION='Token not-a-real-token')

        response = self.client.post(reverse('v1_order_create'), self._order_payload(), format='json')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
