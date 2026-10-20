# Known Bugs

## Refund screen rounded a weighed line's quantity to zero (found 2026-10-20)

`templates/pos_refund.html`'s `activeSelections()` computed how much of a
whole-line refund (variable-weight, fixed-weight animal, or combo
component) to send back to the server as
`Math.round(parseFloat(line.remaining_quantity))`.

**Impact:** for a `variable_weight` line, `remaining_quantity` is a
**weight** in kg, not a count. `Math.round(0.25)` is `0` — so a ticked
checkbox on a sub-0.5kg line (the reported case: 0.250kg, Rs 20) silently
dropped that line's amount out of the refund total, leaving "Refund total"
stuck at Rs 0 and the confirm button permanently disabled, with no
indication why. A weighed line at or above 0.5kg had the opposite
problem: the rounded quantity no longer matched `sale_line.quantity`
exactly, which the server correctly rejected as "not a full-line
refund." Fixed-weight animals and combo components that happened to be
`fixed_quantity` were unaffected (their quantities are always whole
numbers already).

**Status:** fixed — removed the `Math.round()`, sending the exact
`parseFloat(remaining_quantity)` value back (matches
`sale_line.quantity` exactly, as a whole-line refund requires). Also
added a visible "why is this disabled" message above the confirm button,
since the original bug's worst symptom was a disabled button with zero
explanation. Covered by a standalone Node reproduction script (no JS
test runner in this repo) plus server-side regression tests confirming
the lookup endpoint reports a positive refundable amount for a weighed
line and that refunding one actually completes.

**Lesson:** never apply `Math.round()` (or any rounding meant for a
*count*) to a value that might be a *weight* — check what unit a field is
actually in before reusing a rounding helper written for a different
field. See [[Refunds]].

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
