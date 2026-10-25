# Selling Units

`ProductSellingUnit` (`shop/models.py`) lets one `fixed_quantity` product be
sold in several named units — for example eggs by the piece or by the crate.
It is the `fixed_quantity` counterpart of `ProductVariant` (animals). Related:
[[Offers-and-Cart-Quote]], [[Orders-and-Profile]], [[POS]], [[Inventory]].

## Model

- `product` (FK, `related_name='selling_units'`), `name` (one of piece, kg,
  gram, dozen, liter, milliliter, crate; unique per product), `price`,
  `quantity_in_base_units`, `is_default`, `is_available`.
- Only meaningful for `pricing_mode='fixed_quantity'`. A product with no
  rows behaves as before: its own `price` / `price_unit` and a plain
  quantity stepper.

## Price is independent of the base price

A unit's `price` is typed in directly — a crate is usually a bulk discount,
not 30 × the piece price. A line is priced by **the unit's own price × the
count** (`price_pos_cart_line()`); it is never `product.price ×
quantity_in_base_units`. Offers discount that unit price too, exactly as in
the POS. `quantity_in_base_units` is used **only for stock**: selling `n`
of a unit deducts `n × quantity_in_base_units` base units.

## In the API

- Products API: `selling_units` — only `is_available=True` units, and only
  for `fixed_quantity` products (`[]` otherwise) — each
  `{id, name, label, factor, is_default, price}` (`name` is the machine
  name, `label` the display name, `factor` = `quantity_in_base_units`).
  Also `base_unit_label` (the product's `price_unit`), `min_quantity` (1 for
  `fixed_quantity`, otherwise null) and `step_quantity` (`weight_step` for
  `variable_weight`, otherwise null).
- Cart quote / order lines accept an optional `selling_unit_id`. A unit
  that is unavailable or belongs to another product rejects just that line
  (`error` on the quote; `errors` entry on the order).
- Quote/order lines return and store `selling_unit_id` and
  `selling_unit_name` (the display name, e.g. "Crate"). A snapshot line also
  keeps `unit_id`, the key stock deduction reads.

## Storefront behaviour

The Vercel cart keys a line by product **and** unit, so "1 crate" and "3
pieces" of the same product are separate lines. Weighed products are not
selling units: they are `variable_weight`, always in kg, and must be a
multiple of `weight_step` (see [[Offers-and-Cart-Quote]]).
