"""Account-linking bug fix (ProductOrder.user, migration
0051_productorder_hidden_by_customer_productorder_user) and the two new
features it enabled: "remove from history" (hidden_by_customer) and
selling units on the products API. Kept separate from api/tests.py for
the same reason tests_reports.py/tests_refunds.py are.
"""
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from shop.models import Category, Product, ProductOrder, ProductSellingUnit


class OrderAccountLinkingTestsBase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.category = Category.objects.create(name='Profile Orders Test', order=1)
        self.product = Product.objects.create(
            name='Profile Orders Banana', slug='profile-orders-banana', category=self.category,
            description='test', price=Decimal('50.00'), pricing_mode='fixed_quantity',
        )
        self.user_a = User.objects.create_user(
            username='user-a@example.com', email='user-a@example.com', password='pw12345',
        )
        self.token_a = Token.objects.create(user=self.user_a)
        self.user_b = User.objects.create_user(
            username='user-b@example.com', email='user-b@example.com', password='pw12345',
        )
        self.token_b = Token.objects.create(user=self.user_b)

    def place_order(self, token=None, email='guest-checkout@example.com'):
        headers = {}
        if token:
            headers['HTTP_AUTHORIZATION'] = f'Token {token.key}'
        response = self.client.post(
            reverse('v1_order_create'),
            {
                'name': 'Order Linking Buyer', 'email': email,
                'cart': [{'product_id': self.product.id, 'qty': 1}],
            },
            format='json', **headers,
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        return response


class OrderAccountLinkingTests(OrderAccountLinkingTestsBase):
    def test_authenticated_order_is_linked_and_shows_in_profile(self):
        self.place_order(token=self.token_a)

        self.client.credentials(HTTP_AUTHORIZATION=f'Token {self.token_a.key}')
        response = self.client.get(reverse('v1_profile_orders'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.json()['results']), 1)

    def test_guest_order_is_not_linked_to_anyone(self):
        self.place_order(token=None)

        order = ProductOrder.objects.latest('id')
        self.assertIsNone(order.user)

        self.client.credentials(HTTP_AUTHORIZATION=f'Token {self.token_a.key}')
        response = self.client.get(reverse('v1_profile_orders'))
        self.assertEqual(response.json()['results'], [])

    def test_order_from_user_a_not_visible_to_user_b(self):
        self.place_order(token=self.token_a)

        self.client.credentials(HTTP_AUTHORIZATION=f'Token {self.token_b.key}')
        response = self.client.get(reverse('v1_profile_orders'))

        self.assertEqual(response.json()['results'], [])

    def test_user_b_cannot_cancel_user_as_order(self):
        self.place_order(token=self.token_a)
        order = ProductOrder.objects.latest('id')

        self.client.credentials(HTTP_AUTHORIZATION=f'Token {self.token_b.key}')
        response = self.client.post(reverse('v1_order_cancel', args=[order.id]))

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_create_response_includes_pricing_summary_totals(self):
        response = self.place_order(token=None)
        self.assertIn('pricing_summary', response.json())
        self.assertEqual(response.json()['pricing_summary']['total'], '50.00')

    def test_profile_list_includes_per_line_final_prices_and_totals(self):
        self.place_order(token=self.token_a)

        self.client.credentials(HTTP_AUTHORIZATION=f'Token {self.token_a.key}')
        response = self.client.get(reverse('v1_profile_orders'))

        row = response.json()['results'][0]
        self.assertEqual(row['pricing_summary']['total'], '50.00')
        self.assertEqual(row['cart_snapshot'][0]['final_price'], '50.00')


class OrderHideTests(OrderAccountLinkingTestsBase):
    def setUp(self):
        super().setUp()
        self.place_order(token=self.token_a)
        self.order = ProductOrder.objects.latest('id')
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {self.token_a.key}')

    def _hide(self):
        return self.client.post(reverse('v1_profile_order_hide', args=[self.order.id]))

    def test_hide_refused_while_pending(self):
        self.assertEqual(self.order.status, 'pending')
        response = self._hide()
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_hide_refused_while_confirmed(self):
        self.order.status = 'confirmed'
        self.order.save()
        response = self._hide()
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_hide_works_when_delivered(self):
        self.order.status = 'delivered'
        self.order.save()

        response = self._hide()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.order.refresh_from_db()
        self.assertTrue(self.order.hidden_by_customer)

    def test_hide_works_when_cancelled(self):
        self.order.status = 'cancelled'
        self.order.save()
        response = self._hide()
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_hide_refused_for_another_users_order(self):
        self.order.status = 'delivered'
        self.order.save()

        self.client.credentials(HTTP_AUTHORIZATION=f'Token {self.token_b.key}')
        response = self._hide()

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.order.refresh_from_db()
        self.assertFalse(self.order.hidden_by_customer)

    def test_hidden_order_disappears_from_profile_but_stays_in_db(self):
        self.order.status = 'delivered'
        self.order.save()
        self._hide()

        response = self.client.get(reverse('v1_profile_orders'))
        self.assertEqual(response.json()['results'], [])

        # Never deleted -- still a real row, still fully intact.
        still_there = ProductOrder.objects.get(id=self.order.id)
        self.assertTrue(still_there.hidden_by_customer)
        self.assertEqual(still_there.status, 'delivered')

    def test_hide_never_touches_inventory_movements(self):
        from shop.models import InventoryMovement
        before = list(InventoryMovement.objects.filter(related_order=self.order).values_list('id', flat=True))

        self.order.status = 'delivered'
        self.order.save()
        self._hide()

        after = list(InventoryMovement.objects.filter(related_order=self.order).values_list('id', flat=True))
        self.assertEqual(before, after)


class ProductSellingUnitsApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.category = Category.objects.create(name='Selling Units API Test', order=1)
        self.eggs = Product.objects.create(
            name='Selling Units Eggs', slug='selling-units-eggs', category=self.category,
            description='test', price=Decimal('20.00'), pricing_mode='fixed_quantity',
        )
        self.piece = ProductSellingUnit.objects.create(
            product=self.eggs, name='piece', price=Decimal('20.00'),
            quantity_in_base_units=Decimal('1'), is_default=True,
        )
        self.crate = ProductSellingUnit.objects.create(
            product=self.eggs, name='crate', price=Decimal('550.00'),
            quantity_in_base_units=Decimal('30'), is_default=False,
        )
        self.mango = Product.objects.create(
            name='Selling Units Mango', slug='selling-units-mango', category=self.category,
            description='test', price=Decimal('300.00'), pricing_mode='variable_weight',
            weight_step=Decimal('0.50'),
        )

    def test_selling_units_appear_with_correct_factors(self):
        response = self.client.get(reverse('v1_product_detail', args=[self.eggs.id]))

        data = response.json()
        units_by_name = {u['name']: u for u in data['selling_units']}
        self.assertEqual(units_by_name['piece']['factor'], '1.00')
        self.assertEqual(units_by_name['crate']['factor'], '30.00')
        self.assertTrue(units_by_name['piece']['is_default'])
        self.assertFalse(units_by_name['crate']['is_default'])

    def test_fixed_quantity_min_quantity_is_one(self):
        response = self.client.get(reverse('v1_product_detail', args=[self.eggs.id]))
        self.assertEqual(response.json()['min_quantity'], 1)

    def test_variable_weight_has_no_selling_units_but_has_step_quantity(self):
        response = self.client.get(reverse('v1_product_detail', args=[self.mango.id]))

        data = response.json()
        self.assertEqual(data['selling_units'], [])
        self.assertEqual(data['step_quantity'], '0.50')
        self.assertIsNone(data['min_quantity'])

    def test_base_unit_label_matches_price_unit(self):
        response = self.client.get(reverse('v1_product_detail', args=[self.mango.id]))
        self.assertEqual(response.json()['base_unit_label'], self.mango.price_unit)
