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

from decimal import Decimal

from django.db.models import Case, DecimalField, F, Max, Sum, When

from .models import InventoryMovement, Product

# Movement types that count as "this product was restocked" for
# last_restocked_at purposes -- a sale/waste/return/adjustment_remove never
# qualifies, regardless of whether it happens to increase or decrease stock.
RESTOCK_MOVEMENT_TYPES = ('harvest', 'purchase', 'adjustment_add')


def get_stock_table_rows():
    """Returns one dict per non-fixed_weight Product (available or not):

        product_id, name, current_stock (Decimal), low_stock_threshold (int),
        is_low (bool), origin ('farm' / 'sourced'),
        restock_method ("Own farm" / "Outsourced" -- origin's display text),
        last_restocked_at (datetime or None), is_available (bool)

    is_low is a strict less-than: a product sitting exactly AT threshold is
    not yet low, only below it. Mirrored by the Telegram-alert crossing
    check in shop/signals.py -- keep both in sync if this ever changes.

    Sort order: is_low=True rows first (ascending by current_stock, so the
    most urgent restock need is at the very top), then everything else
    alphabetically by name (case-insensitive).
    """
    products = list(Product.objects.exclude(pricing_mode='fixed_weight'))
    product_ids = [p.id for p in products]

    # Bulk equivalent of calling InventoryMovement.current_stock(product)
    # once per product -- same signed-quantity rule (INCREASE_TYPES add,
    # everything else subtracts), computed in the DB instead of fetching
    # every row and summing in Python per product. variant__isnull=True
    # matches current_stock()'s own default call (variant=None) exactly --
    # correct here since every product in `products` is non-fixed_weight,
    # which never has variant-scoped movements.
    signed_quantity = Case(
        When(movement_type__in=InventoryMovement.INCREASE_TYPES, then=F('quantity')),
        default=-F('quantity'),
        # max_digits wider than InventoryMovement.quantity's own 8 -- this
        # is the OUTPUT field Django uses to interpret the SUM() aggregate
        # below, which can need a few more digits than any single row once
        # many movements add up; decimal_places stays 3, matching the
        # model field's own scale exactly.
        output_field=DecimalField(max_digits=14, decimal_places=3),
    )
    stock_by_product = {
        row['product_id']: row['total'] or Decimal('0')
        for row in (
            InventoryMovement.objects
            .filter(product_id__in=product_ids, variant__isnull=True)
            .values('product_id')
            .annotate(total=Sum(signed_quantity))
        )
    }

    # Bulk equivalent of the old per-product
    # "order_by('-created_at', '-id').first()" lookup -- Max('created_at')
    # gives the identical TIMESTAMP VALUE (the only part ever read: see
    # `last_movement.created_at` below), since the old query's '-id'
    # tiebreak only ever mattered for picking between two rows that
    # already share the exact same created_at -- same value either way.
    last_restocked_by_product = {
        row['product_id']: row['latest']
        for row in (
            InventoryMovement.objects
            .filter(product_id__in=product_ids, movement_type__in=RESTOCK_MOVEMENT_TYPES)
            .values('product_id')
            .annotate(latest=Max('created_at'))
        )
    }

    rows = []
    for product in products:
        current_stock = stock_by_product.get(product.id, Decimal('0'))
        threshold = product.low_stock_threshold
        is_low = current_stock < threshold

        rows.append({
            'product_id': product.id,
            'name': product.name,
            'current_stock': current_stock,
            'low_stock_threshold': threshold,
            'is_low': is_low,
            'origin': product.origin,
            'restock_method': 'Own farm' if product.origin == 'farm' else 'Outsourced',
            'last_restocked_at': last_restocked_by_product.get(product.id),
            'is_available': product.is_available,
        })

    # For the is_low group, position 2 is the real current_stock (ascending).
    # For everything else, position 2 is a constant so the comparison falls
    # through to name -- keeping each group's own ordering rule independent
    # without needing two separate sorts.
    rows.sort(key=lambda r: (0 if r['is_low'] else 1, r['current_stock'] if r['is_low'] else 0, r['name'].lower()))
    return rows
