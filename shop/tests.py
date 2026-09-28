from unittest.mock import patch

from django.test import TestCase, Client
from django.urls import reverse

from .models import ContactMessage, NewsletterSubscriber


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
        mock_send_resend_email.assert_not_called()
