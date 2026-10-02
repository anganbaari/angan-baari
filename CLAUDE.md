# Angan Baari — Project Context for Claude Code

Read this fully before making changes. This file exists so you don't have to
re-derive architecture decisions that were already made deliberately.

## ⚠️ Known bug — fix before anything else

`shop/models.py`, inside `InventoryMovement.clean()`:
```python
from django.ce.exceptions import ValidationError   # WRONG — django.ce doesn't exist
```
Should be `from django.core.exceptions import ValidationError`. As written,
`clean()` throws `ModuleNotFoundError` on every call, which means every POS
sale (`pos_create_sale` always calls `full_clean()`) currently fails with a
generic "could not complete this sale" error, no matter what's in the cart.
Website checkout is unaffected — it never calls `full_clean()`. Fix this
first, it's one line, no migration needed.

## What this is

Angan Baari (आँगन बारी) — organic farm e-commerce site + POS system, for a
real working farm in Bhulka Danda, Rupandehi, Nepal. Sells fruits (mango,
lychee, papaya, banana, jackfruit), honey, pickles, and livestock (live
goats and chickens — no butchered/meat shop). Farm also has beehives, water
harvesting infrastructure, and vermicomposting.

There are **two separate systems** — do not conflate them:

1. **This Django project** — the e-commerce site + admin + POS, hosted on
   PythonAnywhere at `anganbaari.pythonanywhere.com`.
2. **ABMS** (आँगन बारी) — a *separate* standalone farm-management PWA, built
   on Firebase/Firestore, deployed at `angan-baari.web.app`. Different
   codebase, not in this repo. Talks to this Django project only through
   `/api/inventory/movements/` (token-authed, CORS-allowed for
   `https://angan-baari.web.app`).

## Stack

- Django + Django REST Framework, Python
- SQLite locally **and in production** (Postgres migration deliberately
  deferred until the POS needs concurrent writes — don't "helpfully" migrate)
- Hosted on PythonAnywhere
- Server-rendered Django templates only — no React/Next.js in this project
  (considered and explicitly rejected)
- Images: ImageKit CDN (`shop/imagekit_storage.py`, custom Storage backend)
- Email: Resend API (`shop/emails.py`), not Django's SMTP backend
- Admin notifications: Telegram bot
- GitHub for version control. Local dev on Windows in VS Code, `venv`.

## Deployment workflow (do not skip steps)

```
local: python manage.py makemigrations
local: git commit, git push
PythonAnywhere: git pull
PythonAnywhere: python manage.py migrate   # NEVER makemigrations on production
PythonAnywhere: python manage.py collectstatic
PythonAnywhere: Web tab → Reload
```

**Known gotcha:** PythonAnywhere venv lives at `~/angan-baari/venv/` and must
be explicitly activated (`source ~/angan-baari/venv/bin/activate`) before
`pip install` — otherwise pip silently installs into the account's global
`~/.local/lib/python3.13/site-packages/`, which the live WSGI process
doesn't use, so new packages silently never reach the running site.

**Any model field change needs `makemigrations` + `migrate`, locally then on
PythonAnywhere after pulling. Say so explicitly when proposing a model
change — don't assume it happens automatically.**

## Architecture decisions already made (don't relitigate these)

- **No REST API layer between POS and website.** Same Django project, same
  database, same process. Both funnel through the shared helper
  `create_inventory_movements_from_snapshot()` in `views.py`. The *only*
  legitimate REST API boundary in this project is ABMS↔Django, because ABMS
  is a genuinely separate hosted system (Firebase) with no other way to
  reach this database.
- **POS auth is server-side staff login (`@staff_member_required`), not a
  token.** Deliberate — avoids the token-exposure/CORS problem entirely,
  since unlike ABMS the POS has no reason to be hosted separately.
- **POS is vanilla JS served by Django** (`templates/pos.html`), not React,
  not separately hosted.
- **`InventoryMovement` is an append-only ledger** — never edit/delete rows
  directly. `movement_type`: harvest/sale/waste/return/adjustment_add/
  adjustment_remove. `source`: admin/abms/website/pos. `current_stock()` is
  always derived by summing the ledger, never stored directly.
  `clean()` enforces (a) quantity must be exactly 1 for `fixed_weight`
  products, (b) stock can never go negative — see the bug note above, this
  currently can't run due to the import typo.
- **`create_inventory_movements_from_snapshot()` has two modes:**
  `strict=False` (website: never raises, bad lines silently skipped, no
  `full_clean()` call — matches the site's existing best-effort email
  pattern) vs. `strict=True` (POS: re-raises, calls `full_clean()`, expected
  to be wrapped in `transaction.atomic()` by the caller so a failed line
  rolls back the whole sale). This asymmetry is intentional: an in-person
  sale hasn't been promised to a customer yet, so blocking it here is more
  correct than the silent-oversell tolerance accepted for an already-placed
  online order.
- **`POSSale.client_sale_id`** — a UUID generated client-side before the
  sale is sent, used for idempotency (double-tap, retried request, or a
  queued offline sale being resynced all return the original result instead
  of creating a duplicate sale).
- Fixed-weight products (goats, chickens) are each an individual animal
  tracked as a `ProductVariant` row (one per animal, own weight, own
  optional `price_override`) — never create a new `Product` per animal.
  `Product.fixed_weight` is a fallback only used when a fixed_weight product
  has zero variant rows.
- **`Product.barcode`** — two workflows exist, don't assume only one:
  1. The field's own help text describes scanning a *pre-printed blank
     barcode label* into this field, then printing that same code onto the
     product's label.
  2. Separately, an `AB` + zero-padded-product-id auto-generation scheme
     was built (`python manage.py generate_barcodes` — not yet in the repo
     as of this writing, exists as a drafted management command) plus a
     staff-only `/pos/labels/` print-sheet view using JsBarcode
     (Code128, client-side, no new Python dependency) — also drafted but
     **not yet wired into `urls.py`**. Check whether these were actually
     merged before assuming either exists.
- `Product.origin` (`farm` vs `sourced`): Apple, Kiwi, Grapes, Dragon Fruit,
  Watermelon are `sourced`; everything else `farm`.
- `Product.is_available` is a manual toggle, not derived from
  `current_stock() > 0` — deliberate deferral, not a bug.
- Cart and "save for later" are both session-based (`request.session['cart']`
  / `['saved']`), keyed by `line_key` — plain product id for unweighted
  lines, `"{product_id}_{weight}"` for variable-weight lines,
  `"{product_id}_v{variant_id}"` for fixed-weight lines. `Wishlist` (unlike
  cart/saved) is a real DB model tied to a logged-in user, since it needs to
  persist across devices/sessions.
- Coupons (`Coupon`) and Offers (`Offer`, `BundleItem`) are two separate
  discount systems: coupons are code-entry, order-level, one per order;
  offers are automatic per-product or per-category discounts, or combo
  bundles with a fixed total price. Both check `is_live()` against
  `is_active` + date range; coupons additionally check `max_uses`.

## REST API (v1)

A versioned REST API now exists under `/api/v1/`, built with Django REST
Framework in a new `api/` app (`api/serializers.py`, `api/views.py`,
`api/urls.py`, `api/permissions.py`, `api/tests.py`).

**Additive only** — the existing server-rendered website and POS
(`templates/pos.html`, vanilla JS) are untouched and still work exactly as
before. Nothing currently calls this API in production — it exists in
parallel, for future use, starting with a planned Next.js
product-browsing frontend.

Endpoints:

- `GET /api/v1/products/` — public, paginated, filterable by `?category=`
- `GET /api/v1/products/{id}/` — public
- `GET /api/v1/categories/` — public
- `GET /api/v1/inventory/movements/` — staff-only. A separate v1
  GET-capable view; the original `/api/inventory/movements/` (POST-only,
  token-authed, used by ABMS) is untouched.
- `GET/POST /api/v1/sales/` — staff-only, session-authed. POST recomputes
  totals server-side, enforces stock, supports `client_sale_id`
  idempotency, and reuses `create_inventory_movements_from_snapshot(...,
  strict=True)` inside `transaction.atomic()` via the shared
  `create_pos_sale()` helper. As of the POS-PWA Phase 1 work, this is the
  **only** way a POS sale gets created — `templates/pos.html`'s
  `completeSaleBtn` posts here directly now, not to a separate
  session-authed Django view (the old `pos_create_sale`/`/pos/sale/` thin
  wrapper around the same helper was removed as dead code once nothing
  called it anymore).
- `POST /api/v1/orders/` — public, guest checkout. Reuses
  `create_order_inventory_movements()` /
  `create_inventory_movements_from_snapshot(..., strict=False)`.
- `GET /api/v1/orders/{id}/` — returns only status fields (`id`,
  `order_number`, `status`, `ordered_at`, `product_interest`) by default.
  Returns the FULL order (name, email, phone, address, cart_snapshot,
  can_cancel) only when called with `?token=<cancel_token>` matching that
  order's own `cancel_token` (compared via `secrets.compare_digest`).
  `cancel_token` itself is never echoed back in either response. This
  reuses the token already generated on `ProductOrder` and already
  emailed via `send_order_received_email` — no new secret was introduced.

17/17 tests pass locally (`python manage.py test api`). Manually verified
in production via the DRF browsable API on 2026-09-27: category list
matches local exactly (13/13), product list matches (32/32, including the
fixed_weight Goat variant case), a POS sale POST correctly recomputed
total and deducted stock, idempotent resubmission returned the same
`sale_number`, a stock-insufficient sale correctly returned 409, guest
order creation worked with no auth, and the order token gate correctly
limited/expanded fields as designed.

**Gotcha:** DRF's `SessionAuthentication` does its own CSRF check
independent of Django's middleware. `pos.html`'s JS is now actually
pointed at this API (see "POS-PWA (Phase 1)" below) — it keeps sending
its existing `X-CSRFToken` header (via `apiFetch()`) on every call, which
is load-bearing, not optional.

## POS-PWA (Phase 1 — PWA shell + offline sale queue)

Scoped to `/pos/` only; nothing else on the site is a PWA. Phase 2
(caching the catalog so `/pos/` can cold-start with zero connectivity) is
intentionally not built yet — Phase 1 only covers a connectivity drop
*during* an already-open session.

- `static/pos-manifest.json` — `start_url`/`scope` both `/pos/`,
  `display: standalone`. Icons include both `any` and `maskable` variants
  (`static/images/pos-icon-*.png` / `pos-icon-*-maskable.png`, the
  maskable ones padded to the safe-zone convention, generated from
  `static/images/logo-icon.png`). iOS ignores the manifest for
  install/standalone behavior — `templates/pos.html`'s `<head>` also has
  the `apple-mobile-web-app-*` meta tags and an `apple-touch-icon` link,
  which is what iOS actually reads.
- `static/js/pos-sw.js` — the service worker, scope `/pos/`. Served at
  `/pos/service-worker.js` by a dedicated view (`pos_service_worker` in
  `shop/views.py`), **not** through the normal static-files pipeline —
  load-bearing, not a style choice: a service worker's effective scope
  can never exceed the directory its own script URL lives in unless the
  response sends a `Service-Worker-Allowed` header, and PythonAnywhere's
  static-file mapping (the Web tab UI) has no way to attach a custom
  header to one specific file. No catalog/asset caching yet (Phase 2) —
  Phase 1's `fetch` handler is a plain pass-through, present only because
  some browsers' installability checks still look for one.
- `static/js/pos-offline-queue.js` — an IndexedDB-backed queue, loaded
  both as a classic `<script>` in `pos.html` and via `importScripts()` in
  the service worker (same file, two contexts). `completeSaleBtn` posts to
  `POST /api/v1/sales/`; a real validation/stock rejection (4xx) is shown
  as a normal failure same as always, but a genuine network failure
  (`fetch()` itself throwing) queues the sale instead of losing it, relying
  on `client_sale_id` idempotency to make a later double-send harmless.
  Replay is attempted on page load, on the browser's `online` event, on
  `visibilitychange`, and via the Background Sync API where supported —
  **Safari/iOS has no Background Sync at all**, so the page-load attempt is
  what actually covers it there (reopening an installed PWA from the home
  screen is a fresh load, not reliably an `online`/`visibilitychange`
  transition). A queued entry that comes back `401`/`403` on replay is
  marked `needsReauth` (session/PIN expired while offline) rather than
  retried forever or dropped; one that comes back a real `400`/`409` is
  marked `failed` and also fires one (not repeated) Telegram alert via
  `PosQueueFailureAlertView` (`POST /api/v1/pos/queue/report-failed/`),
  since a failed entry otherwise only exists in that one device's
  IndexedDB — cleared browser data, a wiped device, or a PWA reinstall
  would otherwise erase the only trace a sale was ever attempted, even
  though the cashier already told that customer it went through.
- **Not yet done — needs a real device before this is fully trusted**:
  actual install-to-home-screen + airplane-mode-mid-sale + reconnect
  testing on a real Android phone (and iOS, if the farm uses one). What's
  verified so far is CDP-scripted against a desktop headless browser —
  solid evidence the logic is correct, but not a substitute for seeing
  Background Sync actually fire on a real OS while the app isn't in the
  foreground.

## Deferred work (known, intentional, not yet built)

- Local Egg / Vermicompost inventory bridge from ABMS (hook into ABMS's
  `productionLog` / `vermiOut`, same pattern as the harvest bridge)
- Goat/Chicken sales bridge needs a live variant-picker in ABMS (must let
  the operator pick which specific animal was sold — never auto-pick
  "cheapest available")
- React POS screen — not planned, see architecture decisions above
- Mango Pickles has no inventory modeling yet (no movement type for "raw
  mango converted into jars of pickle")
- Mother Goat, Pathi, Lady Goat (meat), Mother Chicken — postponed
- Postgres migration — deferred until concurrent writes are actually needed
- Live digital scale reading (Web Serial API) — deferred until a scale is
  bought and its protocol known
- Batch/expiry tracking — deferred
- `profile.html` still has the old pill-style nav, not the honeycomb nav
  used elsewhere
- Barcode label printing (`generate_barcodes` command + `/pos/labels/`
  view) — drafted, confirm whether merged before assuming it's live

## Frontend conventions

- Honeycomb hexagon navigation (`.hc-cell`, `.hc-nav`, `.hc-cta`) is current
  across all pages except `profile.html` (known gap, still old pill-style
  `.nav-links`)
- Inline SVG for icons, not Font Awesome CDN (CDN silently failed before —
  a real incident, not a style preference)
- `path()` clip-paths, not `polygon()`, for hexagon shapes
- Background hexagon strips sized at runtime via JS (`fitStripBg()`,
  `fitMobileBg()`), not static CSS — intentional
- CSS variables: `--moss`, `--gold`, `--forest`, `--cream`, `--mist`,
  `--text-muted`. Fonts: Cormorant Garamond (`--font-display`), DM Sans
  (`--font-body`), Cinzel (`--font-accent`)
- Cache-bust static assets with `?v=N` when editing CSS/JS
- URL names in use: `home`, `shop`, `cart`, `offers`, `profile`, `login`,
  `pos`, `pos_service_worker`, plus cart/wishlist/checkout sub-routes (see
  `shop/urls.py`). `pos_create_sale`/`/pos/sale/` was removed (POS-PWA
  Phase 1) once `templates/pos.html` switched to posting directly to
  `POST /api/v1/sales/` and nothing else referenced it.

## Key files

- `shop/models.py` — Category, Product, ProductVariant, NewsletterSubscriber,
  ContactMessage, ProductOrder, Review, Wishlist, Offer, BundleItem, Coupon,
  InventoryMovement, POSSale
- `shop/views.py` — all view logic: cart (session-based), save-for-later,
  wishlist, checkout, coupon/offer application, POS (`pos_view`,
  `pos_service_worker`), the shared `create_inventory_movements_from_snapshot()`
  and `create_pos_sale()` helpers
- `shop/admin.py` — Django admin customizations, including a custom
  newsletter-campaign compose form (`NewsletterAdmin`) and computed stock
  display on `ProductAdmin`
- `shop/emails.py` — all outbound email (Resend) + Telegram notifications
- `shop/imagekit_storage.py` — custom Django Storage backend for ImageKit
- `shop/signals.py` — connected in `apps.py`'s `ready()`
- `shop/sitemaps.py` — SEO sitemaps
- `templates/pos.html` — the POS screen
- Auth views in `shop/auth_views.py`, under `account/` URLs

## Project knowledge system

This file stays short and operational on purpose. Two companions hold what
doesn't belong here:

- **`Angan-Baari-Knowledge/` (Obsidian-compatible vault, in this repo)** —
  human-readable architecture explanations, decision rationale, deployment
  procedures, bug history. Start at `Angan-Baari-Knowledge/00-Index/Home.md`.
  Read only the specific doc relevant to the task at hand, not the whole
  vault.
- **`claude-mem` (local-only, `~/.claude-mem/`, not in this repo)** —
  automatic per-session memory (Ollama-backed, nothing leaves the machine).
  Builds up from the *next* new session onward.

Only promote something into *this* file if it's a stable instruction or
constraint that should hold in essentially every future session — not a
session log or a one-off observation. See
`Angan-Baari-Knowledge/03-Decisions/Decision-Log.md` for the full promotion
rule.

## Things to always ask about rather than assume

- Whether a change needs a migration (assume yes if `models.py` changed,
  but confirm the command was actually run — local ≠ production)
- Before adding any new third-party package — PythonAnywhere free-tier +
  the venv-activation gotcha above make this less trivial than usual
- Before proposing a rewrite of something already working (POS, ABMS
  bridge, inventory model) — check "architecture decisions already made"
  above first
- Whether a drafted-but-unmerged feature (barcode label printing) actually
  made it into the codebase before building on top of it
