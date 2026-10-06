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

## POS-PWA (Phase 1 + 2 — PWA shell, offline sale queue, offline-first cold start)

Scoped to `/pos/` only; nothing else on the site is a PWA. Phase 1 covered
a connectivity drop *during* an already-open session; Phase 2 makes
`/pos/` itself cold-start with zero connectivity at all.

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
  header to one specific file.
  - **Cold-start caching (Phase 2):** deliberately did NOT move the
    catalog to a separate API-fetch-and-cache flow — `pos.html` still gets
    `PRODUCTS`/`CATEGORIES` as server-rendered embedded JSON exactly as
    before, zero client-side-rendering rewrite. Instead the `fetch`
    handler caches the **whole `/pos/` navigation response** (network-first,
    cache-on-success, fall back to the cached copy when the network
    fails) — the cached page already contains whatever catalog data it
    was rendered with, so there's no separate cache to keep in sync.
    Same-origin `/static/` assets (CSS/JS/icons) get stale-while-revalidate
    so a cold load doesn't block on them either. Cross-origin CDN assets
    (Google Fonts, Font Awesome) are intentionally NOT cached — they just
    fail offline like any ordinary page would, not worth the
    opaque-response/versioning complexity for this phase.
  - **Gotcha, confirmed by testing:** a page's OWN navigation request is
    never controlled by the service worker that request itself just
    registered — only the *next* navigation is. The very first `/pos/`
    load after a SW update won't populate the shell cache; the one after
    that will. This is standard SW behavior, not a bug here.
  - **Versioning:** `POS_SHELL_CACHE` in `pos-sw.js` (`'pos-shell-v2'` as
    of this writing) must be bumped by hand whenever a deploy changes the
    `/pos/` page structure or what it depends on in `/static/` — there's
    no build step in this project to bump it automatically (see Stack
    above), same manual-discipline spirit as the existing
    "cache-bust static assets with `?v=N`" convention. The `activate`
    handler deletes every other `pos-shell-*` cache on each activation, so
    bumping this is what actually forces old cached shells off every
    device.
  - **Update behavior:** `install` calls `self.skipWaiting()` and
    `activate` calls `self.clients.claim()` — together these mean a newly
    deployed SW version goes from "installed" to "fully active and
    controlling the one open `/pos/` tab" as soon as the browser notices
    the changed `pos-sw.js` bytes (normally on that tab's next
    navigation/reload), with no "wait for every tab to close first" delay.
    That's deliberate for a single shared till where there's only ever one
    tab to wait on anyway. The risk this creates — the tab's already-
    loaded page JS (including `pos-offline-queue.js`, loaded once at page
    load, never live-updated) could keep running under a newer, possibly
    schema-incompatible SW — is closed by a
    `navigator.serviceWorker.addEventListener('controllerchange', ...)`
    listener in `pos.html`: the moment a new SW takes over, the page
    reloads itself, so the mismatch window is only the instant between
    `clients.claim()` firing and that reload completing. **What staff
    see:** normally nothing disruptive — if the cart is empty when the
    handover happens, the page just reloads itself once, unprompted. If a
    sale is mid-ring-up, the reload is deferred (a toast says so) until the
    cart empties again (sale completed, queued, or cleared), so an
    in-progress, not-yet-submitted cart (session-only, never persisted) is
    never silently lost by an update landing at the wrong moment.
- `static/js/pos-offline-queue.js` — an IndexedDB-backed queue (DB
  `angan-baari-pos`, now at version 2 for the Phase 2 `meta` store below),
  loaded both as a classic `<script>` in `pos.html` and via
  `importScripts()` in the service worker (same file, two contexts).
  `completeSaleBtn` posts to `POST /api/v1/sales/`; a real validation/stock
  rejection (4xx) is shown as a normal failure same as always, but a
  genuine network failure (`fetch()` itself throwing) queues the sale
  instead of losing it, relying on `client_sale_id` idempotency to make a
  later double-send harmless. Replay is attempted on page load, on the
  browser's `online` event, on `visibilitychange`, and via the Background
  Sync API where supported — **Safari/iOS has no Background Sync at all**,
  so the page-load attempt is what actually covers it there (reopening an
  installed PWA from the home screen is a fresh load, not reliably an
  `online`/`visibilitychange` transition). Each replay attempt has four
  outcomes, not three:
  - `401`/`403` → `needsReauth` (session/PIN expired while offline) —
    never silently dropped or retried forever.
  - `409` → `stockConflict` (Phase 2) — stock genuinely ran out while
    offline, an expected/routine outcome of offline selling, NOT a bug.
    `InventoryMovement.clean()` (`shop/models.py`) names the specific
    product/variant in its message so staff know which line item is the
    problem. Deliberately does **not** fire the Telegram alert below —
    that channel is for real bugs, not routine sold-out conflicts.
    **Resolving one:** the queue panel offers Retry (try again as-is — a
    restock since the conflict could make it succeed) or Dismiss, for both
    `stockConflict` and `failed` (not `needsReauth` — that state means we
    don't yet know if the sale is valid, so dismissing it risks silently
    losing a perfectly good one; only a state the server has actually
    rejected is safe to give up on). Dismissing never touches inventory or
    the ledger either way: the server rejected the attempt, so nothing was
    ever deducted for it — dismissing just removes the IndexedDB record, it
    doesn't reverse anything because nothing happened. There is no
    in-place "edit the cart and resubmit" — ringing up a fresh, corrected
    sale (e.g. without the sold-out line) and then dismissing the stuck
    one is the supported workaround; building a real queued-cart editor
    was judged disproportionate to this phase's scope. Any cash the
    customer already handed over for a dismissed sale is a human
    reconciliation problem outside the software, and the panel says so.
  - any other `4xx` → `failed`, fires one (not repeated) Telegram alert
    via `PosQueueFailureAlertView` (`POST /api/v1/pos/queue/report-failed/`)
    — a failed entry otherwise only exists in that one device's
    IndexedDB, and the cashier already told that customer the sale went
    through.
  - a thrown fetch (no response at all) → stays `pending`, replay loop
    stops (still offline).
  - The same database's `meta` store (Phase 2) holds `lastCachedAt`
    (`posSetMeta`/`posGetMeta`), written by the service worker every time
    it successfully caches a fresh `/pos/` response — `pos.html` reads it
    for the `#offlineBanner` ("Offline — showing data as of ..."). Shown
    on any ONE of three independent signals, not just `navigator.onLine`
    (which stays `true` on "wifi connected, no actual internet" — a dead
    router or ISP outage leaves the network *interface* up, which is all
    `navigator.onLine` ever reflects): (1) `navigator.onLine === false`,
    (2) this exact page load was served from the SW's offline cache
    fallback (it injects a `<meta name="pos-cache-fallback">` tag into the
    HTML when that happens, since a page can't otherwise read its own
    navigation response's headers after the fact), or (3) a lightweight,
    uncached, unauthenticated same-origin probe fetch (to the SW's own
    script URL) actually fails. Re-checked on `online`/`offline`,
    `visibilitychange`, and a 60-second interval while the tab is visible
    — the interval exists specifically for "wifi died silently mid-shift
    with no OS-level event to react to," which a receipt screen can sit on
    for hours between sales.
- **Logout and cache hygiene:** the POS's own Logout button (clears the
  PIN-unlock session, see "architecture decisions" above — not a full
  Django logout) purges the cached `/pos/` entry from Cache Storage and
  reloads, so the next person on this terminal gets a genuinely fresh load
  rather than instantly seeing whatever the previous shift last cached —
  but only when `posProbeConnectivity()` confirms real connectivity first.
  Purging unconditionally was tried and found to be a real bug during
  testing: the SW's navigation handler is network-first regardless, so
  purging buys nothing extra when actually online, but purging while
  offline (including "wifi but no internet") deletes the one thing Phase 2
  exists to provide, and the reload that follows then hits the SW's "no
  cached copy" 503 dead end — turning a tap of Logout into a stranded
  terminal. When the probe says there's no real connectivity, Logout skips
  the purge and the reload entirely and just locks locally (same as the
  idle-timeout auto-lock), attempting the server-side unlock-session clear
  best-effort only.
  The offline sale queue (IndexedDB) deliberately survives a logout —
  purging it would mean a sale rung up right before someone logs out for
  the night gets silently lost. This is safe because replaying a queued
  sale always requires a currently-valid PIN-unlock (`pos_operator_id` in
  the session) regardless of who logs in afterward — an expired/absent
  session correctly lands the entry in `needsReauth` rather than replaying
  it. Note this does mean a queued sale that syncs after a shift change
  gets attributed to whoever is PIN-unlocked *at sync time*, not
  necessarily whoever actually rang it up while offline — the payload
  never carries an operator id for the server to trust (same reasoning as
  never trusting a client-supplied `operator_id` for an online sale), so
  this is an accepted tradeoff, not an oversight.
- **Not yet done — needs a real device before this is fully trusted**:
  actual install-to-home-screen + airplane-mode-mid-sale + reconnect
  testing on a real Android phone (and iOS, if the farm uses one),
  **including a wifi-connected-but-no-internet check** (join a wifi
  network with no real uplink, e.g. by disconnecting its router's WAN
  cable, and confirm the offline banner still appears — this is the one
  case `navigator.onLine` alone can't catch, which is why the banner also
  probes connectivity directly; see above). What's verified so far (both
  phases) is CDP-scripted against a desktop headless browser, including
  genuinely toggling the browser's network to offline
  (not just stubbing `fetch`) and confirming `/pos/` still renders with
  the right cached catalog data — solid evidence the logic is correct, but
  not a substitute for seeing Background Sync actually fire on a real OS
  while the app isn't in the foreground.

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
