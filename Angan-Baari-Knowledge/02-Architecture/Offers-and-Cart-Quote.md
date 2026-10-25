# Offers and Cart Quote

How the public website/API prices a cart and applies offers. The Vercel
storefront (see [[Storefront-and-Vercel]]) is the main caller. POS offer
handling is separate and unchanged — see [[POS]] and [[Receipts-and-Pricing]].
Related: [[Selling-Units]], [[Orders-and-Profile]], [[REST-API]].

## The rule: the server is the only price authority

`POST /api/v1/cart/quote/` and `POST /api/v1/orders/` price every line
themselves via `quote_cart_line()` (`shop/views.py`). The client sends
only identifiers and amounts (product, weight, quantity, variant, selling
unit) — a price in the request body is ignored. The legacy session cart's
`add_to_cart_offer()` follows the same rule: it recomputes from a live
`Offer` instead of trusting a posted `offer_price`
(`shop/tests_offer_cart_security.py`). See the Decision-Log entry
"Server-side pricing authority" in [[Decision-Log]].

## Offers: visible to everyone, priced only for logged-in customers

- `GET /api/v1/offers/` (`PublicOffersListView`, public, no auth) lists
  live offers: `discount_offers` (one row per offered product: original
  and offer price, `discount_percent`, `offer_title`, `end_date`,
  `is_available`) and `combo_bundles` (items as `{name, quantity}`, combo
  price, original price, `savings_percent`, `whatsapp_message`).
- Combos are **display-only**: no cart pricing, customers order them over
  WhatsApp. Their items carry no unit.
- A guest sees the offers but is never charged the offer price. The quote
  tells the client when a login would have changed the price
  (`login_required_for_offers`); the Vercel page uses that flag for its
  "log in to get this price" prompt.

## Choosing the offer: `resolve_automatic_offer(product)`

Offers are applied **automatically** — there is no human picking one. Among
every live, non-combo `Offer` whose `get_products()` includes the product,
the one giving the **lowest** discounted price (on `product.price`) wins;
ties go to the newest offer. The winner only decides *which offer*: the
real line price is then computed by `price_pos_cart_line()` with that
offer's id, so weight, quantity, variant and selling-unit pricing stay
exact. A product priced only through selling units or variants can still
win an offer (ranking treats a blank price as 0).

POS offers are different: the operator chooses `offer_id`
(`resolve_pos_offer_discount()`); nothing picks among candidates there.

## `quote_cart_line()` in short

1. `variable_weight` lines: weight must be a valid number, at least
   `weight_step`, and a whole multiple of it (website/API only — the POS
   is deliberately unchanged). A `weight_step` of 0 falls back to 0.50
   (`product.weight_step or '0.50'`).
2. `price_pos_cart_line()` computes the normal price — the same function
   the POS uses, so rupee rounding matches (whole-rupee rounding on
   weighed lines, after the offer discount).
3. If an offer exists **and** the caller is authenticated, the line is
   priced again with that offer; otherwise `final_price == normal_price`.
4. Returns normal/final/discount, offer id/title, `offer_available`, and
   the selling unit id/name. A line it can't price raises
   `POSSaleValidationError`; callers turn that into a per-line `error`.

Prices in responses are **line totals**, not unit prices.

## `POST /api/v1/cart/quote/`

Public; token optional (`_authenticate_optional()` in `api/views.py`: no
header = guest, invalid/expired token = guest, never a 401). Body
`{"lines": [{product_id, weight, quantity, variant_id, selling_unit_id}]}`.
Response: `lines` (each priced line, or `{product_id, error}`),
`subtotal`, `total_discount`, `total`, `offers_applied`,
`login_required_for_offers`. One bad line never fails the others.

## Known gaps

- Coupons are not applied by the Vercel cart (the storefront only lists
  them, read-only).
- Combo bundle items carry no unit.
- The guest teaser depends on `login_required_for_offers`.
- `weight_step` 0 products (e.g. Coriander) are quoted as step 0.50.
