# Receipts and Pricing

Covers the POS money-rounding rule (`price_pos_cart_line()`) and the two
printable pages built on top of it: the sale receipt and the refund slip.
See [[POS]] for the rest of the POS architecture, [[Refunds]] for the
refund side specifically.

## Pricing: `price_pos_cart_line()`

`shop/views.py`'s `price_pos_cart_line()` is the **single place** every
POS cart line's price is computed, called once per line from
`create_pos_sale()` regardless of pricing mode — extracted specifically
so the whole-rupee rounding rule below (and any future per-line pricing
change) only ever has to be right in one place.

**Whole-rupee rule:** a `variable_weight` line's amount (unit price ×
weight) is rounded to the nearest whole rupee (`ROUND_HALF_UP`) — **after**
any single-product offer discount is applied to the unit price, and
**before** a combo override replaces the line's price with its fixed
share of the combo (a combo-priced line is never rounded again on top of
that — the combo's own apportionment already produces whole-rupee shares
via its own largest-remainder split, see [[Refunds]]). `fixed_weight`
(animal) and `fixed_quantity` lines are **never** rounded this way — in
practice their prices already land on a whole rupee (an animal's price,
a catalogue/selling-unit price), so there's nothing to round.

**Why this exists:** before this rule, only the sale-level total was
rounded (right before the payments-sum check) — a cart made of ordinary
lines would often end up needing a few paisa of "round off" absorbed at
the end. Rounding at the LINE level instead means an ordinary sale's
lines, subtotal, and total all already agree exactly, with nothing left
for the order-level step to fix up. The order-level round-off step itself
is **unchanged** — it still runs, and still matters for the one case that
can still produce a fractional total after whole-rupee lines: a
**percentage coupon's** discount on an already-whole-rupee subtotal
(e.g. 10% of 117 = 11.70, not a whole rupee).

**Browser/server agreement is load-bearing.** `create_pos_sale()` still
enforces the pre-existing strict rule that payment lines must sum to
**exactly** the server-recomputed total — a single paisa of
browser/server rounding drift would make an otherwise-valid sale get
rejected. `templates/pos.html`'s JS mirrors the rounding with matching
**integer** arithmetic (`roundVariableWeightLineTotal()` — price in
paisa × weight in milli-kg, quantized by an integer floor+remainder
check, never a bare floating-point multiply) specifically to avoid any
binary-floating-point drift at a `.50`-rupee boundary.

## Receipts

Printable sale receipt: `GET /pos/receipt/<sale_number>/`
(`pos_receipt_view`, `templates/pos_receipt.html`) — staff-only, reachable
by any staff login (not scoped to who rang the sale up), since it's also
the reprint path (`POSSaleAdmin.receipt_link()`). 80mm by default,
`?width=58` switches the print CSS to a 58mm roll.

**Not yet verified on real thermal printer hardware.** 58mm support is
implemented and covered by (string-assertion) tests confirming the right
CSS/markup renders, but nothing here has been checked against an actual
80mm/58mm thermal printer — same "needs real hardware before fully
trusted" caveat the POS-PWA offline work carries (see [[POS]]).

**Header/branding** — logo, shop name (+ English name under it), and the
Nepal flag render as ONE centred flex group with a small fixed gap, via
`templates/_receipt_header.html` (shared with the refund slip — see
below). Any blank image is skipped entirely (the `<div>` isn't rendered
at all), not shown broken; trade-off: if only one of logo/flag is
present, the group sits slightly off true centre, since nothing reserves
space for the missing side. Address, phone/PAN (Devanagari digits), and
the social-QR row (Website/Tiktok/facebook, via
`templates/_receipt_social_qr.html`, also shared) all come from the
`BusinessSettings` singleton: `shop_name`, `shop_name_en`,
`receipt_logo`, `receipt_flag`, `qr_website`, `qr_tiktok`, `qr_facebook`,
`receipt_footer`, `pan_vat_number`.

**Barcode.** The sale receipt has a Code128 barcode (JsBarcode, vendored
at `static/js/vendor/jsbarcode.min.js` — no CDN, no new pip/npm
dependency) encoding the **full**, untouched `sale_number`. The refund
slip must **never** have one — explicitly asked for and left out on
purpose; don't add it back without being asked again.

**Display quirks, all deliberate:**
- Displayed Sale No. always strips the `POS-` prefix (the barcode still
  encodes the original, untouched value).
- Phone/PAN digits are converted to Devanagari; Sale No. and date/time
  are **not** (shown in plain Latin digits).
- Dates are shown in B.S. (via `shop/bs_calendar.py`'s `ad_to_bs()`),
  falling back to the plain A.D. date if the sale's date is outside the
  calendar table's covered range.
- Cashier is shown by **first name only**.

**No "Round off" row anywhere.** The receipt shows what was actually
paid, not the pre-rounding arithmetic — Subtotal/कर स. मूल्य/Total are
made to agree exactly instead (`pos_receipt_view()`'s
`subtotal_display`/`exempt_value_display` logic absorbs the round-off
into those figures). Since the whole-rupee-per-line rule above now
usually makes `round_off_amount` zero for an ordinary sale anyway, this
mostly stopped being special-cased in practice — but the removal itself
predates that rule and was a deliberate design choice either way (see
[[Decision-Log]]).

**Shared partials.** `templates/_receipt_header.html` and
`templates/_receipt_social_qr.html` hold the header group and QR row
markup, included by both `pos_receipt.html` and `pos_refund_slip.html` so
the two pages can't silently drift apart. The CSS for these classes is
**still duplicated** in each page's own `<style>` block — these are two
separate self-contained print pages with no shared stylesheet mechanism,
so only the *markup* is shared, not the CSS; keep both copies in sync by
hand when touching `.r-header*`/`.r-social-qr*` rules.

## Refund slip

`GET /pos/refund/<refund_number>/` (`pos_refund_slip_view`,
`templates/pos_refund_slip.html`) — same conventions as the sale receipt
(staff-only, 80mm/`?width=58`, B.S. dates, cashier by first name, shared
header/QR partials above) but for a `Refund` instead of a `POSSale`. No
barcode, no thank-you line (neither was asked for). A weighed
(`variable_weight`) line's detail shows its weight with its unit (e.g.
`"0.25 kg"`), not `"Qty: 0.25"` — unit (`fixed_quantity`) lines keep the
plain `"Qty: N"` form.

Reachable from: the receipt itself (a screen-only "View refund slip(s)"
link under the Print button, hidden from the print output by the same
`@media print` mechanism as the Print button itself — see
[[Frontend-Lessons]] for a testing gotcha found here); the refund
screen's own "Previous refunds" list (per-sale) and "Recent refunds"
section (site-wide, last 20); the POSSale/Refund admin list columns; and
a persistent "Open slip" link on the post-refund success screen (not just
a button — stays usable even if the tab it originally opened was
closed).
