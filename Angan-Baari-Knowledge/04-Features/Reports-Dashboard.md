# Reports & Dashboard

Staff-only (owner/manager role, not every staff login) read-only reporting
screen at `/dashboard/`. Built in one pass; see [[Current-State]] for
whether it has actually reached PythonAnywhere yet.

## Where everything lives

- `shop/reports.py` — all aggregation logic. The ONLY place any dashboard
  number is computed. Deliberately read-only: nothing in this module
  writes to `POSSale`/`ProductOrder`/`CreditTransaction`/`InventoryMovement`.
- `api/views.py` — `ReportSummaryView`, `ReportSalesTrendView`,
  `ReportPaymentsView`, `ReportProductsView`, `ReportCreditView`,
  `ReportInventoryView`, `ReportOrdersView`, all under `/api/v1/reports/`,
  GET-only, gated by the new `api.permissions.IsOwnerOrManager`.
- `shop/views.py::dashboard_view` — the page itself, `@staff_member_required`
  + a second `_is_owner_or_manager()` check (a cashier-role staff login can
  use the POS but gets a clear 403 here, not a broken page).
- `templates/dashboard.html` + `static/css/dashboard.css` +
  `static/js/dashboard.js` — server-rendered shell, vanilla JS (no React,
  no build step — see [[Architecture-Decisions]]), Chart.js vendored at
  `static/vendor/chart.min.js` (NOT a CDN — see [[Frontend-Lessons]] for
  why a CDN dependency was rejected here specifically).
- `api/tests_reports.py` — permissions, reconciliation, timezone-boundary,
  compare-math, granularity, invalid-range, CSV+BOM, empty-DB, and
  query-count tests. Kept separate from the already-large `api/tests.py`.

## Why it's deliberately NOT inside `/pos/`

The POS has its own service worker (`static/js/pos-sw.js`, scope `/pos/`
only — see [[POS]]) that caches the POS page for offline use. The
dashboard must never be served stale or offline — a manager making a
decision off this screen needs to know it's live data — so it lives
entirely outside that scope by construction, has no manifest, registers
no service worker, and just shows a plain "needs a connection" message on
a fetch failure.

## The permission model: `UserProfile.role`'s first real use

`UserProfile.role` (admin/manager/cashier, see [[POS]] Phase A) existed
since the PIN-unlock work but was never actually checked by anything —
every POS permission so far has only ever cared about `is_staff`. This
dashboard is the first thing in the codebase that branches on the role
value itself, via `api.permissions.IsOwnerOrManager` (API side) and
`shop.views._is_owner_or_manager()` (page-shell side — deliberately a
small duplicate rather than an import, since `shop/` has never depended on
`api/` and this is one line). A superuser always passes, same as Django's
own convention, so `createsuperuser` accounts aren't locked out for
lacking a `UserProfile` row.

## The metric definitions that matter most

**Revenue is POS-only.** `ProductOrder` (website orders) has no
total/price field at all, and its `cart_snapshot` deliberately omits price
too (see its own help_text — a reorder should re-price at today's rate).
There is no reliable Rs figure for an online order's value anywhere in the
schema. Online orders are shown as a **count** everywhere on this
dashboard (channel split, alerts, the Online Orders tab), never blended
into a revenue total. **This was a locked-in design decision**, confirmed
with the project owner during the Step-0 audit before any dashboard code
was written — not a workaround discovered later.

**Per-product/category revenue is an ESTIMATE.** Neither `POSSale` nor
`ProductOrder` stores a price per cart line — only the sale-level total is
real (`create_pos_sale()` never writes `unit_price`/`line_total` into
`cart_snapshot`, confirmed by reading its actual code, not its docstring).
"Top products by revenue" and "sales by category" price every historical
line at TODAY's rate (`Product.price` / `ProductVariant.total_price()` /
the matching `ProductSellingUnit.price`), which silently overstates
anything that was actually offer/combo-discounted at sale time (the
discount itself isn't preserved — only `offer_id`/`combo_instance_id`
are) and is wrong for anything sold before a later price change. Always
returned with an implicit "(est.)" label client-side; the QUANTITY-based
version of the same breakdown is exact and is what reconciliation tests
check against. **This was the second locked-in decision** from the same
audit conversation.

**Cash collected ≠ revenue.** Cash collected = non-credit POS payment
portions in the period + credit repayments *received* in the period.
Revenue recognizes a credit sale immediately; cash collected only counts
it once it's actually repaid. Conflating these two was the single easiest
way to get this dashboard subtly wrong, so the help panel on the page
spells out the distinction directly for staff reading it.

**No profit/margin anywhere.** `Product` has no cost/purchase-price field
(checked against the full model, not assumed) — there's nothing to
subtract revenue from, so this dashboard doesn't compute one. If that data
starts getting tracked later, this is the gap to revisit.

**Offer discounts aren't tracked as a number** (only that an offer was
used, from `offer_id` appearing in `cart_snapshot`); **coupon discounts
are exact** (`POSSale.discount_amount` is a real stored field). The
Products tab's offers/coupons table reflects this asymmetry directly
rather than hiding it.

## Timezone handling

`settings.TIME_ZONE = 'Asia/Kathmandu'` (UTC+5:45), `USE_TZ = True`. Every
day-boundary calculation in `shop/reports.py` builds its UTC range
explicitly from `zoneinfo.ZoneInfo('Asia/Kathmandu')`
(`local_range_to_utc()`), not from Django's ambient "current timezone"
(which this project never activates per-request) — so a sale at 23:30 or
00:30 local always lands on the correct LOCAL calendar day. Covered by
`TimezoneBoundaryTests` in `api/tests_reports.py`.

## Aggregation strategy / the one deliberate exception to "no per-row loops"

Sale-level numbers (totals, counts, time-series buckets, payment mix,
credit, inventory stock) all use real `Sum`/`Count`/`Trunc*` database
aggregation. Per-LINE numbers (units sold by product, category breakdown,
offer/combo usage) cannot be computed that way — `cart_snapshot` is a JSON
blob, not a related table, so there's no SQL join to aggregate across.
Those are computed with exactly ONE bulk query
(`.values_list('id', 'cart_snapshot')` across the whole date range),
then an in-memory Python loop — not one query per row, and bounded by how
many sales a small farm POS realistically logs in a year, not by total
table size. See `_iter_pos_cart_lines()` in `shop/reports.py`.

**A real N+1 was caught and fixed by this module's own tests**
(`QueryCountTests` in `api/tests_reports.py`): `_line_estimated_price()`
originally called `product.selling_units.all()` once per cart LINE
(needed for `fixed_quantity` products priced by named unit, e.g. jar
sizes) with no prefetch, so a popular product appearing in many lines
across a date range triggered one query per line. Fixed by adding
`.prefetch_related('selling_units')` to `_product_lookup()`. Worth
remembering if this module is extended: anything that reads a related
manager (`.variants.all()`, `.selling_units.all()`, etc.) inside a
per-line loop needs the same prefetch treatment.

## Caching

None, deliberately. A short cache was allowed by the brief ONLY if it
couldn't show a stale credit balance right after a repayment — correctly
invalidating one would mean touching the POS/credit write paths
(`create_pos_sale()`, `CreditRepayView`), which this feature keeps
strictly read-only. Every endpoint computes fresh on every request.

## Channel filter scope

The `channel` param (all/pos/online) only meaningfully affects the
**Overview** tab's KPI cards (which include both POS revenue and the
online order count) and the **Online Orders** tab (already online-only by
nature). The **Sales** and **Products** tabs are POS-only regardless of
the channel filter, since there's no online revenue to show — this is
stated directly in the dashboard's own help panel, not a silent gap.

## Known limitation to flag if this is ever extended

`get_product_table()`/`get_top_products()`/`get_category_breakdown()`
each independently call `_product_aggregates()` for the Products tab,
re-running the same bulk cart-line fetch three times per request. Still a
small, fixed number of queries (not an N+1 — doesn't scale with sale
count, confirmed by `QueryCountTests`), just not maximally efficient. Left
as-is rather than refactored into a shared single pass, given this
dashboard's realistic query volume (staff-only, low request rate) — worth
revisiting only if this page ever needs to serve a much higher request
rate.
