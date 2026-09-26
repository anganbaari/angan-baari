import uuid
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from shop.models import Category, InventoryMovement, Product, ProductVariant


class ApiTestBase(TestCase):
    def setUp(self):
        self.client = APIClient()

        self.category = Category.objects.create(name='Fruits', order=1)

        self.fruit = Product.objects.create(
            name='Mango', slug='mango', category=self.category,
            description='Sweet mango', price=Decimal('300.00'),
            pricing_mode='variable_weight', weight_step=Decimal('0.50'),
        )
        InventoryMovement.objects.create(
            product=self.fruit, movement_type='harvest', source='admin', quantity=Decimal('20.00'),
        )

        self.jar = Product.objects.create(
            name='Pickle Jar', slug='pickle-jar', category=self.category,
            description='Mango pickle', price=Decimal('250.00'),
            pricing_mode='fixed_quantity',
        )
        InventoryMovement.objects.create(
            product=self.jar, movement_type='harvest', source='admin', quantity=Decimal('10'),
        )

        self.goat = Product.objects.create(
            name='Goat', slug='goat', category=self.category,
            description='Live goat', price=Decimal('1200.00'),
            pricing_mode='fixed_weight',
        )
        self.goat_variant = ProductVariant.objects.create(
            product=self.goat, weight=Decimal('20.00'),
        )
        InventoryMovement.objects.create(
            product=self.goat, variant=self.goat_variant,
            movement_type='harvest', source='admin', quantity=Decimal('1'),
        )

        self.staff = User.objects.create_user('cashier', password='pw', is_staff=True)
        self.customer = User.objects.create_user('shopper', password='pw', is_staff=False)


class ProductAndCategoryReadTests(ApiTestBase):
    def test_product_list_is_public_and_paginated(self):
        response = self.client.get(reverse('v1_product_list'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('results', response.data)
        names = {p['name'] for p in response.data['results']}
        self.assertEqual(names, {'Mango', 'Pickle Jar', 'Goat'})

    def test_product_list_filter_by_category(self):
        other_cat = Category.objects.create(name='Honey', order=2)
        Product.objects.create(
            name='Honey Jar', slug='honey-jar', category=other_cat,
            description='Raw honey', price=Decimal('500'), pricing_mode='fixed_quantity',
        )
        response = self.client.get(reverse('v1_product_list'), {'category': self.category.id})
        names = {p['name'] for p in response.data['results']}
        self.assertEqual(names, {'Mango', 'Pickle Jar', 'Goat'})

    def test_product_detail_nests_category_and_variants(self):
        response = self.client.get(reverse('v1_product_detail', args=[self.goat.id]))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['category']['name'], 'Fruits')
        self.assertEqual(len(response.data['variants']), 1)
        self.assertEqual(response.data['variants'][0]['weight'], '20.00')

    def test_category_list_is_public(self):
        response = self.client.get(reverse('v1_category_list'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['results'][0]['name'], 'Fruits')


class InventoryMovementStaffReadTests(ApiTestBase):
    def test_anonymous_is_rejected(self):
        response = self.client.get(reverse('v1_inventory_movement_list'))
        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_non_staff_is_forbidden(self):
        self.client.login(username='shopper', password='pw')
        response = self.client.get(reverse('v1_inventory_movement_list'))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_staff_can_list_and_filter(self):
        self.client.login(username='cashier', password='pw')
        response = self.client.get(reverse('v1_inventory_movement_list'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 3)

        response = self.client.get(reverse('v1_inventory_movement_list'), {'product': self.fruit.id})
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['product_name'], 'Mango')


class POSSaleApiTests(ApiTestBase):
    def test_create_sale_requires_staff(self):
        payload = {
            'client_sale_id': str(uuid.uuid4()),
            'payment_method': 'cash',
            'cart': [{'product_id': self.jar.id, 'qty': 2}],
        }
        response = self.client.post(reverse('v1_sale_list_create'), payload, format='json')
        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_create_sale_recomputes_total_and_writes_movements(self):
        self.client.login(username='cashier', password='pw')
        payload = {
            'client_sale_id': str(uuid.uuid4()),
            'payment_method': 'cash',
            'cart': [
                {'product_id': self.jar.id, 'qty': 2},
                {'product_id': self.fruit.id, 'qty': 1, 'weight': '2.00'},
            ],
        }
        response = self.client.post(reverse('v1_sale_list_create'), payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        # 2 jars @ 250 + 2kg mango @ 300/kg = 500 + 600 = 1100
        # (Decimal multiplication yields trailing zeros, same as pos_create_sale()'s str(total).)
        self.assertEqual(Decimal(response.data['total']), Decimal('1100.00'))

        self.assertEqual(InventoryMovement.current_stock(self.jar), Decimal('8'))
        self.assertEqual(InventoryMovement.current_stock(self.fruit), Decimal('18.00'))

    def test_create_sale_is_idempotent_on_client_sale_id(self):
        self.client.login(username='cashier', password='pw')
        client_sale_id = str(uuid.uuid4())
        payload = {
            'client_sale_id': client_sale_id,
            'payment_method': 'cash',
            'cart': [{'product_id': self.jar.id, 'qty': 1}],
        }
        first = self.client.post(reverse('v1_sale_list_create'), payload, format='json')
        second = self.client.post(reverse('v1_sale_list_create'), payload, format='json')

        self.assertEqual(first.data['sale_number'], second.data['sale_number'])
        # Only ONE sale movement should have been written, despite two requests.
        self.assertEqual(InventoryMovement.current_stock(self.jar), Decimal('9'))

    def test_create_sale_rejects_overselling_fixed_weight_animal(self):
        self.client.login(username='cashier', password='pw')
        payload = {
            'client_sale_id': str(uuid.uuid4()),
            'payment_method': 'cash',
            'cart': [{'product_id': self.goat.id, 'qty': 1, 'variant_id': self.goat_variant.id}],
        }
        first = self.client.post(reverse('v1_sale_list_create'), payload, format='json')
        self.assertEqual(first.status_code, status.HTTP_201_CREATED)

        # Same variant, second (different) sale attempt — no stock left for this animal.
        payload2 = dict(payload, client_sale_id=str(uuid.uuid4()))
        second = self.client.post(reverse('v1_sale_list_create'), payload2, format='json')
        self.assertEqual(second.status_code, status.HTTP_400_BAD_REQUEST)

    def test_sale_list_requires_staff(self):
        response = self.client.get(reverse('v1_sale_list_create'))
        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

        self.client.login(username='cashier', password='pw')
        response = self.client.get(reverse('v1_sale_list_create'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)


class OrderApiTests(ApiTestBase):
    def test_guest_can_create_order(self):
        payload = {
            'name': 'Test Customer',
            'email': 'customer@example.com',
            'phone': '9800000000',
            'address': 'Bhulka Danda',
            'cart': [{'product_id': self.jar.id, 'qty': 3}],
        }
        response = self.client.post(reverse('v1_order_create'), payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertTrue(response.data['order_number'].startswith('AB-'))
        self.assertEqual(InventoryMovement.current_stock(self.jar), Decimal('7'))

    def test_order_detail_without_token_excludes_pii(self):
        payload = {
            'name': 'Test Customer',
            'email': 'customer@example.com',
            'phone': '9800000000',
            'address': 'Bhulka Danda',
            'cart': [{'product_id': self.jar.id, 'qty': 1}],
        }
        create_response = self.client.post(reverse('v1_order_create'), payload, format='json')
        order_id = create_response.data['id']

        response = self.client.get(reverse('v1_order_detail', args=[order_id]))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertNotIn('email', response.data)
        self.assertNotIn('phone', response.data)
        self.assertNotIn('address', response.data)
        self.assertNotIn('cancel_token', response.data)
        self.assertEqual(response.data['status'], 'pending')

    def test_order_detail_with_wrong_token_still_excludes_pii(self):
        payload = {
            'name': 'Test Customer',
            'email': 'customer@example.com',
            'cart': [{'product_id': self.jar.id, 'qty': 1}],
        }
        create_response = self.client.post(reverse('v1_order_create'), payload, format='json')
        order_id = create_response.data['id']

        response = self.client.get(reverse('v1_order_detail', args=[order_id]), {'token': 'not-the-real-token'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertNotIn('email', response.data)

    def test_order_detail_with_correct_token_returns_full_order(self):
        from shop.models import ProductOrder

        payload = {
            'name': 'Test Customer',
            'email': 'customer@example.com',
            'phone': '9800000000',
            'address': 'Bhulka Danda',
            'cart': [{'product_id': self.jar.id, 'qty': 1}],
        }
        create_response = self.client.post(reverse('v1_order_create'), payload, format='json')
        order_id = create_response.data['id']
        real_token = ProductOrder.objects.get(id=order_id).cancel_token

        response = self.client.get(reverse('v1_order_detail', args=[order_id]), {'token': real_token})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['email'], 'customer@example.com')
        self.assertEqual(response.data['phone'], '9800000000')
        self.assertEqual(response.data['address'], 'Bhulka Danda')
        self.assertIn('cart_snapshot', response.data)
        self.assertNotIn('cancel_token', response.data)

    def test_order_with_no_valid_lines_is_rejected(self):
        payload = {
            'name': 'Test Customer',
            'email': 'customer@example.com',
            'cart': [{'product_id': 999999, 'qty': 1}],
        }
        response = self.client.post(reverse('v1_order_create'), payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
