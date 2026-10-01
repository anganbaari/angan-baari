# Known Bugs

## `InventoryMovement.quantity` only supported 2 decimal places (found 2026-10-01)

`shop/models.py`'s `InventoryMovement.quantity` was `decimal_places=2`, but
the POS's Kg/Gram keypad (built in an earlier session than the one that
found this) already produced 3-decimal-place weights in Gram mode — typing
a 335g scale reading gives `0.335kg`. Any weight that wasn't an exact
multiple of 10g failed `full_clean()` at Complete Sale with "no more than 2
decimal places."

**Impact:** silently broke the "exact" `weight_entry_mode` (Papaya, Dragon
Fruit, Cauliflower, Coriander, Watermelon — produce meant to be weighed on
a scale, not bought in round step sizes) for the *majority* of realistic
scale readings, since most gram readings aren't round multiples of 10. The
sale failed with a generic stock-conflict-shaped error message that had
nothing to do with actual stock levels.

**Status:** fixed — `quantity` widened to `decimal_places=3` (migration
`0033`). Checked first that nothing else assumed the old 2-decimal limit
(the API serializers auto-introspect the model field; admin display and
the money-rounding code in `shop/views.py` don't hardcode a precision; the
frontend keypad was already producing 3dp values, confirming it was the
actual bottleneck) before widening it. Covered by a dedicated test,
`test_exact_gram_scale_reading_not_a_multiple_of_10g_saves_end_to_end` in
`api/tests.py`. Part of commit `7eca669` — see [[Current-State]] for
whether that commit has reached PythonAnywhere yet.

**Lesson:** when a frontend input mode is deliberately built to support a
certain precision (the Gram keypad already used `.toFixed(3)`), check that
the backend field it eventually writes to actually supports that same
precision — don't assume the two layers agree just because each one looks
correct in isolation.

## `InventoryMovement.clean()` — bad import (found 2026-09-26/27)

`shop/models.py`, inside `InventoryMovement.clean()`, had:
```python
from django.ce.exceptions import ValidationError   # WRONG — django.ce doesn't exist
```
should be `from django.core.exceptions import ValidationError`.

**Impact:** `clean()` threw `ModuleNotFoundError` on every call. Since `pos_create_sale` always calls `full_clean()`, **every POS sale failed** with a generic "could not complete this sale" error, regardless of cart contents. Website checkout was unaffected (never calls `full_clean()` — see [[Inventory-Rules]] strict/non-strict table).

**Status:** fixed and confirmed committed on `main` (verified 2026-10-01) — no longer a live bug. Kept here as a lesson, not an open item.

**Lesson:** `full_clean()`-dependent validation paths (the strict POS path in particular) can fail silently-ish behind a generic error message. If POS sales start failing again with a vague error, check for import/exception errors in `clean()` first.
