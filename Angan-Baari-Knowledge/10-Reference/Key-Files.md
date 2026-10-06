# Key Files

- `shop/models.py` — `Category`, `Product`, `ProductVariant`, `NewsletterSubscriber`, `ContactMessage`, `ProductOrder`, `Review`, `Wishlist`, `Offer`, `BundleItem`, `Coupon`, `InventoryMovement`, `POSSale`
- `shop/views.py` — all view logic: cart (session-based), save-for-later, wishlist, checkout, coupon/offer application, POS (`pos_view`, `pos_service_worker`), the staff Reports dashboard shell (`dashboard_view`, `_is_owner_or_manager()`), the shared `create_inventory_movements_from_snapshot()` and `create_pos_sale()` helpers
- `shop/reports.py` — all Reports dashboard aggregation (read-only). See [[Reports-Dashboard]].
- `templates/dashboard.html` + `static/css/dashboard.css` + `static/js/dashboard.js` — the Reports dashboard page (`/dashboard/`, staff owner/manager only). See [[Reports-Dashboard]].
- `static/vendor/chart.min.js` — Chart.js v4.4.1, vendored (not a CDN — see [[Frontend-Lessons]]), used only by the Reports dashboard.
- `shop/admin.py` — Django admin customizations: custom newsletter-campaign compose form (`NewsletterAdmin`), computed stock display on `ProductAdmin`
- `shop/emails.py` — all outbound email (Resend) + Telegram notifications
- `shop/imagekit_storage.py` — custom Django Storage backend for ImageKit
- `shop/signals.py` — connected in `apps.py`'s `ready()`
- `shop/sitemaps.py` — SEO sitemaps
- `templates/pos.html` — the POS screen. Installable PWA as of Phase 1
  (`static/pos-manifest.json`, `static/js/pos-sw.js`, scope `/pos/` only).
  `completeSaleBtn` posts to `POST /api/v1/sales/` directly now — the old
  `/pos/sale/` view/URL (`pos_create_sale`) was removed once nothing
  called it anymore.
- `static/js/pos-offline-queue.js` — IndexedDB sale queue shared between
  `pos.html` and `pos-sw.js` (replays a sale that failed to POST only
  because of a real connectivity drop, not a validation error)
- `shop/auth_views.py` — auth views, under `account/` URLs

## URL names in use

`home`, `shop`, `cart`, `offers`, `profile`, `login`, `pos`, `pos_service_worker`, `dashboard`, plus cart/wishlist/checkout sub-routes — see `shop/urls.py` for the full list. `pos_create_sale` was removed (POS-PWA Phase 1).
