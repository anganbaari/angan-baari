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
from shop.models import BundleItem, Coupon, Customer, InventoryMovement, Offer, Refund, RefundLine
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


# ─── Admin read-only enforcement ──────────────────────────────────

class RefundAdminTests(TestCase):
    def test_refund_admin_is_fully_read_only(self):
        site = django_admin.AdminSite()
        admin_instance = RefundAdmin(Refund, site)
        self.assertFalse(admin_instance.has_add_permission(None))
        self.assertFalse(admin_instance.has_change_permission(None))
        self.assertFalse(admin_instance.has_delete_permission(None))
