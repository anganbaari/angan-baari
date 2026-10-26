"""Tests for the token-authed, read-only /api/rudra/ endpoints
(api/rudra_views.py, api.permissions.IsRudraToken)."""
from django.contrib.auth.models import User
from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from .rudra_views import ESTIMATE_CAVEAT, PL_CAVEAT, POS_ONLY_CAVEAT
from .tests import ApiTestBase

TOKEN = 'r' * 40
ENDPOINTS = ['rudra_sales_summary', 'rudra_sales_payments', 'rudra_sales_products', 'rudra_pl']


@override_settings(RUDRA_API_TOKEN=TOKEN)
class RudraApiTests(ApiTestBase):
    def auth(self, token=TOKEN):
        return {'HTTP_AUTHORIZATION': f'Bearer {token}'}

    def test_no_token_denied(self):
        for name in ENDPOINTS:
            self.assertIn(self.client.get(reverse(name)).status_code, (401, 403), name)

    def test_wrong_token_and_wrong_scheme_denied(self):
        for name in ENDPOINTS:
            url = reverse(name)
            self.assertIn(self.client.get(url, **self.auth('x' * 40)).status_code, (401, 403), name)
            self.assertIn(self.client.get(url, HTTP_AUTHORIZATION=f'Token {TOKEN}').status_code, (401, 403), name)
            self.assertIn(self.client.get(url, HTTP_AUTHORIZATION=TOKEN).status_code, (401, 403), name)

    def test_correct_token_allowed_with_caveats(self):
        for name in ENDPOINTS:
            r = self.client.get(reverse(name), **self.auth())
            self.assertEqual(r.status_code, 200, name)
            self.assertIn(POS_ONLY_CAVEAT, r.data['caveats'], name)

    def test_estimate_caveat_on_product_revenue_and_pl_caveat_on_pl(self):
        for name in ('rudra_sales_summary', 'rudra_sales_products'):
            self.assertIn(ESTIMATE_CAVEAT, self.client.get(reverse(name), **self.auth()).data['caveats'])
        self.assertIn(PL_CAVEAT, self.client.get(reverse('rudra_pl'), **self.auth()).data['caveats'])

    def test_unset_or_short_token_fails_closed(self):
        for configured in ('', 'short'):
            with override_settings(RUDRA_API_TOKEN=configured):
                for supplied in ('', configured, 'x'):
                    r = self.client.get(reverse('rudra_pl'), HTTP_AUTHORIZATION=f'Bearer {supplied}')
                    self.assertIn(r.status_code, (401, 403))

    def test_staff_session_does_not_grant_access(self):
        User.objects.create_superuser('boss', 'b@example.com', 'pw')
        c = APIClient()
        c.login(username='boss', password='pw')
        self.assertIn(c.get(reverse('rudra_pl')).status_code, (401, 403))

    def test_rudra_token_does_not_open_v1_reports(self):
        r = self.client.get(reverse('v1_report_summary'), **self.auth())
        self.assertIn(r.status_code, (401, 403))

    def test_read_only_and_no_export(self):
        for method in ('post', 'put', 'patch', 'delete'):
            r = getattr(self.client, method)(reverse('rudra_pl'), {}, **self.auth())
            self.assertEqual(r.status_code, 405, method)
        r = self.client.get(reverse('rudra_pl') + '?export=csv', **self.auth())
        self.assertEqual(r.status_code, 400)

    def test_validation_errors_still_400(self):
        r = self.client.get(reverse('rudra_sales_summary') + '?start=2026-01-01', **self.auth())
        self.assertEqual(r.status_code, 400)

    def test_numbers_match_staff_dashboard_endpoint(self):
        staff = User.objects.create_superuser('boss', 'b@example.com', 'pw')
        c = APIClient()
        c.force_authenticate(staff)
        q = '?start=2026-10-01&end=2026-10-25'
        theirs = c.get(reverse('v1_report_summary') + q).data
        mine = self.client.get(reverse('rudra_sales_summary') + q, **self.auth()).data
        mine.pop('caveats')
        self.assertEqual(mine, theirs)
        pl_q = '?from=2026-10-01&to=2026-10-25'
        theirs = c.get(reverse('v1_report_pl') + pl_q).data
        mine = self.client.get(reverse('rudra_pl') + pl_q, **self.auth()).data
        mine.pop('caveats')
        self.assertEqual(mine, theirs)
