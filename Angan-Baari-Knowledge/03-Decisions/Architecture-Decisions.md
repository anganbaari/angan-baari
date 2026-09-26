# Architecture Decisions (don't relitigate these)

Each of these was made deliberately. If the codebase seems to contradict one, treat that as a bug to flag, not license to silently "fix" the decision.

- **No REST API layer between POS and website.** Same Django project, same database, same process, shared helper. See [[System-Architecture]].
- **POS auth is server-side staff login, not a token.** Avoids token-exposure/CORS entirely — POS has no reason to be hosted separately (unlike ABMS).
- **POS is vanilla JS served by Django**, not React, not separately hosted.
- **`InventoryMovement` is an append-only ledger.** `current_stock()` is always derived, never stored. See [[Inventory-Rules]].
- **`create_inventory_movements_from_snapshot()` has two modes** — `strict=False` for website (never raises, matches the site's best-effort email pattern), `strict=True` for POS (re-raises, wrapped in `transaction.atomic()`). An in-person sale hasn't been promised to a customer yet, so blocking it here is more correct than the silent-oversell tolerance accepted online.
- **`POSSale.client_sale_id`** — client-generated UUID for idempotency (double-tap / retry / offline resync).
- **Fixed-weight animals are individual `ProductVariant` rows**, never a new `Product` per animal. `Product.fixed_weight` is only a fallback for zero-variant products.
- **`Product.barcode` has two independent workflows** — a pre-printed-label-scan field, and a separate drafted `AB`+id auto-generation scheme. Don't assume only one exists; confirm what's actually merged.
- **`Product.origin`** (`farm` vs `sourced`): Apple, Kiwi, Grapes, Dragon Fruit, Watermelon are `sourced`; everything else `farm`.
- **`Product.is_available` is a manual toggle**, not derived from `current_stock() > 0` — deliberate deferral, not a bug.
- **Cart/saved-for-later are session-based**, keyed by `line_key` (plain id / `{id}_{weight}` / `{id}_v{variant_id}`). `Wishlist` is a real DB model (needs to persist across devices).
- **Coupons vs Offers are two separate discount systems** — coupons are code-entry order-level (one per order, `max_uses`); offers are automatic per-product/category or fixed-price bundles. Both gate on `is_live()`.
- **SQLite retained in production deliberately** — Postgres migration deferred until concurrent writes are actually needed. Don't "helpfully" migrate.
- **No React/Next.js anywhere in this project** — considered and explicitly rejected.

See [[Decision-Log]] for dated entries going forward.
