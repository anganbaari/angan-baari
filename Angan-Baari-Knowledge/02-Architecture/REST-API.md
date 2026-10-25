# REST API

This project has **two** legitimate REST API boundaries, both for the same reason: a genuinely separate hosted system with no other way to reach this database.

1. **ABMS ↔ Django**, at `/api/inventory/movements/` (token-authed, CORS-allowed for `https://angan-baari.web.app`). Full detail in [[ABMS-Integration]].
2. **The Next.js storefront on Vercel ↔ Django**, through the versioned `/api/v1/` API (`api/` app). Full detail in [[Storefront-and-Vercel]].

**There is no REST API layer between POS and the website.** They're the same Django project, same database, same process — don't propose one. `/api/v1/` does carry some POS/dashboard/reports endpoints too (session-authed, staff-only), but that's additive infrastructure sitting alongside the shared-process POS, not a replacement boundary — see [[Architecture-Decisions]].

**Pricing/orders endpoints the storefront relies on:** `GET /api/v1/offers/` (public), `POST /api/v1/cart/quote/` (public, token optional), `POST /api/v1/orders/` (guest or token; re-prices server-side, returns `pricing_summary` and an `errors` list) and `POST /api/v1/profile/orders/<id>/hide/` (token). The products API also exposes `selling_units`, `base_unit_label`, `min_quantity`, `step_quantity`. See [[Offers-and-Cart-Quote]], [[Orders-and-Profile]], [[Selling-Units]].
