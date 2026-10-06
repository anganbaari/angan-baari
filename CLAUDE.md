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

## POS-PWA (Phase 1 + 2 + 3 + 4 — PWA shell, offline sale queue, offline-first cold start, offline PIN unlock, offline offers/coupons/credit-lookup)

Scoped to `/pos/` only; nothing else on the site is a PWA. Phase 1 covered
a connectivity drop *during* an already-open session; Phase 2 makes
`/pos/` itself cold-start with zero connectivity at all; Phase 3 makes PIN
unlock itself work offline; Phase 4 extends offline support to credit-
customer lookup, offers/combos, and coupon gating.

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
    opaque-response/versioning complexity for this phase. Confirmed by
    testing: no hang on those uncached CDN assets when offline, since
    nothing in `pos.html` blocks rendering on them.
  - **Gotcha, confirmed by testing:** a page's OWN navigation request is
    never controlled by the service worker that request itself just
    registered — only the *next* navigation is. Confirmed this is wider
    than just "the very first load's own HTML": on a genuinely clean
    profile, a single load with no reload left Cache Storage completely
    empty, including the `/static/` assets requested during that same
    load — whether they get caught by the stale-while-revalidate handler
    depends on a timing race (did the SW finish activating before those
    requests fired?) that usually resolves in a reload's favor on a fast
    local connection but isn't guaranteed on a slow one. Two fixes close
    this instead of relying on the race: (1) `pos-sw.js`'s `install`
    handler now explicitly precaches every `/static/` file `pos.html`
    needs (`POS_PRECACHE_URLS`), each caught independently so one missing
    asset doesn't void the rest; (2) `pos.html` itself writes its own
    rendered HTML into Cache Storage under the `/pos/` key immediately
    after every successful load (`posSelfCachePosPage()`), from the page's
    own context — this isn't subject to the "can't control its own
    navigation" limitation at all, so it guarantees a snapshot exists
    after the FIRST load, not just a lucky-timing reload.
  - **Gotcha #2, found by testing, more serious than the above:** the
    navigation handler's `fetch(request)` call can return an **opaque
    redirect** response (status 0, empty, unreadable) if the server
    redirects — e.g. `@staff_member_required` bouncing an expired session
    to `/admin/login/`. `event.request` for an intercepted navigation has
    an implicit `redirect: 'manual'`, so this doesn't follow the redirect
    the way a normal `fetch()` would. The browser still shows the user the
    right page regardless (confirmed by testing), but the ORIGINAL code
    blindly cached this opaque response under the `/pos/` key, silently
    **replacing the last known good cached page with a useless blank one**
    — worse than stale data, since the next offline load would render
    nothing at all. Fixed by checking `response.type !== 'opaqueredirect'
    && response.ok` before caching. `posSelfCachePosPage()` above sidesteps
    this risk a different way for its own write: it only ever runs from
    inside `pos.html`'s own script, which can't execute at all unless the
    load genuinely reached the real page, not a login redirect.
  - **If site data gets cleared** (any reason — browser setting, storage
    pressure, manual reset), the service worker, Cache Storage, and
    IndexedDB are ALL wiped together. There is no way to open `/pos/`
    offline immediately after that — the terminal needs one real online
    visit to re-register the SW and rebuild both the shell cache and (see
    offline PIN unlock below) at least one operator's cached PIN hash
    before any offline capability works again. The SW's own "no cached
    copy" 503 message already says this; `/pos/`'s locked-screen offline
    PIN error (below) says the equivalent for PIN unlock.
  - **Versioning:** `POS_SHELL_CACHE` (declared once in
    `pos-offline-queue.js` — not `pos-sw.js` — specifically so `pos.html`'s
    `posSelfCachePosPage()` and `pos-sw.js`'s fetch handler are guaranteed
    to agree on the same cache name instead of hand-syncing a literal
    string in two files; `'pos-shell-v4'` as of this writing — bumped from
    v3 since both `templates/pos.html` and this file changed structurally)
    must be
    bumped by hand whenever a deploy changes the `/pos/` page structure or
    what it depends on in `/static/` — there's no build step in this
    project to bump it automatically (see Stack above), same
    manual-discipline spirit as the existing "cache-bust static assets
    with `?v=N`" convention. The `activate` handler deletes every other
    `pos-shell-*` cache on each activation, so bumping this is what
    actually forces old cached shells off every device.
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
  `angan-baari-pos`, now at version 4 — version 2 added the Phase 2 `meta`
  store below, version 3 the Phase 3 `offlineOperators` store further down,
  version 4 the Phase 4 `creditCustomers` store),
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
    restock since the conflict could make it succeed) or Dismiss, for
    `stockConflict`, `offerChanged` (see below), and `failed` (not
    `needsReauth` — that state means we don't yet know if the sale is
    valid, so dismissing it risks silently losing a perfectly good one;
    only a state the server has actually rejected is safe to give up on).
    Dismissing never touches inventory or the ledger either way: the
    server rejected the attempt, so nothing was ever deducted for it —
    dismissing just removes the IndexedDB record, it doesn't reverse
    anything because nothing happened. There is no
    in-place "edit the cart and resubmit" — ringing up a fresh, corrected
    sale (e.g. without the sold-out line) and then dismissing the stuck
    one is the supported workaround; building a real queued-cart editor
    was judged disproportionate to this phase's scope.
    - **Dismiss is a two-step, reported action, not a plain delete.**
      Found by testing: the IndexedDB record being dismissed was the ONLY
      trace a real sale was ever attempted — a single accidental tap
      silently erased it with no server-side record anywhere. Tapping
      Dismiss now shows an explicit confirmation ("This sale will be
      removed from the queue. Have you re-entered it as a new sale...?")
      before anything happens, and confirming calls
      `confirmDismissSale()` (`pos.html`), which POSTs the full sale
      snapshot — items (resolved to product/variant/unit names, not just
      ids), payments, customer, operator, and when it was originally rung
      up — to `POST /api/v1/pos/queue/report-dismissed/`
      (`PosQueueDismissedReportView`, `api/views.py`), a sibling of
      `PosQueueFailureAlertView` below, not an extension of it (different
      trigger — an explicit human Dismiss vs. an automatic first-failure
      alert — and different payload needs: full line-item detail, not
      just a count). The entry is only actually removed from IndexedDB
      *after* that call returns 200. **If the report fails for any reason
      — the till itself is offline, or the server reached the network but
      couldn't reach Telegram — the entry stays in the queue** and staff
      see "can't dismiss until the till is back online," rather than a
      Dismiss that silently appears to work while losing the sale's only
      record. This is why `send_telegram()` (`shop/emails.py`) gained a
      `raise_on_failure` parameter (default `False`, preserving its
      existing best-effort behavior for every other caller — low-stock
      alerts, order notifications, the `report-failed` alert above):
      `PosQueueDismissedReportView` is the one caller that passes
      `raise_on_failure=True`, because here the Telegram send succeeding
      *is* the point of the request, not a side effect of it. Any cash the
      customer already handed over for a dismissed sale is still a human
      reconciliation problem outside the software — the Telegram message
      says so, and is now the mechanism for actually reconciling it, not
      just a courtesy notice.
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
  the night gets silently lost.

  **Queued-sale attribution (revised):** earlier versions of this file
  documented that a queued sale gets attributed to whoever is PIN-unlocked
  *at sync time*, since the server never trusted a client-supplied operator
  id. Found by testing to be a real misattribution problem after a shift
  change, so this was deliberately reversed: `pos.html` now captures
  `currentOperator.id` into the payload as `queued_operator_id` at the
  moment a sale is QUEUED (not replayed), and `get_pos_operator()`
  (`shop/views.py`) trusts that id for a replay specifically, instead of
  resolving from `request.session` — see that function's docstring for the
  full reasoning. This is still independently re-validated against
  `UserProfile`/`is_active`/`is_staff` at replay time, so an operator
  deactivated between queuing and replay is rejected (lands in
  `needsReauth`) exactly like any other invalid operator. The id is
  trusted ONLY on this one path (an offline-queued sale actually replaying)
  — a live online sale never sends `queued_operator_id` at all, and still
  resolves purely from the session, unaffected. This grants no new system
  access (every POST here already requires a live `is_staff` session
  regardless of which operator id is used) — it only changes who a sale is
  ATTRIBUTED to. The residual risk is narrow and accepted: a currently
  logged-in staff member could misattribute a sale to a colleague by
  tampering with their own client before it queues, which is a trust/HR
  concern, not a new way to affect inventory or payment totals.
- **Offline PIN unlock (Phase 3).** `submitPin()` previously had no
  try/catch at all around its call to `POST /api/v1/pos/unlock/` — found
  by testing that going offline and entering a PIN threw an UNCAUGHT
  exception, leaving staff stuck on the lock screen with zero feedback.
  Now: if that request can't even reach the server (a thrown `fetch`, not
  a 401/403 response), it falls back to `posVerifyOfflineOperator()`
  (`static/js/pos-offline-queue.js`), checking the PIN against a
  **PBKDF2-SHA256 hash cached locally** from that operator's last
  successful ONLINE unlock on this exact device (`posStoreOfflineOperator()`,
  called every time an online unlock succeeds — routine daily use is what
  keeps resetting the expiry clock below). Only operators who have
  unlocked online on this device at least once can ever unlock offline.
  - **Deliberate, accepted security tradeoff — a 4-digit PIN is only
    10,000 combinations, and no amount of PBKDF2 iteration count fixes
    that.** An attacker with the physical device can read the hash
    directly out of IndexedDB and brute-force all 10,000 guesses in well
    under a minute even at a deliberately slow iteration count (210,000
    here — a real current PBKDF2-SHA256 floor, not a token default, but it
    only costs a legitimate unlock a negligible delay, not meaningful
    attacker cost). The lockout (5 wrong attempts, 5-minute cooldown,
    device-wide not per-operator — mirrors `PosUnlockView`'s own
    reasoning) only deters a casual guesser; anyone who can read the
    IndexedDB store directly can also clear or edit the lockout counter
    sitting next to it. **Accepted anyway, for the same reason the
    server-side hash already accepts it** (see `UserProfile.set_pin`'s own
    comment in `shop/models.py`): this PIN layer identifies who's
    operating an already-access-controlled terminal, it was never the
    terminal's own access control — that's still the underlying `is_staff`
    Django session, completely unaffected by any of this. A stolen,
    already-logged-in till is a bigger exposure than PIN attribution
    either way. Cached hashes expire after 7 days without a fresh online
    unlock (`POS_OFFLINE_PIN_EXPIRY_MS`), after which offline unlock for
    that operator stops working until they unlock online again.
  - Surviving a Logout/lock is intentional, same reasoning as the queue
    surviving it above — if it were purged on lock, offline unlock would
    stop working for literally everyone the first time anyone locks the
    till while offline.
  - **Queued-sale state reset.** Found by testing: `payments` and
    `selectedCustomer` were reset lazily, only the next time
    `openPaymentPanel()` happened to run, in BOTH the success and queued
    outcomes — not actually exploitable through the normal UI today
    (`openPaymentPanel()` is the only way those fields become visible
    again, and it always wipes them first), but one future change away
    from actually leaking stale amounts into the next sale.
    `completeSaleBtn`'s success and queued paths now share one
    `resetPostSaleState()` that clears cart, coupon, `clientSaleId`,
    `payments`, and `selectedCustomer` eagerly, immediately, in both
    outcomes — not deferred to the next panel open. Separately confirmed:
    `clientSaleId` already rotated correctly on every outcome (success,
    queued, stockConflict, failed, Dismiss) before this change — the
    duplicate-sale risk from a stale id was never actually present.
- **Offline credit-customer lookup (Phase 4).** `lookupCustomerBtn`,
  `createCustomerBtn`, `repayConfirmBtn`, and `loadRepayCustomers()` all
  had the exact same uncaught-exception bug found and fixed for PIN unlock
  in Phase 3 — none had a try/catch around their `apiFetch()` call, so
  going offline threw uncaught and left the UI silently blank (credit
  lookup) or permanently stuck on "Loading…" (Repay Credit table). Not a
  regression — confirmed via testing that credit lookup works correctly
  online; this is the same long-standing pattern, just never previously
  tested offline.
  - **Lookup and Repay Credit browsing** now fall back to a cached
    snapshot (`posGetCachedCustomers()`, `static/js/pos-offline-queue.js`)
    on a genuine network failure, refreshed via `fetchAndCacheCreditCustomers()`
    whenever Repay Credit opens, at page load, and on every `online` event
    (`refreshCreditCustomerCacheQuietly()`) — so lookup has a reasonably
    fresh cache even on a device that never opens Repay Credit. Shown with
    an explicit "balance as of [time], may be out of date" note.
  - **Deliberate, documented privacy tradeoff:** the cache only stores
    `id`/`name`/`nickname`/`phone`/`outstanding_balance` — never `address`
    (its only real purpose is finding someone in person over an unpaid
    debt; no reason to let it sit in a second place at rest) or repayment
    history. Expires after the same 7-day window as the cached PIN data
    (`POS_OFFLINE_PIN_EXPIRY_MS`) and is purged unconditionally on Logout
    (`posPurgeCreditCustomerCache()`) — unlike the `/pos/` shell cache
    purge, this one is NOT connectivity-gated, since purging it carries no
    risk of stranding the terminal.
  - **Creating a new customer and recording a repayment are blocked
    offline outright, with a clear toast, never queued.** Unlike a sale
    (which has `client_sale_id` specifically to make a double-send
    harmless), neither of these has an idempotency key — a queued-then-
    manually-retried repayment or customer creation risks silently
    duplicating a record against a shared credit ledger, which is much
    harder to untangle after the fact than a queued sale is. Browsing the
    Repay Credit table from cache is still allowed (read-only, harmless);
    only the actual repay/create actions are gated.
  - **Queued credit sale replay, confirmed by testing:** if the referenced
    `customer_id` no longer exists by replay time (deleted in the
    meantime), the sale fails cleanly with "Customer not found" (400) —
    no crash, no sale silently created without its customer attached. This
    already worked correctly before Phase 4; no code change was needed,
    only a test (`test_credit_sale_with_deleted_customer_fails_cleanly`,
    `api/tests.py`) confirming it.
  - **No credit limit exists anywhere in this system** — `Customer` has no
    limit field, and nothing enforces one at sale or sync time. A credit
    sale is accepted regardless of existing outstanding balance. This was
    a deliberate check, not an oversight to fix here — flagged in case a
    limit is wanted as a future feature, not built speculatively.
- **Offline offers/combos (Phase 4).** `openOffersModal()` already
  degraded gracefully offline before this (a real try/catch showing
  "couldn't load offers"), so this was a genuine feature gap, not a bug —
  offers/combos are fetched live from `/api/v1/pos/offers/` every time the
  modal opens (deliberately not baked into `/pos/`'s own payload, see
  `PosOffersListView`'s docstring, since an offer's live window can start
  or end mid-shift), with nothing cached for when that fetch fails.
  - Now stale-while-revalidate: still always tries the live fetch first
    when the modal opens (an offer going live or expiring mid-shift should
    never show stale data on a terminal that's been online all along), and
    caches the response (`posCacheOffers()`, reusing the `meta` store as a
    single JSON blob — this isn't per-record data worth a dedicated object
    store) on success. Only falls back to the cached blob on a genuine
    network failure, shown with a "may be out of date" note. Not PII, so
    unlike the credit-customer cache above, this is NOT purged on Logout
    and has no short expiry tradeoff to document — same privacy class as
    the PRODUCTS/CATEGORIES already embedded in the cached `/pos/` shell.
  - **If an offer/coupon expired between when a sale was queued and when
    it replays**, `create_pos_sale()` already rejected it (via
    `resolve_pos_offer_discount()`/`resolve_pos_combo_lines()`/
    `resolve_pos_coupon()` raising `POSSaleValidationError`) — what was
    missing was a way for the offline queue to tell that failure apart
    from an unrelated one. `POSSaleValidationError` now takes an optional
    `reason` (currently only ever `'offer_unavailable'`), surfaced in the
    API error response, which the offline queue (`posReplayQueue()`) uses
    to mark the entry `offerChanged` instead of the generic `failed` —
    same "expected, routine, not a bug, not Telegram-alerted" treatment as
    `stockConflict`. **What staff should do:** nothing was deducted from
    inventory or charged for this attempt (same as `stockConflict`/
    `failed`); ring the cart up as a new sale at today's price, then
    Dismiss the stuck entry. If the customer already paid the old
    (discounted) amount, that difference is a human reconciliation problem
    outside the software, same as the other dismissable states.
- **Coupons stay online-only (Phase 4) — deliberately not cached.**
  Unlike offers, a coupon's validity (`used_count`, `min_order_amount`,
  timing) isn't meaningfully cacheable, so this is a straight block, not
  stale-while-revalidate: `couponInput` is disabled with a "Coupons need a
  connection" placeholder whenever offline (`updateCouponAvailability()`,
  reactive to the existing `online`/`offline`/`visibilitychange`
  listeners), and `applyCouponBtn`'s own click handler refuses to even
  attempt validation offline. **An already-applied coupon is NOT stripped**
  when connectivity drops — it rides through to Complete Sale exactly as
  before, whether that POST succeeds live or gets queued; removing an
  applied coupon (`clearCoupon()`) needs no network at all and stays
  available regardless of connectivity. If a queued sale's coupon turns
  out to be invalid by replay time, that's the same `offerChanged` state
  described above — `resolve_pos_coupon()`'s errors carry the same
  `reason='offer_unavailable'` tag.
- **Stock offline:** `loadStockData()` already handled this correctly —
  confirmed by testing, no change needed there (a failed refresh keeps
  whatever `stockData` already had rather than wiping it). The one gap:
  if `stockData` is still empty (a device that's never successfully
  fetched it at all, e.g. offline since its first-ever cold start), the
  Stock view and its header indicator used to render a blank table and a
  misleading "Stock OK" respectively. Both now say "Stock unavailable
  offline" instead — cheap, since the fix is just checking
  `stockData.length` before claiming anything about it.
- **Not yet done — needs a real device before this is fully trusted**:
  actual install-to-home-screen + airplane-mode-mid-sale + reconnect
  testing on a real Android phone (and iOS, if the farm uses one),
  **including a wifi-connected-but-no-internet check** (join a wifi
  network with no real uplink, e.g. by disconnecting its router's WAN
  cable, and confirm the offline banner still appears — this is the one
  case `navigator.onLine` alone can't catch, which is why the banner also
  probes connectivity directly; see above), **an offline-PIN-unlock
  check** (unlock online once, go offline, lock, confirm the same PIN
  unlocks again without a network round trip, and confirm 5 wrong PINs in
  a row locks it out same as the online path does), **and an offline
  credit-lookup check** (look up a customer online once, go offline,
  confirm the same phone number still finds them from cache with a
  "may be out of date" note, and confirm creating a customer/recording a
  repayment are both cleanly blocked rather than silently failing). What's
  verified so far (all four phases) is CDP-scripted against a desktop
  headless browser, including genuinely toggling the browser's network to
  offline (not just stubbing `fetch`) and confirming `/pos/` still renders
  with the right cached catalog data — solid evidence the logic is
  correct, but not a substitute for seeing Background Sync actually fire
  on a real OS while the app isn't in the foreground.

## Reports dashboard

Staff-only, owner/manager role only (not every staff login) read-only
reporting screen at `/dashboard/` — `templates/dashboard.html`, vanilla JS
(`static/js/dashboard.js` + `static/css/dashboard.css`), Chart.js vendored
at `static/vendor/chart.min.js` (not a CDN — see Frontend conventions
below). All aggregation logic lives in `shop/reports.py`, which only ever
**reads** `POSSale`/`ProductOrder`/`CreditTransaction`/`InventoryMovement`
— nothing on this page can affect the POS, checkout, or the ledger.
Deliberately outside `/pos/`'s service-worker scope: never cached, no
offline mode, a fetch failure just shows a plain "needs a connection"
message.

**Permissions**: gated by a NEW `api.permissions.IsOwnerOrManager` (API
side) and `shop.views._is_owner_or_manager()` (page-shell side) — both
check `UserProfile.role in ('admin', 'manager')`, with a superuser bypass.
This is the first real consumer of `UserProfile.role` anywhere in the
codebase; every other POS permission check only ever used `is_staff`.

**Endpoints**, all GET-only, session-authed, under `/api/v1/reports/`,
sharing `start`/`end`/`channel` (all/pos/online)/`granularity`
(day/week/month)/`compare` (true/false) query params:
`summary` (Overview tab), `sales-trend` (Sales tab), `payments` (payment
mix, also embedded in `summary`), `products` (Products tab), `credit`
(Credit/उधारो tab), `inventory` (Inventory tab), `orders` (Online Orders
tab). Most support `?export=csv` for their main table (UTF-8 BOM prefix
so Excel renders Devanagari names correctly).

**Metric definitions — these were explicit design decisions, not
assumptions, confirmed during this feature's build before any code was
written, because the underlying data genuinely can't support anything
more precise:**

- **Revenue is POS-only.** `ProductOrder` has no total/price field at all,
  and its `cart_snapshot` deliberately omits price too (see that field's
  own help_text — a reorder should re-price at today's rate). Online
  orders are shown as a **count** everywhere on this dashboard, never
  blended into a Rs figure.
- **Per-product/category revenue is an ESTIMATE**, priced at TODAY's
  rates (`cart_snapshot` never stored a per-line price for either
  `POSSale` or `ProductOrder` — confirmed by reading `create_pos_sale()`'s
  actual code, not its docstring). This silently overstates anything that
  was offer/combo-discounted at sale time and is wrong for anything sold
  before a later price change. The QUANTITY-based version of the same
  breakdown is exact.
- **Cash collected ≠ revenue.** Cash collected = non-credit POS payment
  portions in the period + credit repayments *received* in the period. A
  credit sale is revenue immediately but cash only once repaid.
- **No profit/margin anywhere** — `Product` has no cost/purchase-price
  field, so there is nothing to build one from.
- **Offer discounts are not tracked as a number** (only that one was
  used); **coupon discounts are exact** (`POSSale.discount_amount`).
- **Outstanding credit is a live, all-time snapshot**, not scoped to the
  filter date range — it always matches the Credit tab / `Customer.
  outstanding_balance()` exactly, by construction (same method).

**Timezone**: every day boundary uses `zoneinfo.ZoneInfo('Asia/Kathmandu')`
explicitly (`shop/reports.py`'s `local_range_to_utc()`), not Django's
ambient "current timezone" — a sale at 23:30 or 00:30 local always lands
on the correct local calendar day.

**No caching.** A short cache was allowed by this feature's brief only if
it couldn't show a stale credit balance right after a repayment —
invalidating one correctly would mean touching the POS/credit write paths,
which this feature keeps strictly read-only. Every endpoint computes
fresh every time.

**Aggregation**: sale-level numbers use real DB `Sum`/`Count`/`Trunc*`
aggregation. Per-line numbers (units by product, category breakdown,
offer/combo usage) can't be — `cart_snapshot` is a JSON blob, not a
related table — so those use exactly one bulk query across the date range
plus an in-memory Python loop (`_iter_pos_cart_lines()`), never one query
per row. If you extend this module: anything reading a related manager
(`.variants.all()`, `.selling_units.all()`) inside a per-line loop needs
`prefetch_related` on the bulk product lookup first — a real N+1 of
exactly this shape was caught by `QueryCountTests` in
`api/tests_reports.py` and fixed by prefetching `selling_units`.

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
  `pos`, `pos_service_worker`, `dashboard`, plus cart/wishlist/checkout
  sub-routes (see `shop/urls.py`). `pos_create_sale`/`/pos/sale/` was
  removed (POS-PWA Phase 1) once `templates/pos.html` switched to posting
  directly to `POST /api/v1/sales/` and nothing else referenced it.

## Key files

- `shop/models.py` — Category, Product, ProductVariant, NewsletterSubscriber,
  ContactMessage, ProductOrder, Review, Wishlist, Offer, BundleItem, Coupon,
  InventoryMovement, POSSale
- `shop/views.py` — all view logic: cart (session-based), save-for-later,
  wishlist, checkout, coupon/offer application, POS (`pos_view`,
  `pos_service_worker`), the Reports dashboard shell (`dashboard_view`),
  the shared `create_inventory_movements_from_snapshot()`
  and `create_pos_sale()` helpers
- `shop/reports.py` — all Reports dashboard aggregation logic (read-only —
  see the "Reports dashboard" section above)
- `templates/dashboard.html` / `static/js/dashboard.js` /
  `static/css/dashboard.css` — the Reports dashboard page
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
