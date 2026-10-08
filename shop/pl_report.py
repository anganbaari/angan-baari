"""Profit & Loss report -- the staff Reports dashboard's "Profit & Loss"
tab (templates/dashboard.html, api/views.py's ReportPLView,
GET /api/v1/reports/pl/).

build_pl(date_from, date_to) is a pure, read-only function: it only ever
reads POSSale/POSSaleLine/InventoryMovement/CostEntry/FarmAsset/
CostCentreProduct and returns a dict. Nothing here writes to the POS,
checkout, stock, or cost ledger. date_from/date_to are plain A.D. dates
(inclusive); converting a B.S. month/fiscal year/custom B.S. range into
this AD pair is the API view's job (see shop/bs_calendar.py), not this
module's -- build_pl() itself has no B.S. awareness at all, so tests can
call it directly with plain dates.

Decimal throughout, never float -- the API view converts to float only at
the JSON-serialization boundary (same convention shop/reports.py already
uses for every other monetary figure on this dashboard).

WHAT THIS DOES NOT COVER (v1, by the brief's own design):
- Website order (ProductOrder) revenue -- it has no stored price at all
  (confirmed: shop/reports.py's own module docstring says the same thing
  about ProductOrder.cart_snapshot). Orders in the period are reported as
  a plain count in the data-quality panel, never blended into a Rs figure.
- Sales from before POSSaleLine existed have no line detail -- their
  sale-level revenue is still counted in total revenue, but reported
  separately ("Sales before line tracking") since there's no product/
  cost-centre breakdown possible for them.

FORMULA DECISIONS the brief left open (see the final report for the full
list) are called out inline where they matter, prefixed "DECISION:".
"""

from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP

from django.db.models import DecimalField, F, Q, Sum

from . import bs_calendar
from .reports import LOCAL_TZ, ZERO, get_outstanding_credit_total, local_range_to_utc
from .models import (
    CostCentreProduct, CostEntry, CreditTransaction, FarmAsset, InventoryMovement,
    POSSale, POSSalePayment, Product, ProductOrder,
)

MONEY = DecimalField(max_digits=16, decimal_places=4)
CENT = Decimal('0.01')


def _q2(value):
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _sum_qty_times_cost(qs):
    """sum(quantity * unit_cost) over a queryset that already has a
    unit_cost__isnull=False filter applied -- a tiny helper so this one
    F()-expression pattern (also used in InventoryMovement.weighted_
    average_cost(), shop/models.py) isn't retyped five times below."""
    agg = qs.aggregate(total=Sum(F('quantity') * F('unit_cost'), output_field=MONEY))
    return agg['total'] or ZERO


# ─── Depreciation (FarmAsset, straight-line, by B.S. month) ──────────

def _asset_depreciable_bs_months(asset):
    """[(bs_year, bs_month), ...] -- every whole B.S. month this asset
    depreciates in, from its purchase month up to the earlier of (a) the
    end of its life_years*12-month schedule or (b) its disposal month (if
    disposed). A date outside shop/bs_calendar.py's covered range is
    skipped silently (that table only covers "recent/current" years by
    design -- see that module's own docstring) rather than raising and
    breaking the whole report over one old asset."""
    try:
        start_year, start_month, _ = bs_calendar.ad_to_bs(asset.purchase_date)
    except bs_calendar.BSDateOutOfRangeError:
        return []

    total_months = max(1, int(asset.life_years) * 12)
    end_year, end_month = bs_calendar.bs_add_months(start_year, start_month, total_months - 1)

    if asset.status == 'disposed' and asset.disposed_date:
        try:
            disp_year, disp_month, _ = bs_calendar.ad_to_bs(asset.disposed_date)
            if (disp_year, disp_month) < (end_year, end_month):
                end_year, end_month = disp_year, disp_month
        except bs_calendar.BSDateOutOfRangeError:
            pass

    months = []
    y, m = start_year, start_month
    while (y, m) <= (end_year, end_month):
        months.append((y, m))
        if (y, m) == (end_year, end_month):
            break
        y, m = bs_calendar.bs_add_months(y, m, 1)
    return months


def _asset_monthly_depreciation(asset):
    return (asset.cost - (asset.salvage_value or ZERO)) / Decimal(int(asset.life_years) * 12)


def _depreciation_charge_for_period(asset, date_from, date_to):
    """How many of this asset's depreciable B.S. months overlap
    [date_from, date_to] at all, times its monthly charge. DECISION: a
    month "counts" on overlap, not on its start date falling in range --
    every existing dashboard preset (this/last BS month, BS year, fiscal
    year) already aligns to whole B.S. month boundaries, so this only
    matters for a custom range that starts or ends mid-month, where it's
    the more natural reading of "charged for each B.S. month ... inside
    the period"."""
    monthly = _asset_monthly_depreciation(asset)
    count = 0
    for (y, m) in _asset_depreciable_bs_months(asset):
        try:
            month_start = bs_calendar.bs_month_start_ad(y, m)
            month_end = bs_calendar.bs_month_end_ad(y, m)
        except bs_calendar.BSDateOutOfRangeError:
            continue
        if month_start <= date_to and month_end >= date_from:
            count += 1
    return monthly * count


# ─── Shared-cost / shared-depreciation allocation ────────────────────

def _allocate_shared(amount, allocation_rule, allocation_manual, qualifying_centres, by_centre, dq,
                      fallback_key='unallocated'):
    """Adds `amount` into by_centre{centre: Decimal}, per allocation_rule.
    'manual' needs allocation_manual percentages summing to ~100 (0.5pt
    tolerance for rounding); anything else (including a missing/invalid
    rule -- ABMS's own 'equal' radio default, js/costs.js) is treated as
    an equal split across `qualifying_centres`. Falls back to
    dq[fallback_key] ('unallocated' for a shared CostEntry, or
    'unallocated_depreciation' for a shared FarmAsset -- kept separate so
    the two never get mislabelled as the other in the report) and bumps
    the shared-allocation-fallback data-quality counter whenever there's
    nowhere real to put it."""
    if allocation_rule == 'manual' and allocation_manual:
        total_pct = sum((Decimal(str(v)) for v in allocation_manual.values()), ZERO)
        if abs(total_pct - Decimal('100')) > Decimal('0.5'):
            dq[fallback_key] += amount
            dq['shared_allocation_fallback_count'] += 1
            return
        for centre, pct in allocation_manual.items():
            by_centre[centre] += amount * Decimal(str(pct)) / Decimal('100')
        return

    if qualifying_centres:
        share = amount / len(qualifying_centres)
        for centre in qualifying_centres:
            by_centre[centre] += share
    else:
        dq[fallback_key] += amount
        dq['shared_allocation_fallback_count'] += 1


# ─── Main entry point ─────────────────────────────────────────────────

def build_pl(date_from, date_to):
    """date_from/date_to: plain datetime.date, inclusive. Returns a dict
    (see the bottom of this function for the exact shape) -- every
    monetary value is a Decimal."""
    dq = defaultdict(lambda: ZERO)  # a mix of Decimal running totals (unallocated) and plain ints below
    dq['unallocated'] = ZERO
    dq['unallocated_depreciation'] = ZERO
    for key in (
        'website_orders_excluded_count', 'sales_without_lines_count', 'sourced_lines_no_cost_count',
        'waste_no_cost_count', 'unmapped_cost_centre_entries_count', 'farm_products_no_mapping_count',
        'shared_allocation_fallback_count', 'dangling_reversal_count',
    ):
        dq[key] = 0

    start_utc, end_utc = local_range_to_utc(date_from, date_to)

    # ── 1. Revenue (POS only) ────────────────────────────────────────
    sales_qs = POSSale.objects.filter(created_at__gte=start_utc, created_at__lt=end_utc)
    sale_ids = list(sales_qs.values_list('id', flat=True))

    revenue_agg = sales_qs.aggregate(
        retail=Sum(F('taxable_value') + F('exempt_value'), filter=Q(is_wholesale=False), output_field=MONEY),
        wholesale=Sum(F('taxable_value') + F('exempt_value'), filter=Q(is_wholesale=True), output_field=MONEY),
    )
    retail_revenue = revenue_agg['retail'] or ZERO
    wholesale_revenue = revenue_agg['wholesale'] or ZERO
    total_revenue = retail_revenue + wholesale_revenue

    payment_agg = POSSalePayment.objects.filter(sale_id__in=sale_ids).aggregate(
        cash_like=Sum('amount', filter=~Q(method='credit')),
        credit_given=Sum('amount', filter=Q(method='credit')),
    )
    cash_like = payment_agg['cash_like'] or ZERO
    credit_given = payment_agg['credit_given'] or ZERO
    repayments = CreditTransaction.objects.filter(
        transaction_type='repayment', created_at__gte=start_utc, created_at__lt=end_utc,
    ).aggregate(total=Sum('amount'))['total'] or ZERO
    cash_collected = cash_like + repayments
    credit_outstanding_now = get_outstanding_credit_total()

    # Website orders -- revenue excluded entirely (no stored price), just
    # counted for the data-quality panel.
    dq['website_orders_excluded_count'] = ProductOrder.objects.filter(
        ordered_at__gte=start_utc, ordered_at__lt=end_utc,
    ).count()

    # ── 2 & 6 (per-product/centre revenue): walk every sale's lines,
    # spreading the sale-level coupon discount pro-rata so line revenue
    # adds up to the sale's own (taxable_value + exempt_value). ────────
    product_to_centres = defaultdict(list)
    for ccp in CostCentreProduct.objects.all():
        product_to_centres[ccp.product_id].append(ccp.cost_centre)
    mapped_centres = {c for centres in product_to_centres.values() for c in centres}

    products_by_id = {p.id: p for p in Product.objects.all()}

    revenue_by_centre = defaultdict(lambda: ZERO)
    sourced_revenue = ZERO
    unmapped_farm_revenue = ZERO
    unmapped_farm_product_ids = set()
    sales_without_lines_revenue = ZERO

    cost_of_sales = ZERO
    sourced_lines_no_cost_count = 0

    sales = list(sales_qs.prefetch_related('lines'))
    for sale in sales:
        lines = list(sale.lines.all())
        sale_revenue = (sale.taxable_value or ZERO) + (sale.exempt_value or ZERO)
        if not lines:
            dq['sales_without_lines_count'] += 1
            sales_without_lines_revenue += sale_revenue
            continue
        line_total_sum = sum((l.line_total for l in lines), ZERO)
        scale = (sale_revenue / line_total_sum) if line_total_sum != 0 else ZERO
        for line in lines:
            line_revenue = _q2(line.line_total * scale)
            product = products_by_id.get(line.product_id)
            if product is None:
                continue  # Product is on_delete=PROTECT everywhere that matters; defensive only

            if product.origin == 'sourced':
                sourced_revenue += line_revenue
                if line.unit_cost is not None:
                    cost_of_sales += line.quantity * line.unit_cost
                else:
                    sourced_lines_no_cost_count += 1
            else:
                centres = product_to_centres.get(line.product_id)
                if centres:
                    for centre in centres:
                        revenue_by_centre[centre] += line_revenue
                else:
                    unmapped_farm_revenue += line_revenue
                    unmapped_farm_product_ids.add(line.product_id)

    dq['sourced_lines_no_cost_count'] = sourced_lines_no_cost_count
    dq['farm_products_no_mapping_count'] = len(unmapped_farm_product_ids)
    cost_of_sales = _q2(cost_of_sales)

    # ── 3. Wastage loss + stock count adjustments ───────────────────
    waste_qs = InventoryMovement.objects.filter(
        created_at__gte=start_utc, created_at__lt=end_utc, movement_type='waste',
    )
    wastage_loss = _q2(_sum_qty_times_cost(waste_qs.filter(unit_cost__isnull=False)))
    # DECISION: only a SOURCED product's waste row with no unit_cost is a
    # real data-quality problem -- a farm product's waste movement NEVER
    # has a unit_cost (it's only ever auto-filled for origin='sourced',
    # see InventoryMovement.save()), so counting every one of those would
    # just be constant noise, not a genuine gap.
    dq['waste_no_cost_count'] = waste_qs.filter(unit_cost__isnull=True, product__origin='sourced').count()

    adjustment_qs = InventoryMovement.objects.filter(
        created_at__gte=start_utc, created_at__lt=end_utc, movement_type='adjustment_remove',
    )
    stock_adjustments = _q2(_sum_qty_times_cost(adjustment_qs.filter(unit_cost__isnull=False)))

    # ── 4. Farm costs by cost centre (CostEntry) ────────────────────
    cost_entries = list(CostEntry.objects.filter(date__gte=date_from, date__lte=date_to))

    direct_by_centre_category = defaultdict(lambda: defaultdict(lambda: ZERO))
    shared_entries = []
    for entry in cost_entries:
        if entry.is_shared or entry.cost_centre == 'shared':
            shared_entries.append(entry)
        else:
            direct_by_centre_category[entry.cost_centre][entry.category] += entry.amount
            if entry.cost_centre not in mapped_centres:
                dq['unmapped_cost_centre_entries_count'] += 1

    direct_cost_by_centre = {
        centre: sum(cats.values(), ZERO) for centre, cats in direct_by_centre_category.items()
    }

    qualifying_centres = sorted(
        c for c in mapped_centres
        if (revenue_by_centre.get(c, ZERO) > 0) or (direct_cost_by_centre.get(c, ZERO) > 0)
    )

    shared_allocated_by_centre = defaultdict(lambda: ZERO)
    for entry in shared_entries:
        _allocate_shared(
            entry.amount, entry.allocation_rule, entry.allocation_manual, qualifying_centres,
            shared_allocated_by_centre, dq,
        )

    # Dangling reversals -- a reversal in THIS period whose reversal_of
    # doesn't match any real CostEntry (the original can legitimately be
    # from an earlier period, so this checks all-time, not just the period).
    reversal_ids = [e.reversal_of for e in cost_entries if e.entry_type == 'reversal' and e.reversal_of]
    if reversal_ids:
        existing_abms_ids = set(CostEntry.objects.filter(abms_id__in=reversal_ids).values_list('abms_id', flat=True))
        dq['dangling_reversal_count'] = sum(1 for rid in reversal_ids if rid not in existing_abms_ids)

    # ── 5. Depreciation (FarmAsset) ──────────────────────────────────
    depreciation_by_centre = defaultdict(lambda: ZERO)
    for asset in FarmAsset.objects.all():
        charge = _depreciation_charge_for_period(asset, date_from, date_to)
        if charge == 0:
            continue
        if asset.cost_centre == 'shared':
            _allocate_shared(charge, 'equal', None, qualifying_centres, depreciation_by_centre, dq,
                              fallback_key='unallocated_depreciation')
        else:
            depreciation_by_centre[asset.cost_centre] += charge

    # ── Per-centre profit table ───────────────────────────────────────
    all_centres = sorted(
        set(revenue_by_centre) | set(direct_cost_by_centre)
        | set(shared_allocated_by_centre) | set(depreciation_by_centre)
    )
    centre_rows = []
    for centre in all_centres:
        rev = _q2(revenue_by_centre.get(centre, ZERO))
        direct = _q2(direct_cost_by_centre.get(centre, ZERO))
        shared = _q2(shared_allocated_by_centre.get(centre, ZERO))
        dep = _q2(depreciation_by_centre.get(centre, ZERO))
        profit = rev - direct - shared - dep
        margin_pct = _q2(profit / rev * 100) if rev else None
        centre_rows.append({
            'cost_centre': centre,
            'revenue': rev, 'direct_cost': direct, 'allocated_shared_cost': shared,
            'depreciation': dep, 'profit': profit, 'margin_pct': margin_pct,
            'categories': {cat: _q2(amt) for cat, amt in direct_by_centre_category.get(centre, {}).items()},
        })

    total_direct_cost = _q2(sum(direct_cost_by_centre.values(), ZERO))
    total_shared_allocated = _q2(sum(shared_allocated_by_centre.values(), ZERO))
    total_depreciation = _q2(sum(depreciation_by_centre.values(), ZERO))
    unallocated_overhead = _q2(dq['unallocated'])
    unallocated_depreciation = _q2(dq['unallocated_depreciation'])
    total_farm_costs = total_direct_cost + total_shared_allocated + unallocated_overhead

    gross_profit_sourced = _q2(sourced_revenue - cost_of_sales - wastage_loss)

    net_profit = _q2(
        total_revenue - cost_of_sales - wastage_loss - stock_adjustments
        - total_farm_costs - total_depreciation - unallocated_depreciation
    )

    has_data_quality_issues = bool(
        dq['website_orders_excluded_count'] or dq['sales_without_lines_count']
        or dq['sourced_lines_no_cost_count'] or dq['waste_no_cost_count']
        or dq['unmapped_cost_centre_entries_count'] or dq['farm_products_no_mapping_count']
        or dq['shared_allocation_fallback_count'] or dq['dangling_reversal_count']
    )

    return {
        'period': {'from': date_from, 'to': date_to},
        'revenue': {
            'retail': _q2(retail_revenue), 'wholesale': _q2(wholesale_revenue), 'total': _q2(total_revenue),
            'sourced_products': _q2(sourced_revenue),
            'unmapped_farm_products': _q2(unmapped_farm_revenue),
            'sales_before_line_tracking': _q2(sales_without_lines_revenue),
        },
        'cash_vs_credit': {
            'cash_collected': _q2(cash_collected), 'credit_given': _q2(credit_given),
            'credit_outstanding_now': credit_outstanding_now,
        },
        'cost_of_sales': cost_of_sales,
        'wastage_loss': wastage_loss,
        'stock_adjustments': stock_adjustments,
        'gross_profit_sourced': gross_profit_sourced,
        'farm_cost_centres': centre_rows,
        'farm_costs_total': {
            'direct': total_direct_cost, 'shared_allocated': total_shared_allocated,
            'unallocated_overhead': unallocated_overhead, 'total': total_farm_costs,
        },
        'depreciation': {
            'by_centre': {c: _q2(v) for c, v in depreciation_by_centre.items()},
            'unallocated': unallocated_depreciation,
            'total': total_depreciation + unallocated_depreciation,
        },
        'net_profit': net_profit,
        'data_quality': {
            'website_orders_excluded': dq['website_orders_excluded_count'],
            'sales_without_line_data': dq['sales_without_lines_count'],
            'sourced_lines_no_cost': dq['sourced_lines_no_cost_count'],
            'waste_no_cost': dq['waste_no_cost_count'],
            'unmapped_cost_centre_entries': dq['unmapped_cost_centre_entries_count'],
            'farm_products_no_mapping': dq['farm_products_no_mapping_count'],
            'shared_allocation_fallback': dq['shared_allocation_fallback_count'],
            'dangling_reversals': dq['dangling_reversal_count'],
            'has_issues': has_data_quality_issues,
        },
        'wastage_detail': _waste_detail(start_utc, end_utc),
    }


def _waste_detail(start_utc, end_utc):
    """One row per product with a 'waste' movement in the period, value
    computed only from rows that have a unit_cost (see dq['waste_no_cost']
    for the rows this excludes)."""
    rows = (
        InventoryMovement.objects.filter(
            created_at__gte=start_utc, created_at__lt=end_utc, movement_type='waste',
        )
        .values('product__name')
        .annotate(
            total_quantity=Sum('quantity'),
            value=Sum(F('quantity') * F('unit_cost'), filter=Q(unit_cost__isnull=False), output_field=MONEY),
        )
        .order_by('-value')
    )
    return [
        {'product': r['product__name'], 'quantity': r['total_quantity'], 'value': _q2(r['value'] or ZERO)}
        for r in rows
    ]
