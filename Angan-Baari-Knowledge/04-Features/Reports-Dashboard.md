# Reports & Dashboard

Staff-only (owner/manager role, not every staff login) read-only reporting
screen at `/dashboard/`. Built in one pass, then visually/structurally
redesigned in a second pass (2026-10-07) into a dark-themed, sidebar-nav,
B.S.-only layout with a Revenue Target card, sale-level filters, and a
dedicated Alerts section. See [[Current-State]] for whether either phase
has actually reached PythonAnywhere yet.

## Where everything lives

- `shop/reports.py` — all aggregation logic. The ONLY place any dashboard
  number is computed. Deliberately read-only: nothing in this module
  writes to `POSSale`/`ProductOrder`/`CreditTransaction`/`InventoryMovement`/
  `RevenueTarget`.
- `shop/bs_calendar.py` — Bikram Sambat ↔ A.D. conversion, unit-tested in
  `shop/tests_bs_calendar.py`. See "Bikram Sambat only" below.
- `shop/models.py::RevenueTarget` — new model (migration `0037`), admin
  dates in, B.S. display out. See "Revenue Target" below.
- `api/views.py` — `ReportSummaryView`, `ReportSalesTrendView`,
  `ReportPaymentsView`, `ReportProductsView`, `ReportCreditView`,
  `ReportInventoryView`, `ReportOrdersView`, `ReportAlertsView`,
  `ReportTargetView`, `ReportFilterOptionsView`, all under
  `/api/v1/reports/`, GET-only, gated by `api.permissions.IsOwnerOrManager`
  (`ReportAlertsView`/`ReportTargetView` aren't date-range-scoped at all —
  both are "right now" states, not period aggregates).
- `shop/views.py::dashboard_view` — the page itself, `@staff_member_required`
  + a second `_is_owner_or_manager()` check (a cashier-role staff login can
  use the POS but gets a clear 403 here, not a broken page).
- `templates/dashboard.html` + `static/css/dashboard.css` +
  `static/js/dashboard.js` + `static/js/bs-calendar.js` — server-rendered
  shell, vanilla JS (no React, no build step — see
  [[Architecture-Decisions]]), Chart.js vendored at
  `static/vendor/chart.min.js` (NOT a CDN — see [[Frontend-Lessons]] for
  why a CDN dependency was rejected here specifically).
- `api/tests_reports.py` — permissions, reconciliation, timezone-boundary,
  compare-math, granularity (incl. B.S.-month bucketing), invalid-range,
  CSV+BOM, empty-DB, filters, alerts, target-progress, and query-count
  tests. Kept separate from the already-large `api/tests.py`.

## Layout (the 2026-10-07 redesign)

Dark theme by default, built from the site's own brand tokens (not copied
from any reference dashboard's palette) — print switches to a light,
ink-friendly theme automatically. Fixed left sidebar: brand/logo + an
inline-SVG Nepali flag (hand-drawn double-pennant shape, not the flag
emoji — that doesn't render on Windows, which is why CLAUDE.md's Frontend
conventions already prefer inline SVG over anything font/emoji-based),
icon nav for Overview/Sales/Products/Credit/Inventory/Online Orders/
Alerts plus a disabled "Profit & Loss" item, a B.S. year/month grid
(click a month to set the date range to it), a Revenue Target progress
card, and an Admin-only footer link — deliberately no "Back to POS" link
here (the POS's own Logout-column "Reports" link into this page was left
untouched). Sections are client-side-routed via `location.hash`
(`#sales`, `#products`, …) so refresh and the browser back button work;
each lazy-loads its own endpoint on first visit, same lazy-tab principle
as the original build, just hash-addressable now.

Overview became a dense grid of SMALL widgets (no chart taller than
~220px) rather than a few large ones: an 8-tile KPI strip, a sales trend
with a peak-day marker, a payment-mix doughnut, top-5 products, a
product-comparison table (this period vs previous, quantity exact,
revenue "(est.)"), category mix, a compact top-operators ranking, a
compact alerts preview (links to the full Alerts section), a run-rate
projection card, and a 5-card "period at a glance" insight strip. The
other sections kept their full-detail tables/charts/CSV exports from the
original build, just restyled and B.S.-dated.

## Bikram Sambat only — no A.D. dates anywhere on this page

The brief for the redesign was explicit: every date shown (pickers,
presets, axis labels, "last updated", the target period, CSV date
columns, the print header) is B.S. The API itself is untouched in this
respect — it still takes/returns plain A.D. ISO dates; conversion happens
only at the UI boundary.

The underlying day-length data table (B.S. 2075–2099, originally sourced
for `templates/pos.html`'s inline clock from `remotemerge/
nepali-date-converter`) now exists in **three places**, kept in sync by
hand since there's no shared import between Python and vanilla JS:

1. `templates/pos.html`'s own inline copy (AD→BS only, powers the POS
   clock) — deliberately left untouched rather than refactored to import
   from the new Python module; it's working, tested POS code and outside
   this task's scope.
2. `shop/bs_calendar.py` — the SERVER-side source of truth
   (`ad_to_bs`/`bs_to_ad`/month-length/fiscal-year-start), fully
   unit-tested including month-boundary cases (last day of a B.S. month
   immediately precedes the first day of the next, verified against the
   same reference points `pos.html`'s own comment log already checked
   against hamropatro.com). Used by `shop/reports.py` to bucket daily
   rows into B.S. months when `granularity=bs_month` is requested — there
   is no way to express a B.S. month boundary as a database-level
   `Trunc*`, since B.S. months aren't a fixed offset from A.D. ones, so
   the DB buckets by day and Python regroups.
3. `static/js/bs-calendar.js` — a CLIENT-side mirror, for the date
   picker's instant preset clicks (Today, This BS month, a custom B.S.
   date via three day/month/year selects) without a server round trip.
   Carries no authority of its own — every date it produces is sent to
   the unchanged API as a plain A.D. ISO string, same as before.

If the table is ever extended past B.S. 2099 (around A.D. 2043), update
all three by re-fetching `years.ts` from the same source repo, same
discipline `pos.html`'s own comment already documents.

`granularity` is now `day`/`week`/`bs_month` (plain `month` was removed —
it stopped making sense once every date shown is B.S.).

## Sale-level filters (payment type / customer type / operator)

New in the redesign: `payment_type` (cash/other/credit), `customer_type`
(credit/walkin), `operator` (staff id) thread through
`shop/reports.py`'s `pos_sales_qs()` via a `SaleFilters` dataclass, and
apply to Overview/Sales/Products. They deliberately do **not** apply to
Credit (already IS the credit slice), Inventory (ledger movements aren't
sales and have no payment-method/operator concept), or Online Orders (no
POS payment method or operator exists for a website order at all) — each
of those report responses sets `filters_applicable: false` so the
frontend can show a "doesn't apply here" note rather than silently
ignoring an active filter. `GET /api/v1/reports/filter-options/` lists
only operators who have actually rung up a sale, for the dropdown.

## Alerts section

Promoted from an Overview-only preview strip (the original build) to its
own full section, `GET /api/v1/reports/alerts/` — not date-range-scoped,
since every alert is a "right now" state: low stock, products with no
sale in `ALERT_NO_SALES_DAYS` (14) days (searched over a
`ALERT_NO_SALES_LOOKBACK_DAYS`-day (60) window), online orders pending
longer than `ALERT_ORDER_PENDING_DAYS` (3) days, credit balances above
`ALERT_CREDIT_BALANCE_THRESHOLD` (Rs 5,000), and a Revenue Target
milestone (50/75/100%, via `ALERT_TARGET_MILESTONES`). All five are plain
constants at the top of `shop/reports.py`, not admin-editable by design —
small in number and low-stakes enough that a code edit + redeploy beats
building admin UI for them. Never fabricates an alert; an empty category
is an empty list, and the frontend shows "All clear" when every category
is empty.

## Revenue Target

New `RevenueTarget` model — name, start_date, end_date, amount, is_active
— editable in Django admin with plain A.D. dates (the owner just picks
whatever period they mean: a Nepali fiscal year, a calendar year, a
single month; nothing about the admin FORM itself needs to be B.S.,
only the dashboard's own display of it). `shop/reports.py`'s
`get_active_target()` picks whichever `is_active=True` row's period
actually contains today, else the most recently started active one, so
more than one can exist (e.g. a completed one kept for history) without
the sidebar card picking the wrong one. "Achieved" is POS-sales-only —
same revenue rule as everywhere else on this dashboard, labelled "POS
sales" on the card itself so it's never mistaken for a number that
includes online orders. `GET /api/v1/reports/target/` is not
date-range-scoped either; the target's own period is what matters, not
whatever the dashboard's filter bar currently shows.

## Profit & Loss (sidebar, disabled)

A disabled nav item with a "needs cost data" label and tooltip, not a
built feature — `Product` has no cost/purchase-price field anywhere in
the schema (same gap the original build's docs already noted), so
there's nothing to subtract revenue from. Revisit if that data starts
getting tracked.

## Print: a real gotcha with Chart.js and the redesign's dark theme

Chart.js draws directly to canvas pixels. The print media query's CSS
variable overrides (`static/css/dashboard.css`'s `@media print` block
redefines every `--dash-*` custom property to light-theme values, which
every OTHER component reads its color from) have **zero effect** on
already-rendered chart axis/legend text, because that text was never a
DOM element CSS could reach in the first place. The first version of this
fix only handled the CSS side and shipped with charts printing in pale
light-gray-on-white, essentially unreadable — caught by actually
screenshotting the print preview, not just eyeballing the non-chart
parts. Fixed in `static/js/dashboard.js` by `setChartsPrintMode()`,
called on `window.beforeprint`/`afterprint`: walks every live Chart.js
instance in `window.__charts`, mutates its `options` (legend label color,
tick colors, grid colors) directly, and calls `chart.update('none')` —
no refetch, just a recolor of what's already rendered. Worth remembering
for any FUTURE chart added to this page: it needs the same treatment, or
it'll print unreadable exactly the same way.

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
