from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, Client
from django.urls import reverse

from .admin import UserProfileInlineForm
from .models import ContactMessage, NewsletterSubscriber, UserProfile


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
