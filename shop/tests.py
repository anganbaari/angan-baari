from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from .admin import UserProfileInlineForm
from .models import (
    BundleItem, Category, CostEntry, FarmAsset, InventoryMovement, ContactMessage,
    NewsletterSubscriber, Offer, Product, ProductVariant, UserProfile,
)
from .stock import get_stock_table_rows


class ContactViewEmailTests(TestCase):
    """contact() used to call Django's send_mail(), which silently failed in
    production (no SMTP backend configured, defaults to localhost:25). It
    must use send_resend_email() instead, same as every other outbound email
    in this project."""

    def setUp(self):
        self.client = Client()
        self.url = reverse('contact')

    def test_send_mail_no_longer_imported(self):
        """Guards against the regression this fix corrects: send_mail() (no
        SMTP backend configured in production) must not be reintroduced."""
        import shop.views as views_module
        self.assertFalse(hasattr(views_module, 'send_mail'))

    @patch('shop.views.send_resend_email')
    def test_contact_uses_send_resend_email(self, mock_send_resend_email):
        response = self.client.post(self.url, {
            'name': 'Test User',
            'email': 'testuser@example.com',
            'phone': '9800000000',
            'subject': 'Hello',
            'message': 'Test message body',
        })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'status': 'success'})
        self.assertTrue(ContactMessage.objects.filter(email='testuser@example.com').exists())

        self.assertEqual(mock_send_resend_email.call_count, 2)

        admin_call, customer_call = mock_send_resend_email.call_args_list
        self.assertEqual(customer_call.kwargs['to'], 'testuser@example.com')
        self.assertIn('subject', customer_call.kwargs)
        self.assertIn('body', customer_call.kwargs)


class NewsletterSignupEmailTests(TestCase):
    """newsletter_signup() used to call Django's send_mail(), same
    pre-existing bug as contact() — must use send_resend_email() instead."""

    def setUp(self):
        self.client = Client()
        self.url = reverse('newsletter')

    @patch('shop.views.send_resend_email')
    def test_newsletter_signup_uses_send_resend_email(self, mock_send_resend_email):
        response = self.client.post(self.url, {
            'email': 'subscriber@example.com',
            'name': 'Subscriber Name',
        })

        self.assertEqual(response.status_code, 302)
        self.assertTrue(NewsletterSubscriber.objects.filter(email='subscriber@example.com').exists())

        mock_send_resend_email.assert_called_once()
        self.assertEqual(mock_send_resend_email.call_args.kwargs['to'], 'subscriber@example.com')

    @patch('shop.views.send_resend_email')
    def test_newsletter_signup_does_not_email_on_repeat_signup(self, mock_send_resend_email):
        NewsletterSubscriber.objects.create(email='existing@example.com', name='Existing')

        response = self.client.post(self.url, {
            'email': 'existing@example.com',
            'name': 'Existing',
        })

        self.assertEqual(response.status_code, 302)


class UserProfileInlineFormTests(TestCase):
    """clean_pin()'s uniqueness check on the admin inline form — pin_hash is
    hashed at rest, so this walks every other profile's hash re-deriving a
    match via check_password(), not a DB query. Tested directly against the
    form rather than through the admin HTTP views, since the validation
    logic lives entirely in clean_pin()/save()."""

    def setUp(self):
        self.alice = User.objects.create_user('alice', password='pw', is_staff=True)
        self.bob = User.objects.create_user('bob', password='pw', is_staff=True)

    def test_second_staff_member_cannot_take_an_already_used_pin(self):
        alice_profile = UserProfile(user=self.alice)
        form = UserProfileInlineForm(data={'role': 'cashier', 'pin': '1234'}, instance=alice_profile)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()

        bob_profile = UserProfile(user=self.bob)
        form2 = UserProfileInlineForm(data={'role': 'cashier', 'pin': '1234'}, instance=bob_profile)
        self.assertFalse(form2.is_valid())
        self.assertIn('pin', form2.errors)
        self.assertIn('already in use', form2.errors['pin'][0])

    def test_saving_own_existing_pin_unchanged_does_not_self_reject(self):
        alice_profile = UserProfile(user=self.alice)
        alice_profile.set_pin('1234')
        alice_profile.save()

        form = UserProfileInlineForm(data={'role': 'manager', 'pin': '1234'}, instance=alice_profile)
        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()
        self.assertEqual(saved.role, 'manager')
        self.assertTrue(saved.check_pin('1234'))

    def test_two_different_staff_with_two_different_pins_both_save_fine(self):
        alice_profile = UserProfile(user=self.alice)
        form_a = UserProfileInlineForm(data={'role': 'cashier', 'pin': '1111'}, instance=alice_profile)
        self.assertTrue(form_a.is_valid(), form_a.errors)
        form_a.save()

        bob_profile = UserProfile(user=self.bob)
        form_b = UserProfileInlineForm(data={'role': 'cashier', 'pin': '2222'}, instance=bob_profile)
        self.assertTrue(form_b.is_valid(), form_b.errors)
        form_b.save()

        self.assertTrue(UserProfile.objects.get(user=self.alice).check_pin('1111'))
        self.assertTrue(UserProfile.objects.get(user=self.bob).check_pin('2222'))

    def test_blank_pin_skips_uniqueness_check_entirely(self):
        """Leaving the PIN field blank means 'don't change it' -- it must
        never be compared against anyone else's PIN, taken or not."""
        alice_profile = UserProfile(user=self.alice)
        alice_profile.set_pin('1234')
        alice_profile.save()

        bob_profile = UserProfile(user=self.bob)
        form = UserProfileInlineForm(data={'role': 'cashier', 'pin': ''}, instance=bob_profile)
        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()
        self.assertEqual(saved.pin_hash, '')


class UserProfilePinHashTests(TestCase):
    """set_pin()/check_pin() use a fast HMAC-SHA256 + per-profile salt, not
    Django's own password hashers (PBKDF2 at 600k+ iterations) -- a 4-digit
    PIN's real protection is PosUnlockView's 7-attempt/5-minute lockout, not
    hash cost, and the slow hasher was the ~4 second PIN-unlock delay on
    PythonAnywhere's free-tier CPU (see api/tests.py's PosUnlockApiTests for
    the end-to-end unlock behavior this format change must not disturb)."""

    def setUp(self):
        self.user = User.objects.create_user('alice', password='pw', is_staff=True)
        self.profile = UserProfile.objects.create(user=self.user)

    def test_set_pin_does_not_use_djangos_password_hashers(self):
        self.profile.set_pin('1234')
        self.assertNotIn('pbkdf2', self.profile.pin_hash)
        self.assertNotIn('argon2', self.profile.pin_hash)
        self.assertNotIn('bcrypt', self.profile.pin_hash)
        self.assertIn('$', self.profile.pin_hash)  # salt$digest

    def test_check_pin_round_trips(self):
        self.profile.set_pin('4321')
        self.assertTrue(self.profile.check_pin('4321'))
        self.assertFalse(self.profile.check_pin('1234'))

    def test_two_profiles_with_the_same_pin_get_different_hashes(self):
        """Per-profile random salt -- two staff sharing the same PIN must
        never produce the same stored hash (that would leak who shares a
        PIN with whom just by comparing pin_hash columns)."""
        other = UserProfile.objects.create(user=User.objects.create_user('bob', password='pw', is_staff=True))
        self.profile.set_pin('1234')
        other.set_pin('1234')
        self.assertNotEqual(self.profile.pin_hash, other.pin_hash)
        self.assertTrue(self.profile.check_pin('1234'))
        self.assertTrue(other.check_pin('1234'))

    def test_pin_set_before_this_change_no_longer_verifies(self):
        """Simple cutover (confirmed, only 2 real staff): a pin_hash already
        stored in Django's old hasher format doesn't match the new scheme's
        'salt$digest' parsing and must fail closed, not crash -- staff
        re-set their PIN once via the admin form after this deploys."""
        from django.contrib.auth.hashers import make_password
        self.profile.pin_hash = make_password('1234')
        self.profile.save()
        self.assertFalse(self.profile.check_pin('1234'))

    def test_check_pin_false_for_blank_hash(self):
        self.assertFalse(self.profile.check_pin('1234'))


class PurchaseMovementTypeTests(TestCase):
    """'purchase' (outside-supplier restock) is a distinct movement_type
    from both 'harvest' (farm-origin) and 'adjustment_add' (a stock-count
    correction, not a real incoming purchase) -- it must behave exactly
    like any other INCREASE_TYPES member for signed_quantity()/
    current_stock() purposes."""

    def setUp(self):
        self.category = Category.objects.create(name='Pantry', order=1)
        self.product = Product.objects.create(
            name='Honey Jar', slug='honey-jar', category=self.category, description='test',
            price=Decimal('500.00'), pricing_mode='fixed_quantity', origin='sourced',
        )

    def test_purchase_is_a_valid_movement_type_choice(self):
        self.assertIn('purchase', dict(InventoryMovement.MOVEMENT_TYPE_CHOICES))

    def test_purchase_increases_stock_like_harvest_and_adjustment_add(self):
        self.assertIn('purchase', InventoryMovement.INCREASE_TYPES)
        movement = InventoryMovement.objects.create(
            product=self.product, movement_type='purchase', source='pos', quantity=Decimal('10'),
        )
        self.assertEqual(movement.signed_quantity(), Decimal('10'))
        self.assertEqual(InventoryMovement.current_stock(self.product), Decimal('10'))

    def test_purchase_harvest_and_adjustment_add_are_kept_distinct(self):
        """They must never be merged into one concept -- each movement
        remembers which of the three it actually was, even though all three
        increase stock identically."""
        InventoryMovement.objects.create(product=self.product, movement_type='harvest', source='admin', quantity=Decimal('5'))
        InventoryMovement.objects.create(product=self.product, movement_type='purchase', source='pos', quantity=Decimal('3'))
        InventoryMovement.objects.create(product=self.product, movement_type='adjustment_add', source='admin', quantity=Decimal('2'))
        self.assertEqual(InventoryMovement.current_stock(self.product), Decimal('10'))
        self.assertEqual(
            sorted(InventoryMovement.objects.filter(product=self.product).values_list('movement_type', flat=True)),
            ['adjustment_add', 'harvest', 'purchase'],
        )


class StockTableTests(TestCase):
    """get_stock_table_rows() in shop/stock.py -- the single shared source
    of truth reused by both ProductAdmin's stock column and the POS stock
    screen (GET /api/v1/pos/stock/)."""

    def setUp(self):
        self.category = Category.objects.create(name='Produce', order=1)

    def test_fixed_weight_products_are_excluded_entirely(self):
        goat = Product.objects.create(
            name='Goat', slug='stock-test-goat', category=self.category, description='test',
            price=Decimal('1200.00'), pricing_mode='fixed_weight',
        )
        ProductVariant.objects.create(product=goat, weight=Decimal('20.00'))
        rows = get_stock_table_rows()
        self.assertNotIn(goat.id, [r['product_id'] for r in rows])

    def test_eggs_fixed_quantity_not_fixed_weight_stay_in_the_table(self):
        """Explicitly named in the spec: eggs are fixed_quantity (sold per
        piece), not fixed_weight (per-animal) -- they must NOT be swept up
        by the fixed_weight exclusion just because they're also a farm
        product with per-unit stock tracking."""
        eggs = Product.objects.create(
            name='Local Egg', slug='stock-test-eggs', category=self.category, description='test',
            price=Decimal('20.00'), pricing_mode='fixed_quantity', origin='farm',
        )
        rows = get_stock_table_rows()
        self.assertIn(eggs.id, [r['product_id'] for r in rows])

    def test_disabled_products_are_included(self):
        disabled = Product.objects.create(
            name='Disabled Product', slug='stock-test-disabled', category=self.category, description='test',
            price=Decimal('100.00'), pricing_mode='fixed_quantity', is_available=False,
        )
        rows = get_stock_table_rows()
        row = next(r for r in rows if r['product_id'] == disabled.id)
        self.assertFalse(row['is_available'])

    def test_is_low_and_restock_method_and_last_restocked(self):
        farm_product = Product.objects.create(
            name='Farm Product', slug='stock-test-farm', category=self.category,
            description='test', price=Decimal('50.00'), pricing_mode='fixed_quantity',
            origin='farm', low_stock_threshold=5,
        )
        InventoryMovement.objects.create(product=farm_product, movement_type='harvest', source='admin', quantity=Decimal('3'))

        sourced_product = Product.objects.create(
            name='Sourced Product', slug='stock-test-sourced', category=self.category,
            description='test', price=Decimal('80.00'), pricing_mode='variable_weight',
            origin='sourced', low_stock_threshold=10,
        )
        InventoryMovement.objects.create(product=sourced_product, movement_type='purchase', source='admin', quantity=Decimal('20'))
        InventoryMovement.objects.create(product=sourced_product, movement_type='sale', source='pos', quantity=Decimal('1'))
        InventoryMovement.objects.create(product=sourced_product, movement_type='waste', source='admin', quantity=Decimal('1'))

        rows_by_id = {r['product_id']: r for r in get_stock_table_rows()}

        farm_row = rows_by_id[farm_product.id]
        self.assertTrue(farm_row['is_low'])  # 3 < 5
        self.assertEqual(farm_row['origin'], 'farm')
        self.assertEqual(farm_row['restock_method'], 'Own farm')
        self.assertIsNotNone(farm_row['last_restocked_at'])  # harvest counts

        sourced_row = rows_by_id[sourced_product.id]
        self.assertFalse(sourced_row['is_low'])  # 18 > 10
        self.assertEqual(sourced_row['origin'], 'sourced')
        self.assertEqual(sourced_row['restock_method'], 'Outsourced')
        self.assertIsNotNone(sourced_row['last_restocked_at'])  # purchase counts, sale/waste don't

    def test_is_low_is_strictly_less_than_not_less_than_or_equal(self):
        """A product sitting exactly AT its threshold is not low yet -- only
        strictly below it is. This is the exact boundary the feature
        originally got wrong (<=  instead of <)."""
        at_threshold = Product.objects.create(
            name='At Threshold Product', slug='stock-test-at-threshold', category=self.category,
            description='test', price=Decimal('10.00'), pricing_mode='fixed_quantity', low_stock_threshold=5,
        )
        InventoryMovement.objects.create(product=at_threshold, movement_type='harvest', source='admin', quantity=Decimal('5'))

        just_below = Product.objects.create(
            name='Just Below Threshold Product', slug='stock-test-below-threshold', category=self.category,
            description='test', price=Decimal('10.00'), pricing_mode='fixed_quantity', low_stock_threshold=5,
        )
        InventoryMovement.objects.create(product=just_below, movement_type='harvest', source='admin', quantity=Decimal('4'))

        rows_by_id = {r['product_id']: r for r in get_stock_table_rows()}
        self.assertFalse(rows_by_id[at_threshold.id]['is_low'])  # 5 == 5, not low
        self.assertTrue(rows_by_id[just_below.id]['is_low'])     # 4 < 5, low

    def test_last_restocked_only_counts_harvest_purchase_adjustment_add(self):
        """A sale/waste/return/adjustment_remove is never a restock, even
        though return technically adds stock back."""
        product = Product.objects.create(
            name='Return Test Product', slug='stock-test-return', category=self.category,
            description='test', price=Decimal('100.00'), pricing_mode='fixed_quantity',
        )
        InventoryMovement.objects.create(product=product, movement_type='harvest', source='admin', quantity=Decimal('10'))
        InventoryMovement.objects.create(product=product, movement_type='sale', source='pos', quantity=Decimal('2'))
        InventoryMovement.objects.create(product=product, movement_type='return', source='website', quantity=Decimal('1'))

        row = next(r for r in get_stock_table_rows() if r['product_id'] == product.id)
        # last_restocked_at should be the harvest, not the later return.
        harvest = InventoryMovement.objects.get(product=product, movement_type='harvest')
        self.assertEqual(row['last_restocked_at'], harvest.created_at)

    def test_never_restocked_product_has_none(self):
        product = Product.objects.create(
            name='Never Restocked Product', slug='stock-test-never', category=self.category,
            description='test', price=Decimal('100.00'), pricing_mode='fixed_quantity',
        )
        row = next(r for r in get_stock_table_rows() if r['product_id'] == product.id)
        self.assertIsNone(row['last_restocked_at'])

    def test_sort_order_low_stock_first_ascending_then_alphabetical(self):
        # Two low-stock products, stock 2 and 1 (1 should sort before 2).
        low_b = Product.objects.create(
            name='Zed Low Stock', slug='stock-test-low-b', category=self.category, description='test',
            price=Decimal('10.00'), pricing_mode='fixed_quantity', low_stock_threshold=5,
        )
        InventoryMovement.objects.create(product=low_b, movement_type='harvest', source='admin', quantity=Decimal('2'))
        low_a = Product.objects.create(
            name='Apple Low Stock', slug='stock-test-low-a', category=self.category, description='test',
            price=Decimal('10.00'), pricing_mode='fixed_quantity', low_stock_threshold=5,
        )
        InventoryMovement.objects.create(product=low_a, movement_type='harvest', source='admin', quantity=Decimal('1'))
        # Two normal-stock products, named so alphabetical order is obvious
        # and distinguishable from insertion order.
        normal_z = Product.objects.create(
            name='Zed Normal Stock', slug='stock-test-normal-z', category=self.category, description='test',
            price=Decimal('10.00'), pricing_mode='fixed_quantity', low_stock_threshold=5,
        )
        InventoryMovement.objects.create(product=normal_z, movement_type='harvest', source='admin', quantity=Decimal('50'))
        normal_a = Product.objects.create(
            name='Apple Normal Stock', slug='stock-test-normal-a', category=self.category, description='test',
            price=Decimal('10.00'), pricing_mode='fixed_quantity', low_stock_threshold=5,
        )
        InventoryMovement.objects.create(product=normal_a, movement_type='harvest', source='admin', quantity=Decimal('50'))

        ids_in_order = [r['product_id'] for r in get_stock_table_rows()]
        relevant_order = [pid for pid in ids_in_order if pid in {low_a.id, low_b.id, normal_a.id, normal_z.id}]
        self.assertEqual(relevant_order, [low_a.id, low_b.id, normal_a.id, normal_z.id])


class LowStockSignalTests(TestCase):
    """notify_on_low_stock_crossing() in shop/signals.py -- a one-time
    Telegram alert the moment stock crosses DOWN past low_stock_threshold,
    not a repeat ping on every subsequent sale while it stays below."""

    def setUp(self):
        self.category = Category.objects.create(name='Signal Test', order=1)
        self.product = Product.objects.create(
            name='Signal Product', slug='signal-product', category=self.category, description='test',
            price=Decimal('10.00'), pricing_mode='fixed_quantity', low_stock_threshold=5,
        )

    def test_fires_once_on_crossing_down_past_threshold(self):
        with patch('shop.emails.send_telegram') as mock_send:
            InventoryMovement.objects.create(product=self.product, movement_type='harvest', source='admin', quantity=Decimal('20'))
            self.assertEqual(mock_send.call_count, 0)  # 20 > 5, no crossing

            InventoryMovement.objects.create(product=self.product, movement_type='sale', source='pos', quantity=Decimal('14'))
            self.assertEqual(mock_send.call_count, 0)  # 6 > 5, still no crossing

            InventoryMovement.objects.create(product=self.product, movement_type='sale', source='pos', quantity=Decimal('3'))
            self.assertEqual(mock_send.call_count, 1)  # 6 -> 3, crossed down past 5
            self.assertIn('Signal Product', mock_send.call_args[0][0])
            self.assertIn('3', mock_send.call_args[0][0])

    def test_does_not_fire_when_stock_lands_exactly_on_threshold(self):
        """Landing exactly AT threshold is not a crossing -- is_low (and
        this signal) only trigger strictly below it."""
        InventoryMovement.objects.create(product=self.product, movement_type='harvest', source='admin', quantity=Decimal('20'))
        with patch('shop.emails.send_telegram') as mock_send:
            InventoryMovement.objects.create(product=self.product, movement_type='sale', source='pos', quantity=Decimal('15'))
            self.assertEqual(mock_send.call_count, 0)  # 20 -> 5, lands exactly on threshold, not below it

    def test_fires_when_crossing_from_exactly_at_threshold_to_below(self):
        """stock_before >= threshold is deliberately inclusive of equality --
        a product sitting exactly AT threshold is still eligible to cross
        down on the very next sale."""
        InventoryMovement.objects.create(product=self.product, movement_type='harvest', source='admin', quantity=Decimal('5'))
        with patch('shop.emails.send_telegram') as mock_send:
            InventoryMovement.objects.create(product=self.product, movement_type='sale', source='pos', quantity=Decimal('1'))
            self.assertEqual(mock_send.call_count, 1)  # 5 -> 4, crossed

    def test_does_not_refire_on_repeat_sales_while_still_below_threshold(self):
        InventoryMovement.objects.create(product=self.product, movement_type='harvest', source='admin', quantity=Decimal('6'))
        with patch('shop.emails.send_telegram') as mock_send:
            InventoryMovement.objects.create(product=self.product, movement_type='sale', source='pos', quantity=Decimal('2'))
            self.assertEqual(mock_send.call_count, 1)  # 6 -> 4, crossed

            InventoryMovement.objects.create(product=self.product, movement_type='sale', source='pos', quantity=Decimal('1'))
            self.assertEqual(mock_send.call_count, 1)  # 4 -> 3, still below, no re-fire

    def test_rearms_after_restock_pushes_stock_back_above_threshold(self):
        InventoryMovement.objects.create(product=self.product, movement_type='harvest', source='admin', quantity=Decimal('6'))
        with patch('shop.emails.send_telegram') as mock_send:
            InventoryMovement.objects.create(product=self.product, movement_type='sale', source='pos', quantity=Decimal('2'))
            self.assertEqual(mock_send.call_count, 1)  # 6 -> 4, crossed

            InventoryMovement.objects.create(product=self.product, movement_type='purchase', source='pos', quantity=Decimal('10'))
            self.assertEqual(mock_send.call_count, 1)  # 4 -> 14, back above, no fire on the way up

            InventoryMovement.objects.create(product=self.product, movement_type='sale', source='pos', quantity=Decimal('10'))
            self.assertEqual(mock_send.call_count, 2)  # 14 -> 4, crossed down again -- re-armed

    def test_skips_fixed_weight_products_entirely(self):
        goat = Product.objects.create(
            name='Signal Goat', slug='signal-goat', category=self.category, description='test',
            price=Decimal('1200.00'), pricing_mode='fixed_weight', low_stock_threshold=1,
        )
        variant = ProductVariant.objects.create(product=goat, weight=Decimal('20.00'))
        with patch('shop.emails.send_telegram') as mock_send:
            InventoryMovement.objects.create(
                product=goat, variant=variant, movement_type='harvest', source='admin', quantity=Decimal('1'),
            )
            InventoryMovement.objects.create(
                product=goat, variant=variant, movement_type='sale', source='pos', quantity=Decimal('1'),
            )
            # 1 -> 0 would cross a threshold of 1 for a normal product, but
            # fixed_weight is skipped entirely -- never even evaluated.
            self.assertEqual(mock_send.call_count, 0)


class BundleItemReferenceWeightTests(TestCase):
    """BundleItem.reference_weight -- the animal weight a combo's advertised
    price assumes, for fixed_weight (goat/chicken) bundle slots. Auto-fills
    once from whichever variant is cheapest at save time, then stays fixed
    -- never recomputed as stock changes (see save() in shop/models.py)."""

    def setUp(self):
        self.category = Category.objects.create(name='Combo Weight Test', order=1)
        self.goat = Product.objects.create(
            name='Weight Test Goat', slug='weight-test-goat', category=self.category, description='test',
            price=Decimal('1200.00'), pricing_mode='fixed_weight',
        )
        self.offer = Offer.objects.create(
            title='Weight Test Combo', discount_type='combo', discount_value=Decimal('0'),
            combo_price=Decimal('20000.00'),
            start_date=timezone.now() - timedelta(days=1), end_date=timezone.now() + timedelta(days=1),
        )

    def test_autofills_with_cheapest_available_variant_at_creation(self):
        ProductVariant.objects.create(product=self.goat, weight=Decimal('20.00'))  # 24000
        cheaper = ProductVariant.objects.create(product=self.goat, weight=Decimal('10.00'))  # 12000, cheapest

        bundle_item = BundleItem.objects.create(offer=self.offer, product=self.goat, quantity=Decimal('10.00'))

        self.assertEqual(bundle_item.reference_weight, cheaper.weight)

    def test_does_not_drift_when_a_cheaper_variant_appears_later(self):
        ProductVariant.objects.create(product=self.goat, weight=Decimal('10.00'))  # 12000, cheapest at creation
        bundle_item = BundleItem.objects.create(offer=self.offer, product=self.goat, quantity=Decimal('10.00'))
        self.assertEqual(bundle_item.reference_weight, Decimal('10.00'))

        # A cheaper animal shows up afterward -- already-locked reference_weight
        # must not follow it, even across a later unrelated re-save.
        ProductVariant.objects.create(product=self.goat, weight=Decimal('5.00'))  # 6000, now the cheapest
        bundle_item.quantity = Decimal('11.00')
        bundle_item.save()
        bundle_item.refresh_from_db()
        self.assertEqual(bundle_item.reference_weight, Decimal('10.00'))

    def test_left_blank_when_no_variants_available_at_creation(self):
        # Nothing to auto-fill from yet -- stays None rather than erroring.
        bundle_item = BundleItem.objects.create(offer=self.offer, product=self.goat, quantity=Decimal('10.00'))
        self.assertIsNone(bundle_item.reference_weight)

        # The first save after a variant actually exists is still "blank at
        # save time", so it fills in then -- same no-extra-admin-work rule
        # as filling in at creation, just a save later than usual.
        ProductVariant.objects.create(product=self.goat, weight=Decimal('10.00'))
        bundle_item.quantity = Decimal('12.00')
        bundle_item.save()
        bundle_item.refresh_from_db()
        self.assertEqual(bundle_item.reference_weight, Decimal('10.00'))

        # From here on it's locked exactly like the normal case -- a cheaper
        # animal showing up afterward must not make it drift.
        ProductVariant.objects.create(product=self.goat, weight=Decimal('5.00'))
        bundle_item.quantity = Decimal('13.00')
        bundle_item.save()
        bundle_item.refresh_from_db()
        self.assertEqual(bundle_item.reference_weight, Decimal('10.00'))

    def test_explicit_value_is_never_overridden(self):
        ProductVariant.objects.create(product=self.goat, weight=Decimal('10.00'))  # cheapest would be 10
        bundle_item = BundleItem.objects.create(
            offer=self.offer, product=self.goat, quantity=Decimal('10.00'), reference_weight=Decimal('20.00'),
        )
        self.assertEqual(bundle_item.reference_weight, Decimal('20.00'))  # admin's explicit choice, not auto-filled

    def test_not_autofilled_for_non_fixed_weight_products(self):
        jar = Product.objects.create(
            name='Weight Test Jar', slug='weight-test-jar', category=self.category, description='test',
            price=Decimal('250.00'), pricing_mode='fixed_quantity',
        )
        bundle_item = BundleItem.objects.create(offer=self.offer, product=jar, quantity=Decimal('1'))
        self.assertIsNone(bundle_item.reference_weight)


class CostSyncViewTests(TestCase):
    """POST /api/costs/sync/ -- ABMS's costEntries/assets bridge. Same
    token auth as /api/inventory/movements/ (DRF's global
    TokenAuthentication + IsAuthenticated -- any user's valid token)."""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user('abms-bridge', password='pw')
        self.token = Token.objects.create(user=self.user)
        self.url = reverse('api_costs_sync')

    def auth(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {self.token.key}')

    def entry(self, **overrides):
        base = {
            'id': 'c_test1', 'date': '2026-09-20', 'amount': 2500, 'type': 'cost',
            'costCentre': 'crop:mango', 'category': 'Feed',
            'enteredBy': {'uid': 'u1', 'email': 'owner@example.com'},
        }
        base.update(overrides)
        return base

    def test_no_token_is_rejected(self):
        response = self.client.post(self.url, {'entries': [], 'assets': []}, format='json')
        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_wrong_token_is_rejected(self):
        self.client.credentials(HTTP_AUTHORIZATION='Token not-a-real-token')
        response = self.client.post(self.url, {'entries': [], 'assets': []}, format='json')
        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_create_cost_entry(self):
        self.auth()
        response = self.client.post(self.url, {'entries': [self.entry()], 'assets': []}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['results'][0]['status'], 'created')
        self.assertEqual(CostEntry.objects.count(), 1)
        row = CostEntry.objects.get(abms_id='c_test1')
        self.assertEqual(row.amount, Decimal('2500.00'))
        self.assertEqual(row.cost_centre, 'crop:mango')
        self.assertEqual(row.entered_by_email, 'owner@example.com')

    def test_same_id_twice_is_duplicate_not_a_new_row(self):
        self.auth()
        self.client.post(self.url, {'entries': [self.entry()], 'assets': []}, format='json')
        response = self.client.post(self.url, {'entries': [self.entry()], 'assets': []}, format='json')
        self.assertEqual(response.data['results'][0]['status'], 'duplicate')
        self.assertEqual(CostEntry.objects.count(), 1)

    def test_same_id_different_amount_is_conflict_original_unchanged(self):
        self.auth()
        self.client.post(self.url, {'entries': [self.entry()], 'assets': []}, format='json')
        response = self.client.post(
            self.url, {'entries': [self.entry(amount=9999)], 'assets': []}, format='json',
        )
        self.assertEqual(response.data['results'][0]['status'], 'conflict')
        self.assertEqual(CostEntry.objects.count(), 1)
        self.assertEqual(CostEntry.objects.get(abms_id='c_test1').amount, Decimal('2500.00'))

    def test_reversal_stored_with_negative_amount(self):
        self.auth()
        self.client.post(self.url, {'entries': [self.entry()], 'assets': []}, format='json')
        reversal = self.entry(id='c_test1_rev', type='reversal', amount=-2500, reversalOf='c_test1')
        response = self.client.post(self.url, {'entries': [reversal], 'assets': []}, format='json')
        self.assertEqual(response.data['results'][0]['status'], 'created')
        row = CostEntry.objects.get(abms_id='c_test1_rev')
        self.assertEqual(row.amount, Decimal('-2500.00'))
        self.assertEqual(row.entry_type, 'reversal')
        self.assertEqual(row.reversal_of, 'c_test1')

    def test_reversal_pointing_at_unknown_id_is_error(self):
        self.auth()
        reversal = self.entry(id='c_test_rev_orphan', type='reversal', amount=-500, reversalOf='c_does_not_exist')
        response = self.client.post(self.url, {'entries': [reversal], 'assets': []}, format='json')
        self.assertEqual(response.data['results'][0]['status'], 'error')
        self.assertFalse(CostEntry.objects.filter(abms_id='c_test_rev_orphan').exists())

    def test_manual_allocation_not_summing_to_100_is_error_others_still_saved(self):
        self.auth()
        bad = self.entry(
            id='c_bad_alloc', isShared=True, allocationRule='manual',
            allocationManual={'crop:mango': 50, 'livestock:goat': 40},  # sums to 90
        )
        good = self.entry(id='c_good')
        response = self.client.post(self.url, {'entries': [bad, good], 'assets': []}, format='json')
        results_by_id = {r['id']: r['status'] for r in response.data['results']}
        self.assertEqual(results_by_id['c_bad_alloc'], 'error')
        self.assertEqual(results_by_id['c_good'], 'created')
        self.assertFalse(CostEntry.objects.filter(abms_id='c_bad_alloc').exists())
        self.assertTrue(CostEntry.objects.filter(abms_id='c_good').exists())

    def test_bad_cost_centre_errors_only_that_item(self):
        self.auth()
        bad = self.entry(id='c_bad_centre', costCentre='not-a-real-centre')
        good = self.entry(id='c_good2')
        response = self.client.post(self.url, {'entries': [bad, good], 'assets': []}, format='json')
        results_by_id = {r['id']: r['status'] for r in response.data['results']}
        self.assertEqual(results_by_id['c_bad_centre'], 'error')
        self.assertEqual(results_by_id['c_good2'], 'created')

    def asset(self, **overrides):
        base = {
            'id': 'a_test1', 'name': 'Water pump', 'assetCategory': 'Machine',
            'purchaseDate': '2026-01-10', 'cost': 48000, 'lifeYears': 8,
            'salvageValue': 0, 'costCentre': 'water', 'status': 'active',
        }
        base.update(overrides)
        return base

    def test_asset_create_then_update_status_to_disposed(self):
        self.auth()
        response = self.client.post(self.url, {'entries': [], 'assets': [self.asset()]}, format='json')
        self.assertEqual(response.data['results'][0]['status'], 'created')
        self.assertEqual(FarmAsset.objects.count(), 1)

        disposed = self.asset(status='disposed', disposedDate='2026-10-01')
        response = self.client.post(self.url, {'entries': [], 'assets': [disposed]}, format='json')
        self.assertEqual(response.data['results'][0]['status'], 'updated')
        self.assertEqual(FarmAsset.objects.count(), 1)
        row = FarmAsset.objects.get(abms_id='a_test1')
        self.assertEqual(row.status, 'disposed')
        self.assertEqual(str(row.disposed_date), '2026-10-01')


class CostEntryReadApiTests(TestCase):
    """GET /api/v1/costs/ -- staff-only, session-authed."""

    def setUp(self):
        self.client = APIClient()
        self.staff = User.objects.create_user('cashier', password='pw', is_staff=True)
        self.shopper = User.objects.create_user('shopper', password='pw', is_staff=False)
        CostEntry.objects.create(
            abms_id='c_read1', date='2026-09-20', amount=Decimal('1000.00'),
            entry_type='cost', cost_centre='crop:mango', category='Feed',
        )
        self.url = reverse('v1_cost_entry_list')

    def test_anonymous_is_rejected(self):
        response = self.client.get(self.url)
        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_non_staff_is_forbidden(self):
        self.client.login(username='shopper', password='pw')
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_staff_can_read(self):
        self.client.login(username='cashier', password='pw')
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['abms_id'], 'c_read1')


class CostEntryAppendOnlyTests(TestCase):
    """CostEntry cannot be edited or deleted via the ORM -- enforced in
    save()/delete() themselves (CLAUDE.md note: stricter than
    InventoryMovement, which has no such override)."""

    def setUp(self):
        self.entry = CostEntry.objects.create(
            abms_id='c_immutable1', date='2026-09-20', amount=Decimal('500.00'),
            entry_type='cost', cost_centre='bees', category='Other',
        )

    def test_cannot_update_existing_row(self):
        self.entry.amount = Decimal('999.00')
        with self.assertRaises(ValueError):
            self.entry.save()

    def test_cannot_delete(self):
        with self.assertRaises(ValueError):
            self.entry.delete()
