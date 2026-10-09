"""Season/batch costing for farm-grown crops -- the "By batch" view inside
the Reports dashboard's existing "Profit & Loss" tab (templates/
dashboard.html, api/views.py's ReportBatchListView/ReportBatchDetailView,
GET /api/v1/reports/batches/[<code>/]).

build_batch_report(code) is a pure, read-only function: it only ever reads
CropBatch/CostEntry/InventoryMovement/POSSaleLine and returns a dict.
Nothing here writes anything, and nothing here changes shop/pl_report.py's
existing monthly P&L numbers -- this is a second, independent lens on the
same underlying ledgers, scoped to one crop batch's own lifecycle instead
of a calendar period.

Decimal throughout, never float -- same convention as shop/pl_report.py;
the API view converts to float only at the JSON-serialization boundary.

WHAT "COST" MEANS HERE (deliberately narrower than the Monthly P&L):
only CostEntry rows tagged with this batch's code, summed signed (a
reversal is already a negative amount, so it just nets out). No shared-
cost allocation, no depreciation -- see this function's own 'notes' in
its return value, and the Monthly P&L tab for the fuller picture.

THE FIFO ASSUMPTION (brief's own wording, repeated here since it drives
every sold_kg/waste_kg/revenue number below): for this batch's product,
every CropBatch sharing that product is ordered by start_date; every
'sale' and 'waste' InventoryMovement for that product (in chronological
order) consumes stock from the OLDEST pool that still has any -- harvest
stock with no batch_code at all (pre-dating batch tracking for this crop)
is one single pool, always consumed before any real batch. This is a
MODELLED attribution recomputed fresh on every call -- it is never stored
on any row, and a product with no batch mapped at all skips this section
entirely (see 'product' in the returned 'batch' dict).

Revenue for a sold quantity uses POSSaleLine.line_total scaled by the
sale's own coupon discount (post-discount, excl. VAT) -- the exact same
derived figure shop/pl_report.py computes inline for its own per-centre
revenue table, duplicated here as a small local helper rather than
imported, specifically so nothing in THIS module can ever risk changing
pl_report.py's behaviour (see this feature's own brief: "do not change
the existing monthly P&L logic"). A website-channel sale (no
related_pos_sale -- ProductOrder stores no price) still consumes stock
in the FIFO walk but contributes zero revenue, mirroring the Monthly
P&L's own online-order-revenue exclusion.
"""

from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP

from django.db.models import Sum

from .reports import ZERO
from .models import CostEntry, CropBatch, InventoryMovement

CENT = Decimal('0.01')
CONSUMING_TYPES = ('sale', 'waste')


class BatchReportError(Exception):
    """Raised for a bad/unknown batch code. .status lets the API view
    translate it into the right HTTP status (404) without this module
    knowing anything about HTTP."""

    def __init__(self, message, status=404):
        self.message = message
        self.status = status
        super().__init__(message)


def _q2(value):
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _movement_revenue_rate(movement):
    """Rs of (post-discount, excl. VAT) revenue per unit of quantity for
    one 'sale' InventoryMovement -- used to prorate revenue across
    whichever FIFO pool(s) this movement's own quantity gets split
    across. Zero for a website-channel sale (no related_pos_sale) or a
    sale old enough to predate POSSaleLine (no matching line at all) --
    both cases are excluded from revenue the same way shop/pl_report.py
    already excludes them from its own per-centre revenue table."""
    sale = movement.related_pos_sale
    if sale is None or movement.quantity <= 0:
        return ZERO

    all_lines = list(sale.lines.all())
    matched_lines = [
        l for l in all_lines
        if l.product_id == movement.product_id and l.variant_id == movement.variant_id
    ]
    if not matched_lines:
        return ZERO

    matched_qty = sum((l.quantity for l in matched_lines), ZERO)
    matched_total = sum((l.line_total for l in matched_lines), ZERO)
    if matched_qty <= 0:
        return ZERO

    line_total_sum = sum((l.line_total for l in all_lines), ZERO)
    sale_revenue = (sale.taxable_value or ZERO) + (sale.exempt_value or ZERO)
    scale = (sale_revenue / line_total_sum) if line_total_sum != 0 else ZERO
    return (matched_total * scale) / matched_qty


def _fifo_pool_order(product):
    """[''] (unbatched stock, always consumed first) + every CropBatch
    sharing this product, ordered by start_date then code for a
    deterministic tie-break."""
    batch_codes = list(
        CropBatch.objects.filter(product=product).order_by('start_date', 'code').values_list('code', flat=True)
    )
    return [''] + batch_codes


def _fifo_simulate(product, pool_order):
    """Walks every harvest/sale/waste InventoryMovement for `product` in
    chronological order, maintaining a running stock balance per pool
    (pool key = batch_code, '' for unbatched). Returns four {pool_key:
    Decimal} dicts: remaining balance, kg sold, kg wasted, revenue."""
    pools = {code: ZERO for code in pool_order}
    sold_by_pool = {code: ZERO for code in pool_order}
    waste_by_pool = {code: ZERO for code in pool_order}
    revenue_by_pool = {code: ZERO for code in pool_order}

    movements = (
        InventoryMovement.objects
        .filter(product=product, movement_type__in=('harvest',) + CONSUMING_TYPES)
        .select_related('related_pos_sale')
        .prefetch_related('related_pos_sale__lines')
        .order_by('created_at', 'id')
    )
    for m in movements:
        if m.movement_type == 'harvest':
            pool_key = m.batch_code or ''
            if pool_key in pools:
                pools[pool_key] += m.quantity
            continue

        remaining = m.quantity
        rate = _movement_revenue_rate(m) if m.movement_type == 'sale' else ZERO
        for pool_key in pool_order:
            if remaining <= 0:
                break
            available = pools[pool_key]
            if available <= 0:
                continue
            take = min(available, remaining)
            pools[pool_key] -= take
            remaining -= take
            if m.movement_type == 'sale':
                sold_by_pool[pool_key] += take
                revenue_by_pool[pool_key] += take * rate
            else:
                waste_by_pool[pool_key] += take
        # Any `remaining` left over here means this product's ledger has
        # somehow sold/wasted more than was ever harvested -- shouldn't be
        # reachable (InventoryMovement.clean() already guards against
        # negative stock), but this is a read-only report, so the excess
        # is dropped rather than raised.

    return pools, sold_by_pool, waste_by_pool, revenue_by_pool


def build_batch_report(code):
    """code: a CropBatch.code string. Returns a dict (every monetary/kg
    value a Decimal, or None where genuinely undefined -- e.g. cost_per_kg
    with zero harvested_kg). Raises BatchReportError for an unknown code."""
    try:
        batch = CropBatch.objects.select_related('product').get(code=code)
    except CropBatch.DoesNotExist:
        raise BatchReportError(f'No batch with code {code!r}.')

    cost_by_category = defaultdict(lambda: ZERO)
    for entry in CostEntry.objects.filter(batch_code=code):
        cost_by_category[entry.category] += entry.amount
    total_cost = sum(cost_by_category.values(), ZERO)

    harvested_kg = InventoryMovement.objects.filter(
        batch_code=code, movement_type='harvest',
    ).aggregate(total=Sum('quantity'))['total'] or ZERO

    cost_per_kg = _q2(total_cost / harvested_kg) if harvested_kg > 0 else None

    notes = [
        'Cost is DIRECT COSTS ONLY (CostEntry rows tagged with this batch_code) -- shared '
        'farm costs and depreciation are not included here; see the Monthly P&L tab for those.',
        "sold_kg/waste_kg are a modelled FIFO attribution, never stored: stock with no "
        "batch_code (harvested before batch tracking started for this crop) is consumed "
        "first, then each of this product's batches in order of its own start_date. "
        "Website-channel sales consume stock but contribute no revenue (ProductOrder has "
        "no stored price, same exclusion as the Monthly P&L).",
    ]

    sold_kg = waste_kg = remaining_kg = revenue = None
    if batch.product_id:
        pool_order = _fifo_pool_order(batch.product)
        pools, sold_by_pool, waste_by_pool, revenue_by_pool = _fifo_simulate(batch.product, pool_order)
        sold_kg = sold_by_pool.get(code, ZERO)
        waste_kg = waste_by_pool.get(code, ZERO)
        remaining_kg = pools.get(code, ZERO)
        revenue = _q2(revenue_by_pool.get(code, ZERO))
    else:
        notes.append(
            'This batch has no product mapped (see Admin -> Cost Centre Products), so '
            'sold/waste/remaining quantities and revenue cannot be attributed -- only cost '
            'and harvested_kg are available.'
        )

    cost_of_sold = _q2(sold_kg * cost_per_kg) if (cost_per_kg is not None and sold_kg is not None) else None
    waste_loss = _q2(waste_kg * cost_per_kg) if (cost_per_kg is not None and waste_kg is not None) else None
    remaining_value = _q2(remaining_kg * cost_per_kg) if (cost_per_kg is not None and remaining_kg is not None) else None

    profit_to_date = None
    if revenue is not None and cost_of_sold is not None and waste_loss is not None:
        profit_to_date = _q2(revenue - cost_of_sold - waste_loss)

    avg_sale_price_per_kg = _q2(revenue / sold_kg) if (revenue is not None and sold_kg) else None

    return {
        'batch': {
            'code': batch.code, 'name': batch.name, 'cost_centre': batch.cost_centre,
            'product': batch.product.name if batch.product_id else None,
            'status': batch.status, 'start_date': batch.start_date, 'end_date': batch.end_date,
        },
        'cost': {
            'total': _q2(total_cost),
            'by_category': {cat: _q2(amt) for cat, amt in cost_by_category.items()},
        },
        'harvested_kg': harvested_kg,
        'cost_per_kg': cost_per_kg,
        'sold_kg': sold_kg,
        'waste_kg': waste_kg,
        'remaining_kg': remaining_kg,
        'revenue': revenue,
        'cost_of_sold': cost_of_sold,
        'waste_loss': waste_loss,
        'remaining_value': remaining_value,
        'profit_to_date': profit_to_date,
        'break_even_price': cost_per_kg,
        'avg_sale_price_per_kg': avg_sale_price_per_kg,
        'notes': notes,
    }


def list_batches():
    """GET /api/v1/reports/batches/ -- summary fields only (code, name,
    status, total_cost, harvested_kg, cost_per_kg, revenue,
    profit_to_date) for every batch, newest start_date first. Reuses
    build_batch_report() per batch rather than a separate lighter query
    path -- revenue/profit_to_date can't be known without running the
    same FIFO walk anyway, and batch counts are small enough (one crop's
    worth of open/recent seasons at a time) that this isn't worth a
    second code path to keep in sync."""
    rows = []
    for batch in CropBatch.objects.order_by('-start_date', 'code'):
        report = build_batch_report(batch.code)
        rows.append({
            'code': report['batch']['code'], 'name': report['batch']['name'],
            'status': report['batch']['status'], 'total_cost': report['cost']['total'],
            'harvested_kg': report['harvested_kg'], 'cost_per_kg': report['cost_per_kg'],
            'revenue': report['revenue'], 'profit_to_date': report['profit_to_date'],
        })
    return rows


def next_batch_cost_per_kg(product):
    """The cost_per_kg of the oldest CropBatch (open or closed) for this
    product that still has unsold stock -- i.e. whichever batch
    _fifo_simulate() would actually draw from next. None when this
    product has no batch at all, every batch is fully consumed, or the
    next batch in line has 0 harvested_kg (cost_per_kg undefined).

    Used by the POS's below-direct-cost warning (templates/pos.html, via
    shop/views.py's pos_view) -- deliberately calls build_batch_report()
    rather than re-deriving cost_per_kg a second way, so there is only
    ever one place that formula lives."""
    pool_order = _fifo_pool_order(product)
    batch_codes = [code for code in pool_order if code]  # drop '' (unbatched stock)
    if not batch_codes:
        return None
    pools, _, _, _ = _fifo_simulate(product, pool_order)
    for code in batch_codes:  # already start_date-ordered by _fifo_pool_order
        if pools.get(code, ZERO) > 0:
            return build_batch_report(code)['cost_per_kg']
    return None
