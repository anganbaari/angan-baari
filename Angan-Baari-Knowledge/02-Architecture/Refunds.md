# Refunds

Staff-only POS refund feature: lookup/process at `/pos/refund/`
(`templates/pos_refund.html`), printable slip at
`/pos/refund/<refund_number>/` (`templates/pos_refund_slip.html`). Backed
by `create_pos_refund()` (`shop/views.py`), the sole entry point — same
shape as `create_pos_sale()`. See [[POS]] and [[Receipts-and-Pricing]] for
the related sale-side pieces.

## Models

`Refund` (`refund_number` = `"RF-"` + 8 hex uppercase, mirroring
`POSSale.sale_number`; `sale` FK `PROTECT`; `created_by`; `method`;
`reason`; `note`; `total_amount`; `client_refund_id` unique) and
`RefundLine` (`refund` FK; `sale_line` FK to the original `POSSaleLine`;
`product`/`variant` snapshot refs; `description` snapshot text;
`quantity`; `amount`; `disposition` — `restock` or `discard`). Both are
fully read-only in Django admin (no add/change/delete), same append-only
spirit as `InventoryMovement`/`CreditTransaction`/`POSSale`.

## What can be refunded, and how

- A plain `fixed_quantity` line (**not** part of a combo) can be
  **partially** refunded, one base unit at a time (base-unit terms, e.g.
  pieces — not crates; see "Judgment calls" below).
- A `variable_weight` (weighed) line, a `fixed_weight` animal, and **each
  component of a combo** (regardless of that component's own pricing
  mode) are **whole-line-only** — a single yes/no selection, never a
  partial amount.

Remaining-refundable quantity/amount for a line is always
`POSSaleLine.quantity`/its allocated share minus whatever's already been
refunded against it (summed from `RefundLine`) — re-checked once up
front (fail fast) and once more **inside** `create_pos_refund()`'s own
`transaction.atomic()` block, immediately before each line's write, so
two refund attempts racing for the same remaining quantity can't both
succeed.

## Money: allocating a sale's total across its lines

`POSSaleLine.line_total` is the **gross**, pre-discount amount (see that
model's own docstring) — not what a line is actually "worth" out of what
was paid once an order-level coupon discount, VAT split, or cash
round-off has been folded into `POSSale.total_amount`. `allocate_sale_
total_to_lines()` (`shop/views.py`) splits the sale's real total across
its lines proportionally to each line's `line_total`, **largest-remainder
apportioned** (same technique `resolve_pos_combo_lines()` already uses to
split a combo's fixed price across its components) — every share is a
whole rupee, and they sum to the sale total exactly.

For a **partial** refund of a `fixed_quantity` line, that line's own
allocated share is further split into one whole-rupee share per base
unit via `_largest_remainder_shares()` — same largest-remainder idea, one
level down. A new refund request takes exactly the shares for the units
it's consuming, continuing on from however many units were already
refunded; refunding the line's remaining units exactly reproduces its
full allocated share.

**Guaranteed invariants:** (a) all refunds on a sale together never
exceed the sale's own total; (b) refunding every line in full reproduces
the sale total exactly; (c) every refund amount is a whole rupee. All
three fall out of the largest-remainder partition being a true partition
at every level (sale→line, line→unit) — nothing is separately enforced
as a sanity check, the math guarantees it.

## Stock

Per refunded line: "Back to stock" appends one `return`
`InventoryMovement`; "Discard" appends a `return` **and** a `waste`
movement of the same quantity (net stock effect zero, but the waste is
still recorded, same ledger-of-record spirit as everywhere else). The
`return` movement is always written **before** the `waste` one in the
same transaction, which is what guarantees the `waste` write never has to
remove more than was just put back — stock can never dip negative even
momentarily. `discard` is never a valid disposition for a `fixed_weight`
line (an animal can only go back to stock). The ledger stays append-only
either way: a refund never edits or deletes the original sale's
movements, it only adds new rows.

An `InventoryMovement.post_save` signal (`shop/signals.py`) already
re-derives `ProductVariant.is_available`/`Product.is_available` from
current stock on every movement — a refunded animal's variant becomes
available again automatically, no special-casing needed in the refund
code itself.

## Money back / credit

Staff choose exactly one method per refund: Cash, eSewa, Khalti, Bank
transfer, or "reduce customer's credit balance" (only offered when the
sale has a customer). The system only **records** the method — it never
moves money itself. A credit-balance refund creates a `CreditTransaction`
(`transaction_type='repayment'`) the same way `CreditRepayView` already
does, **except** it also sets `related_refund` — a field added
specifically so `shop/pl_report.py` can tell a genuine cash repayment
apart from a refund that only reduces what the customer owes with no
money actually changing hands (see [[Decision-Log]]). `related_pos_sale`
stays blank — a "standalone" repayment in that field's own terms.

## Reporting

Every report that nets sales against refunds (`shop/reports.py`'s
`get_summary`/`get_run_rate`/`get_insight_strip`/`get_sales_trend`/
`get_target_progress`, `shop/pl_report.py`'s revenue/`cash_collected`,
`shop/batch_report.py`'s per-batch revenue) counts a refund in **the
period the refund happened** (`Refund.created_at`), never the original
sale's own date. A refund can legitimately make a period's net sales go
negative if that period has no gross sale of its own to offset it — this
is correct, not a bug (see [[Decision-Log]]).

`shop/reports.py`'s `get_sales_by_operator` deliberately does **not** net
a refund against the original seller's gross total — gross sales stay
keyed by `POSSale.cashier` unchanged, and refunded amount is a *separate*
figure keyed by `Refund.created_by` (whoever actually processed the
refund, which may be a different staff member on a different shift).

`shop/pl_report.py`'s per-cost-centre revenue breakdown
(`farm_cost_centres`) is **not** netted against refunds — only the
top-level revenue figure is. A known, accepted gap (narrower scope than
the top-line netting), not an oversight.

### Batch revenue (FIFO attribution)

`shop/batch_report.py`'s `_fifo_simulate()` walks a product's
harvest/sale/waste movements and attributes `sold_kg`/revenue to whichever
crop batch ("pool") the stock was actually drawn from, oldest-first. A
`RefundLine` has no FIFO-pool concept of its own (that attribution is
simulated, never stored) — `_refund_revenue_reduction_by_pool()` recovers
it by finding the original `sale` `InventoryMovement` the refunded line
corresponds to (same sale + product + variant) and splitting the refund's
amount across whichever pool(s) **that movement** actually drew from, in
the same proportion. A movement that happened to straddle two pools
(stock ran out mid-sale) has its refund split the same way — there's no
way to know which specific physical units came back, so pro-rata by the
original draw is the documented, deliberate approximation; it's exact
whenever a movement only ever drew from one pool (the common case).
`_fifo_simulate()` returns **five** values now (`pools`, `sold_by_pool`,
`waste_by_pool`, `revenue_by_pool`, `movement_pool_draws`) — see
[[Known-Bugs]] for the regression this caused the first time.

`return` movements (refund stock restorations) are deliberately **not**
included in this FIFO walk at all — a refund's effect here is purely
financial (reducing a pool's attributed revenue), never a second stock
movement replayed through the simulation.

## Idempotency, permissions, Telegram

`client_refund_id` (client-generated UUID) is checked first, before any
validation — a repeat arrival returns the original `Refund` unchanged,
same pattern as `POSSale.client_sale_id`. Both refund endpoints
(`GET /api/v1/pos/refunds/lookup/`, `POST /api/v1/pos/refunds/`) and the
page views are staff-only; this feature is deliberately **online-only** —
no offline queueing, unlike sale creation. `GET /api/v1/pos/refunds/
recent/` lists the last 20 refunds site-wide, newest first, for
reprinting a slip without first knowing which sale it was against.

On every successful (non-replay) refund, one best-effort Telegram alert
is sent (`shop/emails.py`'s `send_telegram()`, default
`raise_on_failure=False` — a Telegram outage can never block or roll back
an otherwise-valid refund).

## Judgment calls made building this

- **Combo components are independently whole-line-only.** The brief's own
  wording ("combo/bundle lines: whole line only, a single checkbox") was
  read as *each component line* getting its own independent checkbox, not
  the whole bundle only ever being refundable as one atomic group — there
  is no cross-line linkage enforced server-side. A single component of a
  combo can be refunded alone.
- **A bulk `fixed_quantity` line is refunded in base units, not the
  selling unit it was sold in.** A product sold via a
  `ProductSellingUnit` (e.g. "Crate" = 30 pieces) is refunded in **pieces**
  (the product's base counting unit), not crates — simpler, and avoids
  re-deriving the crate conversion for refund purposes. Means the refund
  screen's quantity picker shows "pieces left," not "crates left," for
  such a product.
- **FIFO batch-revenue attribution is pro-rata by original draw, not an
  exact per-unit trace** — see "Batch revenue" above.
- **A credit-balance refund is explicitly excluded from
  `cash_collected`**, via the new `related_refund` field — this was a
  deliberate fix (the opposite of "treat it as cash"), not a pre-existing
  design; see [[Decision-Log]].
- **No time limit and no cashier-match rule.** Any staff login can refund
  any sale, any age, by whoever happens to be at the till — not
  necessarily the original cashier. Deliberate scope decision: building a
  time-window or an attribution-lock was judged disproportionate to this
  feature's brief.
