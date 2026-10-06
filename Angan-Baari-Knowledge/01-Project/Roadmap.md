# Roadmap / Deferred Work

Known, intentional, not yet built. None of these should be assumed complete without checking the code.

## POS enhancement phases (see [[POS]])

Built: Phase A (staff roles + PIN unlock), Phase B (Customer/credit ledger,
split payments, dormant VAT scaffolding), Phase C (coupon discounts —
reuses the website's `Coupon` model via `resolve_pos_coupon()`, staff
preview at `/api/v1/pos/coupon/validate/`, actual application in
`create_pos_sale()`; `Offer`-style automatic per-product/category
discounts are NOT part of this, website-only for now), Phase D (offers —
single-product + combo, see [[Current-State]]).

**POS-PWA, built** (installable PWA scoped to `/pos/` only — nothing else
on the site is a PWA): Phase 1 (manifest + service worker shell, offline
sale queue via IndexedDB with `client_sale_id` idempotency — see
`static/js/pos-offline-queue.js`), Phase 2 (offline-first cold start: the
whole `/pos/` navigation response is cached network-first so the embedded
product/category JSON survives a cold load with zero connectivity,
`stockConflict` as its own replay outcome distinct from `failed`, a
real connectivity-probe-driven offline banner since `navigator.onLine`
alone can't detect "wifi but no real internet"), and further phases
layering offline credit-customer lookup and offline offers/combos +
coupon gating onto the same queue. Not yet real-device tested — see
[[Current-State]] / `CLAUDE.md`'s POS-PWA section for what's still
CDP-verified-only versus confirmed on an actual phone.

**Reports & Dashboard — built.** Staff-only (owner/manager role) read-only
reporting screen at `/dashboard/`, `/api/v1/reports/*` endpoints,
Chart.js (vendored, not CDN). See [[Reports-Dashboard]] for the full
picture, including the two locked-in scope decisions (POS-only revenue,
estimated per-product revenue) and why there's no profit/margin figure.

Not yet built: receipts & printing, receipt printer/cash drawer hardware
integration, returns & refunds.

- **Local Egg / Vermicompost inventory bridge from ABMS** — hook into ABMS's `productionLog` / `vermiOut`, same pattern as the harvest bridge (see [[ABMS-Integration]]).
- **Goat/Chicken sales bridge** — needs a live variant-picker in ABMS; the operator must pick which specific animal was sold, never auto-pick "cheapest available" (see [[Inventory-Rules]] on `ProductVariant`).
- **React POS screen** — not planned. See [[Architecture-Decisions]] — POS staying vanilla JS is deliberate, not a gap.
- **Mango Pickles inventory modeling** — no movement type yet for "raw mango converted into jars of pickle."
- **Mother Goat, Pathi, Lady Goat (meat), Mother Chicken** — postponed product lines.
- **Postgres migration** — deferred until the POS needs concurrent writes. Don't migrate "helpfully."
- **Live digital scale reading (Web Serial API)** — deferred until a scale is bought and its protocol known.
- **Batch/expiry tracking** — deferred.
- **`profile.html` honeycomb nav** — still has the old pill-style `.nav-links`, hasn't been migrated like the rest of the site (see [[Frontend-Conventions]]).
- **Barcode label printing** — `generate_barcodes` management command + staff-only `/pos/labels/` print view (JsBarcode, Code128, client-side) — drafted but **not confirmed merged**. Check before building on top of either.
