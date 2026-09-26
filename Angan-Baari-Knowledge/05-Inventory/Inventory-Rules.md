# Inventory Rules

Field-level rules for `InventoryMovement` (see [[Inventory]] for the architectural summary).

- **`movement_type`:** `harvest` / `sale` / `waste` / `return` / `adjustment_add` / `adjustment_remove`
- **`source`:** `admin` / `abms` / `website` / `pos`
- **`current_stock()`** is always derived by summing the ledger — never stored directly. Never edit or delete a ledger row to "correct" stock; add a new `adjustment_add`/`adjustment_remove` row instead.
- **`clean()` enforces:**
  (a) quantity must be exactly 1 for `fixed_weight` products
  (b) stock can never go negative
  — as of 2026-09-27 this validation had been broken by an import typo; see [[Known-Bugs]] and check `01-Project/Current-State.md` for whether the fix has landed.

## `create_inventory_movements_from_snapshot()` — two modes

| | website | POS |
|---|---|---|
| `strict` | `False` | `True` |
| bad lines | silently skipped | raises |
| calls `full_clean()` | no | yes |
| wrapped in `transaction.atomic()` | n/a | yes (by caller) |

Rationale: an in-person sale hasn't been promised to a customer yet, so blocking it here is more correct than the silent-oversell tolerance accepted for an already-placed online order. This is intentional asymmetry, not inconsistency.

## Fixed-weight products (goats, chickens)

Each animal is its own `ProductVariant` row — own weight, own optional `price_override`. **Never create a new `Product` per animal.** `Product.fixed_weight` is only a fallback used when a fixed-weight product has zero variant rows.

The goat/chicken sales bridge from ABMS still needs a live variant-picker — see [[Roadmap]] — the operator must pick the specific animal sold, never auto-pick "cheapest available."

## `Product.barcode`

Two independent workflows — don't assume only one exists:
1. Scan a pre-printed blank barcode label into this field, then print that code onto the product's label.
2. A drafted `AB` + zero-padded-product-id auto-generation scheme (`generate_barcodes` management command) plus a staff-only `/pos/labels/` print view (JsBarcode, Code128) — **not confirmed wired into `urls.py`**. Confirm before assuming either exists.
