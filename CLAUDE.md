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
  `pos`, `pos_create_sale`, plus cart/wishlist/checkout sub-routes (see
  `shop/urls.py`)

## Key files

- `shop/models.py` — Category, Product, ProductVariant, NewsletterSubscriber,
  ContactMessage, ProductOrder, Review, Wishlist, Offer, BundleItem, Coupon,
  InventoryMovement, POSSale
- `shop/views.py` — all view logic: cart (session-based), save-for-later,
  wishlist, checkout, coupon/offer application, POS (`pos_view`,
  `pos_create_sale`), the shared `create_inventory_movements_from_snapshot()`
  helper
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
