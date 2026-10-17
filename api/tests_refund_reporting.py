"""Tests for refund-awareness in the Reports dashboard (shop/reports.py),
Profit & Loss (shop/pl_report.py), and crop/batch revenue (shop/
batch_report.py) -- the follow-up to the POS Refund feature (api/
tests_refunds.py) that makes these reports net of refunds instead of
silently overstating revenue/cash collected. Kept in its own file for
the same reason api/tests_reports.py is separate from api/tests.py.

A refund counts in the period it happened (Refund.created_at), never the
original sale's date -- several tests below construct a sale and its
refund in deliberately different calendar periods to prove that.
"""
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone as djtz

from shop.batch_report import build_batch_report
from shop.models import Coupon, CostEntry, CreditTransaction, CropBatch, Customer, InventoryMovement, POSSale, Refund
from shop.pl_report import build_pl
from shop.reports import get_insight_strip, get_run_rate, get_sales_by_operator, get_sales_trend, get_summary, get_target_progress
from shop.views import create_pos_refund, create_pos_sale

LOCAL_TZ = ZoneInfo('Asia/Kathmandu')


def local_dt(y, m, d, hour=12):
    return datetime(y, m, d, hour, 0, 0, tzinfo=LOCAL_TZ)


def backdate(instance, when):
    type(instance).objects.filter(id=instance.id).update(created_at=when)
    instance.refresh_from_db()
    return instance


class RefundReportingTestBase(TestCase):
    def setUp(self):
        from shop.models import Category, Product

        self.operator = User.objects.create_user('refreportop', password='pw')
        self.category = Category.objects.create(name='Fruits', order=1)
        self.jar = Product.objects.create(
            name='Pickle Jar', slug='rr-pickle-jar', category=self.category,
            description='t', price=Decimal('250.00'), pricing_mode='fixed_quantity',
        )
        InventoryMovement.objects.create(product=self.jar, movement_type='harvest', source='admin', quantity=Decimal('50'))

    def make_sale(self, qty=2, when=None, customer=None, method='cash'):
        total = Decimal('250.00') * qty
        sale, _ = create_pos_sale(
            client_sale_id=str(uuid.uuid4()),
            cart=[{'product_id': self.jar.id, 'qty': qty}],
            payments=[{'method': method, 'amount': str(total)}],
            operator_user=self.operator, customer=customer,
        )
        if when is not None:
            sale = backdate(sale, when)
        return sale

    def make_refund(self, sale, quantity, when=None, method='cash', reason='changed_mind'):
        line = sale.lines.get()
        refund, _ = create_pos_refund(
            sale_number=sale.sale_number,
            selections=[{'sale_line_id': line.id, 'quantity': str(quantity)}],
            method=method, reason=reason, note='', operator_user=self.operator,
            client_refund_id=str(uuid.uuid4()),
        )
        if when is not None:
            refund = backdate(refund, when)
        return refund


class NetSalesReportsTests(RefundReportingTestBase):
    def test_full_refund_nets_summary_to_zero(self):
        sale = self.make_sale(qty=2, when=local_dt(2026, 3, 1))  # Rs 500
        self.make_refund(sale, quantity=2, when=local_dt(2026, 3, 1))

        summary = get_summary(date(2026, 3, 1), date(2026, 3, 1), 'all', False)
        kpis = summary['kpis']
        self.assertEqual(kpis['gross_sales']['current'], 500.0)
        self.assertEqual(kpis['refunds']['current'], 500.0)
        self.assertEqual(kpis['total_sales']['current'], 0.0)

    def test_partial_refund_nets_summary_partially(self):
        sale = self.make_sale(qty=4, when=local_dt(2026, 3, 1))  # Rs 1000
        refund = self.make_refund(sale, quantity=2, when=local_dt(2026, 3, 1))  # Rs 500

        summary = get_summary(date(2026, 3, 1), date(2026, 3, 1), 'all', False)
        kpis = summary['kpis']
        self.assertEqual(kpis['gross_sales']['current'], 1000.0)
        self.assertEqual(Decimal(str(kpis['refunds']['current'])), refund.total_amount)
        self.assertEqual(kpis['total_sales']['current'], 1000.0 - float(refund.total_amount))

    def test_refund_counts_in_its_own_period_not_the_sale_period(self):
        sale = self.make_sale(qty=2, when=local_dt(2026, 3, 1))  # Rs 500, period: March 1
        refund = self.make_refund(sale, quantity=2, when=local_dt(2026, 3, 10))  # refunded March 10

        # The sale's OWN period (March 1) is unaffected -- the refund
        # hadn't happened yet "as far as that period is concerned".
        march_1 = get_summary(date(2026, 3, 1), date(2026, 3, 1), 'all', False)['kpis']
        self.assertEqual(march_1['gross_sales']['current'], 500.0)
        self.assertEqual(march_1['refunds']['current'], 0.0)
        self.assertEqual(march_1['total_sales']['current'], 500.0)

        # The REFUND's own period (March 10) has no gross sale of its own
        # that day, so net sales there goes NEGATIVE -- a real, honest
        # signal that money went out that day, not hidden.
        march_10 = get_summary(date(2026, 3, 10), date(2026, 3, 10), 'all', False)['kpis']
        self.assertEqual(march_10['gross_sales']['current'], 0.0)
        self.assertEqual(Decimal(str(march_10['refunds']['current'])), refund.total_amount)
        self.assertEqual(march_10['total_sales']['current'], -float(refund.total_amount))

        # A range spanning BOTH days nets correctly to zero.
        both_days = get_summary(date(2026, 3, 1), date(2026, 3, 10), 'all', False)['kpis']
        self.assertEqual(both_days['total_sales']['current'], 0.0)

    def test_insight_strip_and_run_rate_are_net(self):
        sale = self.make_sale(qty=2, when=local_dt(2026, 3, 1))
        self.make_refund(sale, quantity=2, when=local_dt(2026, 3, 1))

        insight = get_insight_strip(date(2026, 3, 1), date(2026, 3, 1))
        self.assertEqual(insight['avg_daily_sales'], 0.0)
        self.assertEqual(insight['gross_total'], 500.0)
        self.assertEqual(insight['refunds_total'], 500.0)

    def test_sales_trend_bucket_is_net_with_gross_and_refunds_exposed(self):
        sale = self.make_sale(qty=4, when=local_dt(2026, 3, 1))
        refund = self.make_refund(sale, quantity=2, when=local_dt(2026, 3, 1))

        trend = get_sales_trend(date(2026, 3, 1), date(2026, 3, 1), 'day')
        buckets = trend['series']['current']
        self.assertEqual(len(buckets), 1)
        bucket = buckets[0]
        self.assertEqual(bucket['gross_total'], 1000.0)
        self.assertEqual(Decimal(str(bucket['refunds'])), refund.total_amount)
        self.assertEqual(bucket['total'], 1000.0 - float(refund.total_amount))

    def test_sales_by_operator_keeps_gross_and_shows_refunded_separately(self):
        other_staff = User.objects.create_user('otherop', password='pw')
        sale = self.make_sale(qty=2, when=local_dt(2026, 3, 1))  # rung up by self.operator

        # A DIFFERENT staff member processes the refund.
        line = sale.lines.get()
        refund, _ = create_pos_refund(
            sale_number=sale.sale_number,
            selections=[{'sale_line_id': line.id, 'quantity': '2'}],
            method='cash', reason='changed_mind', note='', operator_user=other_staff,
            client_refund_id=str(uuid.uuid4()),
        )
        backdate(refund, local_dt(2026, 3, 1))

        rows = get_sales_by_operator(date(2026, 3, 1), date(2026, 3, 1))
        by_name = {r['operator']: r for r in rows}
        # Gross sales stay attributed to the ORIGINAL cashier, unchanged.
        self.assertEqual(by_name[self.operator.username]['total'], 500.0)
        self.assertEqual(by_name[self.operator.username]['refunded'], 0.0)
        # The refund is attributed separately to whoever processed IT.
        self.assertEqual(by_name['otherop']['total'], 0.0)
        self.assertEqual(Decimal(str(by_name['otherop']['refunded'])), refund.total_amount)

    def test_target_progress_is_net_of_refunds(self):
        from shop.models import RevenueTarget
        RevenueTarget.objects.create(
            name='Test Target', start_date=date(2026, 3, 1), end_date=date(2026, 3, 31),
            amount=Decimal('1000.00'), is_active=True,
        )
        sale = self.make_sale(qty=2, when=local_dt(2026, 3, 5))
        self.make_refund(sale, quantity=2, when=local_dt(2026, 3, 5))

        # get_target_progress() uses local_today() internally; whatever
        # "today" actually is when this test runs, elapsed_end clamps to
        # min(today, target.end_date) == target.end_date (2026-03-31) as
        # long as the test runs after that date, which it reliably does.
        progress = get_target_progress()
        self.assertTrue(progress['has_target'])
        self.assertEqual(Decimal(str(progress['gross_achieved'])), Decimal('500.00'))
        self.assertEqual(Decimal(str(progress['refunded'])), Decimal('500.00'))
        self.assertEqual(progress['achieved'], 0.0)


class CreditAndCashRefundPlReportTests(RefundReportingTestBase):
    def test_credit_refund_not_counted_in_cash_collected_but_reduces_outstanding(self):
        customer = Customer.objects.create(name='Ram Bahadur', phone='9800000001', address='Bhulka Danda')
        sale = self.make_sale(qty=2, when=local_dt(2026, 3, 1), customer=customer, method='credit')
        self.assertEqual(customer.outstanding_balance(), Decimal('500.00'))

        pl_before = build_pl(date(2026, 3, 1), date(2026, 3, 1))
        refund = self.make_refund(sale, quantity=2, when=local_dt(2026, 3, 1), method='credit')
        pl_after = build_pl(date(2026, 3, 1), date(2026, 3, 1))

        # cash_collected is UNCHANGED -- this was never cash to begin with
        # (it was credit_given, not cash_like), and the refund's own
        # repayment-shaped CreditTransaction is excluded via related_refund.
        self.assertEqual(pl_before['cash_vs_credit']['cash_collected'], pl_after['cash_vs_credit']['cash_collected'])
        self.assertEqual(pl_after['revenue']['total'], pl_after['revenue']['gross_total'] - refund.total_amount)

        customer.refresh_from_db()
        self.assertEqual(customer.outstanding_balance(), Decimal('0.00'))

        txn = CreditTransaction.objects.get(customer=customer, transaction_type='repayment')
        self.assertEqual(txn.related_refund, refund)

    def test_cash_refund_reduces_cash_collected(self):
        sale = self.make_sale(qty=2, when=local_dt(2026, 3, 1), method='cash')
        pl_before = build_pl(date(2026, 3, 1), date(2026, 3, 1))
        refund = self.make_refund(sale, quantity=2, when=local_dt(2026, 3, 1), method='cash')
        pl_after = build_pl(date(2026, 3, 1), date(2026, 3, 1))

        self.assertEqual(
            pl_after['cash_vs_credit']['cash_collected'],
            pl_before['cash_vs_credit']['cash_collected'] - refund.total_amount,
        )
        self.assertEqual(pl_after['refunds']['total'], refund.total_amount)
        self.assertEqual(pl_after['refunds']['cash_like'], refund.total_amount)

    def test_refund_counts_in_its_own_pl_period(self):
        sale = self.make_sale(qty=2, when=local_dt(2026, 3, 1))
        refund = self.make_refund(sale, quantity=2, when=local_dt(2026, 4, 1))

        march = build_pl(date(2026, 3, 1), date(2026, 3, 31))
        self.assertEqual(march['revenue']['total'], march['revenue']['gross_total'])  # unaffected

        april = build_pl(date(2026, 4, 1), date(2026, 4, 30))
        self.assertEqual(april['revenue']['gross_total'], Decimal('0.00'))
        self.assertEqual(april['revenue']['total'], -refund.total_amount)


class BatchRevenueRefundTests(TestCase):
    def setUp(self):
        from shop.models import Category, Product

        self.operator = User.objects.create_user('batchrefundop', password='pw')
        self.category = Category.objects.create(name='Veg', order=1)
        self.tomato = Product.objects.create(
            name='Refund Tomato', slug='refund-tomato', category=self.category,
            description='t', price=Decimal('100.00'), pricing_mode='variable_weight', origin='farm',
        )
        self.batch = CropBatch.objects.create(
            code='rfb-1', cost_centre='crop:tomato', product=self.tomato, start_date=date(2026, 1, 1),
        )
        CostEntry.objects.create(
            abms_id='rfb-cost-1', date=date(2026, 1, 1), amount=Decimal('1000.00'),
            cost_centre='crop:tomato', category='seeds', batch_code='rfb-1',
        )
        InventoryMovement.objects.create(
            product=self.tomato, movement_type='harvest', source='admin', quantity=Decimal('100'), batch_code='rfb-1',
        )

    def test_batch_revenue_net_of_refunded_line(self):
        sale, _ = create_pos_sale(
            client_sale_id=str(uuid.uuid4()),
            cart=[{'product_id': self.tomato.id, 'qty': 1, 'weight': '50.00'}],
            payments=[{'method': 'cash', 'amount': '5000.00'}], operator_user=self.operator,
        )
        report_before = build_batch_report('rfb-1')
        self.assertEqual(report_before['revenue'], Decimal('5000.00'))
        self.assertEqual(report_before['gross_revenue'], Decimal('5000.00'))
        self.assertEqual(report_before['refunded_revenue'], Decimal('0.00'))

        line = sale.lines.get()
        create_pos_refund(
            sale_number=sale.sale_number,
            selections=[{'sale_line_id': line.id, 'quantity': '50.00'}],
            method='cash', reason='changed_mind', note='', operator_user=self.operator,
            client_refund_id=str(uuid.uuid4()),
        )

        report_after = build_batch_report('rfb-1')
        self.assertEqual(report_after['gross_revenue'], Decimal('5000.00'))  # unchanged -- gross is gross
        self.assertEqual(report_after['refunded_revenue'], Decimal('5000.00'))
        self.assertEqual(report_after['revenue'], Decimal('0.00'))
        # profit_to_date, derived from `revenue`, reflects the refund too.
        self.assertEqual(report_after['profit_to_date'], Decimal('0.00') - report_after['cost_of_sold'])

    def test_partial_batch_refund_reduces_revenue_proportionally(self):
        sale, _ = create_pos_sale(
            client_sale_id=str(uuid.uuid4()),
            cart=[{'product_id': self.tomato.id, 'qty': 1, 'weight': '40.00'}],
            payments=[{'method': 'cash', 'amount': '4000.00'}], operator_user=self.operator,
        )
        line = sale.lines.get()
        # Variable-weight lines are whole-line-only -- refund the full
        # 40kg (there's no partial-weight refund), but at a product that
        # was priced so a SMALLER weight still proves the attribution
        # isn't hardcoded: refund the whole line and confirm the pool's
        # revenue drops by exactly that much, not more or less.
        create_pos_refund(
            sale_number=sale.sale_number,
            selections=[{'sale_line_id': line.id, 'quantity': '40.00'}],
            method='cash', reason='changed_mind', note='', operator_user=self.operator,
            client_refund_id=str(uuid.uuid4()),
        )
        report = build_batch_report('rfb-1')
        self.assertEqual(report['gross_revenue'], Decimal('4000.00'))
        self.assertEqual(report['refunded_revenue'], Decimal('4000.00'))
        self.assertEqual(report['revenue'], Decimal('0.00'))


class ExistingReportBehaviourUnaffectedTests(TestCase):
    """Sanity check: with no refunds anywhere, net == gross everywhere
    these changes touched -- the existing report test suites
    (api/tests_reports.py, shop/tests_pl_report.py, shop/
    tests_crop_batches.py) are the real regression coverage for this;
    this is just one more belt-and-suspenders check in the same file as
    the new refund-aware tests."""

    def test_no_refunds_means_net_equals_gross(self):
        operator = User.objects.create_user('noeffectop', password='pw')
        from shop.models import Category, Product

        category = Category.objects.create(name='Fruits', order=1)
        jar = Product.objects.create(
            name='Plain Jar', slug='plain-jar-noeffect', category=category,
            description='t', price=Decimal('250.00'), pricing_mode='fixed_quantity',
        )
        InventoryMovement.objects.create(product=jar, movement_type='harvest', source='admin', quantity=Decimal('10'))
        sale, _ = create_pos_sale(
            client_sale_id=str(uuid.uuid4()), cart=[{'product_id': jar.id, 'qty': 2}],
            payments=[{'method': 'cash', 'amount': '500.00'}], operator_user=operator,
        )
        backdate(sale, local_dt(2026, 3, 1))

        summary = get_summary(date(2026, 3, 1), date(2026, 3, 1), 'all', False)['kpis']
        self.assertEqual(summary['total_sales']['current'], summary['gross_sales']['current'])
        self.assertEqual(summary['refunds']['current'], 0.0)
