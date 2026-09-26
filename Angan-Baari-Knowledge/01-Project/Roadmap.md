# Roadmap / Deferred Work

Known, intentional, not yet built. None of these should be assumed complete without checking the code.

- **Local Egg / Vermicompost inventory bridge from ABMS** — hook into ABMS's `productionLog` / `vermiOut`, same pattern as the harvest bridge (see [[ABMS-Integration]]).
- **Goat/Chicken sales bridge** — needs a live variant-picker in ABMS; the operator must pick which specific animal was sold, never auto-pick "cheapest available" (see [[Inventory-Rules]] on `ProductVariant`).
- **React POS screen** — not planned. See [[Architecture-Decisions]] — POS staying vanilla JS is deliberate, not a gap.
- **Mango Pickles inventory modeling** — no movement type yet for "raw mango converted into jars of pickle."
- **Mother Goat, Pathi, Lady Goat (meat), Mother Chicken** — postponed product lines.
- **Postgres migration** — deferred until the POS needs concurrent writes. Don't migrate "helpfully."
- **Live digital scale reading (Web Serial API)** — deferred until a scale is bought and its protocol known.
- **Batch/expiry tracking** — deferred.
- **`profile.html` honeycomb nav** — still has the old pill-style `.nav-links`, hasn't been migrated like the rest of the site (see [[Frontend-Conventions]]).
- **Barcode label printing** — `generate_barcodes` management command + staff-only `/pos/labels/` print view (JsBarcode, Code128, client-side) — drafted but **not confirmed merged**. Check before building on top of either.
