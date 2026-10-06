"""Tests for the staff Reports dashboard: /dashboard/ (shop/views.py) and
the read-only GET /api/v1/reports/* endpoints (api/views.py,
shop/reports.py). Covers permissions, reconciliation against the same
ledger methods the rest of the app trusts, the Nepal-timezone day
boundary, previous-period comparison math, granularity bucketing,
invalid/oversized ranges, CSV export + BOM, and empty-database behaviour.

Kept separate from api/tests.py (already 2500+ lines covering the POS/
checkout/profile surface) rather than appended to it.
"""
import csv
import io
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.contrib.auth.models import User
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.db import connection
from django.urls import reverse
from rest_framework import status

from shop.models import Category, CreditTransaction, Customer, InventoryMovement, POSSale, POSSalePayment, Product, UserProfile
from shop.reports import LOCAL_TZ, delta_info, local_range_to_utc, resolve_date_range, ReportValidationError

from .tests import ApiTestBase

REPORT_ENDPOINTS = [
    'v1_report_summary', 'v1_report_sales_trend', 'v1_report_payments',
    'v1_report_products', 'v1_report_credit', 'v1_report_inventory', 'v1_report_orders',
]


class ReportsTestBase(ApiTestBase):
    def setUp(self):
        super().setUp()
        # self.staff (role='cashier') already exists via ApiTestBase -- the
        # dashboard's "not owner/manager" case. Add the two allowed roles.
        self.manager = User.objects.create_user('manager1', password='pw', is_staff=True)
        UserProfile.objects.create(user=self.manager, role='manager')
        self.owner = User.objects.create_user('owner1', password='pw', is_staff=True)
        UserProfile.objects.create(user=self.owner, role='admin')

    def make_sale(self, total, cashier=None, payments=None, customer=None, discount=Decimal('0'), created_at=None, cart=None):
        sale = POSSale.objects.create(
            cashier=cashier or self.staff,
            customer=customer,
            payment_method='split' if payments and len(payments) > 1 else (payments[0][0] if payments else 'cash'),
            cart_snapshot=cart if cart is not None else [{'product_id': self.jar.id, 'weight': None, 'qty': 1, 'variant_id': None}],
            total_amount=Decimal(str(total)),
            discount_amount=Decimal(str(discount)),
        )
        for method, amount in (payments or [('cash', total)]):
            POSSalePayment.objects.create(sale=sale, method=method, amount=Decimal(str(amount)))
        if created_at is not None:
            POSSale.objects.filter(id=sale.id).update(created_at=created_at)
            sale.refresh_from_db()
        return sale


class DashboardPagePermissionTests(ReportsTestBase):
    def test_anonymous_redirected(self):
        response = self.client.get(reverse('dashboard'))
        self.assertNotEqual(response.status_code, 200)

    def test_cashier_staff_denied(self):
        self.client.login(username='cashier', password='pw')
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 403)

    def test_manager_allowed(self):
        self.client.login(username='manager1', password='pw')
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)

    def test_owner_allowed(self):
        self.client.login(username='owner1', password='pw')
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)


class ReportEndpointPermissionTests(ReportsTestBase):
    def test_anonymous_denied_on_every_endpoint(self):
        for name in REPORT_ENDPOINTS:
            response = self.client.get(reverse(name))
            self.assertIn(response.status_code, (401, 403), name)

    def test_cashier_denied_on_every_endpoint(self):
        self.client.login(username='cashier', password='pw')
        for name in REPORT_ENDPOINTS:
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 403, name)

    def test_manager_allowed_on_every_endpoint(self):
        self.client.login(username='manager1', password='pw')
        for name in REPORT_ENDPOINTS:
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 200, name)

    def test_superuser_without_profile_allowed(self):
        superuser = User.objects.create_superuser('root', 'root@example.com', 'pw')
        self.client.login(username='root', password='pw')
        response = self.client.get(reverse('v1_report_summary'))
        self.assertEqual(response.status_code, 200)


class ReconciliationTests(ReportsTestBase):
    def test_outstanding_credit_matches_customer_method(self):
        customer = Customer.objects.create(name='Hari', phone='9800000000')
        CreditTransaction.objects.create(customer=customer, amount=Decimal('500.00'), transaction_type='credit_sale', recorded_by=self.staff)
        CreditTransaction.objects.create(customer=customer, amount=Decimal('150.00'), transaction_type='repayment', recorded_by=self.staff)
        self.make_sale(Decimal('100.00'), payments=[('credit', Decimal('100.00'))], customer=customer)
        CreditTransaction.objects.create(customer=customer, amount=Decimal('100.00'), transaction_type='credit_sale', related_pos_sale=None, recorded_by=self.staff)

        self.client.login(username='owner1', password='pw')
        response = self.client.get(reverse('v1_report_credit'))
        self.assertEqual(response.status_code, 200)
        expected = sum((c.outstanding_balance() for c in Customer.objects.all()), Decimal('0'))
        self.assertEqual(Decimal(str(response.data['kpis']['outstanding']['current'])), expected)

    def test_revenue_equals_sum_of_sale_totals_pos_only(self):
        today = resolve_date_range(None, None)[1]
        start_utc, _ = local_range_to_utc(today, today)
        self.make_sale(Decimal('300.00'), created_at=start_utc + timedelta(hours=10))
        self.make_sale(Decimal('450.00'), created_at=start_utc + timedelta(hours=11))

        self.client.login(username='owner1', password='pw')
        response = self.client.get(reverse('v1_report_summary'), {'start': today.isoformat(), 'end': today.isoformat()})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Decimal(str(response.data['kpis']['total_sales']['current'])), Decimal('750.00'))
        self.assertEqual(response.data['kpis']['transactions']['current'], 2)

    def test_cash_plus_credit_equals_total(self):
        today = resolve_date_range(None, None)[1]
        start_utc, _ = local_range_to_utc(today, today)
        customer = Customer.objects.create(name='Sita', phone='9811111111')
        self.make_sale(Decimal('500.00'), payments=[('cash', Decimal('300.00')), ('credit', Decimal('200.00'))],
                        customer=customer, created_at=start_utc + timedelta(hours=9))

        self.client.login(username='owner1', password='pw')
        response = self.client.get(reverse('v1_report_summary'), {'start': today.isoformat(), 'end': today.isoformat()})
        k = response.data['kpis']
        self.assertEqual(Decimal(str(k['cash_collected']['current'])), Decimal('300.00'))
        self.assertEqual(Decimal(str(k['credit_given']['current'])), Decimal('200.00'))
        self.assertEqual(Decimal(str(k['total_sales']['current'])), Decimal('500.00'))

    def test_cancelled_orders_excluded_from_online_count(self):
        from shop.models import ProductOrder
        ProductOrder.objects.create(name='A', email='a@example.com', phone='1', address='x', product_interest='Mango', status='pending')
        ProductOrder.objects.create(name='B', email='b@example.com', phone='1', address='x', product_interest='Mango', status='cancelled')

        self.client.login(username='owner1', password='pw')
        response = self.client.get(reverse('v1_report_summary'))
        self.assertEqual(response.data['kpis']['online_order_count']['current'], 1)

    def test_coupon_discount_counted_once(self):
        today = resolve_date_range(None, None)[1]
        start_utc, _ = local_range_to_utc(today, today)
        self.make_sale(Decimal('450.00'), discount=Decimal('50.00'), created_at=start_utc + timedelta(hours=9))

        self.client.login(username='owner1', password='pw')
        response = self.client.get(reverse('v1_report_summary'), {'start': today.isoformat(), 'end': today.isoformat()})
        self.assertEqual(Decimal(str(response.data['kpis']['discounts_given']['current'])), Decimal('50.00'))


class TimezoneBoundaryTests(ReportsTestBase):
    """settings.TIME_ZONE = 'Asia/Kathmandu' (UTC+5:45). A sale at 23:30
    local must land on that local day, not the UTC day it technically
    falls on, and a sale at 00:30 local must land on ITS local day, not
    the previous one -- see shop/reports.py's local_range_to_utc()."""

    def test_2330_local_lands_on_its_own_local_day(self):
        local_day = datetime(2026, 3, 10, 23, 30, tzinfo=LOCAL_TZ)
        utc_dt = local_day.astimezone(ZoneInfo('UTC'))
        self.make_sale(Decimal('111.00'), created_at=utc_dt)

        self.client.login(username='owner1', password='pw')
        response = self.client.get(reverse('v1_report_summary'), {'start': '2026-03-10', 'end': '2026-03-10'})
        self.assertEqual(response.data['kpis']['transactions']['current'], 1)
        response_next_day = self.client.get(reverse('v1_report_summary'), {'start': '2026-03-11', 'end': '2026-03-11'})
        self.assertEqual(response_next_day.data['kpis']['transactions']['current'], 0)

    def test_0030_local_lands_on_its_own_local_day(self):
        local_day = datetime(2026, 3, 11, 0, 30, tzinfo=LOCAL_TZ)
        utc_dt = local_day.astimezone(ZoneInfo('UTC'))
        self.make_sale(Decimal('222.00'), created_at=utc_dt)

        self.client.login(username='owner1', password='pw')
        response = self.client.get(reverse('v1_report_summary'), {'start': '2026-03-11', 'end': '2026-03-11'})
        self.assertEqual(response.data['kpis']['transactions']['current'], 1)
        response_prev_day = self.client.get(reverse('v1_report_summary'), {'start': '2026-03-10', 'end': '2026-03-10'})
        self.assertEqual(response_prev_day.data['kpis']['transactions']['current'], 0)


class PreviousPeriodComparisonTests(ReportsTestBase):
    def test_delta_info_handles_zero_previous_as_new(self):
        info = delta_info(100, 0)
        self.assertEqual(info['direction'], 'new')
        self.assertIsNone(info['delta_pct'])

    def test_delta_info_handles_zero_both_as_flat(self):
        info = delta_info(0, 0)
        self.assertEqual(info['direction'], 'flat')

    def test_delta_info_computes_percentage(self):
        info = delta_info(150, 100)
        self.assertEqual(info['delta_pct'], 50.0)
        self.assertEqual(info['direction'], 'up')

    def test_compare_true_returns_previous_period_values(self):
        today = resolve_date_range(None, None)[1]
        start_utc, _ = local_range_to_utc(today, today)
        yesterday_start_utc, _ = local_range_to_utc(today - timedelta(days=1), today - timedelta(days=1))
        self.make_sale(Decimal('200.00'), created_at=start_utc + timedelta(hours=9))
        self.make_sale(Decimal('100.00'), created_at=yesterday_start_utc + timedelta(hours=9))

        self.client.login(username='owner1', password='pw')
        response = self.client.get(reverse('v1_report_summary'), {
            'start': today.isoformat(), 'end': today.isoformat(), 'compare': 'true',
        })
        k = response.data['kpis']['total_sales']
        self.assertEqual(Decimal(str(k['current'])), Decimal('200.00'))
        self.assertEqual(Decimal(str(k['previous'])), Decimal('100.00'))
        self.assertEqual(k['direction'], 'up')


class GranularityBucketingTests(ReportsTestBase):
    def test_week_granularity_groups_days_together(self):
        today = resolve_date_range(None, None)[1]
        start_utc, _ = local_range_to_utc(today, today)
        self.make_sale(Decimal('100.00'), created_at=start_utc + timedelta(hours=1))
        self.make_sale(Decimal('100.00'), created_at=start_utc + timedelta(hours=2))

        self.client.login(username='owner1', password='pw')
        response = self.client.get(reverse('v1_report_sales_trend'), {
            'start': (today - timedelta(days=6)).isoformat(), 'end': today.isoformat(), 'granularity': 'week',
        })
        self.assertEqual(response.status_code, 200)
        buckets = response.data['trend']['series']['current']
        self.assertEqual(len(buckets), 1)
        self.assertEqual(Decimal(str(buckets[0]['total'])), Decimal('200.00'))


class InvalidRangeTests(ReportsTestBase):
    def test_start_after_end_rejected(self):
        self.client.login(username='owner1', password='pw')
        response = self.client.get(reverse('v1_report_summary'), {'start': '2026-02-01', 'end': '2026-01-01'})
        self.assertEqual(response.status_code, 400)
        self.assertIn('message', response.data)

    def test_oversized_range_rejected(self):
        self.client.login(username='owner1', password='pw')
        response = self.client.get(reverse('v1_report_summary'), {'start': '2024-01-01', 'end': '2026-01-05'})
        self.assertEqual(response.status_code, 400)

    def test_malformed_date_rejected(self):
        self.client.login(username='owner1', password='pw')
        response = self.client.get(reverse('v1_report_summary'), {'start': 'not-a-date', 'end': '2026-01-05'})
        self.assertEqual(response.status_code, 400)

    def test_invalid_channel_rejected(self):
        self.client.login(username='owner1', password='pw')
        response = self.client.get(reverse('v1_report_summary'), {'channel': 'bogus'})
        self.assertEqual(response.status_code, 400)

    def test_resolve_date_range_raises_for_one_sided_params(self):
        with self.assertRaises(ReportValidationError):
            resolve_date_range('2026-01-01', None)


class CsvExportTests(ReportsTestBase):
    def test_sales_csv_export_has_bom_and_rows(self):
        today = resolve_date_range(None, None)[1]
        start_utc, _ = local_range_to_utc(today, today)
        self.make_sale(Decimal('250.00'), created_at=start_utc + timedelta(hours=9))

        self.client.login(username='owner1', password='pw')
        response = self.client.get(reverse('v1_report_sales_trend'), {
            'start': today.isoformat(), 'end': today.isoformat(), 'export': 'csv',
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/csv; charset=utf-8')
        content = response.content.decode('utf-8')
        self.assertTrue(content.startswith('﻿'))
        rows = list(csv.reader(io.StringIO(content.lstrip('﻿'))))
        self.assertEqual(rows[0], ['sale_number', 'date_time', 'customer', 'operator', 'total', 'payments'])
        self.assertEqual(len(rows), 2)

    def test_credit_csv_export(self):
        customer = Customer.objects.create(name='राम बहादुर', phone='9800000001')
        CreditTransaction.objects.create(customer=customer, amount=Decimal('500.00'), transaction_type='credit_sale', recorded_by=self.staff)

        self.client.login(username='owner1', password='pw')
        response = self.client.get(reverse('v1_report_credit'), {'export': 'csv'})
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')
        self.assertTrue(content.startswith('﻿'))
        self.assertIn('राम बहादुर', content)


class EmptyDatabaseTests(TestCase):
    """No products, no sales, no orders, no customers at all -- every
    endpoint must return zeros/empty lists, never a 500."""

    def setUp(self):
        self.client.force_login(User.objects.create_user('solo-owner', password='pw', is_staff=True))
        UserProfile.objects.create(user=User.objects.get(username='solo-owner'), role='admin')

    def test_all_endpoints_return_200_with_empty_data(self):
        for name in REPORT_ENDPOINTS:
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 200, name)

    def test_summary_kpis_are_zero(self):
        response = self.client.get(reverse('v1_report_summary'))
        self.assertEqual(response.data['kpis']['total_sales']['current'], 0)
        self.assertEqual(response.data['kpis']['transactions']['current'], 0)
        self.assertEqual(response.data['top_products'], [])

    def test_credit_customers_empty_list(self):
        response = self.client.get(reverse('v1_report_credit'))
        self.assertEqual(response.data['customers'], [])
        self.assertEqual(response.data['kpis']['outstanding']['current'], 0)

    def test_inventory_stock_table_empty(self):
        response = self.client.get(reverse('v1_report_inventory'))
        self.assertEqual(response.data['stock_table'], [])


class QueryCountTests(ReportsTestBase):
    """Guards against an N+1 regression on the heaviest aggregation
    endpoints -- a generous ceiling, not an exact pin, since the point is
    catching "one query per row" bugs, not locking the query plan."""

    def _make_sales(self, n):
        today = resolve_date_range(None, None)[1]
        start_utc, _ = local_range_to_utc(today, today)
        for i in range(n):
            self.make_sale(
                Decimal('100.00'),
                cart=[{'product_id': self.jar.id, 'weight': None, 'qty': 1, 'variant_id': None}],
                created_at=start_utc + timedelta(minutes=i),
            )

    def test_summary_query_count_does_not_scale_with_sale_count(self):
        self.client.login(username='owner1', password='pw')
        self._make_sales(3)
        with CaptureQueriesContext(connection) as small:
            self.client.get(reverse('v1_report_summary'))
        self._make_sales(30)
        with CaptureQueriesContext(connection) as large:
            self.client.get(reverse('v1_report_summary'))
        self.assertLess(len(large.captured_queries), len(small.captured_queries) + 5)

    def test_products_query_count_does_not_scale_with_sale_count(self):
        self.client.login(username='owner1', password='pw')
        self._make_sales(3)
        with CaptureQueriesContext(connection) as small:
            self.client.get(reverse('v1_report_products'))
        self._make_sales(30)
        with CaptureQueriesContext(connection) as large:
            self.client.get(reverse('v1_report_products'))
        self.assertLess(len(large.captured_queries), len(small.captured_queries) + 5)
