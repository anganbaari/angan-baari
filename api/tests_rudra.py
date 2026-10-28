"""Tests for the token-authed, read-only /api/rudra/ endpoints
(api/rudra_views.py, api.permissions.IsRudraToken)."""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from shop.models import InventoryMovement, Product, ProductOrder

from .rudra_views import ESTIMATE_CAVEAT, LOW_STOCK_CAVEAT, PENDING_ORDERS_CAVEAT, PL_CAVEAT, POS_ONLY_CAVEAT
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


@override_settings(RUDRA_API_TOKEN=TOKEN)
class RudraStockAndOrdersTests(ApiTestBase):
    """GET /api/rudra/stock/low/ and /api/rudra/orders/pending/ (feature I)."""
    NEW = ['rudra_low_stock', 'rudra_pending_orders']

    def auth(self, token=TOKEN):
        return {'HTTP_AUTHORIZATION': f'Bearer {token}'}

    def product(self, name, stock, threshold=5, mode='fixed_quantity', available=True):
        p = Product.objects.create(name=name, slug=name.lower().replace(' ', '-'), category=self.category, description='x',
                                   price=Decimal('10.00'), pricing_mode=mode, low_stock_threshold=threshold,
                                   is_available=available)
        InventoryMovement.objects.create(product=p, movement_type='harvest', source='admin', quantity=Decimal(stock))
        return p

    def order(self, name, status='pending', days_ago=0):
        o = ProductOrder.objects.create(name=name, email='a@example.com', phone='1', address='x',
                                        product_interest='Mango 2kg', status=status)
        ProductOrder.objects.filter(pk=o.pk).update(ordered_at=timezone.now() - timedelta(days=days_ago, minutes=1))
        return o

    def test_token_required_and_read_only(self):
        for name in self.NEW:
            url = reverse(name)
            self.assertIn(self.client.get(url).status_code, (401, 403), name)
            self.assertIn(self.client.get(url, **self.auth('x' * 40)).status_code, (401, 403), name)
            self.assertIn(self.client.get(url, HTTP_AUTHORIZATION=f'Token {TOKEN}').status_code, (401, 403), name)
            self.assertEqual(self.client.get(url, **self.auth()).status_code, 200, name)
            for method in ('post', 'put', 'patch', 'delete'):
                self.assertEqual(getattr(self.client, method)(url, {}, **self.auth()).status_code, 405, (name, method))
        staff = User.objects.create_superuser('boss', 'b@example.com', 'pw')
        c = APIClient()
        c.login(username='boss', password='pw')
        for name in self.NEW:
            self.assertIn(c.get(reverse(name)).status_code, (401, 403), name)

    def test_low_stock_matches_the_pos_stock_screen_rule(self):
        self.product('Almost Out', '2')
        self.product('At Threshold', '5')          # exactly at threshold is NOT low (strict <), like the POS screen
        self.product('Sold Out', '0')               # stock 0 switches is_available off; it must still be listed
        self.product('Loose Honey', '1', mode='variable_weight')
        r = self.client.get(reverse('rudra_low_stock'), **self.auth())
        names = [i['name'] for i in r.data['items']]
        self.assertEqual(sorted(names), ['Almost Out', 'Loose Honey', 'Sold Out'])
        self.assertEqual(r.data['count'], 3)
        self.assertEqual({i['name']: i['unit'] for i in r.data['items']}, {'Almost Out': 'pcs', 'Loose Honey': 'kg', 'Sold Out': 'pcs'})
        self.assertEqual(r.data['items'][0]['name'], 'Sold Out')                 # most urgent first
        self.assertNotIn('Goat', names)                                         # fixed-weight animals are excluded
        self.assertIn(LOW_STOCK_CAVEAT, r.data['caveats'])

    def test_low_stock_empty_when_everything_is_fine(self):
        r = self.client.get(reverse('rudra_low_stock'), **self.auth())
        self.assertEqual((r.data['count'], r.data['items']), (0, []))

    def test_pending_orders_lists_open_orders_oldest_first_and_flags_stale(self):
        self.order('New', 'pending', 0)
        self.order('Old', 'confirmed', 5)
        self.order('Done', 'delivered', 9)
        self.order('Gone', 'cancelled', 9)
        r = self.client.get(reverse('rudra_pending_orders'), **self.auth())
        self.assertEqual([i['name'] for i in r.data['items']], ['Old', 'New'])
        self.assertEqual((r.data['count'], r.data['stale_count'], r.data['stale_after_days']), (2, 1, 3))
        old = r.data['items'][0]
        self.assertEqual((old['days_pending'], old['stale'], old['status']), (5, True, 'confirmed'))
        self.assertFalse(r.data['items'][1]['stale'])
        self.assertNotIn('phone', old)                      # no customer contact details
        self.assertNotIn('address', old)
        self.assertIn(PENDING_ORDERS_CAVEAT, r.data['caveats'])

    def test_pending_orders_empty(self):
        r = self.client.get(reverse('rudra_pending_orders'), **self.auth())
        self.assertEqual((r.data['count'], r.data['items']), (0, []))
