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
"""

from collections import defaultdict
from datetime import date, datetime, time as dt_time, timedelta, timezone as dt_timezone
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo

from django.db.models import Count, DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce, TruncDate, TruncMonth, TruncWeek
from django.utils import timezone as djtz

from .models import (
    Category, Coupon, CreditTransaction, Customer, InventoryMovement,
    Offer, POSSale, POSSalePayment, Product, ProductOrder, ProductVariant,
)

LOCAL_TZ = ZoneInfo('Asia/Kathmandu')
MAX_RANGE_DAYS = 366
DEFAULT_RANGE_DAYS = 30
ZERO = Decimal('0')


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


def resolve_date_range(start_str, end_str):
    """(start_date, end_date), both inclusive, as LOCAL (Nepal) calendar
    dates. Defaults to the last 30 days (today inclusive) when neither is
    given. Raises ReportValidationError for a missing half of the pair,
    start after end, or a range wider than MAX_RANGE_DAYS."""
    if not start_str and not end_str:
        today_local = djtz.now().astimezone(LOCAL_TZ).date()
        end_date = today_local
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
    if value not in (None, '', 'day', 'week', 'month'):
        raise ReportValidationError("'granularity' must be one of: day, week, month.")
    return value or 'day'


# ── Base querysets ───────────────────────────────────────────────────

def pos_sales_qs(start_date, end_date):
    start_utc, end_utc = local_range_to_utc(start_date, end_date)
    return POSSale.objects.filter(created_at__gte=start_utc, created_at__lt=end_utc)


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

def get_summary(start_date, end_date, channel, compare, granularity='day'):
    include_pos = channel in ('all', 'pos')
    include_online = channel in ('all', 'online')

    def compute(s, e):
        pos_qs = pos_sales_qs(s, e) if include_pos else POSSale.objects.none()
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

    # top_products / payment_mix / sales_over_time are POS-only visualizations
    # (there's no online revenue to chart -- see module docstring), so they're
    # left empty rather than silently showing POS data under an "online" filter.
    return {
        'kpis': kpis,
        'alerts': get_alerts_strip(),
        'top_products': get_top_products(start_date, end_date, limit=5, by='quantity') if include_pos else [],
        'channel_split': get_channel_split(start_date, end_date),
        'payment_mix': get_payment_mix(start_date, end_date) if include_pos else [],
        'sales_over_time': (
            get_sales_trend(start_date, end_date, granularity, compare=compare)['series'] if include_pos else {'current': []}
        ),
    }


def get_alerts_strip():
    low_stock_count = len(get_low_stock_products())
    pending_online = ProductOrder.objects.filter(status__in=('pending', 'confirmed')).count()
    overdue_credit = get_outstanding_credit_total()
    return {
        'low_stock_count': low_stock_count,
        'pending_online_orders': pending_online,
        'overdue_credit_total': overdue_credit,
    }


def get_channel_split(start_date, end_date):
    """By COUNT, not revenue — see module docstring for why online has no
    revenue figure to split by."""
    pos_count = pos_sales_qs(start_date, end_date).count()
    online_count = online_orders_qs(start_date, end_date).count()
    return {'pos': pos_count, 'online': online_count}


def get_payment_mix(start_date, end_date):
    """POS payment-method split by amount — 'credit' here is the credit
    portion of sales made in the period (matches "Credit given" on the
    Credit tab), not a running total of all unpaid credit."""
    sale_ids = pos_sales_qs(start_date, end_date).values_list('id', flat=True)
    rows = (
        POSSalePayment.objects.filter(sale_id__in=sale_ids)
        .values('method').annotate(total=Sum('amount')).order_by('-total')
    )
    return [{'method': r['method'], 'total': float(r['total'])} for r in rows]


# ── 2. Sales trend (Sales tab) ───────────────────────────────────────

def get_sales_trend(start_date, end_date, granularity, compare=False):
    trunc = _trunc_fn(granularity)

    def bucketed(s, e):
        start_utc, end_utc = local_range_to_utc(s, e)
        rows = (
            POSSale.objects.filter(created_at__gte=start_utc, created_at__lt=end_utc)
            .annotate(bucket=trunc('created_at', tzinfo=LOCAL_TZ))
            .values('bucket')
            .annotate(total=Sum('total_amount'), count=Count('id'))
            .order_by('bucket')
        )
        return [{'date': r['bucket'].isoformat(), 'total': float(r['total']), 'count': r['count']} for r in rows]

    series = {'current': bucketed(start_date, end_date)}
    if compare:
        prev_start, prev_end = previous_period(start_date, end_date)
        series['previous'] = bucketed(prev_start, prev_end)

    return {
        'series': series,
        'by_hour': get_sales_by_hour(start_date, end_date),
        'by_day_of_week': get_sales_by_day_of_week(start_date, end_date),
    }


def get_sales_by_hour(start_date, end_date):
    """Hour-of-day (0-23) in LOCAL time -- computed in Python from the
    stored created_at, converting each to local time, since SQLite's
    strftime('%H', ...) has no timezone awareness to convert with."""
    hours = [0] * 24
    for created_at in pos_sales_qs(start_date, end_date).values_list('created_at', flat=True):
        hours[created_at.astimezone(LOCAL_TZ).hour] += 1
    return hours


def get_sales_by_day_of_week(start_date, end_date):
    """Monday=0 .. Sunday=6, in LOCAL time, same reasoning as by-hour above."""
    days = [0] * 7
    for created_at in pos_sales_qs(start_date, end_date).values_list('created_at', flat=True):
        days[created_at.astimezone(LOCAL_TZ).weekday()] += 1
    return days


def get_recent_sales(start_date, end_date, page, page_size):
    qs = pos_sales_qs(start_date, end_date).select_related('cashier', 'customer').order_by('-created_at')
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


def get_sales_by_operator(start_date, end_date):
    rows = (
        pos_sales_qs(start_date, end_date)
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

def _product_aggregates(start_date, end_date):
    """{product_id: {'qty', 'weight_kg', 'revenue_est', 'offer_lines', 'combo_lines'}}"""
    qs = pos_sales_qs(start_date, end_date)
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


def get_top_products(start_date, end_date, limit=5, by='quantity'):
    agg, products = _product_aggregates(start_date, end_date)
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


def get_category_breakdown(start_date, end_date):
    agg, products = _product_aggregates(start_date, end_date)
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


def get_product_table(start_date, end_date, compare):
    agg, products = _product_aggregates(start_date, end_date)
    prev_agg = {}
    if compare:
        prev_start, prev_end = previous_period(start_date, end_date)
        prev_agg, _ = _product_aggregates(prev_start, prev_end)

    total_revenue_est = sum((v['revenue_est'] for v in agg.values()), ZERO)
    rows = []
    for pid, v in agg.items():
        product = products.get(pid)
        prev = prev_agg.get(pid, {'revenue_est': ZERO, 'qty': ZERO, 'weight_kg': ZERO})
        share = float(v['revenue_est'] / total_revenue_est * 100) if total_revenue_est else 0.0
        rows.append({
            'product_id': pid,
            'name': product.name if product else f'[deleted product #{pid}]',
            'qty': float(v['qty']), 'weight_kg': float(v['weight_kg']),
            'revenue_est': float(v['revenue_est']), 'share_pct': round(share, 1),
            'trend': delta_info(v['revenue_est'], prev['revenue_est']),
        })
    rows.sort(key=lambda r: r['revenue_est'], reverse=True)
    return rows


def get_slow_movers(start_date, end_date):
    agg, _ = _product_aggregates(start_date, end_date)
    sold_ids = {pid for pid, v in agg.items() if v['qty'] > 0 or v['weight_kg'] > 0}
    return list(
        Product.objects.filter(is_available=True).exclude(id__in=sold_ids).values('id', 'name').order_by('name')
    )


def get_offers_performance(start_date, end_date):
    qs = pos_sales_qs(start_date, end_date)
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
    trunc = _trunc_fn(granularity)
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
    return [{'date': k, **v} for k, v in sorted(buckets.items())]


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


def get_movements_by_type(start_date, end_date):
    start_utc, end_utc = local_range_to_utc(start_date, end_date)
    rows = (
        InventoryMovement.objects.filter(created_at__gte=start_utc, created_at__lt=end_utc)
        .values('movement_type')
        .annotate(total=Sum('quantity'), count=Count('id'))
        .order_by('movement_type')
    )
    return [{'movement_type': r['movement_type'], 'total': float(r['total']), 'count': r['count']} for r in rows]


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
    trunc = _trunc_fn(granularity)
    start_utc, end_utc = local_range_to_utc(start_date, end_date)
    rows = (
        ProductOrder.objects.filter(ordered_at__gte=start_utc, ordered_at__lt=end_utc)
        .exclude(status='cancelled')
        .annotate(bucket=trunc('ordered_at', tzinfo=LOCAL_TZ))
        .values('bucket').annotate(count=Count('id')).order_by('bucket')
    )
    return [{'date': r['bucket'].isoformat(), 'count': r['count']} for r in rows]


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
