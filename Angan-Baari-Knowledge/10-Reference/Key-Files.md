# Key Files

- `shop/models.py` — `Category`, `Product`, `ProductVariant`, `NewsletterSubscriber`, `ContactMessage`, `ProductOrder`, `Review`, `Wishlist`, `Offer`, `BundleItem`, `Coupon`, `InventoryMovement`, `POSSale`
- `shop/views.py` — all view logic: cart (session-based), save-for-later, wishlist, checkout, coupon/offer application, POS (`pos_view`, `pos_create_sale`), the shared `create_inventory_movements_from_snapshot()` helper
- `shop/admin.py` — Django admin customizations: custom newsletter-campaign compose form (`NewsletterAdmin`), computed stock display on `ProductAdmin`
- `shop/emails.py` — all outbound email (Resend) + Telegram notifications
- `shop/imagekit_storage.py` — custom Django Storage backend for ImageKit
- `shop/signals.py` — connected in `apps.py`'s `ready()`
- `shop/sitemaps.py` — SEO sitemaps
- `templates/pos.html` — the POS screen
- `shop/auth_views.py` — auth views, under `account/` URLs

## URL names in use

`home`, `shop`, `cart`, `offers`, `profile`, `login`, `pos`, `pos_create_sale`, plus cart/wishlist/checkout sub-routes — see `shop/urls.py` for the full list.
