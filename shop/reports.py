"""Read-only aggregation for the staff Reports dashboard
(templates/dashboard.html, api/views.py's ReportXxxView classes,
GET /api/v1/reports/*).

Deliberately a separate module from shop/views.py: everything here only
ever READS POSSale/ProductOrder/CreditTransaction/InventoryMovement and
computes aggregates — nothing in here writes to the POS, checkout, or
ledger. See CLAUDE.md's new "Reports dashboard" section for the full
picture; the essentials, so this module's choices make sense on their own:

REVENUE IS POS-ONLY. "Sales (revenue)" and every Rs figure derived from
it is computed from POSSale.total_amount alone. ProductOrder (website
orders) has no total/price field anywhere, and its own cart_snapshot
deliberately omits price too ("a reorder should use CURRENT pricing" —
see that field's own help_text) — there is no stored figure for what an
online order was actually worth, and reconstructing one from today's
prices would be silently wrong for anything ordered before a price
change. Online orders are reported as COUNTS/status everywhere (channel
split, Overview alerts, the Online Orders tab) and never blended into a
Rs total. This was a deliberate decision confirmed with the project owner
before building this module — not an oversight.

PER-PRODUCT / PER-CATEGORY REVENUE IS AN ESTIMATE. Neither POSSale nor
ProductOrder stores a price per cart line either — only the sale-level
total is real. "Top products by revenue" and "sales by category" (revenue
mode) are computed by pricing each historical line at TODAY's price
(current Product.price / ProductVariant.total_price() / selling unit
price), which is wrong for anything sold before a price change, and
overstates anything that was actually offer/combo-discounted at sale time
(the discount isn't preserved either — only `offer_id`/`combo_instance_id`
are). These numbers are always returned with an `is_estimate: true` flag
and the dashboard labels them "(est.)" — the QUANTITY-based version of
the same breakdown is exact (qty/weight genuinely are stored) and is what
reconciliation tests check against, never the revenue estimate.

NO PROFIT/MARGIN ANYWHERE. Product has no cost/purchase-price field (confirmed
against every model in shop/models.py) — there is nothing to subtract
revenue from, so this module never computes or exposes one.

AGGREGATION STRATEGY. Sale-level numbers (totals, counts, trend buckets,
payment mix, credit, inventory) are computed with real Sum/Count/Trunc
aggregation in the database — no per-row Python loops. Per-LINE numbers
(units sold by product, offer/combo usage, category breakdown) cannot be
computed that way at all: cart_snapshot is a JSON blob, not a related
table, so there is no SQL join to aggregate over. Those are computed by
fetching `(id, cart_snapshot)` for every sale in range in ONE query, then
iterating the (small, farm-scale) in-memory list — not one query per row,
and bounded by how many sales a real farm POS does in a year, not by
total table size. See _iter_pos_cart_lines() below.

TIMEZONE. settings.TIME_ZONE is 'Asia/Kathmandu' (UTC+5:45) with
USE_TZ=True. Every day-boundary calculation here uses LOCAL_TZ explicitly
(not Django's ambient "current timezone", which depends on per-request
activation this project never does) so a sale at 23:30 or 00:30 local
time always lands on the correct LOCAL calendar day regardless of request
context — see local_range_to_utc().

CACHING. None. The brief allows a short (30-60s) cache for the heaviest
aggregates ONLY if it can't show a stale credit balance after a
repayment — correctly invalidating on every write would mean touching
create_pos_sale()/CreditRepayView, which this task keeps strictly
read-only. Every endpoint here always computes fresh.

ADDED IN THE 2026-10-07 REDESIGN (dark sidebar layout, B.S.-only dates,
RevenueTarget, filters, Alerts section — see CLAUDE.md's "Reports
dashboard" section and Angan-Baari-Knowledge/04-Features/
Reports-Dashboard.md for the full picture):
- SaleFilters (payment_type/customer_type/operator) thread through
  pos_sales_qs() and apply to Overview/Sales/Products only — see that
  dataclass's own comment for why Credit/Inventory/Online Orders don't.
- granularity='bs_month' buckets daily DB rows into Bikram Sambat months
  in Python (shop/bs_calendar.py) — see _bucket_daily_rows_into_bs_months().
- get_target_progress()/get_active_target() read the new RevenueTarget
  model; "achieved" is POS-only, same rule as everything else here.
- get_alerts_detailed() / get_no_sales_products() / get_run_rate() /
  get_insight_strip() back the new Alerts section, Overview's run-rate
  card, and Overview's bottom insight strip. Alert thresholds are the
  ALERT_* constants just below MAX_RANGE_DAYS.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time as dt_time, timedelta, timezone as dt_timezone
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo

from django.db.models import Count, DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce, TruncDate, TruncMonth, TruncWeek
from django.utils import timezone as djtz

from . import bs_calendar
from .models import (
    Category, Coupon, CreditTransaction, Customer, InventoryMovement,
    Offer, POSSale, POSSalePayment, Product, ProductOrder, ProductVariant, RevenueTarget,
)

LOCAL_TZ = ZoneInfo('Asia/Kathmandu')
MAX_RANGE_DAYS = 366
DEFAULT_RANGE_DAYS = 30
ZERO = Decimal('0')

# ── Alert thresholds (Alerts section + Overview preview + help panel) ──
# Plain constants, not a settings/admin-editable value -- small enough in
# number and low-stakes enough to change that a code edit + redeploy is
# simpler than building admin UI for them, per this task's own scope.
ALERT_NO_SALES_DAYS = 14          # product flagged once unsold this many days
ALERT_NO_SALES_LOOKBACK_DAYS = 60  # how far back "last sold" is searched for
ALERT_CREDIT_BALANCE_THRESHOLD = Decimal('5000.00')
ALERT_ORDER_PENDING_DAYS = 3
ALERT_TARGET_MILESTONES = (50, 75, 100)  # % of target, used for the one-time-per-milestone alert
RUN_RATE_MIN_DAYS_WITH_SALES = 7   # fewer days with a sale in the period and the projection is hidden


class ReportValidationError(Exception):
    """Raised for any request param that can't produce a sane report —
    translated to a 400 with a clear message by the views, same spirit as
    POSSaleValidationError elsewhere in this project."""

    def __init__(self, message):
        self.message = message
        super().__init__(message)


# ── Date range handling ──────────────────────────────────────────────

def _parse_date(value, field_name):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ReportValidationError(f"'{field_name}' must be a date in YYYY-MM-DD format.")


def local_today():
    """Today's date in Nepal local time -- the single shared definition
    every "today"-relative calculation in this module (date-range
    defaults, the Target card, alerts) uses, instead of each computing
    its own djtz.now().astimezone(LOCAL_TZ).date()."""
    return djtz.now().astimezone(LOCAL_TZ).date()


def resolve_date_range(start_str, end_str):
    """(start_date, end_date), both inclusive, as LOCAL (Nepal) calendar
    dates. Defaults to the last 30 days (today inclusive) when neither is
    given. Raises ReportValidationError for a missing half of the pair,
    start after end, or a range wider than MAX_RANGE_DAYS."""
    if not start_str and not end_str:
        end_date = local_today()
        start_date = end_date - timedelta(days=DEFAULT_RANGE_DAYS - 1)
        return start_date, end_date
    if not start_str or not end_str:
        raise ReportValidationError("Both 'start' and 'end' are required together.")
    start_date = _parse_date(start_str, 'start')
    end_date = _parse_date(end_str, 'end')
    if start_date > end_date:
        raise ReportValidationError("'start' must not be after 'end'.")
    if (end_date - start_date).days + 1 > MAX_RANGE_DAYS:
        raise ReportValidationError(f'Date range cannot exceed {MAX_RANGE_DAYS} days.')
    return start_date, end_date


def local_range_to_utc(start_date, end_date):
    """[start_date 00:00 local, end_date+1 00:00 local) as aware UTC
    datetimes. The half-open upper bound is what makes a sale at exactly
    local midnight unambiguous; building the boundary from LOCAL_TZ
    explicitly (not relying on whatever Django's "current timezone"
    happens to be) is what makes a 23:30-local or 00:30-local sale land
    on the correct LOCAL day regardless of request context."""
    start_dt = datetime.combine(start_date, dt_time.min, tzinfo=LOCAL_TZ)
    end_dt = datetime.combine(end_date + timedelta(days=1), dt_time.min, tzinfo=LOCAL_TZ)
    return start_dt.astimezone(dt_timezone.utc), end_dt.astimezone(dt_timezone.utc)


def previous_period(start_date, end_date):
    """The immediately-preceding period of the same length, for
    compare=true deltas."""
    length = (end_date - start_date).days + 1
    prev_end = start_date - timedelta(days=1)
    prev_start = prev_end - timedelta(days=length - 1)
    return prev_start, prev_end


def delta_info(current, previous):
    """{'current', 'previous', 'delta_pct', 'direction'} — 'direction' is
    text ('up'/'down'/'flat'/'new'), never a colour-only signal, and 'new'
    (not a divide-by-zero masquerading as "+inf%") when there was nothing
    in the previous period to compare against."""
    current = float(current or 0)
    previous = float(previous or 0)
    if previous == 0:
        if current == 0:
            return {'current': current, 'previous': previous, 'delta_pct': None, 'direction': 'flat'}
        return {'current': current, 'previous': previous, 'delta_pct': None, 'direction': 'new'}
    delta_pct = ((current - previous) / previous) * 100
    direction = 'up' if delta_pct > 0.005 else ('down' if delta_pct < -0.005 else 'flat')
    return {'current': current, 'previous': previous, 'delta_pct': round(delta_pct, 1), 'direction': direction}


def kpi_entry(current, previous, compare):
    """Wraps delta_info() but suppresses the comparison entirely when
    compare=False, rather than silently comparing against an implicit
    zero -- the dashboard's Compare toggle controls whether a delta is
    shown AT ALL, not just which previous-period value it's computed
    against. direction=None is the frontend's signal to render no delta
    line, distinct from 'new' (a real comparison with nothing to compare
    against)."""
    if not compare:
        return {'current': float(current or 0), 'previous': None, 'delta_pct': None, 'direction': None}
    return delta_info(current, previous)


def _trunc_fn(granularity):
    return {'day': TruncDate, 'week': TruncWeek, 'month': TruncMonth}.get(granularity, TruncDate)


def parse_channel(value):
    if value not in (None, '', 'all', 'pos', 'online'):
        raise ReportValidationError("'channel' must be one of: all, pos, online.")
    return value or 'all'


def parse_granularity(value):
    if value not in (None, '', 'day', 'week', 'month', 'bs_month'):
        raise ReportValidationError("'granularity' must be one of: day, week, month, bs_month.")
    return value or 'day'


def _bucket_daily_rows_into_bs_months(daily_rows):
    """[{'date': iso, <numeric fields...>}, ...] (one row per A.D. day) ->
    the same shape, one row per B.S. month (every numeric field summed),
    labelled by that month's FIRST day (A.D., ISO) so the frontend can
    BS-format it exactly like any other bucket date. This is the
    server-side half of "bucket by B.S. month" -- see shop/bs_calendar.py
    and the module docstring's note on why A.D. day-level DB aggregation
    plus this regroup step is used instead of a database-level B.S. Trunc
    (which can't exist -- B.S. month boundaries aren't a fixed offset
    from A.D. ones). Shape-agnostic so it works for sales trend
    (total/count), credit trend (given/repaid), and orders-over-time
    (count) alike."""
    from collections import OrderedDict
    buckets = OrderedDict()
    for row in daily_rows:
        d = date.fromisoformat(row['date'])
        bs_year, bs_month, _ = bs_calendar.ad_to_bs(d)
        key = (bs_year, bs_month)
        if key not in buckets:
            buckets[key] = {'date': bs_calendar.bs_month_start_ad(bs_year, bs_month).isoformat()}
            for field in row:
                if field != 'date':
                    buckets[key][field] = 0
        for field, value in row.items():
            if field != 'date':
                buckets[key][field] += value
    return list(buckets.values())


# ── Sale-level filters (payment type / customer type / operator) ──────
#
# Threaded through every POS-sale-based aggregation (Overview, Sales,
# Products) via pos_sales_qs() below. Deliberately NOT applied to the
# Credit, Inventory, or Online Orders tabs, or to the Target card's
# achieved figure -- those aren't meaningfully "by payment type" or "by
# operator" in the same sense (credit given/repaid already IS the credit
# slice; outstanding credit is an all-time snapshot; inventory is a
# ledger total; online orders have no operator/payment-method concept at
# all). Each endpoint that ignores these filters says so explicitly in
# its response (`filters_applicable: false`) so the frontend can show a
# "doesn't apply here" note instead of silently ignoring an active filter.

PAYMENT_TYPE_CHOICES = ('cash', 'credit', 'other')
CUSTOMER_TYPE_CHOICES = ('credit', 'walkin')
_OTHER_PAYMENT_METHODS = ('esewa', 'khalti', 'bank_transfer')


@dataclass(frozen=True)
class SaleFilters:
    payment_type: str = None
    customer_type: str = None
    operator_id: int = None

    def is_empty(self):
        return not (self.payment_type or self.customer_type or self.operator_id)


NO_FILTERS = SaleFilters()


def parse_sale_filters(params):
    payment_type = params.get('payment_type') or None
    if payment_type and payment_type not in PAYMENT_TYPE_CHOICES:
        raise ReportValidationError(f"'payment_type' must be one of: {', '.join(PAYMENT_TYPE_CHOICES)}.")
    customer_type = params.get('customer_type') or None
    if customer_type and customer_type not in CUSTOMER_TYPE_CHOICES:
        raise ReportValidationError(f"'customer_type' must be one of: {', '.join(CUSTOMER_TYPE_CHOICES)}.")
    operator_id = params.get('operator') or None
    if operator_id:
        try:
            operator_id = int(operator_id)
        except (TypeError, ValueError):
            raise ReportValidationError("'operator' must be a staff id.")
    return SaleFilters(payment_type=payment_type, customer_type=customer_type, operator_id=operator_id)


# ── Base querysets ───────────────────────────────────────────────────

def pos_sales_qs(start_date, end_date, filters=NO_FILTERS):
    start_utc, end_utc = local_range_to_utc(start_date, end_date)
    qs = POSSale.objects.filter(created_at__gte=start_utc, created_at__lt=end_utc)
    if filters.operator_id:
        qs = qs.filter(cashier_id=filters.operator_id)
    if filters.customer_type == 'credit':
        qs = qs.filter(customer_id__isnull=False)
    elif filters.customer_type == 'walkin':
        qs = qs.filter(customer_id__isnull=True)
    if filters.payment_type == 'cash':
        qs = qs.filter(payments__method='cash')
    elif filters.payment_type == 'credit':
        qs = qs.filter(payments__method='credit')
    elif filters.payment_type == 'other':
        qs = qs.filter(payments__method__in=_OTHER_PAYMENT_METHODS)
    if filters.payment_type:
        qs = qs.distinct()
    return qs


def online_orders_qs(start_date, end_date, exclude_cancelled=True):
    start_utc, end_utc = local_range_to_utc(start_date, end_date)
    qs = ProductOrder.objects.filter(ordered_at__gte=start_utc, ordered_at__lt=end_utc)
    if exclude_cancelled:
        qs = qs.exclude(status='cancelled')
    return qs


# ── cart_snapshot line iteration (see module docstring) ─────────────

def _iter_pos_cart_lines(sales_qs):
    """Yields (sale_id, line_dict) for every cart_snapshot line across
    `sales_qs` — ONE query, then an in-memory loop. See module docstring
    for why this one exception to "aggregate in the database" exists."""
    for sale_id, snapshot in sales_qs.values_list('id', 'cart_snapshot'):
        for line in (snapshot or []):
            if isinstance(line, dict):
                yield sale_id, line


def _product_lookup(product_ids):
    """{product_id: Product} for the given ids, in a fixed small number of
    queries regardless of how many cart lines reference them. Also
    prefetches selling_units: _line_estimated_price() below reads
    product.selling_units.all() once per cart LINE (not per product), and
    without prefetch_related that would be a fresh query every single
    time -- a real N+1 this module's tests caught (QueryCountTests),
    since a popular fixed_quantity product (e.g. pickle jars) can appear
    in dozens of lines across a day's sales."""
    return (
        Product.objects.filter(id__in=product_ids)
        .select_related('category', 'category__parent')
        .prefetch_related('selling_units')
        .in_bulk()
    )


def _variant_lookup(variant_ids):
    return ProductVariant.objects.filter(id__in=[v for v in variant_ids if v]).in_bulk()


def _line_quantity(line, product):
    """(count_qty, weight_kg) this one line contributes — never summed
    together (see module docstring / the UI's own "never mixed" note).
    Exactly one of the two is non-zero for any given line."""
    if product and product.pricing_mode == 'variable_weight':
        try:
            return Decimal('0'), Decimal(str(line.get('weight') or 0))
        except Exception:
            return Decimal('0'), Decimal('0')
    try:
        return Decimal(str(line.get('qty') or 0)), Decimal('0')
    except Exception:
        return Decimal('0'), Decimal('0')


def _line_estimated_price(line, product, variants_by_id):
    """Today's price for one line's qty/weight — see module docstring:
    this is an ESTIMATE (today's price, not the price actually charged),
    always used alongside is_estimate=True in whatever calls this."""
    if not product:
        return ZERO
    try:
        if line.get('variant_id'):
            variant = variants_by_id.get(line['variant_id'])
            if variant:
                return variant.total_price()
            return Decimal(str(product.price))  # animal no longer listed -- fall back to the product's own rate
        if product.pricing_mode == 'variable_weight':
            weight = Decimal(str(line.get('weight') or 0))
            return (Decimal(str(product.price)) * weight).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        qty = Decimal(str(line.get('qty') or 1))
        unit_price = Decimal(str(product.price))
        for unit in product.selling_units.all():
            if unit.id == line.get('unit_id'):
                unit_price = unit.price
                break
        return (unit_price * qty).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    except Exception:
        return ZERO


# ── 1. Summary (Overview tab) ────────────────────────────────────────

def get_summary(start_date, end_date, channel, compare, granularity='day', filters=NO_FILTERS):
    include_pos = channel in ('all', 'pos')
    include_online = channel in ('all', 'online')

    def compute(s, e):
        pos_qs = pos_sales_qs(s, e, filters) if include_pos else POSSale.objects.none()
        online_qs = online_orders_qs(s, e) if include_online else ProductOrder.objects.none()

        pos_agg = pos_qs.aggregate(
            total=Coalesce(Sum('total_amount'), ZERO, output_field=DecimalField()),
            count=Count('id'),
            discount=Coalesce(Sum('discount_amount'), ZERO, output_field=DecimalField()),
        )
        online_count = online_qs.count()

        sale_ids = list(pos_qs.values_list('id', flat=True))
        payment_agg = POSSalePayment.objects.filter(sale_id__in=sale_ids).aggregate(
            cash_like=Coalesce(Sum('amount', filter=~Q(method='credit')), ZERO, output_field=DecimalField()),
            credit=Coalesce(Sum('amount', filter=Q(method='credit')), ZERO, output_field=DecimalField()),
        )
        # Repayments are credit-ledger entries, not POS sales -- payment
        # type/customer type/operator filters (which are about a SALE's
        # own lines) don't apply to them, same as the rest of the Credit
        # tab. Always the full repayment total for the period.
        repayments = CreditTransaction.objects.filter(
            transaction_type='repayment', created_at__gte=local_range_to_utc(s, e)[0],
            created_at__lt=local_range_to_utc(s, e)[1],
        ).aggregate(total=Coalesce(Sum('amount'), ZERO, output_field=DecimalField()))['total']

        units_count = ZERO
        weight_kg = ZERO
        if sale_ids:
            cart_lines = list(_iter_pos_cart_lines(pos_qs))  # one query, reused below -- not re-scanned
            products = _product_lookup({line['product_id'] for _, line in cart_lines if line.get('product_id')})
            for _, line in cart_lines:
                product = products.get(line.get('product_id'))
                c, w = _line_quantity(line, product)
                units_count += c
                weight_kg += w

        aov = (pos_agg['total'] / pos_agg['count']) if pos_agg['count'] else ZERO
        cash_collected = payment_agg['cash_like'] + repayments

        return {
            'total_sales': pos_agg['total'], 'transactions': pos_agg['count'],
            'online_order_count': online_count, 'aov': aov,
            'units_count': units_count, 'weight_kg': weight_kg,
            'cash_collected': cash_collected, 'credit_given': payment_agg['credit'],
            'repayments': repayments, 'discounts_given': pos_agg['discount'],
        }

    current = compute(start_date, end_date)
    if compare:
        prev_start, prev_end = previous_period(start_date, end_date)
        previous = compute(prev_start, prev_end)
        kpis = {key: kpi_entry(current[key], previous[key], True) for key in current}
    else:
        kpis = {key: kpi_entry(current[key], None, False) for key in current}

    outstanding = get_outstanding_credit_total()
    # A live snapshot, not period-scoped -- no meaningful previous-period
    # comparison regardless of the compare toggle.
    kpis['outstanding_credit'] = kpi_entry(outstanding, None, False)

    # top_products / payment_mix / sales_over_time / run_rate / insight_strip
    # / staff_ranking are all POS-only visualizations (there's no online
    # revenue to chart -- see module docstring), so they're left empty
    # rather than silently showing POS data under an "online" channel filter.
    return {
        'kpis': kpis,
        'alerts': get_alerts_strip(),
        'top_products': get_top_products(start_date, end_date, limit=5, by='quantity', filters=filters) if include_pos else [],
        'channel_split': get_channel_split(start_date, end_date, filters),
        'payment_mix': get_payment_mix(start_date, end_date, filters) if include_pos else [],
        'sales_over_time': (
            get_sales_trend(start_date, end_date, granularity, compare=compare, filters=filters)['series']
            if include_pos else {'current': []}
        ),
        'staff_ranking': get_sales_by_operator(start_date, end_date, filters)[:5] if include_pos else [],
        'run_rate': get_run_rate(start_date, end_date, filters) if include_pos else {'available': False, 'reason': 'Online-only channel has no run rate.'},
        'insight_strip': get_insight_strip(start_date, end_date, filters) if include_pos else None,
        # The Overview's own product-comparison widget always compares to
        # the previous period, independent of the page-level Compare
        # toggle (that toggle is about the KPI deltas) -- capped to the
        # top 10 by revenue so this stays a "small widget", not the full
        # Products tab table.
        'product_comparison': get_product_table(start_date, end_date, compare=True, filters=filters)[:10] if include_pos else [],
        'category_mix': get_category_breakdown(start_date, end_date, filters) if include_pos else [],
        'by_hour': get_sales_by_hour(start_date, end_date, filters) if include_pos else [0] * 24,
        'by_day_of_week': get_sales_by_day_of_week(start_date, end_date, filters) if include_pos else [0] * 7,
    }


def get_alerts_strip():
    """Compact counts for the Overview's alerts preview -- see
    get_alerts_detailed() for the full Alerts section's actual lists."""
    low_stock_count = len(get_low_stock_products())
    pending_online = ProductOrder.objects.filter(
        status__in=('pending', 'confirmed'),
        ordered_at__lt=djtz.now() - timedelta(days=ALERT_ORDER_PENDING_DAYS),
    ).count()
    overdue_credit = get_outstanding_credit_total()
    no_sales_count = len(get_no_sales_products())
    return {
        'low_stock_count': low_stock_count,
        'pending_online_orders': pending_online,
        'overdue_credit_total': overdue_credit,
        'no_sales_count': no_sales_count,
    }


def get_no_sales_products(today=None):
    """Available products with no POS sale line in the last
    ALERT_NO_SALES_LOOKBACK_DAYS days (or none ever within that window) --
    days_since_last_sale is None for the latter case, never a fabricated
    number. One bulk query (cart_snapshot scan over the lookback window),
    same pattern as the rest of this module's per-line aggregation."""
    today = today or local_today()
    lookback_start = today - timedelta(days=ALERT_NO_SALES_LOOKBACK_DAYS)
    start_utc, end_utc = local_range_to_utc(lookback_start, today)
    last_sold_date = {}
    for created_at, snapshot in POSSale.objects.filter(
        created_at__gte=start_utc, created_at__lt=end_utc
    ).values_list('created_at', 'cart_snapshot'):
        local_date = created_at.astimezone(LOCAL_TZ).date()
        for line in (snapshot or []):
            if not isinstance(line, dict):
                continue
            pid = line.get('product_id')
            if not pid:
                continue
            if pid not in last_sold_date or local_date > last_sold_date[pid]:
                last_sold_date[pid] = local_date

    out = []
    for product in Product.objects.filter(is_available=True):
        last = last_sold_date.get(product.id)
        days_since = (today - last).days if last else None
        if last is None or days_since >= ALERT_NO_SALES_DAYS:
            out.append({'product_id': product.id, 'name': product.name, 'days_since_last_sale': days_since})
    out.sort(key=lambda r: (r['days_since_last_sale'] is not None, r['days_since_last_sale'] or 0))
    return out


def get_alerts_detailed():
    """The full Alerts section: every list the Overview's preview only
    counts. Never fabricates an alert -- an empty category just returns
    an empty list, and the frontend shows "All clear" when every
    category is empty."""
    low_stock = get_low_stock_products()
    no_sales = get_no_sales_products()

    pending_cutoff = djtz.now() - timedelta(days=ALERT_ORDER_PENDING_DAYS)
    stale_orders = list(
        ProductOrder.objects.filter(status__in=('pending', 'confirmed'), ordered_at__lt=pending_cutoff)
        .order_by('ordered_at')
    )
    stale_orders_out = [
        {
            'id': o.id, 'order_number': o.order_number, 'name': o.name, 'status': o.status,
            'ordered_at': o.ordered_at.astimezone(LOCAL_TZ).isoformat(),
            'days_pending': (djtz.now() - o.ordered_at).days,
        }
        for o in stale_orders
    ]

    high_credit_customers = [
        {'customer_id': c.id, 'name': c.name, 'phone': c.phone, 'balance': float(bal)}
        for c in Customer.objects.all()
        for bal in [c.outstanding_balance()]
        if bal >= ALERT_CREDIT_BALANCE_THRESHOLD
    ]
    high_credit_customers.sort(key=lambda r: r['balance'], reverse=True)

    target_milestone = None
    progress = get_target_progress()
    if progress['has_target'] and not progress['not_started']:
        reached = [m for m in ALERT_TARGET_MILESTONES if progress['pct_achieved'] >= m]
        if reached:
            target_milestone = {'milestone_pct': max(reached), 'target_name': progress['name']}

    return {
        'low_stock': low_stock,
        'no_sales': no_sales,
        'stale_orders': stale_orders_out,
        'high_credit_customers': high_credit_customers,
        'target_milestone': target_milestone,
        'thresholds': {
            'no_sales_days': ALERT_NO_SALES_DAYS,
            'credit_balance': float(ALERT_CREDIT_BALANCE_THRESHOLD),
            'order_pending_days': ALERT_ORDER_PENDING_DAYS,
        },
    }


# ── Run-rate projection + bottom insight strip (Overview) ─────────────

def get_run_rate(start_date, end_date, filters=NO_FILTERS):
    """Projects the FULL selected period's total at the current daily
    pace. Hidden (available=False) when fewer than
    RUN_RATE_MIN_DAYS_WITH_SALES distinct days in the period actually had
    a sale -- a 2-data-point projection is more misleading than useful."""
    today = local_today()
    elapsed_end = min(end_date, today)
    qs = pos_sales_qs(start_date, elapsed_end, filters)
    daily = list(
        qs.annotate(bucket=TruncDate('created_at', tzinfo=LOCAL_TZ)).values('bucket')
        .annotate(total=Sum('total_amount')).order_by('bucket')
    )
    days_with_sales = len(daily)
    if days_with_sales < RUN_RATE_MIN_DAYS_WITH_SALES:
        return {
            'available': False,
            'reason': f'Needs at least {RUN_RATE_MIN_DAYS_WITH_SALES} days with a sale in the period (has {days_with_sales}).',
        }
    total_so_far = sum((r['total'] for r in daily), ZERO)
    elapsed_days = (elapsed_end - start_date).days + 1
    full_period_days = (end_date - start_date).days + 1
    daily_avg = total_so_far / elapsed_days
    return {
        'available': True,
        'daily_avg': float(daily_avg),
        'projected_total': float(daily_avg * full_period_days),
        'total_so_far': float(total_so_far),
        'elapsed_days': elapsed_days,
        'full_period_days': full_period_days,
    }


def get_insight_strip(start_date, end_date, filters=NO_FILTERS):
    """The 5 bottom Overview stat cards -- every figure sourced straight
    from already-defined aggregates, nothing fabricated. Any card with no
    data (e.g. no sales at all in the period) comes back None/0 and the
    frontend shows a plain empty state for it."""
    qs = pos_sales_qs(start_date, end_date, filters)
    daily = list(
        qs.annotate(bucket=TruncDate('created_at', tzinfo=LOCAL_TZ)).values('bucket')
        .annotate(total=Sum('total_amount')).order_by('-total')
    )
    best_day = {'date': daily[0]['bucket'].isoformat(), 'total': float(daily[0]['total'])} if daily else None

    hours = get_sales_by_hour(start_date, end_date, filters)
    peak_hour = max(range(24), key=lambda h: hours[h]) if any(hours) else None

    total_days = (end_date - start_date).days + 1
    total_sales = qs.aggregate(total=Coalesce(Sum('total_amount'), ZERO, output_field=DecimalField()))['total']
    avg_daily_sales = float(total_sales / total_days) if total_days else 0.0

    top = get_top_products(start_date, end_date, limit=1, by='revenue', filters=filters)
    top_product = top[0] if top else None

    return {
        'best_day': best_day,
        'peak_hour': peak_hour,
        'avg_daily_sales': avg_daily_sales,
        'top_product': top_product,
        'days_with_sales': len(daily),
    }


def get_channel_split(start_date, end_date, filters=NO_FILTERS):
    """By COUNT, not revenue — see module docstring for why online has no
    revenue figure to split by."""
    pos_count = pos_sales_qs(start_date, end_date, filters).count()
    online_count = online_orders_qs(start_date, end_date).count()
    return {'pos': pos_count, 'online': online_count}


def get_payment_mix(start_date, end_date, filters=NO_FILTERS):
    """POS payment-method split by amount — 'credit' here is the credit
    portion of sales made in the period (matches "Credit given" on the
    Credit tab), not a running total of all unpaid credit."""
    sale_ids = pos_sales_qs(start_date, end_date, filters).values_list('id', flat=True)
    rows = (
        POSSalePayment.objects.filter(sale_id__in=sale_ids)
        .values('method').annotate(total=Sum('amount')).order_by('-total')
    )
    return [{'method': r['method'], 'total': float(r['total'])} for r in rows]


# ── 2. Sales trend (Sales tab) ───────────────────────────────────────

def get_sales_trend(start_date, end_date, granularity, compare=False, filters=NO_FILTERS):
    bs_mode = granularity == 'bs_month'
    trunc = _trunc_fn('day' if bs_mode else granularity)

    def bucketed(s, e):
        start_utc, end_utc = local_range_to_utc(s, e)
        rows = (
            pos_sales_qs(s, e, filters)
            .annotate(bucket=trunc('created_at', tzinfo=LOCAL_TZ))
            .values('bucket')
            .annotate(total=Sum('total_amount'), count=Count('id'))
            .order_by('bucket')
        )
        out = [{'date': r['bucket'].isoformat(), 'total': float(r['total']), 'count': r['count']} for r in rows]
        return _bucket_daily_rows_into_bs_months(out) if bs_mode else out

    series = {'current': bucketed(start_date, end_date)}
    if compare:
        prev_start, prev_end = previous_period(start_date, end_date)
        series['previous'] = bucketed(prev_start, prev_end)

    return {
        'series': series,
        'by_hour': get_sales_by_hour(start_date, end_date, filters),
        'by_day_of_week': get_sales_by_day_of_week(start_date, end_date, filters),
    }


def get_sales_by_hour(start_date, end_date, filters=NO_FILTERS):
    """Hour-of-day (0-23) in LOCAL time -- computed in Python from the
    stored created_at, converting each to local time, since SQLite's
    strftime('%H', ...) has no timezone awareness to convert with."""
    hours = [0] * 24
    for created_at in pos_sales_qs(start_date, end_date, filters).values_list('created_at', flat=True):
        hours[created_at.astimezone(LOCAL_TZ).hour] += 1
    return hours


def get_sales_by_day_of_week(start_date, end_date, filters=NO_FILTERS):
    """Monday=0 .. Sunday=6, in LOCAL time, same reasoning as by-hour above."""
    days = [0] * 7
    for created_at in pos_sales_qs(start_date, end_date, filters).values_list('created_at', flat=True):
        days[created_at.astimezone(LOCAL_TZ).weekday()] += 1
    return days


def get_recent_sales(start_date, end_date, page, page_size, filters=NO_FILTERS):
    qs = pos_sales_qs(start_date, end_date, filters).select_related('cashier', 'customer').order_by('-created_at')
    total = qs.count()
    offset = (page - 1) * page_size
    rows = []
    for sale in qs[offset:offset + page_size]:
        payments = list(sale.payments.values('method', 'amount'))
        rows.append({
            'sale_number': sale.sale_number,
            'created_at': sale.created_at.astimezone(LOCAL_TZ).isoformat(),
            'customer': sale.customer.name if sale.customer else None,
            'operator': sale.cashier.get_full_name() or sale.cashier.username,
            'total': float(sale.total_amount),
            'payments': [{'method': p['method'], 'amount': float(p['amount'])} for p in payments],
        })
    return {'results': rows, 'count': total, 'page': page, 'page_size': page_size}


def get_sales_by_operator(start_date, end_date, filters=NO_FILTERS):
    rows = (
        pos_sales_qs(start_date, end_date, filters)
        .values('cashier__username', 'cashier__first_name', 'cashier__last_name')
        .annotate(total=Sum('total_amount'), count=Count('id'))
        .order_by('-total')
    )
    out = []
    for r in rows:
        name = f"{r['cashier__first_name']} {r['cashier__last_name']}".strip() or r['cashier__username']
        out.append({'operator': name, 'total': float(r['total']), 'count': r['count']})
    return out


# ── 3. Products tab ───────────────────────────────────────────────────

def _product_aggregates(start_date, end_date, filters=NO_FILTERS):
    """{product_id: {'qty', 'weight_kg', 'revenue_est', 'offer_lines', 'combo_lines'}}"""
    qs = pos_sales_qs(start_date, end_date, filters)
    line_cache = list(_iter_pos_cart_lines(qs))
    product_ids = {line.get('product_id') for _, line in line_cache if line.get('product_id')}
    products = _product_lookup(product_ids)
    variant_ids = {line.get('variant_id') for _, line in line_cache if line.get('variant_id')}
    variants = _variant_lookup(variant_ids)

    agg = defaultdict(lambda: {'qty': ZERO, 'weight_kg': ZERO, 'revenue_est': ZERO})
    for _, line in line_cache:
        pid = line.get('product_id')
        if not pid:
            continue
        product = products.get(pid)
        c, w = _line_quantity(line, product)
        agg[pid]['qty'] += c
        agg[pid]['weight_kg'] += w
        agg[pid]['revenue_est'] += _line_estimated_price(line, product, variants)
    return agg, products


def get_top_products(start_date, end_date, limit=5, by='quantity', filters=NO_FILTERS):
    agg, products = _product_aggregates(start_date, end_date, filters)
    key = 'revenue_est' if by == 'revenue' else None

    def sort_key(pid):
        if by == 'revenue':
            return agg[pid]['revenue_est']
        return agg[pid]['qty'] + agg[pid]['weight_kg']  # fine for ranking purposes only, never displayed summed

    ranked = sorted(agg.keys(), key=sort_key, reverse=True)[:limit]
    return [
        {
            'product_id': pid,
            'name': products[pid].name if pid in products else f'[deleted product #{pid}]',
            'qty': float(agg[pid]['qty']), 'weight_kg': float(agg[pid]['weight_kg']),
            'revenue_est': float(agg[pid]['revenue_est']),
        }
        for pid in ranked
    ]


def get_category_breakdown(start_date, end_date, filters=NO_FILTERS):
    agg, products = _product_aggregates(start_date, end_date, filters)
    by_category = defaultdict(lambda: {'qty': ZERO, 'weight_kg': ZERO, 'revenue_est': ZERO})
    for pid, values in agg.items():
        product = products.get(pid)
        if product and product.category:
            cat = product.category
            while cat.parent_id:
                cat = cat.parent
            name = cat.name
        else:
            name = 'Other'
        by_category[name]['qty'] += values['qty']
        by_category[name]['weight_kg'] += values['weight_kg']
        by_category[name]['revenue_est'] += values['revenue_est']
    return [
        {'category': name, 'qty': float(v['qty']), 'weight_kg': float(v['weight_kg']), 'revenue_est': float(v['revenue_est'])}
        for name, v in sorted(by_category.items(), key=lambda kv: kv[1]['revenue_est'], reverse=True)
    ]


def get_product_table(start_date, end_date, compare, filters=NO_FILTERS):
    """One row per product sold in the period. When compare=True, each
    row also carries this-period-vs-previous-period QUANTITY (exact --
    qty/weight genuinely are stored) alongside the existing revenue
    'trend' (estimate-vs-estimate, see module docstring) -- the Overview
    product comparison table reads qty_now/qty_previous/qty_change_pct
    directly rather than re-deriving them from 'trend'."""
    agg, products = _product_aggregates(start_date, end_date, filters)
    prev_agg = {}
    if compare:
        prev_start, prev_end = previous_period(start_date, end_date)
        prev_agg, _ = _product_aggregates(prev_start, prev_end, filters)

    total_revenue_est = sum((v['revenue_est'] for v in agg.values()), ZERO)
    rows = []
    for pid, v in agg.items():
        product = products.get(pid)
        prev = prev_agg.get(pid, {'revenue_est': ZERO, 'qty': ZERO, 'weight_kg': ZERO})
        share = float(v['revenue_est'] / total_revenue_est * 100) if total_revenue_est else 0.0
        prev_qty_total = float(prev['qty'] + prev['weight_kg'])
        now_qty_total = float(v['qty'] + v['weight_kg'])
        rows.append({
            'product_id': pid,
            'name': product.name if product else f'[deleted product #{pid}]',
            'qty': float(v['qty']), 'weight_kg': float(v['weight_kg']),
            'qty_previous': float(prev['qty']), 'weight_kg_previous': float(prev['weight_kg']),
            'qty_change': delta_info(now_qty_total, prev_qty_total) if compare else None,
            'revenue_est': float(v['revenue_est']), 'share_pct': round(share, 1),
            'trend': delta_info(v['revenue_est'], prev['revenue_est']) if compare else None,
        })
    rows.sort(key=lambda r: r['revenue_est'], reverse=True)
    return rows


def get_slow_movers(start_date, end_date, filters=NO_FILTERS):
    agg, _ = _product_aggregates(start_date, end_date, filters)
    sold_ids = {pid for pid, v in agg.items() if v['qty'] > 0 or v['weight_kg'] > 0}
    return list(
        Product.objects.filter(is_available=True).exclude(id__in=sold_ids).values('id', 'name').order_by('name')
    )


def get_offers_performance(start_date, end_date, filters=NO_FILTERS):
    qs = pos_sales_qs(start_date, end_date, filters)
    offer_lines = defaultdict(lambda: {'uses': 0, 'combo_instances': set()})
    for _, line in _iter_pos_cart_lines(qs):
        offer_id = line.get('offer_id')
        if not offer_id:
            continue
        if line.get('combo_instance_id'):
            offer_lines[offer_id]['combo_instances'].add(line['combo_instance_id'])
        else:
            offer_lines[offer_id]['uses'] += 1
    offers = Offer.objects.filter(id__in=offer_lines.keys()).in_bulk()
    rows = []
    for offer_id, data in offer_lines.items():
        uses = data['uses'] + len(data['combo_instances'])
        offer = offers.get(offer_id)
        rows.append({
            'offer_id': offer_id,
            'title': offer.title if offer else f'[deleted offer #{offer_id}]',
            'uses': uses, 'discount_given': None,  # not tracked -- see module docstring
        })
    rows.sort(key=lambda r: r['uses'], reverse=True)

    coupon_rows = list(
        qs.exclude(coupon__isnull=True)
        .values('coupon__code')
        .annotate(uses=Count('id'), discount_given=Sum('discount_amount'))
        .order_by('-uses')
    )
    return {
        'offers': rows,
        'coupons': [
            {'code': r['coupon__code'], 'uses': r['uses'], 'discount_given': float(r['discount_given'] or 0)}
            for r in coupon_rows
        ],
    }


# ── 4. Credit tab ─────────────────────────────────────────────────────

def get_outstanding_credit_total():
    agg = CreditTransaction.objects.aggregate(
        given=Coalesce(Sum('amount', filter=Q(transaction_type='credit_sale')), ZERO, output_field=DecimalField()),
        repaid=Coalesce(Sum('amount', filter=Q(transaction_type='repayment')), ZERO, output_field=DecimalField()),
    )
    return (agg['given'] - agg['repaid']).quantize(Decimal('0.01'))


def get_credit_summary(start_date, end_date, compare):
    def compute(s, e):
        start_utc, end_utc = local_range_to_utc(s, e)
        agg = CreditTransaction.objects.filter(created_at__gte=start_utc, created_at__lt=end_utc).aggregate(
            given=Coalesce(Sum('amount', filter=Q(transaction_type='credit_sale')), ZERO, output_field=DecimalField()),
            repaid=Coalesce(Sum('amount', filter=Q(transaction_type='repayment')), ZERO, output_field=DecimalField()),
        )
        return agg

    current = compute(start_date, end_date)
    if compare:
        prev_start, prev_end = previous_period(start_date, end_date)
        previous = compute(prev_start, prev_end)
        kpis = {
            'given': kpi_entry(current['given'], previous['given'], True),
            'repaid': kpi_entry(current['repaid'], previous['repaid'], True),
        }
    else:
        kpis = {
            'given': kpi_entry(current['given'], None, False),
            'repaid': kpi_entry(current['repaid'], None, False),
        }
    outstanding = get_outstanding_credit_total()
    kpis['outstanding'] = kpi_entry(outstanding, None, False)
    customers_with_balance = sum(1 for c in Customer.objects.all() if c.outstanding_balance() > 0)
    kpis['customers_with_balance'] = kpi_entry(customers_with_balance, None, False)
    return kpis


def get_credit_trend(start_date, end_date, granularity):
    bs_mode = granularity == 'bs_month'
    trunc = _trunc_fn('day' if bs_mode else granularity)
    start_utc, end_utc = local_range_to_utc(start_date, end_date)
    rows = (
        CreditTransaction.objects.filter(created_at__gte=start_utc, created_at__lt=end_utc)
        .annotate(bucket=trunc('created_at', tzinfo=LOCAL_TZ))
        .values('bucket', 'transaction_type')
        .annotate(total=Sum('amount'))
        .order_by('bucket')
    )
    buckets = defaultdict(lambda: {'given': 0.0, 'repaid': 0.0})
    for r in rows:
        key = r['bucket'].isoformat()
        if r['transaction_type'] == 'credit_sale':
            buckets[key]['given'] = float(r['total'])
        else:
            buckets[key]['repaid'] = float(r['total'])
    out = [{'date': k, **v} for k, v in sorted(buckets.items())]
    return _bucket_daily_rows_into_bs_months(out) if bs_mode else out


def get_credit_ageing_and_customers():
    """Age (days) of a customer's OLDEST unpaid credit_sale — oldest
    unpaid first: walk each customer's credit_sale rows oldest-to-newest,
    consuming repayment total against them FIFO, and the first credit_sale
    row not yet fully covered by repayments-so-far is what "age" is
    measured from. This matches how a shopkeeper would actually think
    about an उधारो tab: the oldest debt is the one still outstanding,
    not an average or a last-transaction date."""
    now = djtz.now()
    buckets = {'0-30': 0, '31-60': 0, '61-90': 0, '90+': 0}
    customers = []
    for customer in Customer.objects.prefetch_related('credit_transactions').all():
        balance = customer.outstanding_balance()
        if balance <= 0:
            continue
        txns = sorted(customer.credit_transactions.all(), key=lambda t: t.created_at)
        repay_pool = sum((t.amount for t in txns if t.transaction_type == 'repayment'), ZERO)
        oldest_unpaid_at = None
        for t in txns:
            if t.transaction_type != 'credit_sale':
                continue
            if repay_pool >= t.amount:
                repay_pool -= t.amount
                continue
            oldest_unpaid_at = t.created_at
            break
        age_days = (now - oldest_unpaid_at).days if oldest_unpaid_at else 0
        bucket = '0-30' if age_days <= 30 else ('31-60' if age_days <= 60 else ('61-90' if age_days <= 90 else '90+'))
        buckets[bucket] += 1

        last_sale = customer.pos_sales.order_by('-created_at').first()
        last_repayment = (
            customer.credit_transactions.filter(transaction_type='repayment').order_by('-created_at').first()
        )
        customers.append({
            'customer_id': customer.id, 'name': customer.name, 'phone': customer.phone,
            'balance': float(balance),
            'last_purchase': last_sale.created_at.astimezone(LOCAL_TZ).isoformat() if last_sale else None,
            'last_repayment': last_repayment.created_at.astimezone(LOCAL_TZ).isoformat() if last_repayment else None,
            'age_days': age_days, 'age_bucket': bucket,
        })
    return buckets, customers


# ── 5. Inventory tab ──────────────────────────────────────────────────

def get_current_stock_table():
    """One row per product (or per variant, for fixed-weight animals),
    using the SAME sign-aware aggregation InventoryMovement.current_stock()
    does, but as a single grouped query instead of one query per product —
    see module docstring. A reconciliation test confirms this matches
    InventoryMovement.current_stock() exactly for a sample of products."""
    increase = InventoryMovement.INCREASE_TYPES
    rows = (
        InventoryMovement.objects.values('product_id', 'variant_id')
        .annotate(
            stock=Sum(
                'quantity', filter=Q(movement_type__in=increase)
            ) - Coalesce(
                Sum('quantity', filter=~Q(movement_type__in=increase)), ZERO, output_field=DecimalField()
            )
        )
    )
    # The annotate() above can't express "Sum(quantity) WHERE increase,
    # defaulting to 0" cleanly in one Coalesce on SQLite with a bare
    # Sum(..., filter=...) possibly being NULL -- recombine safely in
    # Python instead of fighting the ORM over a single-digit edge case.
    stock_by_key = {}
    for product_id, variant_id, qty, movement_type in InventoryMovement.objects.values_list(
        'product_id', 'variant_id', 'quantity', 'movement_type'
    ):
        key = (product_id, variant_id)
        signed = qty if movement_type in increase else -qty
        stock_by_key[key] = stock_by_key.get(key, ZERO) + signed

    products = Product.objects.filter(is_available=True).select_related('category')
    out = []
    for product in products:
        if product.pricing_mode == 'fixed_weight':
            available = product.available_variant_count()
            out.append({
                'product_id': product.id, 'name': product.name, 'pricing_mode': product.pricing_mode,
                'stock': available, 'unit': 'animals available', 'low_stock': False,
            })
        else:
            stock = stock_by_key.get((product.id, None), ZERO)
            out.append({
                'product_id': product.id, 'name': product.name, 'pricing_mode': product.pricing_mode,
                'stock': float(stock),
                'unit': 'kg' if product.pricing_mode == 'variable_weight' else 'pcs',
                'low_stock': stock <= product.low_stock_threshold,
            })
    return out


def get_low_stock_products():
    return [r for r in get_current_stock_table() if r['low_stock']]


_PRICING_MODE_UNIT = {'variable_weight': 'kg', 'fixed_quantity': 'pcs', 'fixed_weight': 'animals'}


def get_movements_by_type(start_date, end_date):
    """One row per (movement_type, unit) -- never sums kg and piece/animal
    counts into one bar. Every movement_type actually present in the data
    is included as-is (harvest/purchase/sale/waste/return/
    adjustment_add/adjustment_remove), whatever its unit turns out to be —
    nothing here special-cases or drops any of them."""
    start_utc, end_utc = local_range_to_utc(start_date, end_date)
    rows = (
        InventoryMovement.objects.filter(created_at__gte=start_utc, created_at__lt=end_utc)
        .values('movement_type', 'product__pricing_mode')
        .annotate(total=Sum('quantity'), count=Count('id'))
        .order_by('movement_type')
    )
    merged = defaultdict(lambda: {'total': ZERO, 'count': 0})
    for r in rows:
        unit = _PRICING_MODE_UNIT.get(r['product__pricing_mode'], 'pcs')
        key = (r['movement_type'], unit)
        merged[key]['total'] += r['total']
        merged[key]['count'] += r['count']
    return [
        {'movement_type': mt, 'unit': unit, 'total': float(v['total']), 'count': v['count']}
        for (mt, unit), v in sorted(merged.items())
    ]


def get_waste_table(start_date, end_date):
    start_utc, end_utc = local_range_to_utc(start_date, end_date)
    rows = (
        InventoryMovement.objects.filter(
            created_at__gte=start_utc, created_at__lt=end_utc, movement_type='waste'
        )
        .values('product__name')
        .annotate(total=Sum('quantity'), count=Count('id'))
        .order_by('-total')
    )
    return [{'product': r['product__name'], 'total': float(r['total']), 'count': r['count']} for r in rows]


# ── 6. Online Orders tab ──────────────────────────────────────────────

def get_orders_by_status(start_date, end_date):
    start_utc, end_utc = local_range_to_utc(start_date, end_date)
    rows = (
        ProductOrder.objects.filter(ordered_at__gte=start_utc, ordered_at__lt=end_utc)
        .values('status').annotate(count=Count('id')).order_by('status')
    )
    return {r['status']: r['count'] for r in rows}


def get_orders_over_time(start_date, end_date, granularity):
    bs_mode = granularity == 'bs_month'
    trunc = _trunc_fn('day' if bs_mode else granularity)
    start_utc, end_utc = local_range_to_utc(start_date, end_date)
    rows = (
        ProductOrder.objects.filter(ordered_at__gte=start_utc, ordered_at__lt=end_utc)
        .exclude(status='cancelled')
        .annotate(bucket=trunc('ordered_at', tzinfo=LOCAL_TZ))
        .values('bucket').annotate(count=Count('id')).order_by('bucket')
    )
    out = [{'date': r['bucket'].isoformat(), 'count': r['count']} for r in rows]
    return _bucket_daily_rows_into_bs_months(out) if bs_mode else out


def get_recent_orders(start_date, end_date, page, page_size):
    qs = ProductOrder.objects.filter(
        ordered_at__gte=local_range_to_utc(start_date, end_date)[0],
        ordered_at__lt=local_range_to_utc(start_date, end_date)[1],
    ).order_by('-ordered_at')
    total = qs.count()
    offset = (page - 1) * page_size
    rows = [
        {
            'id': o.id, 'order_number': o.order_number, 'name': o.name, 'status': o.status,
            'product_interest': o.product_interest,
            'ordered_at': o.ordered_at.astimezone(LOCAL_TZ).isoformat(),
        }
        for o in qs[offset:offset + page_size]
    ]
    return {'results': rows, 'count': total, 'page': page, 'page_size': page_size}


# ── Revenue Target (sidebar card) ─────────────────────────────────────

def get_active_target(today=None):
    """The one RevenueTarget the dashboard shows: among is_active=True
    rows, whichever one's period actually contains today, else the most
    recently started active one. None if there are no active targets at
    all."""
    today = today or local_today()
    active = RevenueTarget.objects.filter(is_active=True)
    containing = active.filter(start_date__lte=today, end_date__gte=today).order_by('-start_date').first()
    if containing:
        return containing
    return active.order_by('-start_date').first()


def get_target_progress():
    """Achieved is always POS sales ONLY (exact, via pos_sales_qs) for
    the same reason revenue is POS-only everywhere else on this
    dashboard -- see module docstring. Returns has_target=False (not an
    error) when no active target exists, so the sidebar card can show a
    clean empty state."""
    today = local_today()
    target = get_active_target(today)
    if not target:
        return {'has_target': False}

    total_days = (target.end_date - target.start_date).days + 1
    if today < target.start_date:
        # Target period hasn't started yet -- nothing achieved, nothing
        # elapsed, but still report full remaining/days-left for display.
        achieved = ZERO
        days_elapsed = 0
        days_left = total_days
    else:
        elapsed_end = min(today, target.end_date)
        achieved = pos_sales_qs(target.start_date, elapsed_end).aggregate(
            total=Coalesce(Sum('total_amount'), ZERO, output_field=DecimalField())
        )['total']
        days_elapsed = (elapsed_end - target.start_date).days + 1
        days_left = max(0, (target.end_date - today).days)

    amount = target.amount
    remaining = amount - achieved
    pct_achieved = float(achieved / amount * 100) if amount else 0.0
    needed_per_day = float(remaining / days_left) if days_left > 0 and remaining > 0 else 0.0
    # Projection: achieved-so-far extrapolated at the same daily pace to
    # the end of the full target period. None (not float('nan')) when
    # there's no elapsed time yet to extrapolate from -- the frontend
    # shows "not enough data" rather than a misleading number.
    projected_total = float(achieved / days_elapsed * total_days) if days_elapsed > 0 else None

    return {
        'has_target': True,
        'name': target.name,
        'start_date': target.start_date.isoformat(),
        'end_date': target.end_date.isoformat(),
        'amount': float(amount),
        'achieved': float(achieved),
        'remaining': float(max(remaining, ZERO)),
        'pct_achieved': round(pct_achieved, 1),
        'days_elapsed': days_elapsed,
        'days_left': days_left,
        'total_days': total_days,
        'needed_per_day': round(needed_per_day, 2),
        'projected_total': round(projected_total, 2) if projected_total is not None else None,
        'is_reached': achieved >= amount,
        'not_started': today < target.start_date,
    }
