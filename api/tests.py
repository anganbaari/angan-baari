import uuid
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from shop.models import (
    BundleItem, BusinessSettings, Category, Coupon, CreditTransaction, Customer, InventoryMovement,
    Offer, POSSale, POSSalePayment, Product, ProductOrder, ProductVariant, UserProfile, Wishlist,
)


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
        # POS Phase A/B: sale creation and credit repayment now need a real
        # "operator" identity, established in the SESSION by a correct PIN
        # on POST /pos/unlock/ -- never a client-supplied operator_id.
        # self.staff_profile doubles as that operator in every existing
        # test that doesn't care who specifically it is; self.staff_pin is
        # its known PIN, used via self.unlock_terminal() below.
        self.staff_pin = '8256'  # distinct from PINs used in PosUnlockApiTests ('4471', '0000')
        self.staff_profile = UserProfile.objects.create(user=self.staff, role='cashier')
        self.staff_profile.set_pin(self.staff_pin)
        self.staff_profile.save()
        self.customer = User.objects.create_user('shopper', password='pw', is_staff=False)

    def unlock_terminal(self, pin=None):
        """Logs the session in as the current operator via a real PIN
        unlock — sale/repay tests call this after self.client.login()
        (the terminal's own day-long staff login) instead of passing
        operator_id directly, matching how the frontend will actually work."""
        response = self.client.post(reverse('v1_pos_unlock'), {'pin': pin or self.staff_pin}, format='json')
        assert response.status_code == status.HTTP_200_OK, response.data
        return response


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

    def test_product_detail_by_slug(self):
        response = self.client.get(reverse('v1_product_detail_by_slug', args=[self.goat.slug]))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['id'], self.goat.id)

    def test_images_lists_every_photo_in_gallery_order_skipping_blanks(self):
        """Product stores photos as four flat ImageFields; `images` collects
        the non-blank ones in the same order product_detail.html's thumb grid
        uses, with main_image first. image3 is deliberately left blank here."""
        self.fruit.main_image = 'https://ik.imagekit.io/x/main.jpg'
        self.fruit.image2 = 'https://ik.imagekit.io/x/two.jpg'
        self.fruit.image4 = 'https://ik.imagekit.io/x/four.jpg'
        self.fruit.save()

        response = self.client.get(reverse('v1_product_detail_by_slug', args=[self.fruit.slug]))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data['images'],
            [
                'https://ik.imagekit.io/x/main.jpg',
                'https://ik.imagekit.io/x/two.jpg',
                'https://ik.imagekit.io/x/four.jpg',
            ],
        )
        # main_image is unchanged for existing consumers, and is images[0].
        self.assertEqual(response.data['main_image'], 'https://ik.imagekit.io/x/main.jpg')
        self.assertEqual(response.data['images'][0], response.data['main_image'])

    def test_images_is_empty_list_when_product_has_no_photos(self):
        response = self.client.get(reverse('v1_product_detail_by_slug', args=[self.jar.slug]))
        self.assertEqual(response.data['main_image'], None)
        self.assertEqual(response.data['images'], [])

    def test_images_present_on_list_endpoint_too(self):
        self.fruit.main_image = 'https://ik.imagekit.io/x/main.jpg'
        self.fruit.image3 = 'https://ik.imagekit.io/x/three.jpg'
        self.fruit.save()

        response = self.client.get(reverse('v1_product_list'))
        mango = next(p for p in response.data['results'] if p['id'] == self.fruit.id)
        self.assertEqual(
            mango['images'],
            ['https://ik.imagekit.io/x/main.jpg', 'https://ik.imagekit.io/x/three.jpg'],
        )

    def test_product_detail_by_slug_404_for_unknown_slug(self):
        response = self.client.get(reverse('v1_product_detail_by_slug', args=['no-such-product']))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

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
            'payments': [{'method': 'cash', 'amount': '500.00'}],
            'cart': [{'product_id': self.jar.id, 'qty': 2}],
        }
        response = self.client.post(reverse('v1_sale_list_create'), payload, format='json')
        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_create_sale_rejects_without_prior_unlock(self):
        """Logged into the terminal (session auth) but no PIN unlock yet in
        this session -- sale creation must be rejected, not silently
        attributed to whoever's logged into the terminal."""
        self.client.login(username='cashier', password='pw')
        payload = {
            'client_sale_id': str(uuid.uuid4()),
            'payments': [{'method': 'cash', 'amount': '250.00'}],
            'cart': [{'product_id': self.jar.id, 'qty': 1}],
        }
        response = self.client.post(reverse('v1_sale_list_create'), payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(POSSale.objects.filter(client_sale_id=payload['client_sale_id']).exists())

    def test_create_sale_recomputes_total_and_writes_movements(self):
        self.client.login(username='cashier', password='pw')
        self.unlock_terminal()
        payload = {
            'client_sale_id': str(uuid.uuid4()),
            'payments': [{'method': 'cash', 'amount': '1100.00'}],
            'cart': [
                {'product_id': self.jar.id, 'qty': 2},
                {'product_id': self.fruit.id, 'qty': 1, 'weight': '2.00'},
            ],
        }
        response = self.client.post(reverse('v1_sale_list_create'), payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        # 2 jars @ 250 + 2kg mango @ 300/kg = 500 + 600 = 1100
        # (Decimal multiplication yields trailing zeros in str(total).)
        self.assertEqual(Decimal(response.data['total']), Decimal('1100.00'))

        self.assertEqual(InventoryMovement.current_stock(self.jar), Decimal('8'))
        self.assertEqual(InventoryMovement.current_stock(self.fruit), Decimal('18.00'))

        sale = POSSale.objects.get(sale_number=response.data['sale_number'])
        self.assertEqual(sale.payment_method, 'cash')  # single payment line -> not 'split'
        self.assertEqual(sale.payments.count(), 1)
        self.assertEqual(sale.cashier, self.staff)  # attributed from the session-unlocked operator

    def test_create_sale_is_idempotent_on_client_sale_id(self):
        self.client.login(username='cashier', password='pw')
        self.unlock_terminal()
        client_sale_id = str(uuid.uuid4())
        payload = {
            'client_sale_id': client_sale_id,
            'payments': [{'method': 'cash', 'amount': '250.00'}],
            'cart': [{'product_id': self.jar.id, 'qty': 1}],
        }
        first = self.client.post(reverse('v1_sale_list_create'), payload, format='json')
        second = self.client.post(reverse('v1_sale_list_create'), payload, format='json')

        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(first.data['sale_number'], second.data['sale_number'])
        # Only ONE sale movement should have been written, despite two requests.
        self.assertEqual(InventoryMovement.current_stock(self.jar), Decimal('9'))
        self.assertEqual(POSSale.objects.filter(client_sale_id=client_sale_id).count(), 1)

    def test_create_sale_rejects_overselling_fixed_weight_animal(self):
        self.client.login(username='cashier', password='pw')
        self.unlock_terminal()
        payload = {
            'client_sale_id': str(uuid.uuid4()),
            'payments': [{'method': 'cash', 'amount': str(self.goat_variant.total_price())}],
            'cart': [{'product_id': self.goat.id, 'qty': 1, 'variant_id': self.goat_variant.id}],
        }
        first = self.client.post(reverse('v1_sale_list_create'), payload, format='json')
        self.assertEqual(first.status_code, status.HTTP_201_CREATED)

        # Same variant, second (different) sale attempt — no stock left for this animal.
        payload2 = dict(payload, client_sale_id=str(uuid.uuid4()))
        second = self.client.post(reverse('v1_sale_list_create'), payload2, format='json')
        self.assertEqual(second.status_code, status.HTTP_400_BAD_REQUEST)

    def test_queued_operator_id_attributes_sale_to_that_operator(self):
        """A replayed offline sale carries queued_operator_id -- captured
        client-side at the moment it was originally queued, not whoever
        happens to be PIN-unlocked in THIS session right now. The second
        operator here never unlocks this session at all; the server must
        still attribute the sale to them, not to self.staff."""
        other_staff = User.objects.create_user('other_cashier', password='pw', is_staff=True)
        other_profile = UserProfile.objects.create(user=other_staff, role='cashier')
        other_profile.set_pin('3391')
        other_profile.save()

        self.client.login(username='cashier', password='pw')
        self.unlock_terminal()  # session operator is self.staff
        payload = {
            'client_sale_id': str(uuid.uuid4()),
            'payments': [{'method': 'cash', 'amount': '250.00'}],
            'cart': [{'product_id': self.jar.id, 'qty': 1}],
            'queued_operator_id': other_staff.id,
        }
        response = self.client.post(reverse('v1_sale_list_create'), payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        sale = POSSale.objects.get(sale_number=response.data['sale_number'])
        self.assertEqual(sale.cashier, other_staff)

    def test_queued_operator_id_rejected_if_no_longer_active_staff(self):
        """The claimed operator is still independently re-validated -- an
        id deactivated between queuing and replay is rejected the same as
        any other invalid operator, not trusted just because it's present."""
        former_staff = User.objects.create_user('former_cashier', password='pw', is_staff=True)
        former_profile = UserProfile.objects.create(user=former_staff, role='cashier')
        former_profile.set_pin('3392')
        former_profile.save()
        former_staff.is_active = False
        former_staff.save()

        self.client.login(username='cashier', password='pw')
        self.unlock_terminal()
        client_sale_id = str(uuid.uuid4())
        payload = {
            'client_sale_id': client_sale_id,
            'payments': [{'method': 'cash', 'amount': '250.00'}],
            'cart': [{'product_id': self.jar.id, 'qty': 1}],
            'queued_operator_id': former_staff.id,
        }
        response = self.client.post(reverse('v1_sale_list_create'), payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(POSSale.objects.filter(client_sale_id=client_sale_id).exists())

    def test_oversell_returns_409_naming_the_product(self):
        # jar isn't fixed_weight, so there's no is_available-syncs-from-
        # stock signal to catch this earlier (see the fixed_weight variant
        # test above, which hits a different, earlier check) -- this is the
        # actual InventoryMovement.clean() ledger-shortage path, the one
        # the offline queue's 'stockConflict' state (pos-offline-queue.js)
        # depends on for a message that actually names which line item is
        # the problem, not just a generic "not enough stock".
        self.client.login(username='cashier', password='pw')
        self.unlock_terminal()
        payload = {
            'client_sale_id': str(uuid.uuid4()),
            'payments': [{'method': 'cash', 'amount': '2750.00'}],  # 11 x 250, only 10 in stock
            'cart': [{'product_id': self.jar.id, 'qty': 11}],
        }
        response = self.client.post(reverse('v1_sale_list_create'), payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT, response.data)
        self.assertIn(self.jar.name, response.data['message'])
        self.assertFalse(POSSale.objects.filter(client_sale_id=payload['client_sale_id']).exists())

    def test_sale_list_requires_staff(self):
        response = self.client.get(reverse('v1_sale_list_create'))
        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

        self.client.login(username='cashier', password='pw')
        response = self.client.get(reverse('v1_sale_list_create'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_locking_terminal_blocks_further_sales_until_unlocked_again(self):
        self.client.login(username='cashier', password='pw')
        self.unlock_terminal()

        lock_response = self.client.post(reverse('v1_pos_lock'))
        self.assertEqual(lock_response.status_code, status.HTTP_200_OK)

        payload = {
            'client_sale_id': str(uuid.uuid4()),
            'payments': [{'method': 'cash', 'amount': '250.00'}],
            'cart': [{'product_id': self.jar.id, 'qty': 1}],
        }
        blocked = self.client.post(reverse('v1_sale_list_create'), payload, format='json')
        self.assertEqual(blocked.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(POSSale.objects.filter(client_sale_id=payload['client_sale_id']).exists())

        # Unlocking again (fresh PIN entry) restores the ability to sell.
        self.unlock_terminal()
        allowed = self.client.post(reverse('v1_sale_list_create'), payload, format='json')
        self.assertEqual(allowed.status_code, status.HTTP_201_CREATED, allowed.data)

    def test_split_cash_and_credit_sale_creates_payments_and_credit_transaction(self):
        self.client.login(username='cashier', password='pw')
        self.unlock_terminal()
        credit_customer = Customer.objects.create(name='Ram Bahadur', phone='9800000001')

        payload = {
            'client_sale_id': str(uuid.uuid4()),
            'customer_id': credit_customer.id,
            'payments': [
                {'method': 'cash', 'amount': '350.00'},
                {'method': 'credit', 'amount': '150.00'},
            ],
            'cart': [{'product_id': self.jar.id, 'qty': 2}],  # 2 x 250 = 500
        }
        response = self.client.post(reverse('v1_sale_list_create'), payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)

        sale = POSSale.objects.get(sale_number=response.data['sale_number'])
        self.assertEqual(sale.payment_method, 'split')
        self.assertEqual(sale.customer, credit_customer)
        self.assertEqual(sale.payments.count(), 2)
        self.assertEqual(
            {(p.method, p.amount) for p in sale.payments.all()},
            {('cash', Decimal('350.00')), ('credit', Decimal('150.00'))},
        )

        credit_txn = CreditTransaction.objects.get(related_pos_sale=sale)
        self.assertEqual(credit_txn.transaction_type, 'credit_sale')
        self.assertEqual(credit_txn.amount, Decimal('150.00'))
        self.assertEqual(credit_txn.customer, credit_customer)
        self.assertEqual(credit_txn.recorded_by, self.staff)
        self.assertEqual(credit_customer.outstanding_balance(), Decimal('150.00'))

    def test_credit_payment_without_customer_id_is_rejected(self):
        self.client.login(username='cashier', password='pw')
        self.unlock_terminal()
        payload = {
            'client_sale_id': str(uuid.uuid4()),
            'payments': [{'method': 'credit', 'amount': '250.00'}],
            'cart': [{'product_id': self.jar.id, 'qty': 1}],
        }
        response = self.client.post(reverse('v1_sale_list_create'), payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(POSSale.objects.filter(client_sale_id=payload['client_sale_id']).exists())
        self.assertFalse(CreditTransaction.objects.exists())

    def test_payments_not_summing_to_total_is_rejected_and_creates_nothing(self):
        self.client.login(username='cashier', password='pw')
        self.unlock_terminal()
        payload = {
            'client_sale_id': str(uuid.uuid4()),
            # Jar is 250, qty 1 -> total should be 250.00, not 200.00.
            'payments': [{'method': 'cash', 'amount': '200.00'}],
            'cart': [{'product_id': self.jar.id, 'qty': 1}],
        }
        response = self.client.post(reverse('v1_sale_list_create'), payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(POSSale.objects.filter(client_sale_id=payload['client_sale_id']).exists())
        # Nothing partially created -- stock untouched either.
        self.assertEqual(InventoryMovement.current_stock(self.jar), Decimal('10'))

    def test_exact_gram_scale_reading_not_a_multiple_of_10g_saves_end_to_end(self):
        """InventoryMovement.quantity supports 3 decimal places (not 2) so a
        real scale reading like 335g (0.335kg) -- the ordinary case for
        'exact' weight_entry_mode produce (Papaya, Dragon Fruit, Cauliflower,
        Coriander, Watermelon), not just round multiples of 10g -- is
        recorded exactly rather than failing full_clean() with 'no more than
        2 decimal places' at Complete Sale."""
        papaya = Product.objects.create(
            name='Papaya', slug='papaya', category=self.category, description='test',
            price=Decimal('200.00'), pricing_mode='variable_weight', weight_entry_mode='exact',
        )
        InventoryMovement.objects.create(
            product=papaya, movement_type='harvest', source='admin', quantity=Decimal('10.000'),
        )

        self.client.login(username='cashier', password='pw')
        self.unlock_terminal()
        payload = {
            'client_sale_id': str(uuid.uuid4()),
            # 200/kg x 0.335kg = 67.00 exactly -- no coupon/VAT/rounding
            # involved, isolating this test to the precision fix itself.
            'payments': [{'method': 'cash', 'amount': '67.00'}],
            'cart': [{'product_id': papaya.id, 'qty': 1, 'weight': '0.335'}],
        }
        response = self.client.post(reverse('v1_sale_list_create'), payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(Decimal(response.data['total']), Decimal('67.00'))

        self.assertEqual(InventoryMovement.current_stock(papaya), Decimal('9.665'))  # 10.000 - 0.335
        movement = InventoryMovement.objects.get(product=papaya, movement_type='sale')
        self.assertEqual(movement.quantity, Decimal('0.335'))

    def test_old_sale_without_payments_still_shows_sensible_payment_method(self):
        """A sale created before this feature existed has no POSSalePayment
        rows at all -- payment_method (still a real stored field, just no
        longer the source of truth for new sales) must keep showing
        whatever it was already set to."""
        old_sale = POSSale.objects.create(
            client_sale_id=str(uuid.uuid4()), cashier=self.staff, payment_method='esewa',
            cart_snapshot=[], total_amount=Decimal('500.00'),
        )
        self.assertEqual(old_sale.payment_method, 'esewa')
        self.assertEqual(old_sale.get_payment_method_display(), 'eSewa')
        self.assertEqual(old_sale.payments.count(), 0)


class POSVatApiTests(ApiTestBase):
    """POS Phase B — VAT scaffolding. Dormant by default (BusinessSettings.
    is_vat_enabled=False), regardless of any product's is_taxable flag."""

    def setUp(self):
        super().setUp()
        self.jar.is_taxable = True
        self.jar.save(update_fields=['is_taxable'])
        self.client.login(username='cashier', password='pw')
        self.unlock_terminal()

    def test_vat_disabled_by_default_exempt_equals_total(self):
        payload = {
            'client_sale_id': str(uuid.uuid4()),
            'payments': [{'method': 'cash', 'amount': '500.00'}],
            'cart': [{'product_id': self.jar.id, 'qty': 2}],  # is_taxable=True, but VAT is off
        }
        response = self.client.post(reverse('v1_sale_list_create'), payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)

        sale = POSSale.objects.get(sale_number=response.data['sale_number'])
        self.assertEqual(sale.exempt_value, Decimal('500.00'))
        self.assertEqual(sale.taxable_value, Decimal('0.00'))
        self.assertEqual(sale.vat_amount, Decimal('0.00'))

    def test_vat_enabled_splits_mixed_cart_correctly(self):
        BusinessSettings.objects.get_or_create(pk=1, defaults={'is_vat_enabled': True})
        BusinessSettings.get_solo()  # ensure the row exists
        BusinessSettings.objects.filter(pk=1).update(is_vat_enabled=True)

        # jar (taxable) 2 x 250 = 500; fruit (exempt, default) 2kg x 300 = 600
        payload = {
            'client_sale_id': str(uuid.uuid4()),
            'payments': [{'method': 'cash', 'amount': '1165.00'}],  # 500 + 600 + 13% of 500 (65)
            'cart': [
                {'product_id': self.jar.id, 'qty': 2},
                {'product_id': self.fruit.id, 'qty': 1, 'weight': '2.00'},
            ],
        }
        response = self.client.post(reverse('v1_sale_list_create'), payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)

        sale = POSSale.objects.get(sale_number=response.data['sale_number'])
        self.assertEqual(sale.taxable_value, Decimal('500.00'))
        self.assertEqual(sale.exempt_value, Decimal('600.00'))
        self.assertEqual(sale.vat_amount, Decimal('65.00'))
        self.assertEqual(sale.total_amount, Decimal('1165.00'))

    def test_toggling_vat_does_not_retroactively_change_completed_sales(self):
        payload = {
            'client_sale_id': str(uuid.uuid4()),
            'payments': [{'method': 'cash', 'amount': '500.00'}],
            'cart': [{'product_id': self.jar.id, 'qty': 2}],
        }
        response = self.client.post(reverse('v1_sale_list_create'), payload, format='json')
        sale = POSSale.objects.get(sale_number=response.data['sale_number'])
        self.assertEqual(sale.exempt_value, Decimal('500.00'))
        self.assertEqual(sale.taxable_value, Decimal('0.00'))

        # Flip VAT on *after* the sale already exists.
        BusinessSettings.objects.update_or_create(pk=1, defaults={'is_vat_enabled': True})

        sale.refresh_from_db()
        self.assertEqual(sale.exempt_value, Decimal('500.00'))
        self.assertEqual(sale.taxable_value, Decimal('0.00'))
        self.assertEqual(sale.vat_amount, Decimal('0.00'))


class POSCouponApiTests(ApiTestBase):
    """POS Phase C — coupon discounts at checkout. Reuses the website's
    Coupon model (resolve_pos_coupon() in shop/views.py) rather than a
    parallel POS-only discount system."""

    def setUp(self):
        super().setUp()
        self.client.login(username='cashier', password='pw')
        self.unlock_terminal()
        now = timezone.now()
        self.percent_coupon = Coupon.objects.create(
            code='dashain25', discount_type='percent', discount_value=Decimal('25'),
            start_date=now - timedelta(days=1), end_date=now + timedelta(days=1),
        )
        self.fixed_coupon = Coupon.objects.create(
            code='FLAT50', discount_type='fixed', discount_value=Decimal('50'),
            start_date=now - timedelta(days=1), end_date=now + timedelta(days=1),
        )

    def test_validate_endpoint_requires_staff(self):
        self.client.logout()
        response = self.client.post(
            reverse('v1_pos_coupon_validate'), {'code': 'DASHAIN25', 'subtotal': '500.00'}, format='json',
        )
        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_validate_endpoint_returns_discount_without_side_effects(self):
        response = self.client.post(
            reverse('v1_pos_coupon_validate'), {'code': 'dashain25', 'subtotal': '500.00'}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data['code'], 'DASHAIN25')  # normalized uppercase, same as website checkout
        self.assertEqual(Decimal(response.data['discount_amount']), Decimal('125.00'))  # 25% of 500

        self.percent_coupon.refresh_from_db()
        self.assertEqual(self.percent_coupon.used_count, 0)  # preview only -- never consumes a use

    def test_validate_endpoint_rejects_unknown_code(self):
        response = self.client.post(
            reverse('v1_pos_coupon_validate'), {'code': 'NOPE', 'subtotal': '500.00'}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_validate_endpoint_enforces_min_order_amount(self):
        self.fixed_coupon.min_order_amount = Decimal('1000.00')
        self.fixed_coupon.save(update_fields=['min_order_amount'])
        response = self.client.post(
            reverse('v1_pos_coupon_validate'), {'code': 'FLAT50', 'subtotal': '500.00'}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_expired_coupon_at_replay_is_tagged_offer_unavailable(self):
        """Simulates a replayed offline-queued sale whose coupon expired
        between queueing and sync -- the offline queue needs reason to be
        'offer_unavailable' to show a distinct message, not a generic one."""
        self.percent_coupon.end_date = timezone.now() - timedelta(hours=1)
        self.percent_coupon.save(update_fields=['end_date'])
        payload = {
            'client_sale_id': str(uuid.uuid4()),
            'coupon_code': 'dashain25',
            'payments': [{'method': 'cash', 'amount': '375.00'}],
            'cart': [{'product_id': self.jar.id, 'qty': 2}],
        }
        response = self.client.post(reverse('v1_sale_list_create'), payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data['reason'], 'offer_unavailable')

    def test_sale_with_percent_coupon_reduces_total_and_increments_used_count(self):
        payload = {
            'client_sale_id': str(uuid.uuid4()),
            'coupon_code': 'dashain25',
            # 2 jars @ 250 = 500, minus 25% (125) = 375
            'payments': [{'method': 'cash', 'amount': '375.00'}],
            'cart': [{'product_id': self.jar.id, 'qty': 2}],
        }
        response = self.client.post(reverse('v1_sale_list_create'), payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(Decimal(response.data['total']), Decimal('375.00'))
        self.assertEqual(Decimal(response.data['discount_amount']), Decimal('125.00'))

        sale = POSSale.objects.get(sale_number=response.data['sale_number'])
        self.assertEqual(sale.coupon, self.percent_coupon)
        self.assertEqual(sale.discount_amount, Decimal('125.00'))
        self.assertEqual(sale.total_amount, Decimal('375.00'))

        self.percent_coupon.refresh_from_db()
        self.assertEqual(self.percent_coupon.used_count, 1)

    def test_sale_with_fixed_coupon_and_payments_matching_pre_discount_total_is_rejected(self):
        payload = {
            'client_sale_id': str(uuid.uuid4()),
            'coupon_code': 'FLAT50',
            # Jar is 250; staff forgot to apply the 50 discount to the payment amount.
            'payments': [{'method': 'cash', 'amount': '250.00'}],
            'cart': [{'product_id': self.jar.id, 'qty': 1}],
        }
        response = self.client.post(reverse('v1_sale_list_create'), payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(POSSale.objects.filter(client_sale_id=payload['client_sale_id']).exists())

    def test_sale_with_expired_coupon_is_rejected_and_creates_nothing(self):
        now = timezone.now()
        self.percent_coupon.end_date = now - timedelta(hours=1)
        self.percent_coupon.save(update_fields=['end_date'])
        payload = {
            'client_sale_id': str(uuid.uuid4()),
            'coupon_code': 'dashain25',
            'payments': [{'method': 'cash', 'amount': '187.50'}],
            'cart': [{'product_id': self.jar.id, 'qty': 1}],
        }
        response = self.client.post(reverse('v1_sale_list_create'), payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(POSSale.objects.filter(client_sale_id=payload['client_sale_id']).exists())
        self.assertEqual(InventoryMovement.current_stock(self.jar), Decimal('10'))  # untouched

    def test_sale_with_unknown_coupon_code_is_rejected(self):
        payload = {
            'client_sale_id': str(uuid.uuid4()),
            'coupon_code': 'MADEUP',
            'payments': [{'method': 'cash', 'amount': '250.00'}],
            'cart': [{'product_id': self.jar.id, 'qty': 1}],
        }
        response = self.client.post(reverse('v1_sale_list_create'), payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_idempotent_replay_does_not_double_increment_used_count(self):
        client_sale_id = str(uuid.uuid4())
        payload = {
            'client_sale_id': client_sale_id,
            'coupon_code': 'dashain25',
            # 250 - 25% = 187.50 -- rounds UP to 188 (POS cash rounding, ROUND_HALF_UP).
            'payments': [{'method': 'cash', 'amount': '188.00'}],
            'cart': [{'product_id': self.jar.id, 'qty': 1}],
        }
        first = self.client.post(reverse('v1_sale_list_create'), payload, format='json')
        second = self.client.post(reverse('v1_sale_list_create'), payload, format='json')
        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        self.assertEqual(second.status_code, status.HTTP_200_OK)

        self.percent_coupon.refresh_from_db()
        self.assertEqual(self.percent_coupon.used_count, 1)

    def test_coupon_only_discounts_non_offer_lines(self):
        # Fruit (300/kg * 0.5kg = 150) is under a 10%-off offer -> 135,
        # untouched by the coupon. Jar (250, plain) is the only line the
        # 25%-off coupon can see -- eligible subtotal is 250, not 400.
        offer = Offer.objects.create(
            title='Mango Sale', discount_type='percent', discount_value=Decimal('10'),
            start_date=timezone.now() - timedelta(days=1), end_date=timezone.now() + timedelta(days=1),
        )
        offer.products.add(self.fruit)
        payload = {
            'client_sale_id': str(uuid.uuid4()),
            'coupon_code': 'dashain25',
            # 135 (offer line, untouched) + 187.50 (250 jar - 25%) = 322.50 -> rounds to 323
            'payments': [{'method': 'cash', 'amount': '323.00'}],
            'cart': [
                {'product_id': self.fruit.id, 'weight': '0.50', 'offer_id': offer.id},
                {'product_id': self.jar.id, 'qty': 1},
            ],
        }
        response = self.client.post(reverse('v1_sale_list_create'), payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(Decimal(response.data['total']), Decimal('323.00'))
        # 25% of the jar's 250 alone, not 25% of (135 + 250) = 385
        self.assertEqual(Decimal(response.data['discount_amount']), Decimal('62.50'))

    def test_coupon_on_an_all_offer_cart_is_not_blocked_and_applies_no_discount(self):
        # Every line is already offer-discounted -- nothing left for the
        # coupon to discount. In the real UI the Apply button refuses this
        # before ever sending the coupon_code (couponEligibleSubtotal() is
        # 0), so reaching create_pos_sale() this way is a direct-API edge
        # case; it must still complete the sale rather than reject it.
        offer = Offer.objects.create(
            title='Mango Sale', discount_type='percent', discount_value=Decimal('10'),
            start_date=timezone.now() - timedelta(days=1), end_date=timezone.now() + timedelta(days=1),
        )
        offer.products.add(self.fruit)
        payload = {
            'client_sale_id': str(uuid.uuid4()),
            'coupon_code': 'dashain25',
            'payments': [{'method': 'cash', 'amount': '135.00'}],  # 150 - 10% offer, no coupon discount
            'cart': [{'product_id': self.fruit.id, 'weight': '0.50', 'offer_id': offer.id}],
        }
        response = self.client.post(reverse('v1_sale_list_create'), payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(Decimal(response.data['total']), Decimal('135.00'))
        self.assertEqual(Decimal(response.data['discount_amount']), Decimal('0'))
        self.assertIsNone(POSSale.objects.get(sale_number=response.data['sale_number']).coupon)

    def test_coupon_discount_reduces_taxable_value_proportionally_under_vat(self):
        BusinessSettings.objects.update_or_create(pk=1, defaults={'is_vat_enabled': True})
        self.jar.is_taxable = True
        self.jar.save(update_fields=['is_taxable'])

        # jar (taxable) 2 x 250 = 500; fruit (exempt) 2kg x 300 = 600. Subtotal 1100.
        # FLAT50 knocks 50 off the subtotal -> discounted 1050, split proportionally:
        # taxable share = 500/1100 * 1050 = 477.27, exempt = 572.73, VAT = 13% of 477.27 = 62.05
        # Pre-round total 1112.05 rounds DOWN to 1112 (POS cash rounding) --
        # the detailed breakdown above stays exactly as computed either way.
        payload = {
            'client_sale_id': str(uuid.uuid4()),
            'coupon_code': 'FLAT50',
            'payments': [{'method': 'cash', 'amount': '1112.00'}],
            'cart': [
                {'product_id': self.jar.id, 'qty': 2},
                {'product_id': self.fruit.id, 'qty': 1, 'weight': '2.00'},
            ],
        }
        response = self.client.post(reverse('v1_sale_list_create'), payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)

        sale = POSSale.objects.get(sale_number=response.data['sale_number'])
        self.assertEqual(sale.discount_amount, Decimal('50.00'))
        self.assertEqual(sale.taxable_value, Decimal('477.27'))
        self.assertEqual(sale.exempt_value, Decimal('572.73'))
        self.assertEqual(sale.vat_amount, Decimal('62.05'))
        self.assertEqual(sale.total_amount, Decimal('1112.00'))
        self.assertEqual(sale.round_off_amount, Decimal('-0.05'))


class POSRoundOffApiTests(ApiTestBase):
    """POS cash rounding: the grand total is rounded to the nearest rupee
    (round-half-up, not Python's banker's-rounding round()) and the
    difference is stored in round_off_amount — see create_pos_sale() in
    shop/views.py, right before the payments_sum check."""

    def setUp(self):
        super().setUp()
        self.client.login(username='cashier', password='pw')
        self.unlock_terminal()
        # A dedicated weight-based product priced so that ordinary
        # 2-decimal-place weights (InventoryMovement.quantity only supports
        # 2dp) still land on a fractional rupee total -- self.fruit's 300/kg
        # price is a round multiple of 100, so any 2dp weight against it
        # always lands on a whole number of rupees, which can't exercise
        # rounding at all.
        self.round_test_product = Product.objects.create(
            name='Round Test Produce', slug='round-test-produce', category=self.category,
            description='test', price=Decimal('233.00'), pricing_mode='variable_weight',
        )
        InventoryMovement.objects.create(
            product=self.round_test_product, movement_type='harvest', source='admin', quantity=Decimal('20.00'),
        )

    def test_total_rounds_up_at_exact_half_rupee(self):
        # 233/kg x 0.50kg = 116.50 exactly -- ROUND_HALF_UP must round up to
        # 117, not down to 116 the way Python's bare round() would (banker's
        # rounding rounds .50 to the nearest EVEN integer).
        payload = {
            'client_sale_id': str(uuid.uuid4()),
            'payments': [{'method': 'cash', 'amount': '117.00'}],
            'cart': [{'product_id': self.round_test_product.id, 'qty': 1, 'weight': '0.50'}],
        }
        response = self.client.post(reverse('v1_sale_list_create'), payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(Decimal(response.data['total']), Decimal('117.00'))
        self.assertEqual(Decimal(response.data['round_off_amount']), Decimal('0.50'))

        sale = POSSale.objects.get(sale_number=response.data['sale_number'])
        self.assertEqual(sale.total_amount, Decimal('117.00'))
        self.assertEqual(sale.round_off_amount, Decimal('0.50'))

    def test_total_rounds_down_below_half_rupee(self):
        # 233/kg x 0.43kg = 100.19 -- rounds down to 100.
        payload = {
            'client_sale_id': str(uuid.uuid4()),
            'payments': [{'method': 'cash', 'amount': '100.00'}],
            'cart': [{'product_id': self.round_test_product.id, 'qty': 1, 'weight': '0.43'}],
        }
        response = self.client.post(reverse('v1_sale_list_create'), payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(Decimal(response.data['total']), Decimal('100.00'))
        self.assertEqual(Decimal(response.data['round_off_amount']), Decimal('-0.19'))

    def test_whole_rupee_total_has_zero_round_off(self):
        payload = {
            'client_sale_id': str(uuid.uuid4()),
            'payments': [{'method': 'cash', 'amount': '250.00'}],
            'cart': [{'product_id': self.jar.id, 'qty': 1}],
        }
        response = self.client.post(reverse('v1_sale_list_create'), payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(Decimal(response.data['round_off_amount']), Decimal('0.00'))

    def test_payment_must_match_rounded_total_not_the_precise_total(self):
        # Paying the PRE-round figure (116.50) must be rejected now that the
        # server requires the rounded whole-rupee total (117) instead.
        payload = {
            'client_sale_id': str(uuid.uuid4()),
            'payments': [{'method': 'cash', 'amount': '116.50'}],
            'cart': [{'product_id': self.round_test_product.id, 'qty': 1, 'weight': '0.50'}],
        }
        response = self.client.post(reverse('v1_sale_list_create'), payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(POSSale.objects.filter(client_sale_id=payload['client_sale_id']).exists())

    def test_round_off_combined_with_coupon_discount(self):
        now = timezone.now()
        coupon = Coupon.objects.create(
            code='SAVE10', discount_type='fixed', discount_value=Decimal('10'),
            start_date=now - timedelta(days=1), end_date=now + timedelta(days=1),
        )
        # 233/kg x 0.50kg = 116.50, minus Rs.10 fixed = 106.50 -- rounds up to 107.
        payload = {
            'client_sale_id': str(uuid.uuid4()),
            'coupon_code': 'SAVE10',
            'payments': [{'method': 'cash', 'amount': '107.00'}],
            'cart': [{'product_id': self.round_test_product.id, 'qty': 1, 'weight': '0.50'}],
        }
        response = self.client.post(reverse('v1_sale_list_create'), payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)

        sale = POSSale.objects.get(sale_number=response.data['sale_number'])
        self.assertEqual(sale.discount_amount, Decimal('10.00'))  # discount itself stays precise
        self.assertEqual(sale.total_amount, Decimal('107.00'))
        self.assertEqual(sale.round_off_amount, Decimal('0.50'))
        coupon.refresh_from_db()
        self.assertEqual(coupon.used_count, 1)

    def test_round_off_combined_with_vat_leaves_breakdown_precise(self):
        BusinessSettings.objects.update_or_create(pk=1, defaults={'is_vat_enabled': True})
        self.jar.is_taxable = True
        self.jar.save(update_fields=['is_taxable'])

        # 250.00 + 13% VAT (32.50) = 282.50 exactly -- rounds up to 283.
        payload = {
            'client_sale_id': str(uuid.uuid4()),
            'payments': [{'method': 'cash', 'amount': '283.00'}],
            'cart': [{'product_id': self.jar.id, 'qty': 1}],
        }
        response = self.client.post(reverse('v1_sale_list_create'), payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)

        sale = POSSale.objects.get(sale_number=response.data['sale_number'])
        # The detailed tax breakdown stays exactly as computed (unrounded) --
        # only total_amount and round_off_amount change.
        self.assertEqual(sale.taxable_value, Decimal('250.00'))
        self.assertEqual(sale.vat_amount, Decimal('32.50'))
        self.assertEqual(sale.exempt_value, Decimal('0.00'))
        self.assertEqual(sale.total_amount, Decimal('283.00'))
        self.assertEqual(sale.round_off_amount, Decimal('0.50'))
        # taxable + exempt + vat (282.50) != total_amount (283) -- the gap
        # is exactly round_off_amount, not silently lost or double-counted.
        self.assertEqual(
            sale.taxable_value + sale.exempt_value + sale.vat_amount + sale.round_off_amount,
            sale.total_amount,
        )


class POSOfferApiTests(ApiTestBase):
    """POS Phase D — offers at checkout. Single-product percent/fixed
    discounts are an ordinary cart line at a discounted price
    (resolve_pos_offer_discount()); combo deals expand into one ordinary
    cart line per BundleItem, all sharing a combo_instance_id
    (resolve_pos_combo_lines()) — see shop/views.py for why BundleItem's
    lack of a variant_id meant pure "expand and reuse per-product movement
    logic" needed the fixed_weight slot to carry a real picked variant_id
    on its cart line, same as any other fixed_weight purchase."""

    def setUp(self):
        super().setUp()
        self.client.login(username='cashier', password='pw')
        self.unlock_terminal()
        now = timezone.now()
        self.percent_offer = Offer.objects.create(
            title='Jar Sale', discount_type='percent', discount_value=Decimal('20'),
            start_date=now - timedelta(days=1), end_date=now + timedelta(days=1),
        )
        self.percent_offer.products.add(self.jar)

    def test_single_product_offer_discount_is_applied_and_revalidated(self):
        # 20% off Rs.250 = Rs.200.
        payload = {
            'client_sale_id': str(uuid.uuid4()),
            'payments': [{'method': 'cash', 'amount': '200.00'}],
            'cart': [{'product_id': self.jar.id, 'qty': 1, 'offer_id': self.percent_offer.id}],
        }
        response = self.client.post(reverse('v1_sale_list_create'), payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(Decimal(response.data['total']), Decimal('200.00'))
        self.assertEqual(InventoryMovement.current_stock(self.jar), Decimal('9'))  # 10 - 1, stock unaffected by offers

    def test_expired_offer_at_replay_is_tagged_offer_unavailable(self):
        """Simulates the exact scenario a replayed offline-queued sale hits
        if the offer expired between when it was rung up and when it
        syncs: resolve_pos_offer_discount() rejects it, and the response
        must carry reason='offer_unavailable' so the offline queue
        (pos-offline-queue.js) can give this its own distinct state instead
        of lumping it in with an unrelated validation failure."""
        self.percent_offer.end_date = timezone.now() - timedelta(hours=1)
        self.percent_offer.save(update_fields=['end_date'])
        payload = {
            'client_sale_id': str(uuid.uuid4()),
            'payments': [{'method': 'cash', 'amount': '200.00'}],
            'cart': [{'product_id': self.jar.id, 'qty': 1, 'offer_id': self.percent_offer.id}],
        }
        response = self.client.post(reverse('v1_sale_list_create'), payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data['reason'], 'offer_unavailable')

    def test_client_sent_price_is_never_trusted_only_offer_id_matters(self):
        # Posting qty=2 still charges 2 x the server-recomputed discounted
        # price, regardless of what a tampered client might imply elsewhere.
        payload = {
            'client_sale_id': str(uuid.uuid4()),
            'payments': [{'method': 'cash', 'amount': '400.00'}],  # 2 x 200
            'cart': [{'product_id': self.jar.id, 'qty': 2, 'offer_id': self.percent_offer.id}],
        }
        response = self.client.post(reverse('v1_sale_list_create'), payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(Decimal(response.data['total']), Decimal('400.00'))

    def test_expired_offer_is_rejected(self):
        self.percent_offer.end_date = timezone.now() - timedelta(hours=1)
        self.percent_offer.save(update_fields=['end_date'])
        payload = {
            'client_sale_id': str(uuid.uuid4()),
            'payments': [{'method': 'cash', 'amount': '200.00'}],
            'cart': [{'product_id': self.jar.id, 'qty': 1, 'offer_id': self.percent_offer.id}],
        }
        response = self.client.post(reverse('v1_sale_list_create'), payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(POSSale.objects.filter(client_sale_id=payload['client_sale_id']).exists())

    def test_offer_not_covering_this_product_is_rejected(self):
        payload = {
            'client_sale_id': str(uuid.uuid4()),
            'payments': [{'method': 'cash', 'amount': '240.00'}],  # fruit isn't discounted
            'cart': [{'product_id': self.fruit.id, 'qty': 1, 'weight': '0.80', 'offer_id': self.percent_offer.id}],
        }
        response = self.client.post(reverse('v1_sale_list_create'), payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_fixed_weight_offer_discounts_each_variant_from_its_own_price(self):
        # Two animals of the same product at very different prices -- the
        # discount must be recomputed against whichever one was actually
        # picked, not a single flat number shared across every variant
        # (the bug this test guards against: discounting once from
        # product.price or from only the first variant checked).
        light_goat = ProductVariant.objects.create(product=self.goat, weight=Decimal('10.00'))  # 10kg * 1200 = 12000
        heavy_goat = ProductVariant.objects.create(product=self.goat, weight=Decimal('30.00'))  # 30kg * 1200 = 36000
        for variant in (light_goat, heavy_goat):
            InventoryMovement.objects.create(
                product=self.goat, variant=variant, movement_type='harvest', source='admin', quantity=Decimal('1'),
            )
        offer = Offer.objects.create(
            title='Goat Sale', discount_type='percent', discount_value=Decimal('10'),
            start_date=timezone.now() - timedelta(days=1), end_date=timezone.now() + timedelta(days=1),
        )
        offer.products.add(self.goat)

        light_payload = {
            'client_sale_id': str(uuid.uuid4()),
            'payments': [{'method': 'cash', 'amount': '10800.00'}],  # 12000 - 10%
            'cart': [{'product_id': self.goat.id, 'variant_id': light_goat.id, 'offer_id': offer.id}],
        }
        response = self.client.post(reverse('v1_sale_list_create'), light_payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(Decimal(response.data['total']), Decimal('10800.00'))

        heavy_payload = {
            'client_sale_id': str(uuid.uuid4()),
            'payments': [{'method': 'cash', 'amount': '32400.00'}],  # 36000 - 10%
            'cart': [{'product_id': self.goat.id, 'variant_id': heavy_goat.id, 'offer_id': offer.id}],
        }
        response = self.client.post(reverse('v1_sale_list_create'), heavy_payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(Decimal(response.data['total']), Decimal('32400.00'))

    def _create_combo(self, combo_price):
        combo = Offer.objects.create(
            title='Dashain Special', discount_type='combo', discount_value=Decimal('0'),
            combo_price=Decimal(combo_price),
            start_date=timezone.now() - timedelta(days=1), end_date=timezone.now() + timedelta(days=1),
        )
        BundleItem.objects.create(offer=combo, product=self.fruit, quantity=Decimal('2.00'))   # 2kg x 300 = 600
        BundleItem.objects.create(offer=combo, product=self.jar, quantity=Decimal('3'))         # 3 x 250 = 750
        BundleItem.objects.create(offer=combo, product=self.goat, quantity=Decimal('20.00'))    # natural 20 x 1200 = 24000
        return combo

    def test_combo_expands_into_one_line_per_bundle_item_summing_to_combo_price(self):
        combo = self._create_combo('20000.00')
        combo_instance_id = str(uuid.uuid4())
        payload = {
            'client_sale_id': str(uuid.uuid4()),
            'payments': [{'method': 'cash', 'amount': '20000.00'}],
            'cart': [
                {'product_id': self.fruit.id, 'qty': 1, 'weight': '2.00', 'offer_id': combo.id, 'combo_instance_id': combo_instance_id},
                {'product_id': self.jar.id, 'qty': 3, 'offer_id': combo.id, 'combo_instance_id': combo_instance_id},
                {'product_id': self.goat.id, 'qty': 1, 'variant_id': self.goat_variant.id, 'offer_id': combo.id, 'combo_instance_id': combo_instance_id},
            ],
        }
        response = self.client.post(reverse('v1_sale_list_create'), payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(Decimal(response.data['total']), Decimal('20000.00'))

        # Stock deducted normally for every line -- offers only override price.
        self.assertEqual(InventoryMovement.current_stock(self.fruit), Decimal('18.00'))  # 20 - 2
        self.assertEqual(InventoryMovement.current_stock(self.jar), Decimal('7'))        # 10 - 3
        self.assertEqual(InventoryMovement.current_stock(self.goat, variant=self.goat_variant), Decimal('0'))  # 1 - 1

        sale = POSSale.objects.get(sale_number=response.data['sale_number'])
        self.assertEqual(len(sale.cart_snapshot), 3)
        for line in sale.cart_snapshot:
            self.assertEqual(line['combo_instance_id'], combo_instance_id)
            self.assertEqual(line['offer_id'], combo.id)

    def test_combo_rejects_incomplete_bundle(self):
        combo = self._create_combo('20000.00')
        combo_instance_id = str(uuid.uuid4())
        # Missing the goat line entirely.
        payload = {
            'client_sale_id': str(uuid.uuid4()),
            'payments': [{'method': 'cash', 'amount': '1000.00'}],
            'cart': [
                {'product_id': self.fruit.id, 'qty': 1, 'weight': '2.00', 'offer_id': combo.id, 'combo_instance_id': combo_instance_id},
                {'product_id': self.jar.id, 'qty': 3, 'offer_id': combo.id, 'combo_instance_id': combo_instance_id},
            ],
        }
        response = self.client.post(reverse('v1_sale_list_create'), payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(POSSale.objects.filter(client_sale_id=payload['client_sale_id']).exists())

    def test_combo_rejects_a_product_not_in_the_bundle(self):
        combo = self._create_combo('20000.00')
        combo_instance_id = str(uuid.uuid4())
        other_product = Product.objects.create(
            name='Honey', slug='honey', category=self.category, description='Honey',
            price=Decimal('800.00'), pricing_mode='variable_weight',
        )
        payload = {
            'client_sale_id': str(uuid.uuid4()),
            'payments': [{'method': 'cash', 'amount': '20000.00'}],
            'cart': [
                {'product_id': self.fruit.id, 'qty': 1, 'weight': '2.00', 'offer_id': combo.id, 'combo_instance_id': combo_instance_id},
                {'product_id': self.jar.id, 'qty': 3, 'offer_id': combo.id, 'combo_instance_id': combo_instance_id},
                {'product_id': other_product.id, 'qty': 1, 'weight': '1.00', 'offer_id': combo.id, 'combo_instance_id': combo_instance_id},
            ],
        }
        response = self.client.post(reverse('v1_sale_list_create'), payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_combo_rejects_when_offer_no_longer_live(self):
        combo = self._create_combo('20000.00')
        combo.end_date = timezone.now() - timedelta(hours=1)
        combo.save(update_fields=['end_date'])
        combo_instance_id = str(uuid.uuid4())
        payload = {
            'client_sale_id': str(uuid.uuid4()),
            'payments': [{'method': 'cash', 'amount': '20000.00'}],
            'cart': [
                {'product_id': self.fruit.id, 'qty': 1, 'weight': '2.00', 'offer_id': combo.id, 'combo_instance_id': combo_instance_id},
                {'product_id': self.jar.id, 'qty': 3, 'offer_id': combo.id, 'combo_instance_id': combo_instance_id},
                {'product_id': self.goat.id, 'qty': 1, 'variant_id': self.goat_variant.id, 'offer_id': combo.id, 'combo_instance_id': combo_instance_id},
            ],
        }
        response = self.client.post(reverse('v1_sale_list_create'), payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(POSSale.objects.filter(client_sale_id=payload['client_sale_id']).exists())

    def test_pos_offers_list_endpoint_returns_live_discount_and_combo_offers(self):
        combo = self._create_combo('20000.00')
        response = self.client.get(reverse('v1_pos_offers_list'))
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        discount_ids = [o['offer_id'] for o in response.data['discount_offers']]
        combo_ids = [o['offer_id'] for o in response.data['combo_offers']]
        self.assertIn(self.percent_offer.id, discount_ids)
        self.assertIn(combo.id, combo_ids)

        combo_entry = next(o for o in response.data['combo_offers'] if o['offer_id'] == combo.id)
        self.assertEqual(len(combo_entry['items']), 3)
        self.assertTrue(combo_entry['fully_available'])
        goat_item = next(i for i in combo_entry['items'] if i['product_id'] == self.goat.id)
        self.assertEqual(len(goat_item['available_variants']), 1)

    def test_pos_offers_list_requires_staff(self):
        self.client.logout()
        response = self.client.get(reverse('v1_pos_offers_list'))
        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_combo_marked_not_fully_available_when_fixed_weight_item_has_no_stock(self):
        combo = self._create_combo('20000.00')
        self.goat_variant.is_available = False
        self.goat_variant.save(update_fields=['is_available'])
        response = self.client.get(reverse('v1_pos_offers_list'))
        combo_entry = next(o for o in response.data['combo_offers'] if o['offer_id'] == combo.id)
        self.assertFalse(combo_entry['fully_available'])

    def _create_combo_with_goat_variants(self, combo_price, reference_weight):
        """Same shape as _create_combo(), but the goat slot gets two extra
        variants (a cheaper 10kg and a pricier 30kg, alongside the existing
        20kg self.goat_variant) so upcharge math actually has something to
        bite on, and an explicit reference_weight instead of whatever
        auto-fills."""
        light = ProductVariant.objects.create(product=self.goat, weight=Decimal('10.00'))  # 10 x 1200 = 12000
        InventoryMovement.objects.create(
            product=self.goat, variant=light, movement_type='harvest', source='admin', quantity=Decimal('1'),
        )
        heavy = ProductVariant.objects.create(product=self.goat, weight=Decimal('30.00'))  # 30 x 1200 = 36000
        InventoryMovement.objects.create(
            product=self.goat, variant=heavy, movement_type='harvest', source='admin', quantity=Decimal('1'),
        )
        combo = Offer.objects.create(
            title='Goat Combo', discount_type='combo', discount_value=Decimal('0'),
            combo_price=Decimal(combo_price),
            start_date=timezone.now() - timedelta(days=1), end_date=timezone.now() + timedelta(days=1),
        )
        BundleItem.objects.create(offer=combo, product=self.fruit, quantity=Decimal('2.00'))
        BundleItem.objects.create(offer=combo, product=self.jar, quantity=Decimal('3'))
        goat_item = BundleItem.objects.create(
            offer=combo, product=self.goat, quantity=Decimal('10.00'), reference_weight=reference_weight,
        )
        return combo, goat_item, light, heavy

    def _combo_payload(self, combo, variant):
        combo_instance_id = str(uuid.uuid4())
        return combo_instance_id, {
            'client_sale_id': str(uuid.uuid4()),
            'cart': [
                {'product_id': self.fruit.id, 'qty': 1, 'weight': '2.00', 'offer_id': combo.id, 'combo_instance_id': combo_instance_id},
                {'product_id': self.jar.id, 'qty': 3, 'offer_id': combo.id, 'combo_instance_id': combo_instance_id},
                {'product_id': self.goat.id, 'qty': 1, 'variant_id': variant.id, 'offer_id': combo.id, 'combo_instance_id': combo_instance_id},
            ],
        }

    def test_combo_fixed_weight_zero_upcharge_at_reference_weight(self):
        # self.goat_variant is exactly 20kg -- picking it when reference_weight
        # is also 20kg costs exactly the plain combo price, no add-on.
        combo, goat_item, light, heavy = self._create_combo_with_goat_variants('20000.00', Decimal('20.00'))
        combo_instance_id, payload = self._combo_payload(combo, self.goat_variant)
        payload['payments'] = [{'method': 'cash', 'amount': '20000.00'}]
        response = self.client.post(reverse('v1_sale_list_create'), payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(Decimal(response.data['total']), Decimal('20000.00'))

    def test_combo_fixed_weight_upcharge_for_heavier_variant(self):
        # 30kg (36000) vs the 20kg (24000) reference -- +12000 on top of the
        # plain combo price.
        combo, goat_item, light, heavy = self._create_combo_with_goat_variants('20000.00', Decimal('20.00'))
        combo_instance_id, payload = self._combo_payload(combo, heavy)
        payload['payments'] = [{'method': 'cash', 'amount': '32000.00'}]  # 20000 + 12000
        response = self.client.post(reverse('v1_sale_list_create'), payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(Decimal(response.data['total']), Decimal('32000.00'))

    def test_combo_fixed_weight_lighter_variant_is_never_a_discount(self):
        # 10kg (12000) is CHEAPER than the 20kg (24000) reference -- still
        # costs the full plain combo price, never a discount for picking
        # smaller.
        combo, goat_item, light, heavy = self._create_combo_with_goat_variants('20000.00', Decimal('20.00'))
        combo_instance_id, payload = self._combo_payload(combo, light)
        payload['payments'] = [{'method': 'cash', 'amount': '20000.00'}]
        response = self.client.post(reverse('v1_sale_list_create'), payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(Decimal(response.data['total']), Decimal('20000.00'))

    def test_combo_fixed_weight_falls_back_to_cheapest_when_reference_variant_gone(self):
        # reference_weight points at 20kg, but that exact animal has since
        # sold -- falls back to the cheapest currently-available variant
        # (10kg/12000) as the reference instead of erroring out, so picking
        # the 30kg/36000 one now upcharges +24000 (against 12000, not 24000).
        combo, goat_item, light, heavy = self._create_combo_with_goat_variants('20000.00', Decimal('20.00'))
        self.goat_variant.is_available = False
        self.goat_variant.save(update_fields=['is_available'])
        combo_instance_id, payload = self._combo_payload(combo, heavy)
        payload['payments'] = [{'method': 'cash', 'amount': '44000.00'}]  # 20000 + (36000 - 12000)
        response = self.client.post(reverse('v1_sale_list_create'), payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(Decimal(response.data['total']), Decimal('44000.00'))

    def test_combo_fixed_weight_upcharge_cannot_be_bypassed_by_underpaying(self):
        # Picking the heavier animal but only paying the plain combo price
        # (as if the upcharge didn't apply) must be rejected -- proves the
        # upcharge is actually enforced server-side, not a client-optional
        # extra.
        combo, goat_item, light, heavy = self._create_combo_with_goat_variants('20000.00', Decimal('20.00'))
        combo_instance_id, payload = self._combo_payload(combo, heavy)
        payload['payments'] = [{'method': 'cash', 'amount': '20000.00'}]  # missing the +12000 upcharge
        response = self.client.post(reverse('v1_sale_list_create'), payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(POSSale.objects.filter(client_sale_id=payload['client_sale_id']).exists())

    def test_pos_offers_list_includes_per_variant_upcharge(self):
        combo, goat_item, light, heavy = self._create_combo_with_goat_variants('20000.00', Decimal('20.00'))
        response = self.client.get(reverse('v1_pos_offers_list'))
        combo_entry = next(o for o in response.data['combo_offers'] if o['offer_id'] == combo.id)
        goat_item_entry = next(i for i in combo_entry['items'] if i['product_id'] == self.goat.id)
        by_id = {v['id']: v for v in goat_item_entry['available_variants']}
        self.assertEqual(Decimal(by_id[self.goat_variant.id]['upcharge']), Decimal('0'))
        self.assertEqual(Decimal(by_id[light.id]['upcharge']), Decimal('0'))  # cheaper -- never negative
        self.assertEqual(Decimal(by_id[heavy.id]['upcharge']), Decimal('12000'))

    def test_combo_with_zero_variant_fixed_weight_item_is_rejected_cleanly(self):
        # Product.fixed_weight/locked_total_price() is a WEBSITE-only display
        # fallback for a fixed_weight product that has no ProductVariant rows
        # yet (see resolve_cart_line() in this same file) -- the POS, combo
        # or not, has always required a real variant row to actually sell
        # one. This must stay a clean rejection, never a silent price off
        # the base rate and never a crash.
        bare_goat = Product.objects.create(
            name='Bare Goat', slug='bare-goat', category=self.category,
            description='no variants yet', price=Decimal('1200.00'),
            pricing_mode='fixed_weight', fixed_weight=Decimal('20.00'),
        )
        combo = Offer.objects.create(
            title='Bare Goat Combo', discount_type='combo', discount_value=Decimal('0'),
            combo_price=Decimal('10000.00'),
            start_date=timezone.now() - timedelta(days=1), end_date=timezone.now() + timedelta(days=1),
        )
        BundleItem.objects.create(offer=combo, product=self.jar, quantity=Decimal('3'))
        bundle_item = BundleItem.objects.create(offer=combo, product=bare_goat, quantity=Decimal('20.00'))
        self.assertIsNone(bundle_item.reference_weight)  # nothing to auto-fill from -- confirmed, not assumed

        combo_instance_id = str(uuid.uuid4())
        payload = {
            'client_sale_id': str(uuid.uuid4()),
            'payments': [{'method': 'cash', 'amount': '10000.00'}],
            'cart': [
                {'product_id': self.jar.id, 'qty': 3, 'offer_id': combo.id, 'combo_instance_id': combo_instance_id},
                # No variant_id -- none exist to pick. This is the only
                # payload shape a real client could even send here, since
                # GET /api/v1/pos/offers/ would list zero available_variants
                # for this slot and mark the combo not fully_available
                # (same as the no-stock case already covered above), so the
                # POS UI itself never lets a cashier reach Complete Sale
                # with this combo in the cart.
                {'product_id': bare_goat.id, 'qty': 1, 'offer_id': combo.id, 'combo_instance_id': combo_instance_id},
            ],
        }
        response = self.client.post(reverse('v1_sale_list_create'), payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('no longer available', response.data['message'])
        self.assertFalse(POSSale.objects.filter(client_sale_id=payload['client_sale_id']).exists())


class PosQueueFailureAlertApiTests(ApiTestBase):
    """POST /api/v1/pos/queue/report-failed/ -- the offline sale queue
    (static/js/pos-offline-queue.js) calls this once per queued entry the
    first time it fails to sync for a real (non-network, non-auth) reason,
    so a lost sale is at least noticed via Telegram rather than only
    existing in that one device's IndexedDB."""

    def setUp(self):
        super().setUp()
        self.client.login(username='cashier', password='pw')

    def test_requires_staff(self):
        self.client.logout()
        response = self.client.post(reverse('v1_pos_queue_report_failed'), {
            'client_sale_id': 'abc', 'error': 'x', 'payload': {},
        }, format='json')
        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    @patch('shop.emails.send_telegram')
    def test_sends_telegram_alert_with_sale_summary(self, mock_send_telegram):
        response = self.client.post(reverse('v1_pos_queue_report_failed'), {
            'client_sale_id': 'abc-123',
            'error': 'Not enough stock: only 0 available.',
            'payload': {
                'cart': [{'product_id': self.jar.id, 'qty': 1}],
                'payments': [{'method': 'cash', 'amount': '250.00'}],
            },
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        mock_send_telegram.assert_called_once()
        message = mock_send_telegram.call_args[0][0]
        self.assertIn('abc-123', message)
        self.assertIn('250.00', message)
        self.assertIn('Not enough stock', message)

    def test_rejects_missing_fields(self):
        response = self.client.post(reverse('v1_pos_queue_report_failed'), {'client_sale_id': 'abc'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class PosQueueDismissedReportApiTests(ApiTestBase):
    """POST /api/v1/pos/queue/report-dismissed/ -- see
    PosQueueDismissedReportView's docstring (api/views.py) for why this
    exists and why, unlike PosQueueFailureAlertView above, it must not
    fail silently: pos.html only removes a dismissed queue entry after
    this call returns 200."""

    def setUp(self):
        super().setUp()
        self.client.login(username='cashier', password='pw')

    def test_requires_staff(self):
        self.client.logout()
        response = self.client.post(reverse('v1_pos_queue_report_dismissed'), {
            'client_sale_id': 'abc', 'payload': {}, 'status': 'failed',
        }, format='json')
        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    @patch('shop.emails.send_telegram')
    def test_sends_detailed_telegram_report(self, mock_send_telegram):
        customer = Customer.objects.create(name='Hari Prasad', phone='9811111111')
        response = self.client.post(reverse('v1_pos_queue_report_dismissed'), {
            'client_sale_id': 'dismiss-1',
            'status': 'stockConflict',
            'last_error': 'Pickle Jar — only 0 currently available.',
            'queued_at': '2026-10-06T10:00:00.000Z',
            'payload': {
                'cart': [{'product_id': self.jar.id, 'qty': 2}],
                'payments': [{'method': 'cash', 'amount': '500.00'}],
                'customer_id': customer.id,
                'queued_operator_id': self.staff.id,
            },
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        mock_send_telegram.assert_called_once()
        message = mock_send_telegram.call_args[0][0]
        self.assertIn('dismiss-1', message)
        self.assertIn('Pickle Jar', message)
        self.assertIn('Hari Prasad', message)
        self.assertIn('500.00', message)
        self.assertIn('stockConflict', message)
        self.assertIn(self.staff.username, message)  # self.staff has no full name set
        self.assertTrue(mock_send_telegram.call_args.kwargs.get('raise_on_failure'))

    @patch('shop.emails.send_telegram')
    def test_resolves_deleted_product_variant_and_customer_gracefully(self, mock_send_telegram):
        deleted_product_id = self.jar.id + 9999
        response = self.client.post(reverse('v1_pos_queue_report_dismissed'), {
            'client_sale_id': 'dismiss-2',
            'status': 'failed',
            'payload': {
                'cart': [
                    {'product_id': deleted_product_id, 'qty': 1},
                    {'product_id': self.goat.id, 'variant_id': 999999, 'qty': 1},
                ],
                'payments': [{'method': 'cash', 'amount': '100.00'}],
                'customer_id': 999999,
            },
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        message = mock_send_telegram.call_args[0][0]
        self.assertIn('deleted product', message)
        self.assertIn('no longer found', message)  # the deleted variant
        self.assertIn('deleted customer', message)
        self.assertIn('not recorded', message)  # no queued_operator_id was sent

    @patch('shop.emails.send_telegram', side_effect=Exception('Telegram unreachable'))
    def test_telegram_failure_returns_error_so_entry_stays_queued(self, mock_send_telegram):
        response = self.client.post(reverse('v1_pos_queue_report_dismissed'), {
            'client_sale_id': 'dismiss-3',
            'status': 'failed',
            'payload': {
                'cart': [{'product_id': self.jar.id, 'qty': 1}],
                'payments': [{'method': 'cash', 'amount': '250.00'}],
            },
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_502_BAD_GATEWAY)

    def test_rejects_missing_fields(self):
        response = self.client.post(reverse('v1_pos_queue_report_dismissed'), {'client_sale_id': 'abc'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class PosStockApiTests(ApiTestBase):
    """GET /api/v1/pos/stock/ and POST /api/v1/pos/restock/ -- the list
    reuses get_stock_table_rows() (shop/stock.py), the exact same function
    ProductAdmin's stock column uses; restock picks movement_type by the
    request's explicit origin field (NOT product.origin -- a normally
    farm-grown product can occasionally be bought in, and the ledger needs
    to record what actually happened that day) and goes through the same
    strict full_clean()-inside-transaction.atomic() pattern create_pos_
    sale() uses for POS-originated movements (not a best-effort write)."""

    def setUp(self):
        super().setUp()
        self.client.login(username='cashier', password='pw')

    def test_stock_list_requires_staff(self):
        self.client.logout()
        response = self.client.get(reverse('v1_pos_stock_list'))
        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_stock_list_excludes_fixed_weight_and_returns_shared_rows(self):
        response = self.client.get(reverse('v1_pos_stock_list'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = [row['product_id'] for row in response.data]
        self.assertIn(self.jar.id, ids)
        self.assertIn(self.fruit.id, ids)
        self.assertNotIn(self.goat.id, ids)  # fixed_weight -- tracked per-animal, excluded

    def test_stock_list_includes_origin(self):
        # The Restock modal's default-origin selector and the inline
        # origin-edit control both read row.origin from this endpoint --
        # without it they silently fall back to nothing being pre-selected.
        response = self.client.get(reverse('v1_pos_stock_list'))
        row = next(r for r in response.data if r['product_id'] == self.jar.id)
        self.assertEqual(row['origin'], self.jar.origin)

    def test_restock_requires_staff(self):
        self.client.logout()
        response = self.client.post(
            reverse('v1_pos_restock'), {'product_id': self.jar.id, 'quantity': '5', 'origin': 'farm'}, format='json',
        )
        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_restock_with_farm_origin_creates_harvest_movement(self):
        before = InventoryMovement.current_stock(self.jar)
        response = self.client.post(
            reverse('v1_pos_restock'),
            {'product_id': self.jar.id, 'quantity': '5', 'origin': 'farm', 'note': 'supplier drop-off'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data['movement_type'], 'harvest')
        self.assertEqual(InventoryMovement.current_stock(self.jar), before + Decimal('5'))
        movement = InventoryMovement.objects.get(product=self.jar, movement_type='harvest', note='supplier drop-off')
        self.assertEqual(movement.source, 'pos')

    def test_restock_with_sourced_origin_creates_purchase_movement(self):
        response = self.client.post(
            reverse('v1_pos_restock'), {'product_id': self.jar.id, 'quantity': '5', 'origin': 'sourced'}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data['movement_type'], 'purchase')
        self.assertTrue(
            InventoryMovement.objects.filter(product=self.jar, movement_type='purchase', source='pos').exists()
        )

    def test_restock_origin_is_independent_of_product_origin(self):
        """The Chilly case: a normally farm-grown product occasionally
        bought in from local farmers when the farm has none that day --
        the explicit per-restock origin must win over product.origin,
        which stays unchanged (see PosProductOriginUpdateView for actually
        changing the product's own default)."""
        self.jar.origin = 'farm'
        self.jar.save(update_fields=['origin'])
        response = self.client.post(
            reverse('v1_pos_restock'), {'product_id': self.jar.id, 'quantity': '5', 'origin': 'sourced'}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data['movement_type'], 'purchase')
        self.jar.refresh_from_db()
        self.assertEqual(self.jar.origin, 'farm')  # product's own default is untouched

    def test_restock_rejects_missing_origin(self):
        response = self.client.post(
            reverse('v1_pos_restock'), {'product_id': self.jar.id, 'quantity': '5'}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_restock_rejects_fixed_weight_products_and_creates_nothing(self):
        before_count = InventoryMovement.objects.filter(product=self.goat).count()
        response = self.client.post(
            reverse('v1_pos_restock'), {'product_id': self.goat.id, 'quantity': '1', 'origin': 'farm'}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(InventoryMovement.objects.filter(product=self.goat).count(), before_count)

    def test_restock_rejects_invalid_quantity_and_creates_nothing(self):
        before_count = InventoryMovement.objects.filter(product=self.jar).count()
        response = self.client.post(
            reverse('v1_pos_restock'), {'product_id': self.jar.id, 'quantity': '0', 'origin': 'farm'}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(InventoryMovement.objects.filter(product=self.jar).count(), before_count)

    def test_restock_response_reflects_availability_flipped_by_existing_signal(self):
        """A restock that crosses stock from <=0 back to positive flips
        Product.is_available via the pre-existing sync_availability_from_
        stock signal -- the restock response must reflect that fresh value,
        not whatever is_available was before this movement saved."""
        self.jar.is_available = False
        self.jar.save(update_fields=['is_available'])
        InventoryMovement.objects.filter(product=self.jar).delete()  # zero out stock from ApiTestBase's own harvest
        response = self.client.post(
            reverse('v1_pos_restock'), {'product_id': self.jar.id, 'quantity': '3', 'origin': 'farm'}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertTrue(response.data['is_available'])
        self.jar.refresh_from_db()
        self.assertTrue(self.jar.is_available)

    def test_restock_updates_stock_list_row_without_a_second_product(self):
        before = InventoryMovement.current_stock(self.fruit)
        self.client.post(
            reverse('v1_pos_restock'), {'product_id': self.fruit.id, 'quantity': '2.500', 'origin': 'farm'}, format='json',
        )
        response = self.client.get(reverse('v1_pos_stock_list'))
        row = next(r for r in response.data if r['product_id'] == self.fruit.id)
        self.assertEqual(Decimal(row['current_stock']), before + Decimal('2.500'))
        self.assertIsNotNone(row['last_restocked_at'])


class PosProductOriginUpdateApiTests(ApiTestBase):
    """PATCH /api/v1/pos/products/<id>/origin/ -- changes a product's own
    default origin going forward, separate from PosRestockView's per-
    restock override above."""

    def setUp(self):
        super().setUp()
        self.client.login(username='cashier', password='pw')

    def test_requires_staff(self):
        self.client.logout()
        response = self.client.patch(
            reverse('v1_pos_product_origin_update', args=[self.jar.id]), {'origin': 'sourced'}, format='json',
        )
        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_updates_product_origin(self):
        self.jar.origin = 'farm'
        self.jar.save(update_fields=['origin'])
        response = self.client.patch(
            reverse('v1_pos_product_origin_update', args=[self.jar.id]), {'origin': 'sourced'}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data['origin'], 'sourced')
        self.assertEqual(response.data['restock_method'], 'Outsourced')
        self.jar.refresh_from_db()
        self.assertEqual(self.jar.origin, 'sourced')

    def test_rejects_invalid_origin_value(self):
        response = self.client.patch(
            reverse('v1_pos_product_origin_update', args=[self.jar.id]), {'origin': 'not-a-real-choice'}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_404_for_unknown_product(self):
        response = self.client.patch(
            reverse('v1_pos_product_origin_update', args=[999999]), {'origin': 'farm'}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class CustomerApiTests(ApiTestBase):
    def test_lookup_requires_staff(self):
        response = self.client.get(reverse('v1_pos_customer_lookup'), {'phone': '9800000001'})
        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_lookup_returns_matches_and_empty_list_for_no_match(self):
        self.client.login(username='cashier', password='pw')
        Customer.objects.create(name='Ram Bahadur', phone='9800000001')
        Customer.objects.create(name='Ram Bahadur Thapa', phone='9800000001')  # shared phone, family account
        Customer.objects.create(name='Someone Else', phone='9811111111')

        response = self.client.get(reverse('v1_pos_customer_lookup'), {'phone': '9800000001'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 2)
        self.assertEqual({c['name'] for c in response.data}, {'Ram Bahadur', 'Ram Bahadur Thapa'})

        empty = self.client.get(reverse('v1_pos_customer_lookup'), {'phone': '9899999999'})
        self.assertEqual(empty.status_code, status.HTTP_200_OK)
        self.assertEqual(empty.data, [])

    def test_create_customer(self):
        self.client.login(username='cashier', password='pw')
        payload = {'name': 'Sita Devi', 'phone': '9822222222', 'nickname': 'Sita', 'address': 'Bhulka Danda'}
        response = self.client.post(reverse('v1_pos_customer_create'), payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data['outstanding_balance'], '0.00')
        self.assertTrue(Customer.objects.filter(phone='9822222222', name='Sita Devi').exists())

    def test_create_customer_without_address_is_rejected(self):
        """Customer.address is required (not just a model-level change --
        the create-customer API itself must reject a missing/blank address,
        not just rely on the admin form)."""
        self.client.login(username='cashier', password='pw')
        payload = {'name': 'No Address Person', 'phone': '9855555555'}
        response = self.client.post(reverse('v1_pos_customer_create'), payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('address', response.data)
        self.assertFalse(Customer.objects.filter(phone='9855555555').exists())

        blank_payload = {'name': 'Blank Address Person', 'phone': '9855555556', 'address': ''}
        blank_response = self.client.post(reverse('v1_pos_customer_create'), blank_payload, format='json')
        self.assertEqual(blank_response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_customer_list_requires_staff(self):
        self.client.logout()
        response = self.client.get(reverse('v1_pos_customer_create'))
        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_customer_list_returns_every_customer_with_last_repayment(self):
        """GET on the same /pos/customers/ URL POST creates on -- powers
        the Repay Credit table: every customer (not phone-filtered, unlike
        CustomerLookupView), each with their most recent repayment amount/
        date, or null if they've never repaid."""
        self.client.login(username='cashier', password='pw')
        never_repaid = Customer.objects.create(name='Never Repaid', phone='9866666666', address='Farm Road')
        repaid_twice = Customer.objects.create(name='Repaid Twice', phone='9877777777', address='Farm Road')
        CreditTransaction.objects.create(
            customer=repaid_twice, amount=Decimal('100.00'), transaction_type='repayment', recorded_by=self.staff,
        )
        later_repayment = CreditTransaction.objects.create(
            customer=repaid_twice, amount=Decimal('250.00'), transaction_type='repayment', recorded_by=self.staff,
        )
        # A credit_sale transaction must never be mistaken for a repayment.
        CreditTransaction.objects.create(
            customer=repaid_twice, amount=Decimal('500.00'), transaction_type='credit_sale', recorded_by=self.staff,
        )

        response = self.client.get(reverse('v1_pos_customer_create'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        rows_by_id = {row['id']: row for row in response.data}

        never_row = rows_by_id[never_repaid.id]
        self.assertIsNone(never_row['last_repaid_amount'])
        self.assertIsNone(never_row['last_repaid_at'])
        self.assertEqual(never_row['address'], 'Farm Road')

        repaid_row = rows_by_id[repaid_twice.id]
        self.assertEqual(Decimal(repaid_row['last_repaid_amount']), Decimal('250.00'))  # the LATER of the two
        self.assertEqual(repaid_row['last_repaid_at'], later_repayment.created_at.isoformat())
        self.assertEqual(Decimal(repaid_row['outstanding_balance']), Decimal('150.00'))  # 500 - 100 - 250


class CreditLedgerApiTests(ApiTestBase):
    def setUp(self):
        super().setUp()
        self.credit_customer = Customer.objects.create(name='Hari Prasad', phone='9833333333')

    def test_credit_sale_with_deleted_customer_fails_cleanly(self):
        """Simulates a replayed offline-queued credit sale whose customer_id
        no longer exists by the time it syncs (deleted in the meantime) --
        must fail with a clear message, not a crash or a sale silently
        created with no customer attached."""
        deleted_id = self.credit_customer.id
        self.credit_customer.delete()
        self.client.login(username='cashier', password='pw')
        self.unlock_terminal()
        client_sale_id = str(uuid.uuid4())
        payload = {
            'client_sale_id': client_sale_id,
            'customer_id': deleted_id,
            'payments': [{'method': 'credit', 'amount': '250.00'}],
            'cart': [{'product_id': self.jar.id, 'qty': 1}],
        }
        response = self.client.post(reverse('v1_sale_list_create'), payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(POSSale.objects.filter(client_sale_id=client_sale_id).exists())

    def test_repay_requires_prior_unlock(self):
        self.client.login(username='cashier', password='pw')
        payload = {'customer_id': self.credit_customer.id, 'amount': '50.00'}
        response = self.client.post(reverse('v1_pos_credit_repay'), payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(CreditTransaction.objects.filter(customer=self.credit_customer).exists())

    def test_repayment_reduces_outstanding_balance(self):
        CreditTransaction.objects.create(
            customer=self.credit_customer, amount=Decimal('300.00'),
            transaction_type='credit_sale', recorded_by=self.staff,
        )
        self.assertEqual(self.credit_customer.outstanding_balance(), Decimal('300.00'))

        self.client.login(username='cashier', password='pw')
        self.unlock_terminal()
        payload = {'customer_id': self.credit_customer.id, 'amount': '100.00'}
        response = self.client.post(reverse('v1_pos_credit_repay'), payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data['outstanding_balance'], '200.00')
        self.assertEqual(self.credit_customer.outstanding_balance(), Decimal('200.00'))

        repay_txn = CreditTransaction.objects.get(customer=self.credit_customer, transaction_type='repayment')
        self.assertEqual(repay_txn.recorded_by, self.staff)

    def test_outstanding_balance_computed_correctly_across_mixed_order(self):
        # Deliberately not in chronological/sorted order -- the aggregate
        # must not depend on row insertion order.
        CreditTransaction.objects.create(
            customer=self.credit_customer, amount=Decimal('100.00'),
            transaction_type='repayment', recorded_by=self.staff,
        )
        CreditTransaction.objects.create(
            customer=self.credit_customer, amount=Decimal('500.00'),
            transaction_type='credit_sale', recorded_by=self.staff,
        )
        CreditTransaction.objects.create(
            customer=self.credit_customer, amount=Decimal('50.00'),
            transaction_type='repayment', recorded_by=self.staff,
        )
        CreditTransaction.objects.create(
            customer=self.credit_customer, amount=Decimal('200.00'),
            transaction_type='credit_sale', recorded_by=self.staff,
        )
        # 500 + 200 credit_sale - (100 + 50) repayment = 550
        self.assertEqual(self.credit_customer.outstanding_balance(), Decimal('550.00'))

    def test_repayment_larger_than_balance_is_allowed_not_clamped(self):
        CreditTransaction.objects.create(
            customer=self.credit_customer, amount=Decimal('100.00'),
            transaction_type='credit_sale', recorded_by=self.staff,
        )
        self.client.login(username='cashier', password='pw')
        self.unlock_terminal()
        payload = {'customer_id': self.credit_customer.id, 'amount': '150.00'}
        response = self.client.post(reverse('v1_pos_credit_repay'), payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        # Balance genuinely goes negative (customer now owed BY the farm) --
        # not clamped to zero.
        self.assertEqual(response.data['outstanding_balance'], '-50.00')

    def test_repayment_rejects_non_positive_amount(self):
        self.client.login(username='cashier', password='pw')
        self.unlock_terminal()
        payload = {'customer_id': self.credit_customer.id, 'amount': '0.00'}
        response = self.client.post(reverse('v1_pos_credit_repay'), payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(CreditTransaction.objects.filter(customer=self.credit_customer).exists())


class PosUnlockApiTests(ApiTestBase):
    """POST /api/v1/pos/unlock/ — POS Phase A's PIN-identify layer. Session
    auth + IsStaffUser (the terminal's own day-long login) gates access to
    the endpoint at all; the PIN itself identifies which UserProfile is
    behind the register right now, independent of who the terminal session
    belongs to."""

    def setUp(self):
        super().setUp()
        # 'cashier'/'shopper' already exist on ApiTestBase (self.staff /
        # self.customer) with no UserProfile at all -- a separate, named
        # profile-holder keeps "who's logged into the terminal" and "whose
        # PIN is being checked" clearly distinct in these tests, matching
        # how the real feature allows them to be different people.
        self.pin_holder = User.objects.create_user('priya', password='pw', is_staff=True, first_name='Priya')
        from shop.models import UserProfile
        self.profile = UserProfile.objects.create(user=self.pin_holder, role='cashier')
        self.profile.set_pin('4471')
        self.profile.save()

    def test_valid_pin_succeeds_and_returns_right_user(self):
        self.client.login(username='cashier', password='pw')
        response = self.client.post(reverse('v1_pos_unlock'), {'pin': '4471'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data['id'], self.pin_holder.id)
        self.assertEqual(response.data['name'], 'Priya')
        self.assertEqual(response.data['role'], 'cashier')

    def test_wrong_pin_fails_and_counts_down_attempts_remaining(self):
        self.client.login(username='cashier', password='pw')

        first = self.client.post(reverse('v1_pos_unlock'), {'pin': '0000'}, format='json')
        self.assertEqual(first.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(first.data['error'], 'invalid_pin')
        self.assertEqual(first.data['attempts_remaining'], 6)

        second = self.client.post(reverse('v1_pos_unlock'), {'pin': '0000'}, format='json')
        self.assertEqual(second.data['attempts_remaining'], 5)

    def test_seventh_wrong_attempt_locks_out_even_the_correct_pin(self):
        self.client.login(username='cashier', password='pw')

        for _ in range(6):
            response = self.client.post(reverse('v1_pos_unlock'), {'pin': '0000'}, format='json')
            self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

        seventh = self.client.post(reverse('v1_pos_unlock'), {'pin': '0000'}, format='json')
        self.assertEqual(seventh.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(seventh.data['error'], 'invalid_pin')
        self.assertNotIn('attempts_remaining', seventh.data)

        # Locked out now -- even the genuinely correct PIN is rejected without
        # being checked, and the response shape switches to locked_out.
        eighth = self.client.post(reverse('v1_pos_unlock'), {'pin': '4471'}, format='json')
        self.assertEqual(eighth.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(eighth.data['error'], 'locked_out')
        self.assertGreater(eighth.data['retry_after_seconds'], 0)
        self.assertLessEqual(eighth.data['retry_after_seconds'], 300)

    def test_lockout_clears_once_cooldown_passes(self):
        self.client.login(username='cashier', password='pw')
        session = self.client.session
        session['pos_failed_attempts'] = 0
        session['pos_lockout_until'] = (timezone.now() - timedelta(seconds=1)).isoformat()
        session.save()

        response = self.client.post(reverse('v1_pos_unlock'), {'pin': '4471'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)

    def test_non_staff_user_gets_403_regardless_of_pin(self):
        self.client.login(username='shopper', password='pw')
        response = self.client.post(reverse('v1_pos_unlock'), {'pin': '4471'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_malformed_pin_is_rejected_before_any_lookup(self):
        self.client.login(username='cashier', password='pw')
        response = self.client.post(reverse('v1_pos_unlock'), {'pin': '12345'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        # A malformed PIN must not itself count as a failed attempt.
        self.assertIsNone(self.client.session.get('pos_failed_attempts'))

    def test_lockout_is_per_session_not_global(self):
        """Two different terminals (test clients) must not share lockout
        state -- one register going into cooldown shouldn't lock out every
        other register in the shop."""
        client_a = APIClient()
        client_a.login(username='cashier', password='pw')
        client_b = APIClient()
        client_b.login(username='cashier', password='pw')

        for _ in range(7):
            client_a.post(reverse('v1_pos_unlock'), {'pin': '0000'}, format='json')

        locked = client_a.post(reverse('v1_pos_unlock'), {'pin': '4471'}, format='json')
        self.assertEqual(locked.status_code, status.HTTP_403_FORBIDDEN)

        # Client B has made zero attempts of its own -- a fresh first guess
        # there must behave like a fresh first guess, not an already-locked
        # terminal.
        fresh = client_b.post(reverse('v1_pos_unlock'), {'pin': '0000'}, format='json')
        self.assertEqual(fresh.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(fresh.data['attempts_remaining'], 6)


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


class ApiV1CorsMiddlewareTests(ApiTestBase):
    allowed_origin = 'http://localhost:3000'

    def test_get_from_allowed_origin_gets_cors_header(self):
        response = self.client.get(reverse('v1_product_list'), HTTP_ORIGIN=self.allowed_origin)
        self.assertEqual(response['Access-Control-Allow-Origin'], self.allowed_origin)

    def test_get_from_disallowed_origin_gets_no_cors_header(self):
        response = self.client.get(reverse('v1_product_list'), HTTP_ORIGIN='https://evil.example.com')
        self.assertNotIn('Access-Control-Allow-Origin', response)

    def test_preflight_for_get_is_allowed(self):
        response = self.client.options(
            reverse('v1_product_list'),
            HTTP_ORIGIN=self.allowed_origin,
            HTTP_ACCESS_CONTROL_REQUEST_METHOD='GET',
        )
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response['Access-Control-Allow-Origin'], self.allowed_origin)
        self.assertIn('GET', response['Access-Control-Allow-Methods'])

    def test_preflight_for_post_allows_post_on_orders_only(self):
        """Guest checkout (POST /api/v1/orders/) is public/AllowAny with no
        session or CSRF involved, so it's the one path allowed to preflight
        for POST cross-origin."""
        response = self.client.options(
            reverse('v1_order_create'),
            HTTP_ORIGIN=self.allowed_origin,
            HTTP_ACCESS_CONTROL_REQUEST_METHOD='POST',
        )
        self.assertEqual(response.status_code, 204)
        self.assertIn('POST', response['Access-Control-Allow-Methods'])

    def test_preflight_for_post_still_blocked_on_sales(self):
        """/api/v1/sales/ is staff/session-authed -- must stay GET-only
        cross-origin until the login phase adds real credential support."""
        response = self.client.options(
            reverse('v1_sale_list_create'),
            HTTP_ORIGIN=self.allowed_origin,
            HTTP_ACCESS_CONTROL_REQUEST_METHOD='POST',
        )
        self.assertEqual(response.status_code, 204)
        self.assertNotIn('POST', response['Access-Control-Allow-Methods'])

    def test_actual_post_to_orders_succeeds_and_gets_cors_header(self):
        payload = {
            'name': 'Test Customer',
            'email': 'customer@example.com',
            'cart': [{'product_id': self.jar.id, 'qty': 1}],
        }
        response = self.client.post(
            reverse('v1_order_create'), payload, format='json', HTTP_ORIGIN=self.allowed_origin,
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response['Access-Control-Allow-Origin'], self.allowed_origin)
        self.assertNotIn('Access-Control-Allow-Credentials', response)

    def test_actual_post_to_sales_gets_no_cors_header(self):
        """CORS never blocks the request itself -- only whether a browser
        exposes the response to foreign-origin JS. A real staff sale would
        still need to be authenticated regardless; this just confirms
        cross-origin JS still can't read the response."""
        self.client.login(username='cashier', password='pw')
        self.unlock_terminal()
        payload = {
            'client_sale_id': 'cors-test-sale-1',
            'payments': [{'method': 'cash', 'amount': '250.00'}],
            'cart': [{'product_id': self.jar.id, 'qty': 1}],
        }
        response = self.client.post(
            reverse('v1_sale_list_create'), payload, format='json', HTTP_ORIGIN=self.allowed_origin,
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertNotIn('Access-Control-Allow-Origin', response)

    def test_non_v1_paths_are_unaffected(self):
        response = self.client.get(reverse('shop'), HTTP_ORIGIN=self.allowed_origin)
        self.assertNotIn('Access-Control-Allow-Origin', response)

    def test_preflight_for_post_allows_post_on_auth_endpoints(self):
        for name in ('v1_auth_signup', 'v1_auth_login', 'v1_auth_password_reset'):
            response = self.client.options(
                reverse(name),
                HTTP_ORIGIN=self.allowed_origin,
                HTTP_ACCESS_CONTROL_REQUEST_METHOD='POST',
            )
            self.assertEqual(response.status_code, 204, name)
            self.assertIn('POST', response['Access-Control-Allow-Methods'], name)

    def test_actual_post_to_phase4_endpoints_gets_cors_header(self):
        """Regression test: these 7 endpoints were added in Phase 4 but
        never added to the CORS middleware's allowlist, so the actual
        response (success or error) never carried Access-Control-Allow-
        Origin -- the browser sent the request (it landed and, for
        wishlist/toggle specifically, could even succeed server-side) but
        then refused to let frontend JS read *any* response, surfacing as a
        generic network-error toast/silent failure regardless of what the
        API actually returned. A bad/missing token (401) is enough to prove
        the CORS header itself is present; the request doesn't need to
        succeed for this check."""
        cases = [
            ('post', reverse('v1_wishlist_toggle'), {'product_id': self.jar.id}),
            ('post', reverse('v1_wishlist_set_variant'), {'product_id': self.jar.id, 'variant_id': 1}),
            ('post', reverse('v1_wishlist_move_to_cart'), {'product_id': self.jar.id}),
            ('post', reverse('v1_order_cancel', args=[1]), None),
            ('post', reverse('v1_order_reorder', args=[1]), None),
            ('post', reverse('v1_profile_change_password'), {}),
            ('patch', reverse('v1_profile_update'), {'name': 'X', 'email': 'x@example.com'}),
        ]
        for method, url, payload in cases:
            client_method = getattr(self.client, method)
            kwargs = {'HTTP_ORIGIN': self.allowed_origin, 'HTTP_AUTHORIZATION': 'Token not-a-real-token'}
            if payload is not None:
                kwargs['data'] = payload
                kwargs['format'] = 'json'
            response = client_method(url, **kwargs)
            self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED, url)
            self.assertEqual(response['Access-Control-Allow-Origin'], self.allowed_origin, url)

    def test_preflight_allows_patch_on_profile_update_only(self):
        response = self.client.options(
            reverse('v1_profile_update'),
            HTTP_ORIGIN=self.allowed_origin,
            HTTP_ACCESS_CONTROL_REQUEST_METHOD='PATCH',
        )
        self.assertEqual(response.status_code, 204)
        self.assertIn('PATCH', response['Access-Control-Allow-Methods'])

        # A path that was never granted PATCH must not get it either.
        other_response = self.client.options(
            reverse('v1_product_list'),
            HTTP_ORIGIN=self.allowed_origin,
            HTTP_ACCESS_CONTROL_REQUEST_METHOD='PATCH',
        )
        self.assertNotIn('PATCH', other_response['Access-Control-Allow-Methods'])

    def test_order_detail_path_still_excluded_from_post_cross_origin(self):
        """/api/v1/orders/<id>/ (GET-only status lookup) must not be swept
        in by the new /orders/<id>/cancel|reorder/ regex patterns."""
        response = self.client.options(
            reverse('v1_order_detail', args=[1]),
            HTTP_ORIGIN=self.allowed_origin,
            HTTP_ACCESS_CONTROL_REQUEST_METHOD='POST',
        )
        self.assertNotIn('POST', response['Access-Control-Allow-Methods'])


class AuthApiTests(ApiTestBase):
    """Covers the token-auth endpoints under /api/v1/auth/. These create
    real auth.User rows in the same table shop/auth_views.py's traditional
    signup/login/forgot-password views use — not a parallel user system."""

    def test_signup_creates_user_and_returns_token(self):
        payload = {
            'name': 'Ramesh Thapa',
            'email': 'ramesh@example.com',
            'password': 'a-strong-p4ssword',
            'confirm_password': 'a-strong-p4ssword',
        }
        response = self.client.post(reverse('v1_auth_signup'), payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data['user']['email'], 'ramesh@example.com')
        self.assertEqual(response.data['user']['name'], 'Ramesh Thapa')
        self.assertTrue(response.data['token'])

        # Same table the traditional site's signup() uses -- username=email.
        user = User.objects.get(email='ramesh@example.com')
        self.assertEqual(user.username, 'ramesh@example.com')
        self.assertEqual(user.first_name, 'Ramesh')
        self.assertEqual(user.last_name, 'Thapa')
        self.assertTrue(user.check_password('a-strong-p4ssword'))

        # The returned token is real and actually authenticates.
        self.assertEqual(Token.objects.get(user=user).key, response.data['token'])

    def test_signup_rejects_duplicate_email(self):
        User.objects.create_user(username='dup@example.com', email='dup@example.com', password='whatever123')
        payload = {
            'name': 'Someone Else',
            'email': 'dup@example.com',
            'password': 'a-strong-p4ssword',
            'confirm_password': 'a-strong-p4ssword',
        }
        response = self.client.post(reverse('v1_auth_signup'), payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('email', response.data)

    def test_signup_rejects_mismatched_passwords(self):
        payload = {
            'name': 'X', 'email': 'x@example.com',
            'password': 'a-strong-p4ssword', 'confirm_password': 'different-password',
        }
        response = self.client.post(reverse('v1_auth_signup'), payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('confirm_password', response.data)

    def test_signup_rejects_weak_password(self):
        payload = {
            'name': 'X', 'email': 'x2@example.com',
            'password': '1234', 'confirm_password': '1234',
        }
        response = self.client.post(reverse('v1_auth_signup'), payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('password', response.data)
        self.assertFalse(User.objects.filter(email='x2@example.com').exists())

    def test_login_succeeds_with_correct_credentials(self):
        User.objects.create_user(username='shopper2@example.com', email='shopper2@example.com', password='correct-horse')
        response = self.client.post(
            reverse('v1_auth_login'),
            {'email': 'shopper2@example.com', 'password': 'correct-horse'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['user']['email'], 'shopper2@example.com')
        self.assertTrue(response.data['token'])

    def test_login_rejects_wrong_password(self):
        User.objects.create_user(username='shopper3@example.com', email='shopper3@example.com', password='correct-horse')
        response = self.client.post(
            reverse('v1_auth_login'),
            {'email': 'shopper3@example.com', 'password': 'wrong-password'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_login_rejects_unknown_email(self):
        response = self.client.post(
            reverse('v1_auth_login'),
            {'email': 'nobody@example.com', 'password': 'whatever'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_logout_deletes_token_and_requires_auth(self):
        user = User.objects.create_user(username='logout@example.com', email='logout@example.com', password='whatever123')
        token = Token.objects.create(user=user)

        # No token presented at all -- rejected.
        anon_response = self.client.post(reverse('v1_auth_logout'), format='json')
        self.assertIn(anon_response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

        self.client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')
        response = self.client.post(reverse('v1_auth_logout'), format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(Token.objects.filter(user=user).exists())

    def test_password_reset_request_always_returns_ok(self):
        User.objects.create_user(username='hasaccount@example.com', email='hasaccount@example.com', password='whatever123')

        real_response = self.client.post(
            reverse('v1_auth_password_reset'), {'email': 'hasaccount@example.com'}, format='json',
        )
        unknown_response = self.client.post(
            reverse('v1_auth_password_reset'), {'email': 'nobody@example.com'}, format='json',
        )
        # Same response either way -- no account-existence signal (matches
        # forgot_password()'s own anti-enumeration behavior).
        self.assertEqual(real_response.status_code, status.HTTP_200_OK)
        self.assertEqual(unknown_response.status_code, status.HTTP_200_OK)
        self.assertEqual(real_response.data['message'], unknown_response.data['message'])

    def test_password_reset_confirm_with_valid_token(self):
        user = User.objects.create_user(username='reset@example.com', email='reset@example.com', password='old-password-1')
        cache.set('pwd_reset_test-token-abc', user.id, 3600)

        response = self.client.post(
            reverse('v1_auth_password_reset_confirm'),
            {'token': 'test-token-abc', 'password': 'brand-new-p4ssword', 'confirm_password': 'brand-new-p4ssword'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)

        user.refresh_from_db()
        self.assertTrue(user.check_password('brand-new-p4ssword'))
        # Token is single-use -- consumed on success.
        self.assertIsNone(cache.get('pwd_reset_test-token-abc'))

    def test_password_reset_confirm_with_invalid_token(self):
        response = self.client.post(
            reverse('v1_auth_password_reset_confirm'),
            {'token': 'no-such-token', 'password': 'brand-new-p4ssword', 'confirm_password': 'brand-new-p4ssword'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('token', response.data)

    def test_password_reset_confirm_rejects_mismatched_passwords(self):
        user = User.objects.create_user(username='reset2@example.com', email='reset2@example.com', password='old-password-1')
        cache.set('pwd_reset_test-token-xyz', user.id, 3600)

        response = self.client.post(
            reverse('v1_auth_password_reset_confirm'),
            {'token': 'test-token-xyz', 'password': 'brand-new-p4ssword', 'confirm_password': 'does-not-match'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('confirm_password', response.data)


class ProfileApiTestBase(ApiTestBase):
    """Shared setup for the Phase 4 profile-page endpoints: a logged-in
    'owner' (token-authed) plus a second unrelated user, so ownership checks
    can be proven to actually exclude someone else's data, not just
    coincidentally pass because there was nothing else in the database."""

    def setUp(self):
        super().setUp()
        self.owner = User.objects.create_user(
            username='owner@example.com', email='owner@example.com',
            password='whatever123', first_name='Owner', last_name='One',
        )
        self.owner_token = Token.objects.create(user=self.owner)
        self.other = User.objects.create_user(
            username='other@example.com', email='other@example.com', password='whatever123',
        )
        self.other_token = Token.objects.create(user=self.other)

    def authenticate_as_owner(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {self.owner_token.key}')

    def authenticate_as_other(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {self.other_token.key}')


class ProfileOrderApiTests(ProfileApiTestBase):
    """GET /api/v1/profile/orders/, POST /api/v1/orders/<pk>/cancel/,
    POST /api/v1/orders/<pk>/reorder/ -- ProductOrder has no User FK, so
    ownership here is the same email-match profile() itself uses."""

    def make_order(self, email=None, **kwargs):
        defaults = dict(
            name='Test Customer', email=email or self.owner.email, phone='9800000000',
            address='Bhulka Danda', product_interest='Mango x1', status='pending',
            cart_snapshot=[{'product_id': self.jar.id, 'weight': None, 'qty': 1, 'variant_id': None}],
        )
        defaults.update(kwargs)
        return ProductOrder.objects.create(**defaults)

    def test_list_requires_auth(self):
        response = self.client.get(reverse('v1_profile_orders'))
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_list_returns_only_my_orders(self):
        mine = self.make_order()
        self.make_order(email=self.other.email)  # someone else's -- must not appear

        self.authenticate_as_owner()
        response = self.client.get(reverse('v1_profile_orders'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data['results']
        ids = [row['id'] for row in results]
        self.assertEqual(ids, [mine.id])
        self.assertIn('status_display', results[0])
        self.assertIn('can_cancel', results[0])
        self.assertIn('has_cart_snapshot', results[0])
        # No PII beyond what the owner already knows from being logged in.
        self.assertNotIn('cancel_token', results[0])
        self.assertNotIn('email', results[0])

    def test_cancel_happy_path(self):
        order = self.make_order()
        self.authenticate_as_owner()
        response = self.client.post(reverse('v1_order_cancel', args=[order.id]))
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        order.refresh_from_db()
        self.assertEqual(order.status, 'cancelled')
        self.assertTrue(
            InventoryMovement.objects.filter(related_order=order, movement_type='return').exists()
        )

    def test_cancel_already_cancelled_returns_400(self):
        order = self.make_order(status='cancelled')
        self.authenticate_as_owner()
        response = self.client.post(reverse('v1_order_cancel', args=[order.id]))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('already been cancelled', response.data['message'])

    def test_cancel_expired_window_returns_400(self):
        order = self.make_order()
        ProductOrder.objects.filter(id=order.id).update(ordered_at=timezone.now() - timedelta(minutes=40))
        self.authenticate_as_owner()
        response = self.client.post(reverse('v1_order_cancel', args=[order.id]))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('expired', response.data['message'])
        order.refresh_from_db()
        self.assertEqual(order.status, 'pending')

    def test_cannot_cancel_someone_elses_order(self):
        order = self.make_order(email=self.other.email)
        self.authenticate_as_owner()
        response = self.client.post(reverse('v1_order_cancel', args=[order.id]))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        order.refresh_from_db()
        self.assertEqual(order.status, 'pending')

    def test_reorder_no_snapshot_returns_400(self):
        order = self.make_order(cart_snapshot=None)
        self.authenticate_as_owner()
        response = self.client.post(reverse('v1_order_reorder', args=[order.id]))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_reorder_resolves_lines_and_skips_fixed_weight_and_unavailable(self):
        order = self.make_order(cart_snapshot=[
            {'product_id': self.fruit.id, 'weight': '1.3', 'qty': 1, 'variant_id': None},
            {'product_id': self.jar.id, 'weight': None, 'qty': 2, 'variant_id': None},
            {'product_id': self.goat.id, 'weight': '20.00', 'qty': 1, 'variant_id': self.goat_variant.id},
            {'product_id': 999999, 'weight': None, 'qty': 1, 'variant_id': None},
        ])
        self.authenticate_as_owner()
        response = self.client.post(reverse('v1_order_reorder', args=[order.id]))
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data['skipped_count'], 2)  # goat (fixed_weight) + missing product
        items = response.data['items']
        self.assertEqual(len(items), 2)

        fruit_line = next(i for i in items if i['product']['id'] == self.fruit.id)
        self.assertEqual(fruit_line['weight'], '1.50')  # snapped to the 0.50 step, same as format_weight()
        self.assertEqual(fruit_line['qty'], 1)

        jar_line = next(i for i in items if i['product']['id'] == self.jar.id)
        self.assertIsNone(jar_line['weight'])
        self.assertEqual(jar_line['qty'], 2)

    def test_reorder_someone_elses_order_404s(self):
        order = self.make_order(email=self.other.email, cart_snapshot=[
            {'product_id': self.jar.id, 'weight': None, 'qty': 1, 'variant_id': None},
        ])
        self.authenticate_as_owner()
        response = self.client.post(reverse('v1_order_reorder', args=[order.id]))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class WishlistApiTests(ProfileApiTestBase):
    """GET /api/v1/wishlist/, POST toggle/set-variant/move-to-cart -- mirror
    wishlist_toggle()/wishlist_set_variant()/wishlist_move_to_cart() exactly."""

    def test_list_requires_auth(self):
        response = self.client.get(reverse('v1_wishlist_list'))
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_list_returns_only_my_items(self):
        Wishlist.objects.create(user=self.owner, product=self.fruit)
        Wishlist.objects.create(user=self.other, product=self.jar)

        self.authenticate_as_owner()
        response = self.client.get(reverse('v1_wishlist_list'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data['results']
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['product']['id'], self.fruit.id)

    def test_toggle_adds_then_removes(self):
        self.authenticate_as_owner()
        url = reverse('v1_wishlist_toggle')

        add_response = self.client.post(url, {'product_id': self.jar.id}, format='json')
        self.assertEqual(add_response.status_code, status.HTTP_200_OK)
        self.assertTrue(add_response.data['is_saved'])
        self.assertTrue(Wishlist.objects.filter(user=self.owner, product=self.jar).exists())

        remove_response = self.client.post(url, {'product_id': self.jar.id}, format='json')
        self.assertEqual(remove_response.status_code, status.HTTP_200_OK)
        self.assertFalse(remove_response.data['is_saved'])
        self.assertFalse(Wishlist.objects.filter(user=self.owner, product=self.jar).exists())

    def test_toggle_fixed_weight_defaults_to_cheapest_available_variant(self):
        cheaper = ProductVariant.objects.create(product=self.goat, weight=Decimal('12.00'))  # cheaper than self.goat_variant
        self.authenticate_as_owner()
        response = self.client.post(reverse('v1_wishlist_toggle'), {'product_id': self.goat.id}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        item = Wishlist.objects.get(user=self.owner, product=self.goat)
        self.assertEqual(item.variant_id, cheaper.id)

    def test_set_variant_happy_path(self):
        second_variant = ProductVariant.objects.create(product=self.goat, weight=Decimal('25.00'))
        Wishlist.objects.create(user=self.owner, product=self.goat, variant=self.goat_variant)

        self.authenticate_as_owner()
        response = self.client.post(
            reverse('v1_wishlist_set_variant'),
            {'product_id': self.goat.id, 'variant_id': second_variant.id},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        item = Wishlist.objects.get(user=self.owner, product=self.goat)
        self.assertEqual(item.variant_id, second_variant.id)

    def test_set_variant_rejects_unavailable_variant(self):
        unavailable = ProductVariant.objects.create(product=self.goat, weight=Decimal('30.00'), is_available=False)
        Wishlist.objects.create(user=self.owner, product=self.goat, variant=self.goat_variant)

        self.authenticate_as_owner()
        response = self.client.post(
            reverse('v1_wishlist_set_variant'),
            {'product_id': self.goat.id, 'variant_id': unavailable.id},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_move_to_cart_returns_product_and_clears_wishlist_row(self):
        Wishlist.objects.create(user=self.owner, product=self.goat, variant=self.goat_variant)
        self.authenticate_as_owner()
        response = self.client.post(
            reverse('v1_wishlist_move_to_cart'),
            {'product_id': self.goat.id, 'variant_id': self.goat_variant.id},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data['product']['id'], self.goat.id)
        self.assertEqual(response.data['variant']['id'], self.goat_variant.id)
        self.assertFalse(Wishlist.objects.filter(user=self.owner, product=self.goat).exists())

    def test_move_to_cart_non_fixed_weight_returns_null_variant(self):
        Wishlist.objects.create(user=self.owner, product=self.fruit)
        self.authenticate_as_owner()
        response = self.client.post(
            reverse('v1_wishlist_move_to_cart'),
            {'product_id': self.fruit.id},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNone(response.data['variant'])
        self.assertEqual(response.data['product']['id'], self.fruit.id)


class CouponApiTests(ProfileApiTestBase):
    """GET /api/v1/coupons/ -- same get_live_coupons() list the public
    offers() page and the traditional profile page already show everyone;
    no per-user filtering exists (Coupon has no User relation)."""

    def make_coupon(self, code, **kwargs):
        now = timezone.now()
        defaults = dict(
            code=code, discount_type='percent', discount_value=Decimal('10'),
            start_date=now - timedelta(days=1), end_date=now + timedelta(days=1), is_active=True,
        )
        defaults.update(kwargs)
        return Coupon.objects.create(**defaults)

    def test_requires_auth(self):
        response = self.client.get(reverse('v1_coupon_list'))
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_returns_only_currently_live_coupons(self):
        live = self.make_coupon('DASHAIN25')
        self.make_coupon('EXPIRED10', start_date=timezone.now() - timedelta(days=10), end_date=timezone.now() - timedelta(days=5))
        self.make_coupon('INACTIVE10', is_active=False)
        self.make_coupon('USEDUP10', max_uses=5, used_count=5)

        self.authenticate_as_owner()
        response = self.client.get(reverse('v1_coupon_list'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        codes = [c['code'] for c in response.data['results']]
        self.assertEqual(codes, [live.code])

    def test_same_list_is_shared_across_users(self):
        """Coupons are global, not per-user -- confirms this isn't
        accidentally filtered by the requesting user."""
        self.make_coupon('SHARED10')

        self.authenticate_as_owner()
        owner_response = self.client.get(reverse('v1_coupon_list'))
        self.authenticate_as_other()
        other_response = self.client.get(reverse('v1_coupon_list'))

        self.assertEqual(
            [c['code'] for c in owner_response.data['results']],
            [c['code'] for c in other_response.data['results']],
        )


class ProfileUpdateApiTests(ProfileApiTestBase):
    """PATCH /api/v1/profile/, POST /api/v1/profile/change-password/ --
    mirror edit_profile()/change_password() exactly: only name+email are
    editable, and email uniqueness + the username-follows-email coupling
    both apply the same way."""

    def test_update_requires_auth(self):
        response = self.client.patch(reverse('v1_profile_update'), {'name': 'X', 'email': 'x@example.com'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_update_name_and_email_happy_path(self):
        self.authenticate_as_owner()
        response = self.client.patch(
            reverse('v1_profile_update'),
            {'name': 'New Full Name', 'email': 'newemail@example.com'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.first_name, 'New')
        self.assertEqual(self.owner.last_name, 'Full Name')
        self.assertEqual(self.owner.email, 'newemail@example.com')
        # Changing email also changes username, same as edit_profile().
        self.assertEqual(self.owner.username, 'newemail@example.com')

    def test_update_rejects_email_already_used_by_another_account(self):
        self.authenticate_as_owner()
        response = self.client.patch(
            reverse('v1_profile_update'),
            {'name': 'Owner One', 'email': self.other.email},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('email', response.data)

    def test_update_allows_keeping_own_current_email(self):
        self.authenticate_as_owner()
        response = self.client.patch(
            reverse('v1_profile_update'),
            {'name': 'Owner One', 'email': self.owner.email},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)

    def test_change_password_requires_auth(self):
        response = self.client.post(reverse('v1_profile_change_password'), {}, format='json')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_change_password_happy_path(self):
        self.authenticate_as_owner()
        response = self.client.post(
            reverse('v1_profile_change_password'),
            {'old_password': 'whatever123', 'new_password1': 'a-strong-p4ssword', 'new_password2': 'a-strong-p4ssword'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.check_password('a-strong-p4ssword'))
        # Token stays valid -- no session to keep alive, nothing to rotate.
        self.assertTrue(Token.objects.filter(user=self.owner, key=self.owner_token.key).exists())

    def test_change_password_rejects_wrong_old_password(self):
        self.authenticate_as_owner()
        response = self.client.post(
            reverse('v1_profile_change_password'),
            {'old_password': 'not-the-real-password', 'new_password1': 'a-strong-p4ssword', 'new_password2': 'a-strong-p4ssword'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('old_password', response.data)
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.check_password('whatever123'))

    def test_change_password_rejects_mismatched_new_passwords(self):
        self.authenticate_as_owner()
        response = self.client.post(
            reverse('v1_profile_change_password'),
            {'old_password': 'whatever123', 'new_password1': 'a-strong-p4ssword', 'new_password2': 'different-one'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('new_password2', response.data)
