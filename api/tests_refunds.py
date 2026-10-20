"""Tests for the POS Refund feature: Refund/RefundLine (shop/models.py),
create_pos_refund()/allocate_sale_total_to_lines()/_largest_remainder_shares()/
get_pos_sale_refund_info()/normalize_sale_number() (shop/views.py), and
GET/POST /api/v1/pos/refunds/* (api/views.py). Also covers the staff-only
page views (/pos/refund/, /pos/refund/<refund_number>/) and the read-only
admin registration.

Kept separate from api/tests.py for the same reason api/tests_reports.py
is -- a distinct, sizeable feature area.
"""
import uuid
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib import admin as django_admin
from django.db.models import Sum
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework import status

from shop.admin import RefundAdmin
from shop.models import BundleItem, BusinessSettings, Coupon, Customer, InventoryMovement, Offer, Product, Refund, RefundLine
from shop.views import (
    _largest_remainder_shares,
    allocate_sale_total_to_lines,
    create_pos_sale,
    normalize_sale_number,
)

from .tests import ApiTestBase


class RefundTestBase(ApiTestBase):
    def setUp(self):
        super().setUp()
        self.client.login(username='cashier', password='pw')
        self.unlock_terminal()

    def complete_sale(self, cart, payments, **kwargs):
        sale, _ = create_pos_sale(
            client_sale_id=str(uuid.uuid4()), cart=cart, payments=payments,
            operator_user=self.staff, **kwargs,
        )
        return sale

    def post_refund(self, sale_number, selections, method='cash', reason='changed_mind',
                     note='', client_refund_id=None):
        return self.client.post(reverse('v1_pos_refund_create'), {
            'sale_number': sale_number,
            'selections': selections,
            'method': method,
            'reason': reason,
            'note': note,
            'client_refund_id': client_refund_id or str(uuid.uuid4()),
        }, format='json')

    def lookup(self, sale_number):
        return self.client.get(reverse('v1_pos_refund_lookup'), {'sale_number': sale_number})


# ─── Pure helper functions ──────────────────────────────────────

class LargestRemainderSharesTests(TestCase):
    def test_divides_evenly(self):
        self.assertEqual(_largest_remainder_shares(Decimal('100'), 4), [Decimal(25)] * 4)

    def test_remainder_goes_to_last_shares(self):
        # 10/3 = 3 remainder 1 -- the ONE extra rupee goes to the LAST share.
        self.assertEqual(_largest_remainder_shares(Decimal('10'), 3), [Decimal(3), Decimal(3), Decimal(4)])

    def test_sums_exactly_to_amount(self):
        shares = _largest_remainder_shares(Decimal('1250'), 5)
        self.assertEqual(sum(shares, Decimal('0')), Decimal('1250'))


class NormalizeSaleNumberTests(TestCase):
    def test_adds_missing_prefix(self):
        self.assertEqual(normalize_sale_number('A45DF22C'), 'POS-A45DF22C')

    def test_lowercase_and_whitespace(self):
        self.assertEqual(normalize_sale_number('  pos-a45df22c  '), 'POS-A45DF22C')

    def test_already_prefixed(self):
        self.assertEqual(normalize_sale_number('POS-A45DF22C'), 'POS-A45DF22C')

    def test_blank(self):
        self.assertEqual(normalize_sale_number(''), '')
        self.assertEqual(normalize_sale_number(None), '')


# ─── Lookup endpoint ─────────────────────────────────────────────

class RefundLookupApiTests(RefundTestBase):
    def test_lookup_requires_staff(self):
        self.client.logout()
        response = self.lookup('POS-XXXXXXXX')
        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_lookup_normalizes_without_prefix_lowercase_and_spaces(self):
        sale = self.complete_sale(
            cart=[{'product_id': self.jar.id, 'qty': 1}], payments=[{'method': 'cash', 'amount': '250.00'}],
        )
        bare = sale.sale_number[len('POS-'):]
        for candidate in (bare, bare.lower(), f'  {bare.lower()}  ', sale.sale_number, sale.sale_number.lower()):
            response = self.lookup(candidate)
            self.assertEqual(response.status_code, 200, candidate)
            self.assertEqual(response.data['sale_number'], sale.sale_number)

    def test_sale_not_found_returns_404(self):
        response = self.lookup('POS-NOSUCHSAL')
        self.assertEqual(response.status_code, 404)

    def test_lookup_shows_lines_payments_and_fully_refunded_flag(self):
        sale = self.complete_sale(
            cart=[{'product_id': self.jar.id, 'qty': 2}], payments=[{'method': 'cash', 'amount': '500.00'}],
        )
        response = self.lookup(sale.sale_number)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data['fully_refunded'])
        self.assertEqual(len(response.data['lines']), 1)
        line = response.data['lines'][0]
        self.assertEqual(line['remaining_quantity'], '2.000')
        self.assertEqual(Decimal(line['allocated_amount']), Decimal('500'))
        self.assertEqual(response.data['payments'], [{'method': 'cash', 'amount': '500.00'}])


class RefundLookupRefundableAmountTests(RefundTestBase):
    """Regression coverage for a real bug: templates/pos_refund.html's
    activeSelections() used Math.round(parseFloat(line.remaining_quantity))
    to decide how much of a whole-line (variable_weight/fixed_weight/combo
    component) to refund. For a weighed line under 0.5kg (e.g. the
    0.250kg/Rs 20 case that was actually reported), that rounds the
    quantity to 0, silently dropping a ticked line out of the refund
    total -- and for a weighed line AT OR ABOVE 0.5kg, it sent a quantity
    that no longer matched sale_line.quantity at all, which the server
    correctly rejects as "not a full-line refund". The server-side
    allocation itself (get_pos_sale_refund_info()/create_pos_refund(),
    shop/views.py) was never the problem -- these tests pin that down by
    checking the LOOKUP response's own numbers directly, independent of
    the front end, and then exercising a real weighed-line refund
    end-to-end through the API."""

    def test_lookup_gives_positive_refundable_amount_for_a_weighed_line(self):
        # The exact reported scenario: 0.250kg at a price that lands on a
        # whole rupee (80/kg x 0.25kg = 20).
        cauliflower = Product.objects.create(
            name='Cauliflower', slug='refund-bug-cauliflower', category=self.category,
            description='t', price=Decimal('80.00'), pricing_mode='variable_weight',
        )
        InventoryMovement.objects.create(
            product=cauliflower, movement_type='harvest', source='admin', quantity=Decimal('10.000'),
        )
        sale = self.complete_sale(
            cart=[{'product_id': cauliflower.id, 'qty': 1, 'weight': '0.250'}],
            payments=[{'method': 'cash', 'amount': '20.00'}],
        )
        response = self.lookup(sale.sale_number)
        self.assertEqual(response.status_code, 200)
        line = response.data['lines'][0]
        self.assertEqual(line['remaining_quantity'], '0.250')
        self.assertFalse(line['is_partial'])
        self.assertGreater(Decimal(line['allocated_amount']), Decimal('0'))
        self.assertEqual(Decimal(line['allocated_amount']), Decimal('20'))
        self.assertEqual(Decimal(line['remaining_amount']), Decimal('20'))

    def test_lookup_gives_positive_refundable_amount_for_a_unit_line(self):
        sale = self.complete_sale(
            cart=[{'product_id': self.jar.id, 'qty': 3}], payments=[{'method': 'cash', 'amount': '750.00'}],
        )
        response = self.lookup(sale.sale_number)
        line = response.data['lines'][0]
        self.assertTrue(line['is_partial'])
        self.assertGreater(Decimal(line['remaining_amount']), Decimal('0'))
        self.assertEqual(Decimal(line['allocated_amount']), Decimal('750'))

    def test_lookup_gives_positive_refundable_amount_for_an_animal_line(self):
        sale = self.complete_sale(
            cart=[{'product_id': self.goat.id, 'qty': 1, 'variant_id': self.goat_variant.id}],
            payments=[{'method': 'cash', 'amount': str(self.goat_variant.total_price())}],
        )
        response = self.lookup(sale.sale_number)
        line = response.data['lines'][0]
        self.assertEqual(line['remaining_quantity'], '1.000')
        self.assertFalse(line['is_partial'])
        self.assertGreater(Decimal(line['remaining_amount']), Decimal('0'))
        self.assertEqual(Decimal(line['remaining_amount']), self.goat_variant.total_price())

    def test_refunding_a_sub_half_kg_weighed_line_succeeds_with_the_right_total(self):
        """End-to-end: the server itself was always fine for this case
        (confirmed above), but this pins down that a full-line refund of
        a weighed line under 0.5kg actually completes and charges the
        exact right amount, through the same endpoint the front end
        calls -- the kind of test that would have caught the bug if the
        server had ever sent a rounded quantity like the buggy front end
        did."""
        cauliflower = Product.objects.create(
            name='Cauliflower 2', slug='refund-bug-cauliflower-2', category=self.category,
            description='t', price=Decimal('80.00'), pricing_mode='variable_weight',
        )
        InventoryMovement.objects.create(
            product=cauliflower, movement_type='harvest', source='admin', quantity=Decimal('10.000'),
        )
        sale = self.complete_sale(
            cart=[{'product_id': cauliflower.id, 'qty': 1, 'weight': '0.250'}],
            payments=[{'method': 'cash', 'amount': '20.00'}],
        )
        line = sale.lines.get()
        response = self.post_refund(sale.sale_number, [{'sale_line_id': line.id, 'quantity': '0.250'}])
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(Decimal(response.data['total_amount']), Decimal('20.00'))
        self.assertEqual(InventoryMovement.current_stock(cauliflower), Decimal('10.000'))  # 10 - 0.25 + 0.25


# ─── Create refund: partial / whole-line rules ─────────────────

class RefundCreateApiTests(RefundTestBase):
    def test_create_requires_staff(self):
        self.client.logout()
        sale = self.complete_sale(
            cart=[{'product_id': self.jar.id, 'qty': 1}], payments=[{'method': 'cash', 'amount': '250.00'}],
        )
        response = self.post_refund(sale.sale_number, [{'sale_line_id': sale.lines.get().id, 'quantity': '1'}])
        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_partial_unit_refund_of_fixed_quantity_line(self):
        sale = self.complete_sale(
            cart=[{'product_id': self.jar.id, 'qty': 5}], payments=[{'method': 'cash', 'amount': '1250.00'}],
        )
        line = sale.lines.get()
        response = self.post_refund(sale.sale_number, [{'sale_line_id': line.id, 'quantity': '2', 'disposition': 'restock'}])
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(Decimal(response.data['total_amount']), Decimal('500.00'))
        self.assertEqual(InventoryMovement.current_stock(self.jar), Decimal('7'))  # 10 - 5 + 2

        # Refund 2 more, then the last 1 -- the three amounts must sum to
        # the line's full allocated share (invariant b).
        r2 = self.post_refund(sale.sale_number, [{'sale_line_id': line.id, 'quantity': '2'}])
        self.assertEqual(r2.status_code, 201, r2.data)
        r3 = self.post_refund(sale.sale_number, [{'sale_line_id': line.id, 'quantity': '1'}])
        self.assertEqual(r3.status_code, 201, r3.data)
        total_refunded = sum((Decimal(r.data['total_amount']) for r in (response, r2, r3)), Decimal('0'))
        self.assertEqual(total_refunded, Decimal('1250.00'))

    def test_variable_weight_line_is_whole_line_only(self):
        sale = self.complete_sale(
            cart=[{'product_id': self.fruit.id, 'qty': 1, 'weight': '2.00'}],
            payments=[{'method': 'cash', 'amount': '600.00'}],
        )
        line = sale.lines.get()
        # A partial weight (1.00 of 2.00kg) must be rejected.
        response = self.post_refund(sale.sale_number, [{'sale_line_id': line.id, 'quantity': '1.00'}])
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Refund.objects.exists())

        # The full weight succeeds.
        response = self.post_refund(sale.sale_number, [{'sale_line_id': line.id, 'quantity': '2.00'}])
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(Decimal(response.data['total_amount']), Decimal('600.00'))

    def test_fixed_weight_animal_whole_line_only_and_restores_variant(self):
        sale = self.complete_sale(
            cart=[{'product_id': self.goat.id, 'qty': 1, 'variant_id': self.goat_variant.id}],
            payments=[{'method': 'cash', 'amount': str(self.goat_variant.total_price())}],
        )
        self.goat_variant.refresh_from_db()
        self.assertFalse(self.goat_variant.is_available)  # sold

        line = sale.lines.get()
        response = self.post_refund(sale.sale_number, [{'sale_line_id': line.id, 'quantity': '1', 'disposition': 'restock'}])
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(InventoryMovement.current_stock(self.goat, variant=self.goat_variant), Decimal('1'))
        self.goat_variant.refresh_from_db()
        self.assertTrue(self.goat_variant.is_available)  # back in stock, via the existing signal

    def test_discard_not_offered_for_animal(self):
        sale = self.complete_sale(
            cart=[{'product_id': self.goat.id, 'qty': 1, 'variant_id': self.goat_variant.id}],
            payments=[{'method': 'cash', 'amount': str(self.goat_variant.total_price())}],
        )
        line = sale.lines.get()
        response = self.post_refund(sale.sale_number, [{'sale_line_id': line.id, 'quantity': '1', 'disposition': 'discard'}])
        self.assertEqual(response.status_code, 400)
        self.assertIn('animal', response.data['message'].lower())
        self.assertFalse(Refund.objects.exists())

    def test_over_refund_blocked(self):
        sale = self.complete_sale(
            cart=[{'product_id': self.jar.id, 'qty': 3}], payments=[{'method': 'cash', 'amount': '750.00'}],
        )
        line = sale.lines.get()
        response = self.post_refund(sale.sale_number, [{'sale_line_id': line.id, 'quantity': '4'}])
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Refund.objects.exists())

    def test_double_refund_of_whole_line_blocked(self):
        sale = self.complete_sale(
            cart=[{'product_id': self.fruit.id, 'qty': 1, 'weight': '2.00'}],
            payments=[{'method': 'cash', 'amount': '600.00'}],
        )
        line = sale.lines.get()
        first = self.post_refund(sale.sale_number, [{'sale_line_id': line.id, 'quantity': '2.00'}])
        self.assertEqual(first.status_code, 201, first.data)
        second = self.post_refund(sale.sale_number, [{'sale_line_id': line.id, 'quantity': '2.00'}])
        self.assertEqual(second.status_code, 400)
        self.assertIn('already refunded', second.data['message'].lower())
        self.assertEqual(Refund.objects.count(), 1)

    def test_concurrent_looking_replay_blocked(self):
        """Two sequential (not idempotent-replayed) refund attempts each
        asking for more than what's left once the first has already been
        recorded -- the second's inside-transaction re-check must see the
        first's already-committed RefundLine and reject it, same as a real
        race would be caught at the DB level."""
        sale = self.complete_sale(
            cart=[{'product_id': self.jar.id, 'qty': 5}], payments=[{'method': 'cash', 'amount': '1250.00'}],
        )
        line = sale.lines.get()
        first = self.post_refund(sale.sale_number, [{'sale_line_id': line.id, 'quantity': '3'}])
        self.assertEqual(first.status_code, 201, first.data)
        second = self.post_refund(sale.sale_number, [{'sale_line_id': line.id, 'quantity': '3'}])
        self.assertEqual(second.status_code, 400)
        self.assertIn('only 2', second.data['message'])
        self.assertEqual(InventoryMovement.current_stock(self.jar), Decimal('8'))  # 10 - 5 + 3, not + 6

    def test_idempotent_replay_via_client_refund_id(self):
        sale = self.complete_sale(
            cart=[{'product_id': self.jar.id, 'qty': 2}], payments=[{'method': 'cash', 'amount': '500.00'}],
        )
        line = sale.lines.get()
        client_refund_id = str(uuid.uuid4())
        selections = [{'sale_line_id': line.id, 'quantity': '2'}]
        first = self.post_refund(sale.sale_number, selections, client_refund_id=client_refund_id)
        second = self.post_refund(sale.sale_number, selections, client_refund_id=client_refund_id)
        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.data['refund_number'], second.data['refund_number'])
        self.assertEqual(Refund.objects.count(), 1)
        self.assertEqual(InventoryMovement.current_stock(self.jar), Decimal('10'))  # restocked exactly once

    def test_sale_number_is_normalized_on_create_too(self):
        sale = self.complete_sale(
            cart=[{'product_id': self.jar.id, 'qty': 1}], payments=[{'method': 'cash', 'amount': '250.00'}],
        )
        line = sale.lines.get()
        bare = sale.sale_number[len('POS-'):].lower()
        response = self.post_refund(bare, [{'sale_line_id': line.id, 'quantity': '1'}])
        self.assertEqual(response.status_code, 201, response.data)

    def test_reason_other_requires_note(self):
        sale = self.complete_sale(
            cart=[{'product_id': self.jar.id, 'qty': 1}], payments=[{'method': 'cash', 'amount': '250.00'}],
        )
        line = sale.lines.get()
        response = self.post_refund(sale.sale_number, [{'sale_line_id': line.id, 'quantity': '1'}], reason='other', note='')
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Refund.objects.exists())

        response = self.post_refund(sale.sale_number, [{'sale_line_id': line.id, 'quantity': '1'}], reason='other', note='Smelled off')
        self.assertEqual(response.status_code, 201, response.data)


# ─── Stock disposition ───────────────────────────────────────────

class RefundStockDispositionApiTests(RefundTestBase):
    def test_back_to_stock_creates_return_movement(self):
        sale = self.complete_sale(
            cart=[{'product_id': self.jar.id, 'qty': 1}], payments=[{'method': 'cash', 'amount': '250.00'}],
        )
        line = sale.lines.get()
        stock_before = InventoryMovement.current_stock(self.jar)
        response = self.post_refund(sale.sale_number, [{'sale_line_id': line.id, 'quantity': '1', 'disposition': 'restock'}])
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(InventoryMovement.current_stock(self.jar), stock_before + 1)
        movements = InventoryMovement.objects.filter(related_pos_sale=sale, movement_type='return')
        self.assertEqual(movements.count(), 1)
        self.assertEqual(movements.first().quantity, Decimal('1'))

    def test_discard_creates_return_and_waste_movements_net_zero(self):
        sale = self.complete_sale(
            cart=[{'product_id': self.jar.id, 'qty': 1}], payments=[{'method': 'cash', 'amount': '250.00'}],
        )
        line = sale.lines.get()
        stock_before = InventoryMovement.current_stock(self.jar)
        response = self.post_refund(sale.sale_number, [{'sale_line_id': line.id, 'quantity': '1', 'disposition': 'discard'}])
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(InventoryMovement.current_stock(self.jar), stock_before)  # net zero

        movements = list(InventoryMovement.objects.filter(related_pos_sale=sale).order_by('id'))
        types = [m.movement_type for m in movements if m.movement_type in ('return', 'waste')]
        # 'return' always precedes 'waste' -- guarantees waste never has to
        # remove more than was just put back, so stock never dips negative
        # even momentarily.
        self.assertEqual(types[-2:], ['return', 'waste'])


# ─── Credit method ────────────────────────────────────────────────

class RefundCreditMethodApiTests(RefundTestBase):
    def test_credit_method_requires_customer(self):
        sale = self.complete_sale(
            cart=[{'product_id': self.jar.id, 'qty': 1}], payments=[{'method': 'cash', 'amount': '250.00'}],
        )
        line = sale.lines.get()
        response = self.post_refund(sale.sale_number, [{'sale_line_id': line.id, 'quantity': '1'}], method='credit')
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Refund.objects.exists())

    def test_credit_method_reduces_outstanding_balance(self):
        customer = Customer.objects.create(name='Ram Bahadur', phone='9800000001', address='Bhulka Danda')
        sale = self.complete_sale(
            cart=[{'product_id': self.jar.id, 'qty': 2}],
            payments=[{'method': 'credit', 'amount': '500.00'}], customer=customer,
        )
        self.assertEqual(customer.outstanding_balance(), Decimal('500.00'))

        line = sale.lines.get()
        response = self.post_refund(sale.sale_number, [{'sale_line_id': line.id, 'quantity': '2'}], method='credit')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(customer.outstanding_balance(), Decimal('0.00'))

        from shop.models import CreditTransaction, Refund
        txn = CreditTransaction.objects.filter(customer=customer, transaction_type='repayment').get()
        self.assertEqual(txn.amount, Decimal('500.00'))
        self.assertIsNone(txn.related_pos_sale)  # a "standalone" repayment, same convention as CreditRepayView
        # Set so shop/pl_report.py can exclude this row from cash_collected --
        # no real money moved, it only reduced what the customer owes.
        self.assertEqual(txn.related_refund, Refund.objects.get(sale=sale))


# ─── Discount/round-off allocation invariants ───────────────────

class RefundAllocationInvariantTests(RefundTestBase):
    def test_allocation_sums_exactly_to_sale_total_with_coupon_discount(self):
        now = timezone.now()
        Coupon.objects.create(
            code='FLAT50', discount_type='fixed', discount_value=Decimal('50'),
            start_date=now - timedelta(days=1), end_date=now + timedelta(days=1),
        )
        # jar 2x250=500 + fruit 2kg*300=600 -> gross 1100, FLAT50 -> 1050.
        sale = self.complete_sale(
            cart=[
                {'product_id': self.jar.id, 'qty': 2},
                {'product_id': self.fruit.id, 'qty': 1, 'weight': '2.00'},
            ],
            payments=[{'method': 'cash', 'amount': '1050.00'}], coupon_code='FLAT50',
        )
        self.assertEqual(sale.total_amount, Decimal('1050.00'))

        allocations = allocate_sale_total_to_lines(sale)
        self.assertEqual(sum(allocations.values(), Decimal('0')), sale.total_amount)
        for amount in allocations.values():
            self.assertEqual(amount, amount.to_integral_value())  # invariant (c): whole rupee

        jar_line, fruit_line = sale.lines.order_by('id')
        r1 = self.post_refund(sale.sale_number, [{'sale_line_id': jar_line.id, 'quantity': '2'}])
        r2 = self.post_refund(sale.sale_number, [{'sale_line_id': fruit_line.id, 'quantity': '2.00'}])
        self.assertEqual(r1.status_code, 201, r1.data)
        self.assertEqual(r2.status_code, 201, r2.data)
        total_refunded = Decimal(r1.data['total_amount']) + Decimal(r2.data['total_amount'])
        self.assertEqual(total_refunded, sale.total_amount)  # invariant (b)

    def test_refunds_on_a_sale_never_exceed_its_total(self):
        sale = self.complete_sale(
            cart=[{'product_id': self.jar.id, 'qty': 3}], payments=[{'method': 'cash', 'amount': '750.00'}],
        )
        line = sale.lines.get()
        self.post_refund(sale.sale_number, [{'sale_line_id': line.id, 'quantity': '3'}])
        # Nothing left -- any further attempt must be rejected, not partially honored.
        blocked = self.post_refund(sale.sale_number, [{'sale_line_id': line.id, 'quantity': '1'}])
        self.assertEqual(blocked.status_code, 400)
        total_refunded = Refund.objects.aggregate(t=Sum('total_amount'))['t']
        self.assertLessEqual(total_refunded, sale.total_amount)


# ─── Combo / bundle lines ────────────────────────────────────────

class RefundComboApiTests(RefundTestBase):
    def setUp(self):
        super().setUp()
        now = timezone.now()
        self.combo_offer = Offer.objects.create(
            title='Jar + Mango Combo', discount_type='combo', discount_value=Decimal('0'),
            combo_price=Decimal('500'),
            start_date=now - timedelta(days=1), end_date=now + timedelta(days=1),
        )
        BundleItem.objects.create(offer=self.combo_offer, product=self.jar, quantity=Decimal('1'))
        BundleItem.objects.create(offer=self.combo_offer, product=self.fruit, quantity=Decimal('1'))

    def make_combo_sale(self):
        combo_instance_id = str(uuid.uuid4())
        cart = [
            {'product_id': self.jar.id, 'qty': 1, 'offer_id': self.combo_offer.id, 'combo_instance_id': combo_instance_id},
            {'product_id': self.fruit.id, 'qty': 1, 'weight': '1.00', 'offer_id': self.combo_offer.id, 'combo_instance_id': combo_instance_id},
        ]
        return self.complete_sale(cart=cart, payments=[{'method': 'cash', 'amount': '500.00'}])

    def test_combo_component_line_is_whole_line_only(self):
        sale = self.make_combo_sale()
        jar_line, fruit_line = sale.lines.order_by('id')
        # fixed_quantity is normally partial-eligible, but NOT as part of a combo.
        response = self.post_refund(sale.sale_number, [{'sale_line_id': jar_line.id, 'quantity': '0.5'}])
        self.assertEqual(response.status_code, 400)
        self.assertIn('full', response.data['message'].lower())

    def test_refunding_whole_combo_refunds_both_components_summing_to_combo_price(self):
        sale = self.make_combo_sale()
        jar_line, fruit_line = sale.lines.order_by('id')
        response = self.post_refund(sale.sale_number, [
            {'sale_line_id': jar_line.id, 'quantity': '1'},
            {'sale_line_id': fruit_line.id, 'quantity': '1.00'},
        ])
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(Decimal(response.data['total_amount']), Decimal('500.00'))
        self.assertEqual(RefundLine.objects.filter(refund__sale=sale).count(), 2)

    def test_refunding_a_single_combo_component_alone_is_allowed(self):
        """Documented judgment call: 'a single checkbox' (the task's own
        spec wording) is read here as EACH combo component line getting
        its own independent whole-line checkbox, not the whole bundle
        being refundable only as one atomic group -- there is no cross-
        line linkage enforced. See create_pos_refund()'s docstring."""
        sale = self.make_combo_sale()
        jar_line, _fruit_line = sale.lines.order_by('id')
        response = self.post_refund(sale.sale_number, [{'sale_line_id': jar_line.id, 'quantity': '1'}])
        self.assertEqual(response.status_code, 201, response.data)


# ─── Telegram best-effort ─────────────────────────────────────────

class RefundTelegramApiTests(RefundTestBase):
    @patch('shop.emails.requests.post')
    def test_telegram_failure_does_not_block_refund(self, mock_post):
        mock_post.side_effect = Exception('Telegram is down')
        sale = self.complete_sale(
            cart=[{'product_id': self.jar.id, 'qty': 1}], payments=[{'method': 'cash', 'amount': '250.00'}],
        )
        line = sale.lines.get()
        response = self.post_refund(sale.sale_number, [{'sale_line_id': line.id, 'quantity': '1'}])
        self.assertEqual(response.status_code, 201, response.data)
        self.assertTrue(Refund.objects.filter(sale=sale).exists())
        mock_post.assert_called()  # it really did try


# ─── Refund slip page ─────────────────────────────────────────────

class RefundSlipViewTests(RefundTestBase):
    def test_refund_slip_is_staff_only_and_shows_right_figures(self):
        sale = self.complete_sale(
            cart=[{'product_id': self.jar.id, 'qty': 2}], payments=[{'method': 'cash', 'amount': '500.00'}],
        )
        line = sale.lines.get()
        create_response = self.post_refund(sale.sale_number, [{'sale_line_id': line.id, 'quantity': '2'}])
        refund_number = create_response.data['refund_number']

        self.client.logout()
        anon = self.client.get(reverse('pos_refund_slip', args=[refund_number]))
        self.assertNotEqual(anon.status_code, 200)

        self.client.login(username='cashier', password='pw')
        response = self.client.get(reverse('pos_refund_slip', args=[refund_number]))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn(refund_number, content)
        self.assertIn(sale.sale_number[len('POS-'):], content)
        self.assertIn('500', content)
        self.assertIn('REFUND', content)

    def test_refund_page_is_staff_only(self):
        self.client.logout()
        response = self.client.get(reverse('pos_refund'))
        self.assertNotEqual(response.status_code, 200)

        self.client.login(username='cashier', password='pw')
        response = self.client.get(reverse('pos_refund'))
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Refund', response.content)

    def test_refund_page_header_matches_pos_page_header(self):
        """Refund-page-polish task: the page header reuses pos.html's own
        brand markup (logo, Devanagari shop name, English name, flag) and
        RETAIL tag, rather than looking like a different app."""
        response = self.client.get(reverse('pos_refund'))
        content = response.content.decode()
        self.assertIn('class="pos-header"', content)
        self.assertIn('class="pos-brand"', content)
        self.assertIn('आँगन बारी', content)
        self.assertIn('Angan Baari', content)
        self.assertIn('RETAIL', content)
        self.assertIn('id="posDate"', content)
        self.assertIn('id="posTime"', content)

    def test_recent_refunds_history_has_capped_scroll_height(self):
        """Fixed-shell history-scroll task: #recentRefundsCard and
        #priorRefundsList are both capped at max-height:150px with their
        own overflow-y:auto, so an open 20-row history can never squeeze
        the item list / right-hand panel out of their own share of the
        available height. The title inside each is position:sticky so it
        stays pinned at the top of that same scrollable box rather than
        living outside it (capping only an inner list while leaving the
        outer card's own height unbounded was exactly the bug this
        replaced)."""
        response = self.client.get(reverse('pos_refund'))
        content = response.content.decode()
        self.assertIn('#recentRefundsCard, #priorRefundsList {', content)
        self.assertIn('flex: none; max-height: 150px; overflow-y: auto;', content)
        self.assertIn('#recentRefundsCard h3, #priorRefundsList strong {', content)
        self.assertIn('position: sticky;', content)

    def test_wrap_uses_explicit_calc_height_at_768px_and_up(self):
        """Fixed-shell history-scroll task: .wrap's height is an explicit
        calc(100dvh - var(--hdr-h) - var(--ftr-h)) from 768px up, with
        --hdr-h/--ftr-h kept in sync with the real header/footer heights
        by JS (syncShellHeights(), a ResizeObserver) -- not a flex-grow
        distribution that depends on every descendant's own flex-shrink
        being correctly overridden."""
        response = self.client.get(reverse('pos_refund'))
        content = response.content.decode()
        self.assertIn('--hdr-h: 0px;', content)
        self.assertIn('--ftr-h: 0px;', content)
        self.assertIn('height: calc(100dvh - var(--hdr-h) - var(--ftr-h));', content)
        self.assertIn('function syncShellHeights()', content)
        self.assertIn("new ResizeObserver(syncShellHeights)", content)

    def test_cancel_button_uses_the_same_red_as_pos_low_stock_indicator(self):
        """Refund-page-polish task: Cancel is filled, red/white, using the
        same red as the POS screen's own "Low Stock" indicator (#b23b3b
        -- --danger in both templates), not an ad-hoc colour, and keeps
        its outlined-green look nowhere else."""
        response = self.client.get(reverse('pos_refund'))
        content = response.content.decode()
        self.assertIn('--danger: #b23b3b;', content)
        cancel_rule_start = content.index('#cancelRefundBtn {')
        cancel_rule = content[cancel_rule_start:cancel_rule_start + 400]
        self.assertIn('background: var(--danger)', cancel_rule)
        self.assertIn('color: #fff', cancel_rule)

        # Cross-check against the POS screen's own colour for the same red.
        pos_response = self.client.get(reverse('pos'))
        self.assertIn('#b23b3b', pos_response.content.decode())

    def test_top_nav_buttons_are_styled_as_buttons(self):
        """Refund-page-polish task: "Back to POS" and "Recent refunds"
        look like real buttons (rounded, outlined, >=44px tall), not
        plain text links."""
        response = self.client.get(reverse('pos_refund'))
        content = response.content.decode()
        self.assertIn('class="link-row"', content)
        self.assertEqual(content.count('class="back-link"'), 2)
        self.assertIn('min-height: 44px', content)
        self.assertIn('border: 2px solid var(--pos-text-green)', content)

    def test_title_row_has_title_and_both_nav_buttons_together(self):
        """Fixed-shell-layout task: "Refund / फिर्ता" and the two nav
        buttons share one .title-row (title left, buttons right of the
        same row from 481px up; CSS handles the <=480px wrap to a
        second row -- see the .title-row/.link-row rules)."""
        response = self.client.get(reverse('pos_refund'))
        content = response.content.decode()
        title_row_start = content.index('class="title-row"')
        title_row_chunk = content[title_row_start:title_row_start + 600]
        self.assertIn('Refund / फिर्ता', title_row_chunk)
        self.assertIn('class="link-row"', title_row_chunk)
        self.assertIn('id="recentRefundsToggle"', title_row_chunk)

    def test_method_grid_has_the_four_payment_buttons_in_one_container(self):
        """Fixed-shell-layout task: cash/eSewa/Khalti/Bank live in one
        #methodGrid container, one row of equal-width buttons. "Bank
        Transfer" is shortened to "Bank" on screen only -- the
        data-method value sent to the server stays bank_transfer."""
        response = self.client.get(reverse('pos_refund'))
        content = response.content.decode()
        grid_start = content.index('id="methodGrid"')
        grid_end = content.index('</div>', grid_start)
        grid_chunk = content[grid_start:grid_end]
        self.assertIn('data-method="cash"', grid_chunk)
        self.assertIn('data-method="esewa"', grid_chunk)
        self.assertIn('data-method="khalti"', grid_chunk)
        self.assertIn('data-method="bank_transfer"', grid_chunk)
        self.assertIn('>Bank<', grid_chunk)
        self.assertNotIn('>Bank Transfer<', grid_chunk)

    def test_note_container_is_hidden_by_default_with_an_add_note_toggle(self):
        """Fixed-shell-layout task: the note textarea is wrapped in
        #noteContainer, hidden by default (shown automatically only when
        "Other" is picked, or on demand via #addNoteToggle -- both
        client-side, see pos_refund.html's updateNoteVisibility())."""
        response = self.client.get(reverse('pos_refund'))
        content = response.content.decode()
        # Hidden via the stylesheet (#noteContainer { display: none; }),
        # not an inline style -- JS only ever sets an explicit 'block' or
        # 'none' afterwards (see updateNoteVisibility()'s own comment
        # about why an inline '' wouldn't actually show it again).
        self.assertIn('#noteContainer { display: none; }', content)
        self.assertIn('id="addNoteToggle"', content)
        self.assertIn('+ Add note', content)
        note_container_start = content.index('id="noteContainer"')
        note_container_chunk = content[note_container_start:note_container_start + 200]
        self.assertIn('id="noteInput"', note_container_chunk)

    def test_refund_page_success_box_has_back_to_pos_link(self):
        """Refund-page-polish task: the "Refund complete" success box has
        a Back to POS button/link, in addition to Print refund slip and
        New refund, so staff aren't stuck without a way back to the
        till screen."""
        response = self.client.get(reverse('pos_refund'))
        content = response.content.decode()
        self.assertIn('id="backToPosBtn"', content)
        self.assertIn(reverse('pos'), content)
        # Order: Print refund slip, then Back to POS, then New refund.
        print_pos = content.index('id="printSlipBtn"')
        back_pos = content.index('id="backToPosBtn"')
        new_pos = content.index('id="newRefundBtn"')
        self.assertLess(print_pos, back_pos)
        self.assertLess(back_pos, new_pos)

    def test_refund_page_has_cancel_button_and_confirm_dialog(self):
        """Refund-page-polish task: a Cancel button exists in the footer,
        plus its own confirmation dialog markup (separate from the
        refund-confirmation dialog)."""
        response = self.client.get(reverse('pos_refund'))
        content = response.content.decode()
        self.assertIn('id="cancelRefundBtn"', content)
        self.assertIn('id="cancelConfirmBackdrop"', content)
        self.assertIn('id="keepEditingBtn"', content)
        self.assertIn('id="cancelRefundYesBtn"', content)

    def test_refund_slip_has_no_barcode_element(self):
        """Refund-slip polish task: no barcode, and no JsBarcode script
        at all -- unlike the sale receipt, which has both."""
        sale = self.complete_sale(
            cart=[{'product_id': self.jar.id, 'qty': 2}], payments=[{'method': 'cash', 'amount': '500.00'}],
        )
        line = sale.lines.get()
        create_response = self.post_refund(sale.sale_number, [{'sale_line_id': line.id, 'quantity': '2'}])
        response = self.client.get(reverse('pos_refund_slip', args=[create_response.data['refund_number']]))
        content = response.content.decode()
        self.assertNotIn('<svg', content)
        self.assertNotIn('JsBarcode', content)
        self.assertNotIn('jsbarcode', content)

    def test_refund_slip_shows_weighed_line_with_kg_unit(self):
        """Refund-slip polish task: a weighed line shows "<weight> kg",
        not "Qty: <weight>" -- the same way the sale receipt shows a
        weighed line's own detail with its unit."""
        fruit = Product.objects.create(
            name='Refund Slip Mango', slug='refund-slip-mango', category=self.category,
            description='t', price=Decimal('80.00'), pricing_mode='variable_weight',
        )
        InventoryMovement.objects.create(product=fruit, movement_type='harvest', source='admin', quantity=Decimal('10.000'))
        sale = self.complete_sale(
            cart=[{'product_id': fruit.id, 'qty': 1, 'weight': '0.250'}],
            payments=[{'method': 'cash', 'amount': '20.00'}],
        )
        line = sale.lines.get()
        create_response = self.post_refund(sale.sale_number, [{'sale_line_id': line.id, 'quantity': '0.250'}])
        self.assertEqual(create_response.status_code, 201, create_response.data)
        response = self.client.get(reverse('pos_refund_slip', args=[create_response.data['refund_number']]))
        content = response.content.decode()
        self.assertIn('0.25 kg', content)
        self.assertNotIn('Qty: 0.25', content)

    def test_refund_slip_shows_unit_line_with_qty_prefix(self):
        """Unit (fixed_quantity) lines keep the "Qty: N" style -- only
        weighed lines switch to "<weight> <unit>"."""
        sale = self.complete_sale(
            cart=[{'product_id': self.jar.id, 'qty': 2}], payments=[{'method': 'cash', 'amount': '500.00'}],
        )
        line = sale.lines.get()
        create_response = self.post_refund(sale.sale_number, [{'sale_line_id': line.id, 'quantity': '2'}])
        response = self.client.get(reverse('pos_refund_slip', args=[create_response.data['refund_number']]))
        content = response.content.decode()
        self.assertIn('Qty: 2', content)

    def test_refund_slip_shows_flag_and_qr_images_when_set_skips_blank_ones(self):
        settings_row = BusinessSettings.get_solo()
        settings_row.receipt_flag = 'business/flag-test.png'
        settings_row.qr_website = 'business/website-qr-test.png'
        settings_row.save()

        sale = self.complete_sale(
            cart=[{'product_id': self.jar.id, 'qty': 1}], payments=[{'method': 'cash', 'amount': '250.00'}],
        )
        line = sale.lines.get()
        create_response = self.post_refund(sale.sale_number, [{'sale_line_id': line.id, 'quantity': '1'}])
        response = self.client.get(reverse('pos_refund_slip', args=[create_response.data['refund_number']]))
        content = response.content.decode()

        self.assertIn('business/flag-test.png', content)
        self.assertIn('<div class="r-social-qr-row">', content)
        self.assertIn('business/website-qr-test.png', content)
        self.assertIn('>Website<', content)
        # Tiktok/facebook were never set -- skipped, not shown blank.
        self.assertNotIn('>Tiktok<', content)
        self.assertNotIn('>facebook<', content)

    def test_refund_slip_width_58_renders_with_all_qr_images_set(self):
        """?width=58 must still render cleanly even with all three QR
        images set (the narrowest width, the most QR images -- the
        combination most likely to overflow; see flex-wrap:wrap on
        .r-social-qr-row in both this page's and the receipt's own CSS)."""
        settings_row = BusinessSettings.get_solo()
        settings_row.qr_website = 'business/website-qr-test.png'
        settings_row.qr_tiktok = 'business/tiktok-qr-test.png'
        settings_row.qr_facebook = 'business/facebook-qr-test.png'
        settings_row.save()

        sale = self.complete_sale(
            cart=[{'product_id': self.jar.id, 'qty': 1}], payments=[{'method': 'cash', 'amount': '250.00'}],
        )
        line = sale.lines.get()
        create_response = self.post_refund(sale.sale_number, [{'sale_line_id': line.id, 'quantity': '1'}])
        response = self.client.get(
            reverse('pos_refund_slip', args=[create_response.data['refund_number']]), {'width': '58'},
        )
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn('width: 58mm', content)
        self.assertIn('@page { size: 58mm auto; margin: 0; }', content)
        self.assertIn('>Website<', content)
        self.assertIn('>Tiktok<', content)
        self.assertIn('>facebook<', content)

    def test_refund_slip_skips_flag_and_qr_row_entirely_when_all_blank(self):
        sale = self.complete_sale(
            cart=[{'product_id': self.jar.id, 'qty': 1}], payments=[{'method': 'cash', 'amount': '250.00'}],
        )
        line = sale.lines.get()
        create_response = self.post_refund(sale.sale_number, [{'sale_line_id': line.id, 'quantity': '1'}])
        response = self.client.get(reverse('pos_refund_slip', args=[create_response.data['refund_number']]))
        content = response.content.decode()
        self.assertNotIn('<img src', content)
        self.assertNotIn('<div class="r-social-qr-row">', content)


# ─── Admin read-only enforcement ──────────────────────────────────

class RefundAdminTests(TestCase):
    def test_refund_admin_is_fully_read_only(self):
        site = django_admin.AdminSite()
        admin_instance = RefundAdmin(Refund, site)
        self.assertFalse(admin_instance.has_add_permission(None))
        self.assertFalse(admin_instance.has_change_permission(None))
        self.assertFalse(admin_instance.has_delete_permission(None))


# ─── "Make refund slips reachable later" follow-up ─────────────────
# Previous-refunds list on the lookup card, the site-wide "Recent
# refunds" endpoint, the sale receipt's screen-only "View refund
# slip(s)" link, and the POSSaleAdmin "Refunded" column's slip link.

class RefundLookupPreviousRefundsTests(RefundTestBase):
    def test_lookup_refunds_list_is_empty_for_an_unrefunded_sale(self):
        sale = self.complete_sale(
            cart=[{'product_id': self.jar.id, 'qty': 1}], payments=[{'method': 'cash', 'amount': '250.00'}],
        )
        response = self.lookup(sale.sale_number)
        self.assertEqual(response.data['refunds'], [])

    def test_lookup_refunds_list_is_populated_after_a_refund(self):
        sale = self.complete_sale(
            cart=[{'product_id': self.jar.id, 'qty': 2}], payments=[{'method': 'cash', 'amount': '500.00'}],
        )
        line = sale.lines.get()
        create_response = self.post_refund(sale.sale_number, [{'sale_line_id': line.id, 'quantity': '1'}])
        self.assertEqual(create_response.status_code, 201, create_response.data)

        response = self.lookup(sale.sale_number)
        self.assertEqual(len(response.data['refunds']), 1)
        row = response.data['refunds'][0]
        self.assertEqual(row['refund_number'], create_response.data['refund_number'])
        self.assertIn('created_at', row)
        self.assertEqual(Decimal(row['amount']), Decimal('250.00'))
        self.assertEqual(row['method'], 'cash')


class RecentRefundsApiTests(RefundTestBase):
    def test_recent_refunds_requires_staff(self):
        self.client.logout()
        response = self.client.get(reverse('v1_pos_refund_recent'))
        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_recent_refunds_newest_first_capped_at_20(self):
        refunds = []
        for i in range(25):
            sale = self.complete_sale(
                cart=[{'product_id': self.jar.id, 'qty': 1}], payments=[{'method': 'cash', 'amount': '250.00'}],
            )
            line = sale.lines.get()
            create_response = self.post_refund(sale.sale_number, [{'sale_line_id': line.id, 'quantity': '1'}])
            self.assertEqual(create_response.status_code, 201, create_response.data)
            refund = Refund.objects.get(refund_number=create_response.data['refund_number'])
            when = timezone.now() + timedelta(minutes=i)  # strictly increasing -- #24 is newest
            Refund.objects.filter(id=refund.id).update(created_at=when)
            refunds.append(refund.refund_number)

        response = self.client.get(reverse('v1_pos_refund_recent'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 20)
        # Newest first: the LAST sale created (#24, highest backdated
        # timestamp) must be first in the response.
        returned_numbers = [r['refund_number'] for r in response.data]
        self.assertEqual(returned_numbers, list(reversed(refunds))[:20])
        for row in response.data:
            self.assertIn('sale_number', row)
            self.assertIn('created_at', row)
            self.assertIn('total_amount', row)
            self.assertIn('method', row)
            self.assertIn('created_by', row)


class RefundSlipLinkResolutionTests(RefundTestBase):
    def test_slip_link_resolves_200_for_staff_and_redirects_anonymous(self):
        sale = self.complete_sale(
            cart=[{'product_id': self.jar.id, 'qty': 1}], payments=[{'method': 'cash', 'amount': '250.00'}],
        )
        line = sale.lines.get()
        create_response = self.post_refund(sale.sale_number, [{'sale_line_id': line.id, 'quantity': '1'}])
        refund_number = create_response.data['refund_number']

        self.client.logout()
        anon = self.client.get(reverse('pos_refund_slip', args=[refund_number]))
        self.assertEqual(anon.status_code, 302)

        self.client.login(username='cashier', password='pw')
        staff_response = self.client.get(reverse('pos_refund_slip', args=[refund_number]))
        self.assertEqual(staff_response.status_code, 200)


class SaleReceiptRefundLinkTests(RefundTestBase):
    def test_no_refund_link_on_receipt_for_an_unrefunded_sale(self):
        sale = self.complete_sale(
            cart=[{'product_id': self.jar.id, 'qty': 1}], payments=[{'method': 'cash', 'amount': '250.00'}],
        )
        response = self.client.get(reverse('pos_receipt', args=[sale.sale_number]))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        # The CLASS NAME alone always appears (it's defined in the
        # <style> block regardless), so check for the actual rendered
        # element, not just the bare string -- same false-positive this
        # project has hit before matching a CSS rule instead of markup.
        self.assertNotIn('<div class="refund-slip-links-wrap">', content)
        self.assertNotIn('View refund slip', content)

    def test_refund_link_appears_on_receipt_after_a_refund_and_is_print_hidden(self):
        sale = self.complete_sale(
            cart=[{'product_id': self.jar.id, 'qty': 1}], payments=[{'method': 'cash', 'amount': '250.00'}],
        )
        line = sale.lines.get()
        create_response = self.post_refund(sale.sale_number, [{'sale_line_id': line.id, 'quantity': '1'}])
        refund_number = create_response.data['refund_number']

        response = self.client.get(reverse('pos_receipt', args=[sale.sale_number]))
        content = response.content.decode()
        self.assertIn('View refund slip', content)
        self.assertIn(reverse('pos_refund_slip', args=[refund_number]), content)
        # Same print-hiding mechanism as the Print button itself
        # (.print-btn-wrap) -- the link's own wrapper class must be
        # hidden under the SAME @media print block.
        self.assertIn('.refund-slip-links-wrap { display: none; }', content)
        print_block = content[content.index('@media print'):]
        self.assertIn('.print-btn-wrap { display: none; }', print_block)
        self.assertIn('.refund-slip-links-wrap { display: none; }', print_block)


class POSSaleAdminRefundedLinkTests(RefundTestBase):
    def test_refunded_column_links_to_slip_when_exactly_one_refund(self):
        from shop.admin import POSSaleAdmin

        sale = self.complete_sale(
            cart=[{'product_id': self.jar.id, 'qty': 1}], payments=[{'method': 'cash', 'amount': '250.00'}],
        )
        line = sale.lines.get()
        create_response = self.post_refund(sale.sale_number, [{'sale_line_id': line.id, 'quantity': '1'}])
        refund_number = create_response.data['refund_number']

        site = django_admin.AdminSite()
        admin_instance = POSSaleAdmin(sale.__class__, site)
        html = str(admin_instance.refunded_display(sale))
        self.assertIn(reverse('pos_refund_slip', args=[refund_number]), html)
        self.assertIn('<a ', html)

    def test_refunded_column_is_plain_text_when_no_refund(self):
        from shop.admin import POSSaleAdmin

        sale = self.complete_sale(
            cart=[{'product_id': self.jar.id, 'qty': 1}], payments=[{'method': 'cash', 'amount': '250.00'}],
        )
        site = django_admin.AdminSite()
        admin_instance = POSSaleAdmin(sale.__class__, site)
        self.assertEqual(admin_instance.refunded_display(sale), '—')

    def test_refunded_column_is_plain_text_when_more_than_one_refund(self):
        from shop.admin import POSSaleAdmin

        sale = self.complete_sale(
            cart=[{'product_id': self.jar.id, 'qty': 4}], payments=[{'method': 'cash', 'amount': '1000.00'}],
        )
        line = sale.lines.get()
        self.post_refund(sale.sale_number, [{'sale_line_id': line.id, 'quantity': '1'}])
        self.post_refund(sale.sale_number, [{'sale_line_id': line.id, 'quantity': '1'}])

        site = django_admin.AdminSite()
        admin_instance = POSSaleAdmin(sale.__class__, site)
        html = str(admin_instance.refunded_display(sale))
        self.assertNotIn('<a ', html)
        self.assertIn('500', html)  # 2 x 250 refunded
