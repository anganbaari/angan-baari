"""Tests for shop/pl_report.py's build_pl() -- the Profit & Loss report's
sole calculation entry point. API-level permission/param/CSV tests for
GET /api/v1/reports/pl/ live in api/tests_reports.py instead, same split
already used for every other Reports endpoint in this project.

PLReportHandComputedTests is the one detailed scenario required by this
feature's brief: every number in it was independently hand-computed, then
cross-checked against a live run of build_pl() before being written down
here (see the session notes) -- so these assertions are the authoritative
expected values, not a restatement of whatever the code happened to produce.
"""
import uuid
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone as djtz

from .models import (
    Category, CostCentreProduct, CostEntry, CreditTransaction, Customer, FarmAsset,
    InventoryMovement, POSSale, Product, ProductOrder, ProductVariant, PurchaseBatch,
    Coupon,
)
from .pl_report import build_pl
from .views import create_pos_sale


class PLReportEmptyPeriodTests(TestCase):
    def test_empty_period_returns_zeros_not_errors(self):
        result = build_pl(date(2020, 1, 1), date(2020, 1, 31))
        self.assertEqual(result['revenue']['total'], Decimal('0'))
        self.assertEqual(result['net_profit'], Decimal('0'))
        self.assertEqual(result['farm_cost_centres'], [])
        self.assertEqual(result['wastage_detail'], [])
        self.assertFalse(result['data_quality']['has_issues'])
        for key, value in result['data_quality'].items():
            if key != 'has_issues':
                self.assertEqual(value, 0)


class PLReportHandComputedTests(TestCase):
    """One retail sale (10% coupon, mixing a sourced + a mapped farm
    product) + one wholesale sale on credit (a different sourced product)
    + a waste movement + a stock-count adjustment + five CostEntry rows
    (two direct, one reversed-to-net-zero, one shared-equal, one
    shared-manual-60/40) + two FarmAssets (one bought mid-period, one
    disposed mid-period) -- see this module's docstring for how these
    numbers were derived and verified.

    The whole scenario is deliberately confined to one B.S. month (2083
    Bhadra = 2026-08-17..2026-09-16) so the depreciation math only ever
    has to reason about a single B.S. month boundary."""

    PERIOD_START = date(2026, 8, 17)
    PERIOD_END = date(2026, 9, 16)

    def setUp(self):
        self.category = Category.objects.create(name='PL Test', order=1)

        self.apple = Product.objects.create(
            name='Apple', slug='apple-pl-test', category=self.category, description='t',
            price=Decimal('400.00'), pricing_mode='variable_weight', weight_step=Decimal('0.5'),
            origin='sourced',
        )
        PurchaseBatch.objects.create(
            product=self.apple, purchase_date=date(2026, 8, 17), quantity=Decimal('50'),
            unit_price=Decimal('300'), transport_cost=Decimal('500'),
        )  # landed_unit_cost = (50*300 + 500) / 50 = 310.0000

        self.mango = Product.objects.create(
            name='Mango', slug='mango-pl-test', category=self.category, description='t',
            price=Decimal('150.00'), pricing_mode='variable_weight', weight_step=Decimal('0.5'),
            origin='farm',
        )
        InventoryMovement.objects.create(product=self.mango, movement_type='harvest', source='admin', quantity=Decimal('20'))
        CostCentreProduct.objects.create(cost_centre='crop:mango', product=self.mango)

        self.oil = Product.objects.create(
            name='Olive Oil', slug='oil-pl-test', category=self.category, description='t',
            price=Decimal('250.00'), pricing_mode='fixed_quantity', origin='sourced',
        )
        PurchaseBatch.objects.create(
            product=self.oil, purchase_date=date(2026, 8, 17), quantity=Decimal('50'), unit_price=Decimal('120'),
        )  # landed_unit_cost = 120.0000, no transport

        self.goat = Product.objects.create(
            name='Goat', slug='goat-pl-test', category=self.category, description='t',
            price=Decimal('5000.00'), pricing_mode='fixed_weight', origin='farm',
        )
        ProductVariant.objects.create(product=self.goat, weight=Decimal('20.00'))
        CostCentreProduct.objects.create(cost_centre='livestock:goat', product=self.goat)  # never sold this period

        self.operator = User.objects.create_user('pltestop', password='pw', is_staff=True)
        self.buyer = Customer.objects.create(name='Big Trader', phone='9811111111', address='Butwal')
        self.coupon = Coupon.objects.create(
            code='TENOFF', discount_type='percent', discount_value=Decimal('10'),
            start_date=djtz.now() - djtz.timedelta(days=30), end_date=djtz.now() + djtz.timedelta(days=30),
        )

        # Sale 1: retail, 10% coupon, Apple (sourced) + Mango (farm, mapped).
        # Confirmed by direct run: total=1125.00, exempt_value=1125.0000,
        # discount_amount=125.00, Apple line_total=800.00/unit_cost=310.0000,
        # Mango line_total=450.00/unit_cost=None.
        self.sale1, _ = create_pos_sale(
            client_sale_id=str(uuid.uuid4()),
            cart=[
                {'product_id': self.apple.id, 'qty': 1, 'weight': '2.00'},
                {'product_id': self.mango.id, 'qty': 1, 'weight': '3.00'},
            ],
            payments=[{'method': 'cash', 'amount': '1125.00'}],
            operator_user=self.operator, coupon_code=self.coupon.code,
        )
        POSSale.objects.filter(id=self.sale1.id).update(
            created_at=djtz.make_aware(djtz.datetime(2026, 8, 20, 12, 0, 0))
        )

        # Sale 2: wholesale, on credit, Olive Oil (sourced) at an override price.
        # Confirmed by direct run: total=1800.00, exempt_value=1800.00,
        # line_total=1800.00, unit_cost=120.0000.
        self.sale2, _ = create_pos_sale(
            client_sale_id=str(uuid.uuid4()),
            cart=[{'product_id': self.oil.id, 'qty': 10, 'price_override': '180'}],
            payments=[{'method': 'credit', 'amount': '1800.00'}],
            operator_user=self.operator, customer=self.buyer,
            is_wholesale=True, wholesale_authorized=True,
        )
        POSSale.objects.filter(id=self.sale2.id).update(
            created_at=djtz.make_aware(djtz.datetime(2026, 8, 25, 12, 0, 0))
        )

        # 1kg waste of Apple -- auto-filled unit_cost (moving average stays
        # 310.0000, the only supply rate on record) -> value 310.0000.
        waste = InventoryMovement.objects.create(
            product=self.apple, movement_type='waste', source='admin', quantity=Decimal('1'),
        )
        InventoryMovement.objects.filter(id=waste.id).update(
            created_at=djtz.make_aware(djtz.datetime(2026, 8, 22, 12, 0, 0))
        )

        # 0.5kg stock-count adjustment of Apple -> value 155.0000.
        adjustment = InventoryMovement.objects.create(
            product=self.apple, movement_type='adjustment_remove', source='admin', quantity=Decimal('0.5'),
        )
        InventoryMovement.objects.filter(id=adjustment.id).update(
            created_at=djtz.make_aware(djtz.datetime(2026, 8, 23, 12, 0, 0))
        )

        # Direct costs: crop:mango nets to 1000.00 (1000 fertilizer + a
        # 300 seeds entry fully reversed to zero); livestock:goat gets 200.
        CostEntry.objects.create(
            abms_id='c_fert1', date=date(2026, 8, 20), amount=Decimal('1000.00'),
            cost_centre='crop:mango', category='fertilizer',
        )
        CostEntry.objects.create(
            abms_id='c_seed1', date=date(2026, 8, 20), amount=Decimal('300.00'),
            cost_centre='crop:mango', category='seeds',
        )
        CostEntry.objects.create(
            abms_id='c_seed1_rev', date=date(2026, 8, 21), amount=Decimal('-300.00'),
            cost_centre='crop:mango', category='seeds', entry_type='reversal', reversal_of='c_seed1',
        )
        CostEntry.objects.create(
            abms_id='c_vet1', date=date(2026, 8, 20), amount=Decimal('200.00'),
            cost_centre='livestock:goat', category='vet fees',
        )

        # Shared costs: 600 split equally (300/300), 1000 split 60/40 (600/400).
        # Both crop:mango and livestock:goat qualify (each has direct cost > 0).
        CostEntry.objects.create(
            abms_id='c_elec1', date=date(2026, 8, 20), amount=Decimal('600.00'),
            cost_centre='shared', category='electricity', is_shared=True, allocation_rule='equal',
        )
        CostEntry.objects.create(
            abms_id='c_sec1', date=date(2026, 8, 20), amount=Decimal('1000.00'),
            cost_centre='shared', category='security', is_shared=True, allocation_rule='manual',
            allocation_manual={'crop:mango': 60, 'livestock:goat': 40},
        )

        # FarmAsset 1: bought mid-period, 5-year straight-line -> Rs 200/month,
        # charged for exactly the one B.S. month this test period covers.
        FarmAsset.objects.create(
            abms_id='a_pump1', name='Water pump', asset_category='equipment',
            purchase_date=date(2026, 8, 28), cost=Decimal('12000.00'), life_years=5,
            salvage_value=Decimal('0'), cost_centre='crop:mango', status='active',
        )
        # FarmAsset 2: bought years ago (shared centre), disposed mid-period ->
        # Rs 2000/month, charged for exactly its disposal month, split equally
        # (1000/1000) across the two qualifying centres.
        FarmAsset.objects.create(
            abms_id='a_tractor1', name='Old Tractor', asset_category='equipment',
            purchase_date=date(2020, 1, 1), cost=Decimal('240000.00'), life_years=10,
            salvage_value=Decimal('0'), cost_centre='shared', status='disposed',
            disposed_date=date(2026, 9, 1),
        )

    def build(self):
        return build_pl(self.PERIOD_START, self.PERIOD_END)

    def test_revenue_split(self):
        r = self.build()['revenue']
        self.assertEqual(r['retail'], Decimal('1125.00'))
        self.assertEqual(r['wholesale'], Decimal('1800.00'))
        self.assertEqual(r['total'], Decimal('2925.00'))
        self.assertEqual(r['sourced_products'], Decimal('2520.00'))  # 720 (Apple) + 1800 (Oil)
        self.assertEqual(r['unmapped_farm_products'], Decimal('0.00'))
        self.assertEqual(r['sales_before_line_tracking'], Decimal('0.00'))

    def test_cash_vs_credit(self):
        c = self.build()['cash_vs_credit']
        self.assertEqual(c['cash_collected'], Decimal('1125.00'))
        self.assertEqual(c['credit_given'], Decimal('1800.00'))
        self.assertEqual(c['credit_outstanding_now'], Decimal('1800.00'))
        self.assertEqual(
            CreditTransaction.objects.get().transaction_type, 'credit_sale',
        )

    def test_cost_of_sales_wastage_and_adjustments(self):
        result = self.build()
        self.assertEqual(result['cost_of_sales'], Decimal('1820.00'))  # 620 (Apple) + 1200 (Oil)
        self.assertEqual(result['wastage_loss'], Decimal('310.00'))
        self.assertEqual(result['stock_adjustments'], Decimal('155.00'))
        self.assertEqual(result['gross_profit_sourced'], Decimal('390.00'))  # 2520 - 1820 - 310
        self.assertEqual(result['wastage_detail'], [{'product': 'Apple', 'quantity': Decimal('1'), 'value': Decimal('310.00')}])

    def test_farm_cost_centre_table(self):
        centres = {row['cost_centre']: row for row in self.build()['farm_cost_centres']}
        self.assertEqual(set(centres), {'crop:mango', 'livestock:goat'})

        mango = centres['crop:mango']
        self.assertEqual(mango['revenue'], Decimal('405.00'))  # 450 line_total * (1125/1250) coupon scale
        self.assertEqual(mango['direct_cost'], Decimal('1000.00'))
        self.assertEqual(mango['allocated_shared_cost'], Decimal('900.00'))  # 300 equal + 600 manual
        self.assertEqual(mango['depreciation'], Decimal('1200.00'))  # 200 direct + 1000 shared half
        self.assertEqual(mango['profit'], Decimal('-2695.00'))
        self.assertEqual(mango['margin_pct'], Decimal('-665.43'))
        self.assertEqual(mango['categories'], {'seeds': Decimal('0.00'), 'fertilizer': Decimal('1000.00')})

        goat = centres['livestock:goat']
        self.assertEqual(goat['revenue'], Decimal('0.00'))
        self.assertEqual(goat['direct_cost'], Decimal('200.00'))
        self.assertEqual(goat['allocated_shared_cost'], Decimal('700.00'))  # 300 equal + 400 manual
        self.assertEqual(goat['depreciation'], Decimal('1000.00'))
        self.assertEqual(goat['profit'], Decimal('-1900.00'))
        self.assertIsNone(goat['margin_pct'])  # zero revenue -- no margin to compute

    def test_farm_costs_and_depreciation_totals(self):
        result = self.build()
        self.assertEqual(result['farm_costs_total'], {
            'direct': Decimal('1200.00'), 'shared_allocated': Decimal('1600.00'),
            'unallocated_overhead': Decimal('0.00'), 'total': Decimal('2800.00'),
        })
        self.assertEqual(result['depreciation'], {
            'by_centre': {'crop:mango': Decimal('1200.00'), 'livestock:goat': Decimal('1000.00')},
            'unallocated': Decimal('0.00'), 'total': Decimal('2200.00'),
        })

    def test_net_profit(self):
        # 2925 revenue - 1820 cost_of_sales - 310 wastage - 155 adjustments
        # - 2800 farm_costs_total - 2200 depreciation = -4360.00
        self.assertEqual(self.build()['net_profit'], Decimal('-4360.00'))

    def test_no_data_quality_issues(self):
        dq = self.build()['data_quality']
        self.assertFalse(dq['has_issues'])
        for key, value in dq.items():
            if key != 'has_issues':
                self.assertEqual(value, 0, f'{key} should be 0 in this clean scenario')


class PLReportDataQualityCounterTests(TestCase):
    """Each test below deliberately creates exactly ONE instance of one
    data-quality problem, in an otherwise-empty period, and asserts that
    counter is exactly 1 while every other counter stays 0."""

    PERIOD_START = date(2026, 1, 1)
    PERIOD_END = date(2026, 1, 31)

    def setUp(self):
        self.category = Category.objects.create(name='DQ Test', order=1)
        self.operator = User.objects.create_user('dqtestop', password='pw', is_staff=True)

    def assertOnlyCounterIs(self, result, key, expected):
        dq = result['data_quality']
        self.assertEqual(dq[key], expected, dq)
        for other_key, value in dq.items():
            if other_key not in (key, 'has_issues'):
                self.assertEqual(value, 0, f'{other_key} unexpectedly non-zero: {dq}')

    def test_website_orders_excluded(self):
        order = ProductOrder.objects.create(
            name='Test Buyer', email='buyer@example.com', phone='9800000000',
            address='Farm Road', product_interest='Mango',
        )
        ProductOrder.objects.filter(id=order.id).update(
            ordered_at=djtz.make_aware(djtz.datetime(2026, 1, 15, 12, 0, 0))
        )
        result = build_pl(self.PERIOD_START, self.PERIOD_END)
        self.assertOnlyCounterIs(result, 'website_orders_excluded', 1)

    def test_sales_without_line_data(self):
        sale = POSSale.objects.create(
            cashier=self.operator, payment_method='cash',
            cart_snapshot=[], total_amount=Decimal('100.00'),
        )
        POSSale.objects.filter(id=sale.id).update(
            created_at=djtz.make_aware(djtz.datetime(2026, 1, 15, 12, 0, 0))
        )
        result = build_pl(self.PERIOD_START, self.PERIOD_END)
        self.assertOnlyCounterIs(result, 'sales_without_line_data', 1)
        self.assertEqual(result['revenue']['sales_before_line_tracking'], Decimal('0.00'))

    def test_sourced_lines_no_cost(self):
        product = Product.objects.create(
            name='Uncosted Sourced', slug='uncosted-sourced-dq', category=self.category, description='t',
            price=Decimal('100.00'), pricing_mode='fixed_quantity', origin='sourced',
        )
        InventoryMovement.objects.create(product=product, movement_type='harvest', source='admin', quantity=Decimal('10'))
        sale, _ = create_pos_sale(
            client_sale_id=str(uuid.uuid4()), cart=[{'product_id': product.id, 'qty': 1}],
            payments=[{'method': 'cash', 'amount': '100.00'}], operator_user=self.operator,
        )
        POSSale.objects.filter(id=sale.id).update(
            created_at=djtz.make_aware(djtz.datetime(2026, 1, 15, 12, 0, 0))
        )
        self.assertIsNone(sale.lines.get().unit_cost)
        result = build_pl(self.PERIOD_START, self.PERIOD_END)
        self.assertOnlyCounterIs(result, 'sourced_lines_no_cost', 1)

    def test_waste_no_cost(self):
        product = Product.objects.create(
            name='Uncosted Waste', slug='uncosted-waste-dq', category=self.category, description='t',
            price=Decimal('100.00'), pricing_mode='fixed_quantity', origin='sourced',
        )
        InventoryMovement.objects.create(product=product, movement_type='harvest', source='admin', quantity=Decimal('10'))
        waste = InventoryMovement.objects.create(product=product, movement_type='waste', source='admin', quantity=Decimal('1'))
        InventoryMovement.objects.filter(id=waste.id).update(
            created_at=djtz.make_aware(djtz.datetime(2026, 1, 15, 12, 0, 0))
        )
        self.assertIsNone(InventoryMovement.objects.get(id=waste.id).unit_cost)
        result = build_pl(self.PERIOD_START, self.PERIOD_END)
        self.assertOnlyCounterIs(result, 'waste_no_cost', 1)

    def test_unmapped_cost_centre_entries(self):
        CostEntry.objects.create(
            abms_id='c_unmapped1', date=date(2026, 1, 15), amount=Decimal('500.00'),
            cost_centre='crop:unmapped_crop', category='misc',
        )
        result = build_pl(self.PERIOD_START, self.PERIOD_END)
        self.assertOnlyCounterIs(result, 'unmapped_cost_centre_entries', 1)

    def test_farm_products_no_mapping(self):
        product = Product.objects.create(
            name='Unmapped Farm Product', slug='unmapped-farm-dq', category=self.category, description='t',
            price=Decimal('100.00'), pricing_mode='fixed_quantity', origin='farm',
        )
        InventoryMovement.objects.create(product=product, movement_type='harvest', source='admin', quantity=Decimal('10'))
        sale, _ = create_pos_sale(
            client_sale_id=str(uuid.uuid4()), cart=[{'product_id': product.id, 'qty': 1}],
            payments=[{'method': 'cash', 'amount': '100.00'}], operator_user=self.operator,
        )
        POSSale.objects.filter(id=sale.id).update(
            created_at=djtz.make_aware(djtz.datetime(2026, 1, 15, 12, 0, 0))
        )
        result = build_pl(self.PERIOD_START, self.PERIOD_END)
        self.assertOnlyCounterIs(result, 'farm_products_no_mapping', 1)
        self.assertEqual(result['revenue']['unmapped_farm_products'], Decimal('100.00'))

    def test_shared_allocation_fallback(self):
        # No CostCentreProduct mappings at all exist in this test -> zero
        # qualifying centres -> the shared entry falls back to "unallocated".
        CostEntry.objects.create(
            abms_id='c_shared_orphan', date=date(2026, 1, 15), amount=Decimal('400.00'),
            cost_centre='shared', category='misc', is_shared=True, allocation_rule='equal',
        )
        result = build_pl(self.PERIOD_START, self.PERIOD_END)
        self.assertOnlyCounterIs(result, 'shared_allocation_fallback', 1)
        self.assertEqual(result['farm_costs_total']['unallocated_overhead'], Decimal('400.00'))

    def test_dangling_reversal(self):
        # cost_centre is deliberately a MAPPED one here -- otherwise this
        # entry would also trip unmapped_cost_centre_entries, which is a
        # separate, correctly-firing counter tested on its own above.
        mango = Product.objects.create(
            name='Mapped Mango', slug='mapped-mango-dq', category=self.category, description='t',
            price=Decimal('100.00'), pricing_mode='fixed_quantity', origin='farm',
        )
        CostCentreProduct.objects.create(cost_centre='crop:mango', product=mango)
        CostEntry.objects.create(
            abms_id='c_rev_orphan', date=date(2026, 1, 15), amount=Decimal('-100.00'),
            cost_centre='crop:mango', category='misc', entry_type='reversal', reversal_of='c_never_existed',
        )
        result = build_pl(self.PERIOD_START, self.PERIOD_END)
        self.assertOnlyCounterIs(result, 'dangling_reversals', 1)
