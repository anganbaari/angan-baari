# Orders and Profile

How website/API orders are created, linked to accounts, shown in a
customer's profile, hidden, and cancelled. Related: [[Offers-and-Cart-Quote]],
[[Selling-Units]], [[Inventory]], [[REST-API]], [[Storefront-and-Vercel]].

## Placing an order: `POST /api/v1/orders/`

- Public. Guest, or a valid `Authorization: Token <key>`; an invalid or
  expired token is treated as a guest, never a 401
  (`_authenticate_optional()`).
- Every cart line is re-priced server-side with `quote_cart_line()` (see
  [[Offers-and-Cart-Quote]]); client prices are never used.
- Each `cart_snapshot` line records `product_id`, `weight`, `qty`,
  `variant_id`, `unit_id`/`selling_unit_id`/`selling_unit_name`,
  `normal_price`, `final_price`, `discount`, `offer_id`, `offer_title`.
  `cart_snapshot` is a JSONField, so these are new keys on an existing
  structure — no migration for pricing.
- Response (201): `status`, `order_number`, `id`, `pricing_summary`
  (`subtotal`, `total_discount`, `total`) and `errors` — a list of
  `{product_id, error}` for lines that were skipped. A line with an
  unavailable selling unit or an invalid weight is skipped and reported;
  the order is still created from the lines that priced. If **no** line
  priced, the response is a 400 `{"status": "error", "message": "No valid
  items in cart.", "errors": [...]}`.
- Stock: `create_order_inventory_movements()` →
  `create_inventory_movements_from_snapshot(strict=False)` (best effort,
  as before). Selling-unit lines deduct `qty × quantity_in_base_units`.

## Orders belong to accounts by `user`, never by email

`ProductOrder.user` (nullable FK to `auth.User`, `SET_NULL`, migration
`0051_productorder_hidden_by_customer_productorder_user`) is set only when
the create request carried a valid token. `GET /api/v1/profile/orders/`,
cancel and reorder all match `user=request.user` — there is **no** email
fallback, because the project has no email verification and an unverified
email is no proof of ownership. Guest orders and orders placed before the
field existed have `user=None`; link one by hand in Django admin if needed.
See [[Decision-Log]].

## Profile order history

`GET /api/v1/profile/orders/` (`OrderHistorySerializer`) returns the
customer's orders, newest first, excluding hidden ones. Rows carry
`cart_snapshot` (per-line prices) and `pricing_summary`. An order placed
before per-line pricing existed has no `final_price` keys, and its
`pricing_summary` is `null` — clients show those orders as they always did.

## Remove from history: `POST /api/v1/profile/orders/<id>/hide/`

Token-authed, owner only (another user's id 404s). Allowed only for
**Delivered** or **Cancelled** orders (400 otherwise). Sets
`ProductOrder.hidden_by_customer = True` and nothing else — the order, its
`InventoryMovement` rows and its admin entry are untouched. The flag only
filters `GET /profile/orders/`. See [[Decision-Log]].

## Cancel and stock

Three paths can set `status='cancelled'`: the profile cancel
(`OrderCancelView`), the emailed cancel-token link (`cancel_order()`), and
a plain status change in Django admin. All of them end in
`ensure_order_cancellation_stock_returned(order)` (`shop/views.py`): it
creates the `return` movements unless the order never had a `sale`
movement or already has a `return` one, so it is **idempotent**. A
`post_save` signal (`return_stock_when_order_cancelled`, `shop/signals.py`)
calls it for any `ProductOrder` save with status cancelled — that is what
makes an admin cancel return stock, and it is safe alongside the two
explicit calls.

**Known gap:** reactivating a cancelled order (status back to pending or
confirmed) does **not** deduct stock again. Nothing re-creates the `sale`
movements, so stock stays returned for an order that is live again.
Correct it with a manual `InventoryMovement` adjustment for now.

## Tests

`api/tests_profile_orders.py` (user link, hide, cancel),
`api/tests_offers_pricing.py` (quote + order pricing),
`api/tests_selling_units_followup.py` (selling units, weight validation,
admin-cancel stock return).
