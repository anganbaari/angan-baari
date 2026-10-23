"""Follow-up to the offers-on-the-API / profile-orders work: selling-unit
pricing+stock in the public cart/order API, variable_weight validation,
and idempotent stock-return when an order is cancelled via Django admin.
Kept separate from api/tests.py for the same reason tests_reports.py/
tests_refunds.py are.
"""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from shop.models import Category, InventoryMovement, Product, ProductOrder, ProductSellingUnit


class SellingUnitApiTestsBase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.category = Category.objects.create(name='Selling Unit Followup Test', order=1)
        self.eggs = Product.objects.create(
            name='Followup Eggs', slug='followup-eggs', category=self.category,
            description='test', price=Decimal('20.00'), pricing_mode='fixed_quantity',
        )
        self.crate = ProductSellingUnit.objects.create(
            product=self.eggs, name='crate', price=Decimal('550.00'),
            quantity_in_base_units=Decimal('30'), is_default=False,
        )
        self.other_product = Product.objects.create(
            name='Followup Other', slug='followup-other', category=self.category,
            description='test', price=Decimal('10.00'), pricing_mode='fixed_quantity',
        )
        self.foreign_unit = ProductSellingUnit.objects.create(
            product=self.other_product, name='piece', price=Decimal('10.00'),
            quantity_in_base_units=Decimal('1'), is_default=True,
        )
        self.unavailable_unit = ProductSellingUnit.objects.create(
            product=self.eggs, name='piece', price=Decimal('20.00'),
            quantity_in_base_units=Decimal('1'), is_available=False,
        )
        self.mango = Product.objects.create(
            name='Followup Mango', slug='followup-mango', category=self.category,
            description='test', price=Decimal('300.00'), pricing_mode='variable_weight',
            weight_step=Decimal('0.50'),
        )


class SellingUnitProductApiTests(SellingUnitApiTestsBase):
    def test_unit_price_appears_and_unavailable_units_excluded(self):
        response = self.client.get(reverse('v1_product_detail', args=[self.eggs.id]))

        units = response.json()['selling_units']
        self.assertEqual(len(units), 1)  # unavailable `piece` excluded
        self.assertEqual(units[0]['id'], self.crate.id)
        self.assertEqual(units[0]['price'], '550.00')


class SellingUnitCartQuoteTests(SellingUnitApiTestsBase):
    def test_quote_uses_unit_price_not_base_price(self):
        response = self.client.post(
            reverse('v1_cart_quote'),
            {'lines': [{'product_id': self.eggs.id, 'quantity': 1, 'selling_unit_id': self.crate.id}]},
            format='json',
        )
        line = response.json()['lines'][0]
        self.assertEqual(line['final_price'], '550.00')
        self.assertEqual(line['selling_unit_id'], self.crate.id)
        self.assertEqual(line['selling_unit_name'], 'Crate')

    def test_quote_rejects_unit_belonging_to_different_product(self):
        response = self.client.post(
            reverse('v1_cart_quote'),
            {'lines': [{'product_id': self.eggs.id, 'quantity': 1, 'selling_unit_id': self.foreign_unit.id}]},
            format='json',
        )
        line = response.json()['lines'][0]
        self.assertIn('error', line)
        self.assertNotIn('final_price', line)

    def test_quote_rejects_unavailable_unit(self):
        response = self.client.post(
            reverse('v1_cart_quote'),
            {'lines': [{'product_id': self.eggs.id, 'quantity': 1, 'selling_unit_id': self.unavailable_unit.id}]},
            format='json',
        )
        line = response.json()['lines'][0]
        self.assertIn('error', line)

    def test_weight_below_step_is_rejected(self):
        response = self.client.post(
            reverse('v1_cart_quote'),
            {'lines': [{'product_id': self.mango.id, 'weight': '0.20'}]},
            format='json',
        )
        line = response.json()['lines'][0]
        self.assertIn('error', line)

    def test_weight_not_a_multiple_of_step_is_rejected(self):
        response = self.client.post(
            reverse('v1_cart_quote'),
            {'lines': [{'product_id': self.mango.id, 'weight': '1.30'}]},
            format='json',
        )
        line = response.json()['lines'][0]
        self.assertIn('error', line)

    def test_valid_weight_is_accepted(self):
        response = self.client.post(
            reverse('v1_cart_quote'),
            {'lines': [{'product_id': self.mango.id, 'weight': '1.50'}]},
            format='json',
        )
        line = response.json()['lines'][0]
        self.assertEqual(line['final_price'], '450')  # whole-rupee rounded, variable_weight lines
        self.assertNotIn('error', line)


class SellingUnitOrderTests(SellingUnitApiTestsBase):
    def _order_payload(self, **cart_line_extra):
        cart_line = {'product_id': self.eggs.id, 'qty': 2}
        cart_line.update(cart_line_extra)
        return {
            'name': 'Selling Unit Buyer', 'email': 'selling-unit-buyer@example.com',
            'cart': [cart_line],
        }

    def test_order_deducts_quantity_times_factor(self):
        response = self.client.post(
            reverse('v1_order_create'),
            self._order_payload(selling_unit_id=self.crate.id),
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data if hasattr(response, 'data') else response.json())

        order = ProductOrder.objects.get(id=response.json()['id'])
        line = order.cart_snapshot[0]
        self.assertEqual(line['final_price'], '1100.00')  # 2 crates x Rs.550
        self.assertEqual(line['selling_unit_id'], self.crate.id)
        self.assertEqual(line['selling_unit_name'], 'Crate')

        movement = InventoryMovement.objects.get(related_order=order, movement_type='sale')
        self.assertEqual(movement.quantity, Decimal('60.00'))  # 2 x 30 base units

    def test_order_rejects_invalid_unit_but_keeps_other_valid_lines(self):
        payload = {
            'name': 'Selling Unit Buyer', 'email': 'selling-unit-buyer2@example.com',
            'cart': [
                {'product_id': self.eggs.id, 'qty': 1, 'selling_unit_id': self.foreign_unit.id},
                {'product_id': self.other_product.id, 'qty': 1},
            ],
        }
        response = self.client.post(reverse('v1_order_create'), payload, format='json')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        data = response.json()
        self.assertEqual(len(data['errors']), 1)
        order = ProductOrder.objects.get(id=data['id'])
        self.assertEqual(len(order.cart_snapshot), 1)
        self.assertEqual(order.cart_snapshot[0]['product_id'], self.other_product.id)


class AdminCancelStockReturnTests(SellingUnitApiTestsBase):
    def _place_order(self):
        response = self.client.post(
            reverse('v1_order_create'),
            {
                'name': 'Admin Cancel Buyer', 'email': 'admin-cancel-buyer@example.com',
                'cart': [{'product_id': self.other_product.id, 'qty': 3}],
            },
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        return ProductOrder.objects.get(id=response.json()['id'])

    def test_admin_cancel_returns_stock_once(self):
        order = self._place_order()
        self.assertTrue(InventoryMovement.objects.filter(related_order=order, movement_type='sale').exists())
        self.assertFalse(InventoryMovement.objects.filter(related_order=order, movement_type='return').exists())

        # Simulate changing status to Cancelled in Django admin -- a plain
        # model save, exactly what OrderAdmin's list_editable=['status']
        # (no custom save_model) actually does.
        order.status = 'cancelled'
        order.save()

        returns = InventoryMovement.objects.filter(related_order=order, movement_type='return')
        self.assertEqual(returns.count(), 1)

    def test_second_admin_save_while_still_cancelled_adds_nothing(self):
        order = self._place_order()
        order.status = 'cancelled'
        order.save()
        self.assertEqual(
            InventoryMovement.objects.filter(related_order=order, movement_type='return').count(), 1,
        )

        # Admin re-saves the already-cancelled order (e.g. editing an
        # unrelated field) -- must not double-return stock.
        order.save()

        self.assertEqual(
            InventoryMovement.objects.filter(related_order=order, movement_type='return').count(), 1,
        )

    def test_cancel_with_no_sale_movements_does_nothing(self):
        order = ProductOrder.objects.create(
            name='No Snapshot Buyer', email='no-snapshot@example.com', phone='', address='',
            product_interest='Nothing', cart_snapshot=None,
        )
        order.status = 'cancelled'
        order.save()

        self.assertFalse(InventoryMovement.objects.filter(related_order=order).exists())
