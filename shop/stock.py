"""Shared stock-table query logic — one implementation reused by both the
Django admin (ProductAdmin's stock column) and the POS stock screen
(GET /api/v1/pos/stock/), so the two can never drift apart on what counts
as "low stock" or how restock history is derived.

Fixed-weight products (goats, chickens) are deliberately excluded
everywhere here -- they're tracked per-animal via ProductVariant, not by a
single stock count, so "current stock" / "low stock threshold" don't apply
to them the way they do for everything else (including eggs, which are
fixed_quantity, not fixed_weight, and so stay in this table normally).
"""

from .models import InventoryMovement, Product

# Movement types that count as "this product was restocked" for
# last_restocked_at purposes -- a sale/waste/return/adjustment_remove never
# qualifies, regardless of whether it happens to increase or decrease stock.
RESTOCK_MOVEMENT_TYPES = ('harvest', 'purchase', 'adjustment_add')


def get_stock_table_rows():
    """Returns one dict per non-fixed_weight Product (available or not):

        product_id, name, current_stock (Decimal), low_stock_threshold (int),
        is_low (bool), restock_method ("Own farm" / "Outsourced"),
        last_restocked_at (datetime or None), is_available (bool)

    Sort order: is_low=True rows first (ascending by current_stock, so the
    most urgent restock need is at the very top), then everything else
    alphabetically by name (case-insensitive).
    """
    products = Product.objects.exclude(pricing_mode='fixed_weight')

    rows = []
    for product in products:
        current_stock = InventoryMovement.current_stock(product)
        threshold = product.low_stock_threshold
        is_low = current_stock <= threshold

        last_movement = (
            InventoryMovement.objects
            .filter(product=product, movement_type__in=RESTOCK_MOVEMENT_TYPES)
            .order_by('-created_at')
            .first()
        )

        rows.append({
            'product_id': product.id,
            'name': product.name,
            'current_stock': current_stock,
            'low_stock_threshold': threshold,
            'is_low': is_low,
            'restock_method': 'Own farm' if product.origin == 'farm' else 'Outsourced',
            'last_restocked_at': last_movement.created_at if last_movement else None,
            'is_available': product.is_available,
        })

    # For the is_low group, position 2 is the real current_stock (ascending).
    # For everything else, position 2 is a constant so the comparison falls
    # through to name -- keeping each group's own ordering rule independent
    # without needing two separate sorts.
    rows.sort(key=lambda r: (0 if r['is_low'] else 1, r['current_stock'] if r['is_low'] else 0, r['name'].lower()))
    return rows
