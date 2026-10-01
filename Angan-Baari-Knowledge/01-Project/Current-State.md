# Current State

*Living document — update as work lands. Last checked: 2026-10-01.*

## Recently landed (as of 2026-10-01, uncommitted — Repay Credit full view, Logout, offer/coupon fixes)

Same session as everything below, done as a later, separate pass on top of
commits `2ebe357`/`38390bb` — **none of this is committed yet**, see
"Deploy status" below.

- **Repay Credit converted from a modal to a full `repayView` screen**,
  matching the `stockView` pattern exactly (same `currentView` toggle, same
  sticky-header table styling, a "« Back" button instead of a backdrop
  close). The search/row-click-select/repay-amount flow underneath is
  functionally unchanged. Cancel now just clears the in-progress selection
  (Back is the "leave the screen" action); a successful repayment stays on
  the screen and refreshes the table rather than bouncing back to the
  receipt, so staff can process several customers' repayments back to back
  without re-navigating each time.
- **Logout button** added next to the reminder card (which shrank slightly
  to make room), styled slate gray (`#54606b`) — deliberately not
  red/green/orange/blue, since those already mean Low Stock/Repay
  Credit/Offers/Stock OK. Does a full Django logout (`{% url 'logout' %}`),
  not a re-lock to the PIN screen — confirmed correct rather than assumed:
  the PIN-unlock layer (`PosUnlockView`) only tracks who's currently
  operating an already-logged-in terminal, it was never the terminal's own
  access control, so end-of-shift needs the real session to end. Verified
  directly (not just read) that `/account/logout/` ends the session and a
  follow-up `/pos/` request redirects to login.
- **Fixed-weight offer pricing bug in the edit flow.** Diagnosed with CDP
  tracing before changing anything — the *add-to-cart* path
  (`cloneProductWithOffer()`) already discounted every variant correctly.
  The actual bug was in *editing* an offer-priced line: switching to a
  different animal/variant via `editCartLine()` used
  `productWithFixedRate()`, which only patched whichever variant was
  originally picked, leaving every other variant at full undiscounted
  price if you switched to it. Fixed by having `editCartLine()` re-fetch
  the live offer and recompute every variant fresh (same as the initial
  add), with a graceful fallback (plain prices + a toast) if the offer has
  since expired. Also fixed a related bug where switching variants during
  an edit could leave a stale `offer_id` on the line. Combo pricing
  (`resolve_pos_combo_lines()`) was untouched — confirmed working as
  designed, not part of this bug. Server-side `resolve_pos_offer_discount()`
  needed no change — it already validates against the actual selected
  variant's real price, not a flat number; added a regression test
  (`test_fixed_weight_offer_discounts_each_variant_from_its_own_price`)
  to lock that in.
- **Coupons now exclude offer/combo-discounted lines.** `create_pos_sale()`
  computes a separate `coupon_eligible_subtotal` (only lines with no
  `offer_id`/`combo_instance_id`) and validates the coupon against that,
  not the full cart total — an offer/combo line keeps its already-
  discounted price untouched by a coupon. An all-offer cart with a coupon
  code no longer blocks the sale (this can't happen via the normal UI
  anyway, since `couponEligibleSubtotal()` on the Apply button already
  catches it first) — it just completes at full price. The live
  `/api/v1/pos/coupon/validate/` preview matches this same restricted-
  subtotal rule via what the client sends it. The Apply button shows a
  clear toast ("doesn't apply — every item is already discounted by an
  offer") instead of silently doing nothing when nothing in the cart is
  coupon-eligible.
- Verified via a full CDP browser pass (real clicks, real session/logout
  checks against the live server, not mocked) — found the fixed-weight
  edit-flow bug this way before touching code, then confirmed the fix.
  **184/184 tests passing** (`api shop`). No new migration this round.

## Recently landed (as of 2026-10-01, commits `2ebe357`/`38390bb` — pushed to GitHub `main`)

- **Low-stock/restock follow-up fixes**, after the first production test of
  the `d61daff` stock screen surfaced several rough edges:
  - The compact Stock box is now actually colored — blue "Stock OK" via
    `.stock-col.stock-all-ok`, red "⚠ Low Stock" via `.stock-col.stock-has-low`
    (previously had no background color at all).
  - `is_low` corrected from `<=` to strict `<` everywhere it's checked
    (`shop/stock.py::get_stock_table_rows()` and the Telegram-crossing check
    in `shop/signals.py`) — a product sitting exactly at its minimum no
    longer flags as low.
  - Stock numbers are now genuinely live: `loadStockData()` refetches
    `GET /api/v1/pos/stock/` fresh every time the Stock box/view is about to
    display, and again right after every completed sale — no stale cached
    JS variable.
  - The Restock modal now lets staff explicitly pick the movement's origin
    (farm / outsourced) per restock, defaulting to the product's current
    `origin` but overridable — for a normally-farm-grown item occasionally
    bought in (e.g. Chilly) without changing the product's actual default.
    `POST /api/v1/pos/restock/` takes this as an explicit `origin` field
    instead of inferring it from `product.origin`.
  - A separate inline pencil-edit control on the Stock table's "Restock
    Method" column changes `Product.origin` itself going forward, via a new
    `PATCH /api/v1/pos/products/<id>/origin/` — distinct from the per-restock
    override above, which never touches the product's own default.
  - **Bug found and fixed during CDP verification**: `PosStockListView`'s
    `GET /api/v1/pos/stock/` built its own response dict and never included
    `origin`, even though `get_stock_table_rows()` already returned it —
    silently broke both the Restock modal's default-origin preselection and
    the inline-edit's preselection (neither origin button showed "selected"
    on open). Fixed by adding the field; regression test
    `test_stock_list_includes_origin` added to `api/tests.py`.
- **Repay Credit rework.** Replaced the old phone-search-first lookup with a
  full customer table (No./Name/Nickname/Phone/Address/Current Credit/Last
  Repaid Amount/Last Repaid Date), styled like the Stock table (sticky
  header, vertical scroll), with a live phone-substring search box (phone
  isn't unique — shared family phones) and click-to-select feeding into the
  unchanged repay-amount flow. Backed by a new `GET /api/v1/pos/customers/`
  (same URL as the existing create-customer `POST`, now a combined
  list/create view).
- **`Customer.address` is now required** (migration `0035`, committed and
  pushed — see "Deploy status" below). The Payment Panel's create-customer
  form shows an inline error under the Address field instead of a generic
  toast when it's left blank, both client-side (before the API call) and
  when the server rejects it.
- Verified with a full CDP browser pass (real clicks, real fetches, real
  page reloads, real DB state checked afterward — not mocked), including
  the origin-bug fix above. **181/181 tests passing** (`api shop`).

## Recently landed (as of 2026-10-01, commit `d61daff` — pushed to GitHub `main`)

Additive to the POS Phase C entry below — that one is left as-is.

- **Offers (percent/fixed + combo) — POS Phase D.** A single-product offer
  reuses the existing add-to-cart flow via a discounted clone
  (`cloneProductWithOffer()` / `productWithFixedRate()` in `pos.html`)
  rather than a parallel UI path. A combo offer expands into one ordinary
  cart line per `BundleItem`, all sharing a `combo_instance_id`; a
  `fixed_weight` bundle item (goat/chicken) pops the real variant picker,
  since `BundleItem` itself has no `variant_id` — the cashier picks the
  actual animal at add-to-cart time. `resolve_pos_offer_discount()` /
  `resolve_pos_combo_lines()` in `shop/views.py` re-validate everything
  server-side (live offer, bundle contents still match, price recomputed
  from scratch) — a client-sent offer/combo price is never trusted, same
  principle as `resolve_pos_coupon()`. New staff-only
  `GET /api/v1/pos/offers/`, fetched live each time the Offers picker opens
  (replaces the old inert "Counter" button in the five-column row).
- **Cash round-off.** `create_pos_sale()` rounds the final total to the
  nearest rupee — explicit `ROUND_HALF_UP`, not Python's bare `round()`
  (which does banker's rounding and would round e.g. 222.50 down to 222) —
  right before the payments-match check, storing the difference in a new
  `POSSale.round_off_amount` field (migration `0032`). The tax breakdown
  (taxable/exempt/VAT) stays exactly as computed; only the grand total and
  this field change. `pos.html`'s `computeTotals()` mirrors the identical
  math and shows a "Round off: ±Rs. X" row in the tax box only when
  nonzero.
- **Fast PIN hashing.** `UserProfile.set_pin()` / `check_pin()` now use
  HMAC-SHA256 + a per-profile random salt instead of Django's PBKDF2
  password hasher. The real security boundary for a 4-digit PIN is
  `PosUnlockView`'s 7-attempt/5-minute lockout, not hash cost — PBKDF2
  (re-checked against *every* staff profile on each unlock attempt) was the
  actual cause of the ~4 second PIN-unlock delay on PythonAnywhere's
  free-tier CPU. This is a different stored format, so a PIN set under the
  old scheme won't verify anymore — **staff need to re-set their PIN once
  via the admin form after this reaches PythonAnywhere.** As of this
  writing that deploy hasn't happened (see "Deploy status" below), so no
  PIN has actually needed re-setting yet — don't assume this step is done
  without checking the deploy status first.
- **`InventoryMovement.quantity` precision fix.** See [[Known-Bugs]] —
  widened from 2 to 3 decimal places (migration `0033`) so an exact-gram
  scale reading (Papaya, Dragon Fruit, Cauliflower, Coriander, Watermelon)
  that isn't a multiple of 10g actually saves.
- **Low-stock tracking + POS restocking** (committed and pushed to GitHub
  `main` — see "Deploy status" below; the follow-up fixes and Repay Credit
  rework in the section above are a later, still-uncommitted pass on top of
  this). `Product.low_stock_threshold`; a new
  `purchase` `InventoryMovement` type (stock bought from an outside
  supplier) kept deliberately distinct from both `harvest` (farm-origin)
  and `adjustment_add` (a stock-count correction, not a real incoming
  purchase) — never merge these three. `shop/stock.py::get_stock_table_rows()`
  is the one shared query reused by both `ProductAdmin`'s new sortable
  "Stock alert" column and the new POS stock screen, so the two can't show
  different numbers — excludes `fixed_weight` products (tracked per-animal,
  not by a stock count; eggs are `fixed_quantity` so they correctly stay
  in), includes disabled products, sorted low-stock-first (ascending
  current stock) then alphabetical. A `post_save` signal in
  `shop/signals.py` sends one Telegram alert the moment stock crosses
  *down* past `low_stock_threshold` — not on every later sale while it
  stays below, and it re-arms once a restock pushes stock back above
  threshold. New staff-only `GET /api/v1/pos/stock/` +
  `POST /api/v1/pos/restock/` (picks `harvest` vs `purchase` by
  `product.origin`, `full_clean()` inside `transaction.atomic()`), and a
  new `stockView` screen in `pos.html` (sticky header row *and* sticky name
  column, vertical scroll only) with a Restock modal styled like the
  existing Repay Credit modal.

### Deploy status — check this before assuming any of the above is live

- **Offers/round-off/PIN-hashing/quantity-precision** (commit `7eca669`),
  **low-stock tracking + restocking** (commit `3748afa`, plus the stock-box
  simplification in `d61daff`), **and the stock follow-up fixes + Repay
  Credit table rework** (commits `2ebe357`/`38390bb`, including migration
  `0035` — `Customer.address` required): all committed and pushed to GitHub
  `main` — confirmed via `git rev-list --left-right --count
  origin/main...HEAD` returning `0  0` (local `main` and `origin/main` point
  at the same commit, `38390bb`). **Not confirmed deployed to
  PythonAnywhere as of this writing** — needs `git pull`, then `migrate`
  (applies `0030` through `0035`), `collectstatic`, and a Web tab reload
  there. Until that actually happens on the live PythonAnywhere instance:
  real staff PINs are still on the old PBKDF2 hashes and still work as
  before, round-off/offers/combo pricing aren't touching real sales, the
  low-stock/restock screen doesn't exist there at all yet, and Repay Credit
  is still whatever it was before this rework.
- **This round's Repay Credit full-view conversion, Logout button, and the
  offer/coupon fixes** (see the top section above): **not committed at
  all** — still local working-tree changes as of this writing, doesn't
  exist on `main`, let alone on PythonAnywhere. No new migration this round
  (no model changes), so once committed this is a pure code deploy —
  `git pull`, `collectstatic`, Web tab reload, no `migrate` step needed for
  this part specifically (though check whether migration `0035` above has
  landed yet first).

## Recently landed (as of 2026-10-01, commit 76865d4 + POS Phase C)

- The `django.ce.exceptions` typo (see [[Known-Bugs]]) is fixed and committed — no longer an open item.
- POS Phase A (staff roles + PIN unlock) and Phase B (Customer/credit ledger, split payments, dormant VAT scaffolding) are built, tested, committed, and confirmed on `main`.
- POS Phase C (coupon discounts) added this session: `resolve_pos_coupon()` + coupon fields on `POSSale` + `/api/v1/pos/coupon/validate/` preview endpoint + pos.html coupon UI. 10 new tests, 122/122 passing locally. **Not yet migrated/deployed to PythonAnywhere as of this writing** — migration `shop/0030_possale_coupon_possale_discount_amount.py` needs `migrate` there after the next `git pull`.
- `ProductSellingUnit` (fixed_quantity selling units, e.g. jar sizes) and a shared weight/qty keypad modal with Kg/Gram switching also landed since the doc below was last accurate — check `shop/models.py` directly for the current full model list rather than trusting an older summary.

## Recently set up (2026-09-27)

- This Obsidian vault, `claude-mem` (local-only, Ollama-backed), as a companion to `CLAUDE.md`. See `03-Decisions/Decision-Log.md`.
- `.claude/` and `.agents/` skill directories exist from Claude Code skill installs (`skill-creator`, `find-skills`) — not application code, don't confuse with project structure.

## Not yet true

Anything in [[Roadmap]] is explicitly **not built**. Don't assume deferred work has been picked up without checking the actual code first.
