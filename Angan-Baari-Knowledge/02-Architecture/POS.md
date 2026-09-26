# POS

- **Auth:** server-side staff login (`@staff_member_required`), **not a token**. Deliberate — avoids the token-exposure/CORS problem entirely, since unlike ABMS the POS has no reason to be hosted separately.
- **Frontend:** vanilla JS served by Django (`templates/pos.html`) — not React, not separately hosted. See [[Architecture-Decisions]] for why this isn't a gap to "fix."
- **Idempotency:** `POSSale.client_sale_id` — a UUID generated client-side before the sale is sent. A double-tap, a retried request, or a queued offline sale being resynced all return the original result instead of creating a duplicate sale.
- **Inventory writes:** POS calls `create_inventory_movements_from_snapshot()` in `strict=True` mode — re-raises on failure, calls `full_clean()`, expected to be wrapped in `transaction.atomic()` so a failed line rolls back the whole sale. Contrast with website checkout's `strict=False`. Full rationale in [[Inventory-Rules]].

Known bug affecting POS specifically: see [[Known-Bugs]] (the `django.ce.exceptions` import typo broke `full_clean()` for every POS sale — fix is in progress, see `01-Project/Current-State.md`).
