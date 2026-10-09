"""Tests for season/batch costing: the CropBatch model, its two sync
endpoints (CropBatchSyncView, and CostSyncView/the ABMS inventory-movement
endpoint's new batch_code handling), and shop/batch_report.py's
build_batch_report(). Kept separate from shop/tests.py (already huge)
and from shop/tests_pl_report.py (the Monthly P&L -- this feature is
additive and independent of that one; see shop/batch_report.py's own
module docstring)."""
import uuid
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError as DjangoValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone as djtz
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from .batch_report import BatchReportError, build_batch_report, list_batches, next_batch_cost_per_kg
from .models import (
    Category, CostCentreProduct, CostEntry, CropBatch, InventoryMovement, POSSale, Product, ProductVariant,
)
from .views import create_pos_sale


class CropBatchSyncViewTests(TestCase):
    """POST /api/costs/batches/sync/ -- same token auth as CostSyncView."""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user('abms-batch-bridge', password='pw')
        self.token = Token.objects.create(user=self.user)
        self.url = reverse('api_costs_batches_sync')
        self.category = Category.objects.create(name='Batch Sync Test', order=1)

    def auth(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {self.token.key}')

    def payload(self, **overrides):
        base = {
            'id': 'b_test1', 'code': 'tomato-2026-01', 'name': 'Tomato Jan batch',
            'costCentre': 'crop:tomato', 'startDate': '2026-01-01', 'status': 'open',
        }
        base.update(overrides)
        return base

    def snake_payload(self, **overrides):
        # This is what the actually-deployed ABMS client really sends --
        # the production bug this field-aliasing fixes was a real 400
        # against exactly this shape (see CropBatchSyncView's docstring).
        base = {
            'abms_id': 'b_test1', 'code': 'tomato-2026-01', 'name': 'Tomato Jan batch',
            'cost_centre': 'crop:tomato', 'start_date': '2026-01-01', 'status': 'open',
        }
        base.update(overrides)
        return base

    def test_no_token_is_rejected(self):
        response = self.client.post(self.url, self.payload(), format='json')
        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_snake_case_payload_creates_a_batch(self):
        self.auth()
        response = self.client.post(self.url, self.snake_payload(), format='json')
        self.assertEqual(response.status_code, 201, response.data)
        batch = CropBatch.objects.get(abms_id='b_test1')
        self.assertEqual(batch.code, 'tomato-2026-01')
        self.assertEqual(batch.cost_centre, 'crop:tomato')
        self.assertEqual(batch.start_date, date(2026, 1, 1))
        self.assertEqual(batch.status, 'open')

    def test_camel_case_payload_still_works(self):
        self.auth()
        response = self.client.post(self.url, self.payload(), format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertTrue(CropBatch.objects.filter(abms_id='b_test1', code='tomato-2026-01').exists())

    def test_conflicting_duplicate_spellings_is_400(self):
        self.auth()
        response = self.client.post(self.url, self.snake_payload(costCentre='crop:different'), format='json')
        self.assertEqual(response.status_code, 400)
        self.assertFalse(CropBatch.objects.exists())

    def test_same_value_duplicate_spellings_is_allowed(self):
        self.auth()
        response = self.client.post(self.url, self.snake_payload(costCentre='crop:tomato'), format='json')
        self.assertEqual(response.status_code, 201, response.data)

    def test_resync_with_snake_case_updates_name_and_status(self):
        self.auth()
        self.client.post(self.url, self.snake_payload(), format='json')
        response = self.client.post(self.url, self.snake_payload(
            name='Tomato Jan batch (renamed)', status='closed',
        ), format='json')
        self.assertEqual(response.status_code, 200, response.data)
        batch = CropBatch.objects.get(abms_id='b_test1')
        self.assertEqual(batch.name, 'Tomato Jan batch (renamed)')
        self.assertEqual(batch.status, 'closed')

    def test_create_batch(self):
        self.auth()
        response = self.client.post(self.url, self.payload(), format='json')
        self.assertEqual(response.status_code, 201, response.data)
        batch = CropBatch.objects.get(abms_id='b_test1')
        self.assertEqual(batch.code, 'tomato-2026-01')
        self.assertEqual(batch.status, 'open')
        self.assertIsNone(batch.product)  # no CostCentreProduct mapping exists yet

    def test_product_auto_resolved_when_exactly_one_mapping_exists(self):
        tomato = Product.objects.create(
            name='Tomato', slug='tomato-batch-sync', category=self.category, description='t',
            price=Decimal('100'), pricing_mode='variable_weight', origin='farm',
        )
        CostCentreProduct.objects.create(cost_centre='crop:tomato', product=tomato)
        self.auth()
        response = self.client.post(self.url, self.payload(), format='json')
        self.assertEqual(response.status_code, 201, response.data)
        batch = CropBatch.objects.get(abms_id='b_test1')
        self.assertEqual(batch.product_id, tomato.id)

    def test_product_stays_null_when_mapping_is_ambiguous(self):
        tomato = Product.objects.create(
            name='Tomato A', slug='tomato-a-ambig', category=self.category, description='t',
            price=Decimal('100'), pricing_mode='variable_weight', origin='farm',
        )
        tomato2 = Product.objects.create(
            name='Tomato B', slug='tomato-b-ambig', category=self.category, description='t',
            price=Decimal('100'), pricing_mode='variable_weight', origin='farm',
        )
        CostCentreProduct.objects.create(cost_centre='crop:tomato', product=tomato)
        CostCentreProduct.objects.create(cost_centre='crop:tomato', product=tomato2)
        self.auth()
        response = self.client.post(self.url, self.payload(), format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertIsNone(CropBatch.objects.get(abms_id='b_test1').product)

    def test_idempotent_resync_is_a_no_op_update(self):
        self.auth()
        self.client.post(self.url, self.payload(), format='json')
        self.assertEqual(CropBatch.objects.count(), 1)
        response = self.client.post(self.url, self.payload(), format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['status'], 'updated')
        self.assertEqual(CropBatch.objects.count(), 1)

    def test_resync_can_rename_and_close(self):
        self.auth()
        self.client.post(self.url, self.payload(), format='json')
        response = self.client.post(self.url, self.payload(
            name='Tomato Jan batch (renamed)', status='closed', endDate='2026-03-01',
        ), format='json')
        self.assertEqual(response.status_code, 200)
        batch = CropBatch.objects.get(abms_id='b_test1')
        self.assertEqual(batch.name, 'Tomato Jan batch (renamed)')
        self.assertEqual(batch.status, 'closed')
        self.assertEqual(batch.end_date, date(2026, 3, 1))
        self.assertEqual(batch.code, 'tomato-2026-01')  # unchanged

    def test_changing_code_on_resync_is_rejected(self):
        self.auth()
        self.client.post(self.url, self.payload(), format='json')
        response = self.client.post(self.url, self.payload(code='a-different-code'), format='json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(CropBatch.objects.get(abms_id='b_test1').code, 'tomato-2026-01')

    def test_unknown_field_rejected(self):
        self.auth()
        response = self.client.post(self.url, self.payload(somethingElse='x'), format='json')
        self.assertEqual(response.status_code, 400)
        self.assertFalse(CropBatch.objects.exists())

    def test_duplicate_code_different_abms_id_rejected(self):
        self.auth()
        self.client.post(self.url, self.payload(), format='json')
        response = self.client.post(self.url, self.payload(id='b_test2'), format='json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(CropBatch.objects.count(), 1)


class CostSyncBatchCodeTests(TestCase):
    """POST /api/costs/sync/'s new optional batchCode field."""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user('abms-cost-batch-bridge', password='pw')
        self.token = Token.objects.create(user=self.user)
        self.url = reverse('api_costs_sync')
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {self.token.key}')
        self.batch = CropBatch.objects.create(
            code='tomato-2026-01', abms_id='b1', cost_centre='crop:tomato', start_date=date(2026, 1, 1),
        )

    def entry(self, **overrides):
        base = {
            'id': 'c_batch1', 'date': '2026-01-05', 'amount': 9800, 'type': 'cost',
            'costCentre': 'crop:tomato', 'category': 'fertilizer', 'batchCode': 'tomato-2026-01',
        }
        base.update(overrides)
        return base

    def test_valid_batch_code_is_stored(self):
        response = self.client.post(self.url, {'entries': [self.entry()], 'assets': []}, format='json')
        self.assertEqual(response.data['results'][0]['status'], 'created')
        self.assertEqual(CostEntry.objects.get(abms_id='c_batch1').batch_code, 'tomato-2026-01')

    def test_unknown_batch_code_is_rejected(self):
        response = self.client.post(
            self.url, {'entries': [self.entry(id='c_batch2', batchCode='no-such-batch')], 'assets': []}, format='json',
        )
        self.assertEqual(response.data['results'][0]['status'], 'batch_not_found')
        self.assertFalse(CostEntry.objects.filter(abms_id='c_batch2').exists())

    def test_closed_batch_is_rejected(self):
        self.batch.status = 'closed'
        self.batch.save()
        response = self.client.post(self.url, {'entries': [self.entry()], 'assets': []}, format='json')
        self.assertEqual(response.data['results'][0]['status'], 'batch_closed')
        self.assertFalse(CostEntry.objects.exists())

    def test_blank_batch_code_is_unaffected(self):
        response = self.client.post(
            self.url, {'entries': [self.entry(id='c_unbatched', batchCode='')], 'assets': []}, format='json',
        )
        self.assertEqual(response.data['results'][0]['status'], 'created')
        self.assertEqual(CostEntry.objects.get(abms_id='c_unbatched').batch_code, '')

    def test_reversal_against_a_batched_entry_also_needs_a_valid_batch(self):
        self.client.post(self.url, {'entries': [self.entry()], 'assets': []}, format='json')
        reversal = {
            'id': 'c_batch1_rev', 'date': '2026-01-06', 'amount': -9800, 'type': 'reversal',
            'reversalOf': 'c_batch1', 'costCentre': 'crop:tomato', 'category': 'fertilizer',
            'batchCode': 'tomato-2026-01',
        }
        response = self.client.post(self.url, {'entries': [reversal], 'assets': []}, format='json')
        self.assertEqual(response.data['results'][0]['status'], 'created')
        net = sum((e.amount for e in CostEntry.objects.filter(batch_code='tomato-2026-01')), Decimal('0'))
        self.assertEqual(net, Decimal('0.00'))


class InventoryMovementBatchCodeTests(TestCase):
    """POST /api/inventory/movements/ (the ABMS harvest endpoint) -- the
    new optional batch_code field and its two checks + the product-match
    check."""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user('abms-harvest-bridge', password='pw')
        self.token = Token.objects.create(user=self.user)
        self.url = reverse('api_inventory_movement_create')
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {self.token.key}')
        self.category = Category.objects.create(name='Harvest Batch Test', order=1)
        self.tomato = Product.objects.create(
            name='Harvest Tomato', slug='harvest-tomato-batch', category=self.category, description='t',
            price=Decimal('100'), pricing_mode='variable_weight', origin='farm',
        )
        self.other_product = Product.objects.create(
            name='Other Crop', slug='other-crop-batch', category=self.category, description='t',
            price=Decimal('100'), pricing_mode='variable_weight', origin='farm',
        )
        self.batch = CropBatch.objects.create(
            code='tomato-2026-01', abms_id='b1', cost_centre='crop:tomato',
            product=self.tomato, start_date=date(2026, 1, 1),
        )

    def test_harvest_with_valid_batch_code_succeeds(self):
        response = self.client.post(self.url, {
            'product': self.tomato.id, 'movement_type': 'harvest', 'source': 'abms',
            'quantity': '50', 'batch_code': 'tomato-2026-01',
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(InventoryMovement.objects.get(id=response.data['id']).batch_code, 'tomato-2026-01')

    def test_unknown_batch_code_is_400(self):
        response = self.client.post(self.url, {
            'product': self.tomato.id, 'movement_type': 'harvest', 'source': 'abms',
            'quantity': '50', 'batch_code': 'no-such-batch',
        }, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertFalse(InventoryMovement.objects.filter(batch_code='no-such-batch').exists())

    def test_closed_batch_is_409(self):
        self.batch.status = 'closed'
        self.batch.save()
        response = self.client.post(self.url, {
            'product': self.tomato.id, 'movement_type': 'harvest', 'source': 'abms',
            'quantity': '50', 'batch_code': 'tomato-2026-01',
        }, format='json')
        self.assertEqual(response.status_code, 409)
        self.assertFalse(InventoryMovement.objects.exists())

    def test_product_mismatch_is_400(self):
        response = self.client.post(self.url, {
            'product': self.other_product.id, 'movement_type': 'harvest', 'source': 'abms',
            'quantity': '50', 'batch_code': 'tomato-2026-01',
        }, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertFalse(InventoryMovement.objects.exists())

    def test_batch_code_on_a_sale_movement_is_400(self):
        InventoryMovement.objects.create(product=self.tomato, movement_type='harvest', source='admin', quantity=Decimal('10'))
        response = self.client.post(self.url, {
            'product': self.tomato.id, 'movement_type': 'sale', 'source': 'abms',
            'quantity': '1', 'batch_code': 'tomato-2026-01',
        }, format='json')
        self.assertEqual(response.status_code, 400)

    # ── 4A: product becomes optional when batch_code resolves it ────────

    def test_product_omitted_with_batch_is_resolved_from_batch_product(self):
        response = self.client.post(self.url, {
            'movement_type': 'harvest', 'source': 'abms', 'quantity': '50', 'batch_code': 'tomato-2026-01',
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(InventoryMovement.objects.get(id=response.data['id']).product_id, self.tomato.id)

    def test_batch_with_no_mapped_product_is_400_with_specific_message(self):
        unmapped_batch = CropBatch.objects.create(
            code='unmapped-2026-01', abms_id='b2', cost_centre='crop:unmapped', start_date=date(2026, 1, 1),
        )
        response = self.client.post(self.url, {
            'movement_type': 'harvest', 'source': 'abms', 'quantity': '50', 'batch_code': unmapped_batch.code,
        }, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('No product is mapped to cost centre crop:unmapped', str(response.data))
        self.assertIn('Admin > Cost centre products', str(response.data))
        self.assertFalse(InventoryMovement.objects.exists())

    def test_batch_with_no_mapped_product_is_400_even_when_product_is_given(self):
        # batch.product is the one source of truth here -- a caller-supplied
        # product can't substitute for a missing CostCentreProduct mapping.
        unmapped_batch = CropBatch.objects.create(
            code='unmapped-2026-02', abms_id='b3', cost_centre='crop:unmapped2', start_date=date(2026, 1, 1),
        )
        response = self.client.post(self.url, {
            'product': self.tomato.id, 'movement_type': 'harvest', 'source': 'abms',
            'quantity': '50', 'batch_code': unmapped_batch.code,
        }, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertFalse(InventoryMovement.objects.exists())

    def test_no_batch_code_still_requires_product(self):
        response = self.client.post(self.url, {
            'movement_type': 'harvest', 'source': 'abms', 'quantity': '50',
        }, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('product', response.data)
        self.assertFalse(InventoryMovement.objects.exists())

    def test_no_batch_code_with_product_is_unchanged(self):
        response = self.client.post(self.url, {
            'product': self.tomato.id, 'movement_type': 'harvest', 'source': 'abms', 'quantity': '50',
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(InventoryMovement.objects.get(id=response.data['id']).batch_code, '')


class CostCentreProductSlugValidatorTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(name='Slug Validator Test', order=1)
        self.product = Product.objects.create(
            name='Slug Test Product', slug='slug-test-product', category=self.category, description='t',
            price=Decimal('1'), pricing_mode='fixed_quantity', origin='farm',
        )

    def test_accepts_valid_slug_form(self):
        row = CostCentreProduct(cost_centre='crop:bottle-gourd', product=self.product)
        row.full_clean()  # must not raise

    def test_rejects_spaces(self):
        row = CostCentreProduct(cost_centre='crop:local tomato', product=self.product)
        with self.assertRaises(DjangoValidationError):
            row.full_clean()

    def test_rejects_capitals(self):
        row = CostCentreProduct(cost_centre='crop:Green chili', product=self.product)
        with self.assertRaises(DjangoValidationError):
            row.full_clean()


class CheckCostCentreKeysCommandTests(TestCase):
    """Read-only management command -- just confirms it finds what it's
    supposed to find and writes nothing to the database."""

    def setUp(self):
        self.category = Category.objects.create(name='Command Test', order=1)
        self.product = Product.objects.create(
            name='Command Test Product', slug='command-test-product', category=self.category, description='t',
            price=Decimal('1'), pricing_mode='fixed_quantity', origin='farm',
        )

    def run_command(self):
        import io
        from django.core.management import call_command
        out = io.StringIO()
        call_command('check_cost_centre_keys', stdout=out)
        return out.getvalue()

    def test_reports_non_slug_mapping_and_unmapped_centres(self):
        CostCentreProduct.objects.create(cost_centre='water', product=self.product)  # legacy, pre-dates the validator
        CostEntry.objects.create(abms_id='cmdtest1', date=date(2026, 1, 1), amount=Decimal('10'), cost_centre='crop:never_mapped')
        before_count = CostCentreProduct.objects.count()
        output = self.run_command()
        self.assertIn("'water'", output)
        self.assertIn("'crop:never_mapped'", output)
        self.assertEqual(CostCentreProduct.objects.count(), before_count)  # read-only

    def test_clean_state_reports_none(self):
        output = self.run_command()
        self.assertIn('(none)', output)


class BuildBatchReportTests(TestCase):
    """shop/batch_report.py's build_batch_report() -- cost/kg arithmetic,
    reversal handling, FIFO across two overlapping batches, zero harvest."""

    def setUp(self):
        self.category = Category.objects.create(name='Batch Report Test', order=1)
        self.operator = User.objects.create_user('batchreportop', password='pw')

    def test_unknown_code_raises(self):
        with self.assertRaises(BatchReportError) as cm:
            build_batch_report('does-not-exist')
        self.assertEqual(cm.exception.status, 404)

    def test_cost_per_kg_arithmetic(self):
        tomato = Product.objects.create(
            name='Arith Tomato', slug='arith-tomato', category=self.category, description='t',
            price=Decimal('100'), pricing_mode='variable_weight', origin='farm',
        )
        CropBatch.objects.create(code='arith-1', abms_id='ar1', cost_centre='crop:tomato', product=tomato, start_date=date(2026, 1, 1))
        CostEntry.objects.create(abms_id='arc1', date=date(2026, 1, 5), amount=Decimal('9800.00'), cost_centre='crop:tomato', category='fertilizer', batch_code='arith-1')
        InventoryMovement.objects.create(product=tomato, movement_type='harvest', source='admin', quantity=Decimal('200'), batch_code='arith-1')
        report = build_batch_report('arith-1')
        self.assertEqual(report['cost']['total'], Decimal('9800.00'))
        self.assertEqual(report['harvested_kg'], Decimal('200'))
        self.assertEqual(report['cost_per_kg'], Decimal('49.00'))
        self.assertEqual(report['break_even_price'], Decimal('49.00'))

    def test_zero_harvest_has_no_division_error(self):
        tomato = Product.objects.create(
            name='Zero Tomato', slug='zero-tomato', category=self.category, description='t',
            price=Decimal('100'), pricing_mode='variable_weight', origin='farm',
        )
        CropBatch.objects.create(code='zero-1', abms_id='z1', cost_centre='crop:tomato', product=tomato, start_date=date(2026, 1, 1))
        CostEntry.objects.create(abms_id='zc1', date=date(2026, 1, 5), amount=Decimal('500.00'), cost_centre='crop:tomato', category='seeds', batch_code='zero-1')
        report = build_batch_report('zero-1')
        self.assertEqual(report['harvested_kg'], Decimal('0'))
        self.assertIsNone(report['cost_per_kg'])
        self.assertIsNone(report['cost_of_sold'])
        self.assertIsNone(report['waste_loss'])
        self.assertIsNone(report['remaining_value'])
        self.assertIsNone(report['profit_to_date'])
        self.assertEqual(report['sold_kg'], Decimal('0'))  # product IS set -- FIFO still runs, just finds nothing

    def test_no_product_mapped_skips_fifo_entirely(self):
        CropBatch.objects.create(code='noproduct-1', abms_id='np1', cost_centre='crop:unmapped', start_date=date(2026, 1, 1))
        CostEntry.objects.create(abms_id='npc1', date=date(2026, 1, 5), amount=Decimal('100.00'), cost_centre='crop:unmapped', category='misc', batch_code='noproduct-1')
        report = build_batch_report('noproduct-1')
        self.assertIsNone(report['sold_kg'])
        self.assertIsNone(report['revenue'])
        self.assertTrue(any('no product mapped' in n for n in report['notes']))

    def test_reversal_nets_out_cost(self):
        tomato = Product.objects.create(
            name='Rev Tomato', slug='rev-tomato', category=self.category, description='t',
            price=Decimal('100'), pricing_mode='variable_weight', origin='farm',
        )
        CropBatch.objects.create(code='rev-1', abms_id='rv1', cost_centre='crop:tomato', product=tomato, start_date=date(2026, 1, 1))
        CostEntry.objects.create(abms_id='rvc1', date=date(2026, 1, 5), amount=Decimal('1000.00'), cost_centre='crop:tomato', category='seeds', batch_code='rev-1')
        CostEntry.objects.create(abms_id='rvc1_rev', date=date(2026, 1, 6), amount=Decimal('-400.00'), cost_centre='crop:tomato', category='seeds', batch_code='rev-1', entry_type='reversal', reversal_of='rvc1')
        report = build_batch_report('rev-1')
        self.assertEqual(report['cost']['total'], Decimal('600.00'))
        self.assertEqual(report['cost']['by_category']['seeds'], Decimal('600.00'))

    def test_fifo_across_two_overlapping_batches_with_a_line_spanning_sale(self):
        tomato = Product.objects.create(
            name='Fifo Tomato', slug='fifo-tomato-formal', category=self.category, description='t',
            price=Decimal('100'), pricing_mode='variable_weight', weight_step=Decimal('0.5'), origin='farm',
        )
        CropBatch.objects.create(code='ffa', abms_id='ffa1', cost_centre='crop:tomato', product=tomato, start_date=date(2026, 1, 1))
        CropBatch.objects.create(code='ffb', abms_id='ffb1', cost_centre='crop:tomato', product=tomato, start_date=date(2026, 2, 1))

        m0 = InventoryMovement.objects.create(product=tomato, movement_type='harvest', source='admin', quantity=Decimal('50'))
        InventoryMovement.objects.filter(id=m0.id).update(created_at=djtz.make_aware(djtz.datetime(2025, 12, 1, 8, 0, 0)))
        m1 = InventoryMovement.objects.create(product=tomato, movement_type='harvest', source='admin', quantity=Decimal('100'), batch_code='ffa')
        InventoryMovement.objects.filter(id=m1.id).update(created_at=djtz.make_aware(djtz.datetime(2026, 1, 2, 8, 0, 0)))
        m2 = InventoryMovement.objects.create(product=tomato, movement_type='harvest', source='admin', quantity=Decimal('100'), batch_code='ffb')
        InventoryMovement.objects.filter(id=m2.id).update(created_at=djtz.make_aware(djtz.datetime(2026, 2, 2, 8, 0, 0)))

        # sale 1: 120kg -- consumes 50 unbatched + 70 from batch A
        sale1, _ = create_pos_sale(
            client_sale_id=str(uuid.uuid4()), cart=[{'product_id': tomato.id, 'qty': 1, 'weight': '120.00'}],
            payments=[{'method': 'cash', 'amount': '12000.00'}], operator_user=self.operator,
        )
        POSSale.objects.filter(id=sale1.id).update(created_at=djtz.make_aware(djtz.datetime(2026, 1, 10, 8, 0, 0)))

        # sale 2: 50kg -- spans batch A's remaining 30kg + batch B's first 20kg
        sale2, _ = create_pos_sale(
            client_sale_id=str(uuid.uuid4()), cart=[{'product_id': tomato.id, 'qty': 1, 'weight': '50.00'}],
            payments=[{'method': 'cash', 'amount': '5000.00'}], operator_user=self.operator,
        )
        POSSale.objects.filter(id=sale2.id).update(created_at=djtz.make_aware(djtz.datetime(2026, 2, 10, 8, 0, 0)))

        report_a = build_batch_report('ffa')
        report_b = build_batch_report('ffb')

        self.assertEqual(report_a['sold_kg'], Decimal('100.000'))
        self.assertEqual(report_a['remaining_kg'], Decimal('0.000'))
        self.assertEqual(report_a['revenue'], Decimal('10000.00'))  # 70kg@100 + 30kg@100

        self.assertEqual(report_b['sold_kg'], Decimal('20.000'))
        self.assertEqual(report_b['remaining_kg'], Decimal('80.000'))
        self.assertEqual(report_b['revenue'], Decimal('2000.00'))  # 20kg@100

    def test_adjustment_remove_consumes_fifo_stock_like_waste(self):
        # The exact scenario from the brief: harvest 10, sale 1, then an
        # adjustment_remove of 9 -- the batch should show 0 unsold, not 9
        # (which is what it showed before this fix, since adjustment_remove
        # wasn't consumed by the FIFO walk at all).
        tomato = Product.objects.create(
            name='Adj Tomato', slug='adj-tomato-formal', category=self.category, description='t',
            price=Decimal('100'), pricing_mode='variable_weight', weight_step=Decimal('0.5'), origin='farm',
        )
        CropBatch.objects.create(code='adjr-1', abms_id='adjr1', cost_centre='crop:tomato', product=tomato, start_date=date(2026, 1, 1))
        CostEntry.objects.create(abms_id='adjrc1', date=date(2026, 1, 1), amount=Decimal('500.00'), cost_centre='crop:tomato', category='fert', batch_code='adjr-1')
        InventoryMovement.objects.create(product=tomato, movement_type='harvest', source='admin', quantity=Decimal('10'), batch_code='adjr-1')

        sale, _ = create_pos_sale(
            client_sale_id=str(uuid.uuid4()), cart=[{'product_id': tomato.id, 'qty': 1, 'weight': '1.00'}],
            payments=[{'method': 'cash', 'amount': '100.00'}], operator_user=self.operator,
        )
        InventoryMovement.objects.create(product=tomato, movement_type='adjustment_remove', source='admin', quantity=Decimal('9'))

        report = build_batch_report('adjr-1')
        self.assertEqual(report['sold_kg'], Decimal('1.000'))
        self.assertEqual(report['waste_kg'], Decimal('9.000'))  # the adjustment_remove, folded into waste
        self.assertEqual(report['remaining_kg'], Decimal('0.000'))
        # cost_per_kg = 500/10 = 50 -- the removed 9kg is a real loss at that rate.
        self.assertEqual(report['waste_loss'], Decimal('450.00'))
        self.assertEqual(report['remaining_value'], Decimal('0.00'))
        self.assertEqual(report['cost_of_sold'], Decimal('50.00'))
        self.assertEqual(report['profit_to_date'], Decimal('-400.00'))  # 100 revenue - 50 cost_of_sold - 450 waste_loss

    def test_next_batch_cost_per_kg_skips_batch_emptied_by_adjustment(self):
        tomato = Product.objects.create(
            name='Adj Skip Tomato', slug='adj-skip-tomato', category=self.category, description='t',
            price=Decimal('100'), pricing_mode='variable_weight', weight_step=Decimal('0.5'), origin='farm',
        )
        CropBatch.objects.create(code='adjs-a', abms_id='adjsa1', cost_centre='crop:tomato', product=tomato, start_date=date(2026, 1, 1))
        CropBatch.objects.create(code='adjs-b', abms_id='adjsb1', cost_centre='crop:tomato', product=tomato, start_date=date(2026, 2, 1))
        CostEntry.objects.create(abms_id='adjsac1', date=date(2026, 1, 1), amount=Decimal('500'), cost_centre='crop:tomato', category='fert', batch_code='adjs-a')
        CostEntry.objects.create(abms_id='adjsbc1', date=date(2026, 2, 1), amount=Decimal('900'), cost_centre='crop:tomato', category='fert', batch_code='adjs-b')

        m1 = InventoryMovement.objects.create(product=tomato, movement_type='harvest', source='admin', quantity=Decimal('10'), batch_code='adjs-a')
        InventoryMovement.objects.filter(id=m1.id).update(created_at=djtz.make_aware(djtz.datetime(2026, 1, 2, 8, 0, 0)))
        m2 = InventoryMovement.objects.create(product=tomato, movement_type='harvest', source='admin', quantity=Decimal('10'), batch_code='adjs-b')
        InventoryMovement.objects.filter(id=m2.id).update(created_at=djtz.make_aware(djtz.datetime(2026, 2, 2, 8, 0, 0)))

        self.assertEqual(next_batch_cost_per_kg(tomato), Decimal('50.00'))  # batch A still has stock

        sale, _ = create_pos_sale(
            client_sale_id=str(uuid.uuid4()), cart=[{'product_id': tomato.id, 'qty': 1, 'weight': '1.00'}],
            payments=[{'method': 'cash', 'amount': '100.00'}], operator_user=self.operator,
        )
        InventoryMovement.objects.create(product=tomato, movement_type='adjustment_remove', source='admin', quantity=Decimal('9'))

        # batch A is now fully emptied (1 sold + 9 adjusted away = all 10kg) --
        # next_batch_cost_per_kg must skip it and report batch B's rate.
        self.assertEqual(next_batch_cost_per_kg(tomato), Decimal('90.00'))

    def test_no_adjustments_gives_unchanged_numbers(self):
        # Same build_batch_report() scenario as test_cost_per_kg_arithmetic,
        # with zero adjustment_remove movements -- confirms adding
        # 'adjustment_remove' to CONSUMING_TYPES doesn't change anything
        # for a batch that never has one.
        tomato = Product.objects.create(
            name='No Adj Tomato', slug='no-adj-tomato', category=self.category, description='t',
            price=Decimal('100'), pricing_mode='variable_weight', origin='farm',
        )
        CropBatch.objects.create(code='noadj-1', abms_id='noadj1', cost_centre='crop:tomato', product=tomato, start_date=date(2026, 1, 1))
        CostEntry.objects.create(abms_id='noadjc1', date=date(2026, 1, 5), amount=Decimal('9800.00'), cost_centre='crop:tomato', category='fertilizer', batch_code='noadj-1')
        InventoryMovement.objects.create(product=tomato, movement_type='harvest', source='admin', quantity=Decimal('200'), batch_code='noadj-1')
        report = build_batch_report('noadj-1')
        self.assertEqual(report['cost_per_kg'], Decimal('49.00'))
        self.assertEqual(report['sold_kg'], Decimal('0'))
        self.assertEqual(report['waste_kg'], Decimal('0'))
        self.assertEqual(report['remaining_kg'], Decimal('200'))

    def test_list_batches_includes_summary_fields(self):
        tomato = Product.objects.create(
            name='List Tomato', slug='list-tomato', category=self.category, description='t',
            price=Decimal('100'), pricing_mode='variable_weight', origin='farm',
        )
        CropBatch.objects.create(code='list-1', abms_id='l1', cost_centre='crop:tomato', product=tomato, start_date=date(2026, 1, 1))
        rows = list_batches()
        self.assertEqual(len(rows), 1)
        self.assertEqual(set(rows[0]), {'code', 'name', 'status', 'total_cost', 'harvested_kg', 'cost_per_kg', 'revenue', 'profit_to_date'})


class NextBatchCostPerKgTests(TestCase):
    """shop/batch_report.py's next_batch_cost_per_kg() -- the POS's
    below-direct-cost warning reads this per product. Null cases, and
    picking the correct batch across a FIFO transition, are the two
    things the brief asked to be tested explicitly."""

    def setUp(self):
        self.category = Category.objects.create(name='Next Batch Cost Test', order=1)
        self.operator = User.objects.create_user('nextcostop', password='pw')

    def test_no_batch_at_all_is_null(self):
        product = Product.objects.create(
            name='No Batch Product', slug='no-batch-product', category=self.category, description='t',
            price=Decimal('100'), pricing_mode='variable_weight', origin='farm',
        )
        self.assertIsNone(next_batch_cost_per_kg(product))

    def test_batch_exists_but_zero_harvest_is_null(self):
        product = Product.objects.create(
            name='Zero Harvest Product', slug='zero-harvest-product', category=self.category, description='t',
            price=Decimal('100'), pricing_mode='variable_weight', origin='farm',
        )
        CropBatch.objects.create(code='zh-1', abms_id='zh1', cost_centre='crop:zeroharvest', product=product, start_date=date(2026, 1, 1))
        CostEntry.objects.create(abms_id='zhc1', date=date(2026, 1, 1), amount=Decimal('500'), cost_centre='crop:zeroharvest', category='seeds', batch_code='zh-1')
        # No harvest movement at all -- the batch exists but has 0 harvested_kg,
        # so pools['zh-1'] never rises above 0 and next_batch_cost_per_kg
        # correctly finds no batch with unsold stock to report a rate for.
        self.assertIsNone(next_batch_cost_per_kg(product))

    def test_fifo_picks_the_batch_still_holding_stock(self):
        product = Product.objects.create(
            name='FIFO Cost Product', slug='fifo-cost-product', category=self.category, description='t',
            price=Decimal('100'), pricing_mode='variable_weight', weight_step=Decimal('0.5'), origin='farm',
        )
        CropBatch.objects.create(code='fcp-a', abms_id='fcpa1', cost_centre='crop:fifocost', product=product, start_date=date(2026, 1, 1))
        CropBatch.objects.create(code='fcp-b', abms_id='fcpb1', cost_centre='crop:fifocost', product=product, start_date=date(2026, 2, 1))
        CostEntry.objects.create(abms_id='fcpac1', date=date(2026, 1, 1), amount=Decimal('5000'), cost_centre='crop:fifocost', category='fert', batch_code='fcp-a')
        CostEntry.objects.create(abms_id='fcpbc1', date=date(2026, 2, 1), amount=Decimal('9000'), cost_centre='crop:fifocost', category='fert', batch_code='fcp-b')

        m1 = InventoryMovement.objects.create(product=product, movement_type='harvest', source='admin', quantity=Decimal('100'), batch_code='fcp-a')
        InventoryMovement.objects.filter(id=m1.id).update(created_at=djtz.make_aware(djtz.datetime(2026, 1, 2, 8, 0, 0)))
        m2 = InventoryMovement.objects.create(product=product, movement_type='harvest', source='admin', quantity=Decimal('100'), batch_code='fcp-b')
        InventoryMovement.objects.filter(id=m2.id).update(created_at=djtz.make_aware(djtz.datetime(2026, 2, 2, 8, 0, 0)))

        self.assertEqual(next_batch_cost_per_kg(product), Decimal('50.00'))  # 5000/100, batch A still has stock

        sale, _ = create_pos_sale(
            client_sale_id=str(uuid.uuid4()), cart=[{'product_id': product.id, 'qty': 1, 'weight': '100.00'}],
            payments=[{'method': 'cash', 'amount': '10000.00'}], operator_user=self.operator,
        )
        POSSale.objects.filter(id=sale.id).update(created_at=djtz.make_aware(djtz.datetime(2026, 1, 10, 8, 0, 0)))

        self.assertEqual(next_batch_cost_per_kg(product), Decimal('90.00'))  # 9000/100, batch A now fully sold


class PosViewCostPerKgTests(TestCase):
    """pos_view's embedded PRODUCTS payload -- cost_per_kg present/null
    correctly, and never present on the public product endpoints."""

    def setUp(self):
        self.category = Category.objects.create(name='Pos View Cost Test', order=1)
        self.staff = User.objects.create_user('poscostviewstaff', password='pw', is_staff=True)
        self.client.login(username='poscostviewstaff', password='pw')

    def _products_payload(self):
        import json
        response = self.client.get(reverse('pos'))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')
        marker = 'id="posProductsData"'
        start = content.index(marker)
        json_start = content.index('>', start) + 1
        json_end = content.index('</script>', json_start)
        return json.loads(content[json_start:json_end])

    def test_product_with_no_batch_has_null_cost_per_kg(self):
        Product.objects.create(
            name='No Batch Pos Product', slug='no-batch-pos-product', category=self.category, description='t',
            price=Decimal('100'), pricing_mode='variable_weight', origin='farm', is_available=True,
        )
        products = self._products_payload()
        entry = next(p for p in products if p['name'] == 'No Batch Pos Product')
        self.assertIsNone(entry['cost_per_kg'])

    def test_product_with_fifo_eligible_batch_has_matching_cost_per_kg(self):
        product = Product.objects.create(
            name='Fifo Pos Product', slug='fifo-pos-product', category=self.category, description='t',
            price=Decimal('100'), pricing_mode='variable_weight', origin='farm', is_available=True,
        )
        CropBatch.objects.create(code='fpp-1', abms_id='fpp1', cost_centre='crop:fifopos', product=product, start_date=date(2026, 1, 1))
        CostEntry.objects.create(abms_id='fppc1', date=date(2026, 1, 1), amount=Decimal('5000'), cost_centre='crop:fifopos', category='fert', batch_code='fpp-1')
        InventoryMovement.objects.create(product=product, movement_type='harvest', source='admin', quantity=Decimal('100'), batch_code='fpp-1')

        products = self._products_payload()
        entry = next(p for p in products if p['name'] == 'Fifo Pos Product')
        self.assertEqual(entry['cost_per_kg'], '50.00')

    def test_cost_per_kg_absent_from_public_product_list_endpoint(self):
        product = Product.objects.create(
            name='Public Endpoint Product', slug='public-endpoint-product', category=self.category, description='t',
            price=Decimal('100'), pricing_mode='variable_weight', origin='farm', is_available=True,
        )
        CropBatch.objects.create(code='pep-1', abms_id='pep1', cost_centre='crop:publicendpoint', product=product, start_date=date(2026, 1, 1))
        CostEntry.objects.create(abms_id='pepc1', date=date(2026, 1, 1), amount=Decimal('5000'), cost_centre='crop:publicendpoint', category='fert', batch_code='pep-1')
        InventoryMovement.objects.create(product=product, movement_type='harvest', source='admin', quantity=Decimal('100'), batch_code='pep-1')

        response = self.client.get(reverse('v1_product_list'))
        self.assertEqual(response.status_code, 200)
        entry = next(p for p in response.data['results'] if p['name'] == 'Public Endpoint Product')
        self.assertNotIn('cost_per_kg', entry)

        detail_response = self.client.get(reverse('v1_product_detail', args=[product.id]))
        self.assertNotIn('cost_per_kg', detail_response.data)

    def test_pos_sale_flow_is_unaffected(self):
        # No batch/cost data involved at all -- confirms Task B's changes
        # (pos_view, templates/pos.html, batch_report.py) don't touch the
        # sale-creation path in any way. The sale payload itself (what
        # templates/pos.html's completeSaleBtn sends) was never changed --
        # this exercises the same server-side entry point it posts to.
        product = Product.objects.create(
            name='Sale Flow Product', slug='sale-flow-product', category=self.category, description='t',
            price=Decimal('200'), pricing_mode='fixed_quantity', origin='farm', is_available=True,
        )
        InventoryMovement.objects.create(product=product, movement_type='harvest', source='admin', quantity=Decimal('10'))
        sale, created = create_pos_sale(
            client_sale_id=str(uuid.uuid4()), cart=[{'product_id': product.id, 'qty': 2}],
            payments=[{'method': 'cash', 'amount': '400.00'}], operator_user=self.staff,
        )
        self.assertTrue(created)
        self.assertEqual(sale.total_amount, Decimal('400.00'))
        self.assertEqual(sale.lines.get().quantity, Decimal('2'))
