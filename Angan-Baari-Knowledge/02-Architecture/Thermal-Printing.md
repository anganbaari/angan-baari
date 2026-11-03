# Thermal Printing

How a POS sale actually gets onto paper: the hardware, the silent-print
setup, and the print-quality tuning knobs. See [[Receipts-and-Pricing]]
for the receipt/refund-slip page content and markup themselves; this note
covers only the printing mechanism and the physical printer.

## Hardware

- **Printer: Zy-Q821**, Windows driver name **"POS-80C"**, 80mm roll
  (actual roll width 79.5mm), USB.
- Driver installed from a generic POS-80C driver (`pos-printer-drv.exe`,
  an unofficial third-party source — no official maker site was found for
  this printer).
- Windows' built-in **"Generic / Text Only" driver does not work** for
  receipts — no graphics support, no Nepali (Devanagari) text. The
  POS-80C driver above is required.

## Real print area is ~72mm, not 80mm

The roll is 80mm but the printer's actual printable area is about 72mm —
content that assumes the full 80mm gets clipped on the right.

- `--receipt-print-width` (CSS var, `templates/pos_receipt.html` /
  `templates/pos_refund_slip.html`) is computed server-side as
  `content_width_mm` in `pos_receipt_view()` / `pos_refund_slip_view()`
  (`shop/views.py`) — **72mm** for the 80mm roll, unchanged for 58mm.
- `--receipt-print-shift` (same files) nudges the content box left to
  centre it within this specific printer's offset — **-4mm** for 80mm, 0
  for 58mm.
- Both are single server-side values (`content_width_mm`/
  `content_shift_mm`) — retune them there if a different printer or a
  different unit needs different numbers. `@page { size: {{ width_mm }}mm
  auto; margin: 0; }` still spans the full roll width; the content box is
  centred inside it via these two variables.
- **`?width=58` (58mm roll) is implemented and covered by tests, but
  still not verified on real hardware** — only the 80mm/Zy-Q821 path
  above has actually been printed and checked.

## Print-quality rules

- All receipt text is pure `#000` at font-weight 500+ — a thermal head
  prints grey/lighter weights as faint or missing entirely.
- The **flag** image uses the inline SVG `thermalThreshold` filter
  (greyscale + threshold to pure black/white) — a kept, continuous-tone
  image prints as noise on this printer.
- The **logo** is a pre-made 1-bit dithered PNG (Bayer dither, the
  current version about 25% lighter than the first version tried),
  uploaded in `BusinessSettings.receipt_logo` admin field, shown with
  `image-rendering: pixelated` at `--receipt-logo-size` (80px) — **not**
  run through `thermalThreshold` (that filter would destroy an
  already-1-bit image's dither pattern).
  - `--receipt-logo-brightness` (CSS var, default `1.4`) exists but has
    **almost no visible effect on a 1-bit image** — `brightness()` can't
    lighten a pixel that's already pure black. To actually change how
    dark the printed logo looks, replace the uploaded image with a
    lighter/heavier dither, not this variable.

## Silent printing (Chrome `--kiosk-printing`)

Chrome started with the `--kiosk-printing` flag prints directly to the
Windows **default printer** with no dialog at all — this is what makes
"Print bill" silent on the shop computer.

- The POS runs from a dedicated shortcut, not a normal Chrome window:
  ```
  chrome.exe --kiosk-printing --user-data-dir="C:\ChromePOS" --app=https://anganbaari.pythonanywhere.com/pos/
  ```
- **POS-80C must be the Windows default printer**, and "Let Windows
  manage my default printer" must be turned **off** (otherwise Windows
  can silently switch the default away from it).
- The `--kiosk-printing` flag is **ignored if Chrome is already running**
  under that `--user-data-dir` — a stray already-open Chrome window using
  the same profile will make the shortcut's flag do nothing on the next
  launch.
- "Print bill" (the sale-complete overlay, `templates/pos.html`) prints
  by creating a hidden `<iframe>` pointed at `/pos/receipt/<sale_number>/`
  and calling `print()` on its `contentWindow`. Two things are load-bearing
  here, both found by testing on the real printer:
  1. The iframe must have a **real, non-zero size** — a `0x0` iframe
     doesn't reliably take the print job in Chrome; it printed the parent
     `/pos/` page instead (with Chrome's default header/footer) the first
     time this was built.
  2. `pos_receipt_view` carries `@xframe_options_sameorigin`
     (`shop/views.py`) specifically so this same-origin iframe is allowed
     to load at all — the site-wide `X-Frame-Options: DENY`
     (`XFrameOptionsMiddleware`, `farmsite/settings.py`) blocks it
     otherwise, either failing to print or printing the POS page.
     **This decorator is scoped to this one view and `pos_refund_slip_view`**
     (see "Refund slip printing" below) — every other view keeps the
     global `DENY`.
  `printReceiptViaIframe()` also waits for the iframe's images and
  `document.fonts.ready` before calling `print()`, so nothing prints
  half-loaded. `POS_SHELL_CACHE` was `pos-shell-v25` (`static/js/
  pos-offline-queue.js`) at the point this was last verified working —
  confirm the current value with the file itself, not this note.

## Refund slip printing

The refund slip (`/pos/refund/<refund_number>/`, `pos_refund_slip_view`)
prints the same way: a hidden-iframe `print()` call, triggered from
`templates/pos_refund.html`'s "Print refund slip" button on the
post-refund success screen, and from "Reprint slip" buttons in both the
"Previous refunds" (on a looked-up sale) and "Recent refunds" (site-wide,
last 20) lists.

- `printRefundSlipViaIframe()` is a deliberate **copy** of `pos.html`'s
  `printReceiptViaIframe()` into `pos_refund.html` itself, not a shared-JS
  refactor — `pos.html` and the proven sale-receipt print flow are
  untouched by this. Keep the two in sync by hand if the printing
  mechanism itself ever changes.
- `pos_refund_slip_view` carries `@xframe_options_sameorigin` for the same
  reason `pos_receipt_view` does — it's now the only other view with this
  override.
- The slip still has **no barcode** (see [[Receipts-and-Pricing]]) —
  unaffected by how it's printed.
- `POS_SHELL_CACHE` is **not involved** here — `pos_refund.html` doesn't
  register the service worker, isn't precached, and isn't the `/pos/`
  navigation the worker's shell-cache logic matches on, so it isn't
  service-worker-tracked at all.

## New shop computer: setup checklist

1. Install the POS-80C driver (`pos-printer-drv.exe`).
2. Plug in power and USB.
3. Set POS-80C as the **Windows default printer**; turn off "Let Windows
   manage my default printer".
4. In the driver's printer properties, set the default paper size to
   **RECEIPT (80mm x 297mm)**.
5. Create the `chrome.exe --kiosk-printing --user-data-dir=... --app=...`
   shortcut above (adjust the path/URL as needed).
6. Launch it once and log in in that Chrome profile (the profile persists
   the session in `--user-data-dir`).
7. Ring up a test sale and tap "Print bill" to confirm silent printing
   actually works end to end on this machine.
