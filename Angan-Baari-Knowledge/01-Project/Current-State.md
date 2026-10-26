# Current State

*Living document — update as work lands. Last checked: 2026-10-25.*

Older dated entries describing individual POS features as they landed
have been superseded by this snapshot; see git log / `09-Session-Notes/`
for that history if needed. This doc answers one question: **what is
actually true right now**, across the four systems that make up Angan
Baari.

## 1. The Django project

Still the e-commerce site + admin + POS, on PythonAnywhere, SQLite
(deliberately — Postgres/Supabase re-evaluated and rejected again on
2026-10-23, see [[Decision-Log]]). Latest migration:
`0051_productorder_hidden_by_customer_productorder_user` (adds
`ProductOrder.user` and `hidden_by_customer` — see [[Orders-and-Profile]]).
Full test suite: **643 passing** as of this writing — re-run rather than
trust this number once it ages. POS, refunds, and the Reports dashboard
are all unchanged by the recent API work below; that work was explicitly
additive throughout.

## 2. The Vercel shop

Live, not just planned — a separate Next.js project (not in this repo) at
`https://angan-baari-shop.vercel.app`, reading this project only through
`/api/v1/`. Django-side links to it switch on via `STOREFRONT_URL` +
`{% storefront_link %}` (links only, no redirects) — see
[[Storefront-and-Vercel]]. `'offers'` was added to that tag's mapping once
the storefront had a real public offers endpoint to build its own
`/offers` page from.

## 3. The API

`/api/v1/` now carries real pricing and account logic, not just reads:

- `GET /api/v1/offers/` (public) and `POST /api/v1/cart/quote/` (public,
  token auth optional) — live, non-combo offers price automatically for
  logged-in customers only; combo bundles stay WhatsApp-hand-off/display
  only. See [[Offers-and-Cart-Quote]].
- `POST /api/v1/orders/` re-prices every line server-side (never trusts a
  client price), links the order to the authenticated account via
  `ProductOrder.user` (never by email — this project has no
  email-verification step), and returns `pricing_summary` +  a per-line
  `errors` list. `GET /api/v1/profile/orders/` matches by that same
  `user` FK and excludes anything hidden via
  `POST .../profile/orders/<id>/hide/`. See [[Orders-and-Profile]].
- Selling units (`ProductSellingUnit`) are now fully priced through the
  API — `selling_unit_id` on a cart/order line prices by the unit's own
  independent price and deducts `quantity_in_base_units` from stock,
  reusing `price_pos_cart_line()`/`create_inventory_movements_from_
  snapshot()` unchanged. See [[Selling-Units]].
- Cancelling an order now reliably returns stock from **any** path —
  profile-page cancel, the emailed cancel-token link, or a plain status
  change in Django admin (previously the only gap: admin did nothing) —
  via one idempotent helper + a `post_save` signal, safe against being
  cancelled through more than one path. Re-activating a cancelled order
  does **not** re-deduct stock (reported, not yet fixed — see
  [[Orders-and-Profile]]).

## 4. Backups

No scheduled-task feature on PythonAnywhere's free tier, so
`OnDemandBackupMiddleware` (`shop/middleware.py`) runs `backup_db` once,
synchronously, from inside the first authenticated-staff GET to `/pos/`
or `/admin/` after the newest backup turns 20 hours old. Means the
backup depends on a staff member actually opening one of those pages
daily. Off-server copy is still a manual monthly habit, not automated.
See [[Backups]].

## Not yet true

Anything in [[Roadmap]] is explicitly **not built**. Don't assume
deferred work has been picked up without checking the actual code first.
