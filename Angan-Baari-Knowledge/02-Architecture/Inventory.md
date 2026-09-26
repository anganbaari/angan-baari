# Inventory (Architecture-Level)

`InventoryMovement` is an **append-only ledger** — rows are never edited or deleted directly. `current_stock()` is always *derived* by summing the ledger, never stored as a standalone field.

Both the website and POS funnel through one shared helper, `create_inventory_movements_from_snapshot()` (in `shop/views.py`), in two different modes. This asymmetry is intentional, not an inconsistency — see [[Inventory-Rules]] for the full field-level rules (`movement_type`, `source`, `clean()` validation) and the strict/non-strict rationale.

Fixed-weight products (goats, chickens) are each an **individual animal**, tracked as a `ProductVariant` row — never a new `Product` per animal. See [[Inventory-Rules]].

For how ABMS feeds this ledger, see [[ABMS-Integration]].
