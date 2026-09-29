import json
import uuid
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, Client
from django.urls import reverse

from .admin import UserProfileInlineForm
from .models import (
    Category, Customer, InventoryMovement, ContactMessage, NewsletterSubscriber, POSSale,
    Product, UserProfile,
)


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


class PosCreateSaleViewTests(TestCase):
    """pos_create_sale() — the traditional view templates/pos.html actually
    calls. Thin parity coverage confirming it's correctly wired to the same
    create_pos_sale() helper POSSaleApiTests already covers in depth,
    including that the currently-live "Missing client_sale_id" bug (every
    real click on Complete Sale used to 400, since pos.html never sent
    one) is fixed on this exact path."""

    def setUp(self):
        self.client = Client()
        self.staff_user = User.objects.create_user('till1', password='pw', is_staff=True)
        self.operator = UserProfile.objects.create(user=self.staff_user, role='cashier')
        self.operator_pin = '9182'
        self.operator.set_pin(self.operator_pin)
        self.operator.save()
        self.client.login(username='till1', password='pw')

        self.category = Category.objects.create(name='Honey', order=1)
        self.jar = Product.objects.create(
            name='Honey Jar', slug='honey-jar', category=self.category,
            description='Raw honey', price=Decimal('400.00'), pricing_mode='fixed_quantity',
        )
        InventoryMovement.objects.create(
            product=self.jar, movement_type='harvest', source='admin', quantity=Decimal('10'),
        )

    def unlock_terminal(self):
        response = self.client.post(
            reverse('v1_pos_unlock'), data=json.dumps({'pin': self.operator_pin}), content_type='application/json',
        )
        assert response.status_code == 200, response.json()

    def post_sale(self, **overrides):
        payload = {
            'client_sale_id': str(uuid.uuid4()),
            'payments': [{'method': 'cash', 'amount': '400.00'}],
            'cart': [{'product_id': self.jar.id, 'qty': 1}],
        }
        payload.update(overrides)
        return self.client.post(
            reverse('pos_create_sale'), data=json.dumps(payload), content_type='application/json',
        )

    def test_full_cash_sale_works_end_to_end(self):
        self.unlock_terminal()
        response = self.post_sale()
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['status'], 'ok')
        self.assertEqual(Decimal(data['total']), Decimal('400.00'))
        self.assertEqual(InventoryMovement.current_stock(self.jar), Decimal('9'))

        sale = POSSale.objects.get(sale_number=data['sale_number'])
        self.assertEqual(sale.cashier, self.staff_user)
        self.assertEqual(sale.payment_method, 'cash')

    def test_missing_client_sale_id_is_still_rejected(self):
        self.unlock_terminal()
        response = self.post_sale(client_sale_id=None)
        self.assertEqual(response.status_code, 400)
        self.assertIn('client_sale_id', response.json()['message'])

    def test_sale_rejected_without_prior_unlock(self):
        """Logged into the terminal but no PIN entered yet this session."""
        response = self.post_sale()
        self.assertEqual(response.status_code, 403)
        self.assertFalse(POSSale.objects.exists())

    def test_credit_sale_requires_customer(self):
        self.unlock_terminal()
        response = self.post_sale(payments=[{'method': 'credit', 'amount': '400.00'}])
        self.assertEqual(response.status_code, 400)

    def test_credit_sale_with_customer_creates_ledger_row(self):
        from .models import CreditTransaction
        self.unlock_terminal()
        credit_customer = Customer.objects.create(name='Bina Karki', phone='9844444444')
        response = self.post_sale(
            customer_id=credit_customer.id,
            payments=[{'method': 'credit', 'amount': '400.00'}],
        )
        self.assertEqual(response.status_code, 200, response.json())
        self.assertEqual(credit_customer.outstanding_balance(), Decimal('400.00'))
        self.assertTrue(CreditTransaction.objects.filter(customer=credit_customer).exists())

    def test_lock_endpoint_blocks_further_sales_until_unlocked_again(self):
        self.unlock_terminal()
        lock_response = self.client.post(reverse('v1_pos_lock'))
        self.assertEqual(lock_response.status_code, 200)

        blocked = self.post_sale()
        self.assertEqual(blocked.status_code, 403)

        self.unlock_terminal()
        allowed = self.post_sale()
        self.assertEqual(allowed.status_code, 200, allowed.json())
