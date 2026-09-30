# Known Bugs

## `InventoryMovement.clean()` — bad import (found 2026-09-26/27)

`shop/models.py`, inside `InventoryMovement.clean()`, had:
```python
from django.ce.exceptions import ValidationError   # WRONG — django.ce doesn't exist
```
should be `from django.core.exceptions import ValidationError`.

**Impact:** `clean()` threw `ModuleNotFoundError` on every call. Since `pos_create_sale` always calls `full_clean()`, **every POS sale failed** with a generic "could not complete this sale" error, regardless of cart contents. Website checkout was unaffected (never calls `full_clean()` — see [[Inventory-Rules]] strict/non-strict table).

**Status:** fixed and confirmed committed on `main` (verified 2026-10-01) — no longer a live bug. Kept here as a lesson, not an open item.

**Lesson:** `full_clean()`-dependent validation paths (the strict POS path in particular) can fail silently-ish behind a generic error message. If POS sales start failing again with a vague error, check for import/exception errors in `clean()` first.
