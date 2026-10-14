# ABMS Integration

The only legitimate REST API boundary in this project. See [[REST-API]] and [[ABMS]] for why it's the sole exception to "no REST layer between subsystems."

- **Endpoint:** `/api/inventory/movements/`
- **Auth:** token-authed
- **CORS:** allowed for `https://angan-baari.web.app`
- **Why it exists:** ABMS is a genuinely separate hosted system (Firebase/Firestore), with no other way to reach this Django project's database.

## Idempotency and reversal (abms_id / reversal_of_abms_id)

`InventoryMovement` is append-only — a harvest or waste entry can never be
edited or deleted once ABMS has pushed it. To let ABMS safely retry a POST
(network timeout, app restart) and to let it correct a harvest/waste it
later edits or deletes on its own side, the request body accepts two new
optional fields, mirroring `CostEntry`/`CostSyncView`'s own
duplicate/conflict/create pattern (`shop/costs_views.py`):

- **`abms_id`** (string, max 64) — the idempotency key for this one
  movement. Omit it and the endpoint behaves exactly as it always has: a
  plain create, 201, no dedup at all. This is the only way POS ever calls
  this endpoint — abms_id is purely an ABMS-sync concern. Stored as `NULL`
  when blank/omitted, never `''` (a unique column would otherwise treat
  every blank row as colliding with every other).
- **`reversal_of_abms_id`** (string, max 64) — set this to undo an earlier
  ABMS-sourced harvest or waste (e.g. the farm app edited/deleted a
  harvest after Django already has it). A reversal is a brand new row, not
  a change to the original.

### Response shapes (when `abms_id` is present)

| Case | Status | Body |
|---|---|---|
| New `abms_id` | 201 | full movement + `"status": "created"` (no `"code"`) |
| Same `abms_id`, identical content | 200 | the **existing** movement + `"status": "duplicate"` (nothing created, no `"code"`) |
| Same `abms_id`, different content | 409 | `"status": "error", "code": "abms_id_conflict"` (nothing changed) |
| Concurrent identical POSTs racing | 200 | whichever one actually landed, `"status": "duplicate"` (no `"code"`) |

"Identical content" compares `product`/`variant`/`movement_type`/`quantity`/
`batch_code`/`reversal_of_abms_id` only — not `source`, `note`, or
`related_order`. Every response that references a real row always includes
its `id`. Success bodies never carry a `"code"` field — only an error
response does.

### Reversal rules (when `reversal_of_abms_id` is set)

- `source` must be `'abms'`, and the original (looked up by its own
  `abms_id`) must also have `source='abms'` — not found → 400,
  `"code": "original_not_found"`.
- `movement_type='adjustment_remove'` may only reverse an original
  `'harvest'`; `movement_type='adjustment_add'` may only reverse an
  original `'waste'`. Any other combination, or a product/variant/
  batch_code mismatch against the original (see below) → 400,
  `"code": "invalid_reversal"`.
- `product`/`variant`/`batch_code` are derived from the original when
  omitted, and must match it exactly when supplied.
- Total reversed quantity against one original can never exceed the
  original's own quantity (summed across all its reversals) — 400,
  `"code": "over_reversal"`.
- The usual stock-negative check (`InventoryMovement.clean()`) still runs
  for a reversal exactly as it does for any other movement — 400,
  `"code": "insufficient_stock"`, carrying the check's own message.
- A closed `CropBatch` (`status='closed'`) now also blocks a reversal that
  carries that batch's `batch_code`, the same way it already blocked a
  plain harvest — `batch_code` was loosened from "harvest movements only"
  to "harvest, or an adjustment_remove/adjustment_add reversing one," and
  the closed-batch check sits inside that same code path — 409,
  `"code": "batch_closed"` (this one applies to a plain batched harvest
  too, not just a reversal).

### Error codes (abms_id / reversal_of_abms_id requests only)

Every non-2xx response to a request carrying `abms_id` or
`reversal_of_abms_id` includes a top-level `"code"`, so ABMS can branch on
it without parsing message text:

| `code` | HTTP | Meaning |
|---|---|---|
| `abms_id_conflict` | 409 | same `abms_id`, different content |
| `batch_closed` | 409 | harvest or reversal against a closed `CropBatch` |
| `insufficient_stock` | 400 | would push stock negative |
| `original_not_found` | 400 | `reversal_of_abms_id` doesn't match a known ABMS-sourced movement |
| `over_reversal` | 400 | total reversed would exceed the original's own quantity |
| `invalid_reversal` | 400 | wrong movement_type combination, or product/variant/batch_code mismatch against the original |

A generic validation failure unrelated to this feature (e.g. an unknown
`batch_code` on a plain, non-reversal harvest, or a missing `product`)
keeps the plain pre-existing `{"status": "error", "message": {...}}` shape
with no `"code"` — only the six scenarios above are guaranteed one.

### Known gap — batch costing (`shop/batch_report.py`)

A harvest reversal (`adjustment_remove`) is already correctly folded into
the FIFO batch report via the existing `CONSUMING_TYPES` tuple — no change
needed there. A **waste reversal** (`adjustment_add`) correctly restores
overall product stock (`InventoryMovement.current_stock()` sums
everything), but `_fifo_simulate()`'s own query deliberately excludes
`adjustment_add` from its batch-level accounting (see that function's
docstring) — so a batch's `waste_kg`/cost-per-kg/pool-remaining figures
stay stale after a waste reversal even though the ledger and overall stock
are correct. Flagged, not fixed — fixing batch costing was out of scope
for this change.

## What's bridged today vs. not

Not yet connected (see [[Roadmap]] for full list):
- Local Egg / Vermicompost inventory bridge (ABMS's `productionLog` / `vermiOut`) — planned to follow the same pattern as the existing harvest bridge.
- Goat/Chicken sales bridge — blocked on ABMS needing a live variant-picker UI so the operator selects the specific animal sold.
