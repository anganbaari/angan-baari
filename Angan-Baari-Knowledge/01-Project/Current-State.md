# Current State

*Living document — update as work lands. Last checked: 2026-10-01.*

## Recently landed (as of 2026-10-01, commit 7eca669 + uncommitted stock work)

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
- **Low-stock tracking + POS restocking** (uncommitted as of this writing —
  see "Deploy status" below). `Product.low_stock_threshold`; a new
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

- **Offers/round-off/PIN-hashing/quantity-precision** (commit `7eca669`):
  committed and pushed to GitHub `main`. **Not confirmed deployed to
  PythonAnywhere as of this writing** — needs `git pull`, then `migrate`
  (applies `0030` through `0033`), `collectstatic`, and a Web tab reload
  there. Until that actually happens on the live PythonAnywhere instance:
  real staff PINs are still on the old PBKDF2 hashes and still work as
  before, and round-off/offers/combo pricing aren't touching real sales.
- **Low-stock tracking + restocking**: **not committed at all yet** — still
  local working-tree changes as of this writing, doesn't exist on `main`,
  let alone on PythonAnywhere. Needs its own commit + push before any
  deploy step is even possible; migration `0034` once it does land.

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
