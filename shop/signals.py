from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from .models import InventoryMovement


@receiver(post_save, sender=InventoryMovement)
def notify_on_low_stock_crossing(sender, instance, created, **kwargs):
    """A one-time Telegram alert the moment stock crosses DOWN past a
    product's low_stock_threshold -- not a ping on every subsequent sale
    while it stays below that line, and it naturally re-arms once a restock
    pushes stock back above threshold and it later crosses down again.

    Fixed-weight products (goats/chickens) are skipped entirely -- they're
    tracked per-animal via ProductVariant, not by a single stock count, so
    "low stock threshold" doesn't apply to them (same exclusion as the POS
    stock table in shop/stock.py).

    Only fires on a newly-created row: InventoryMovement is an append-only
    ledger (rows are never edited after the fact), but this guard costs
    nothing and makes that assumption explicit rather than silent.
    """
    if not created or instance.product.pricing_mode == 'fixed_weight':
        return

    product = instance.product
    stock_after = InventoryMovement.current_stock(product)
    stock_before = stock_after - instance.signed_quantity()
    threshold = product.low_stock_threshold

    if stock_before > threshold and stock_after <= threshold:
        from .emails import send_telegram
        send_telegram(
            f"⚠️ <b>Low stock</b>\n{product.name} is down to "
            f"{stock_after} (threshold {threshold})."
        )


@receiver([post_save, post_delete], sender=InventoryMovement)
def sync_availability_from_stock(sender, instance, **kwargs):
    """Fires on every InventoryMovement change — from the POS, website
    checkout, ABMS, or a manual admin correction — and keeps 'in stock' on
    the storefront honest regardless of which of those actually moved the
    number.

    Fixed-weight products (goats/chickens) are keyed by variant: selling
    the last 21kg goat should only hide THAT listing, not the whole
    product, so ProductVariant.is_available follows that variant's own
    stock, and the product itself stays available as long as ANY variant
    still does. Everything else follows the product's own current_stock()
    directly, the same number admin's Current Stock column already shows.
    """
    product = instance.product

    if instance.variant_id:
        variant = instance.variant
        if variant is None:
            return  # the variant itself was deleted separately — nothing to sync
        variant.is_available = InventoryMovement.current_stock(product, variant=variant) > 0
        variant.save(update_fields=['is_available'])
        product.is_available = any(
            InventoryMovement.current_stock(product, variant=v) > 0
            for v in product.variants.all()
        )
        product.save(update_fields=['is_available'])

    elif product.pricing_mode == 'fixed_weight' and product.variants.exists():
        # A variant-less movement on a product that does have variant rows
        # shouldn't normally happen — our own validation requires a variant
        # for fixed_weight — but recompute from all variants defensively
        # rather than assume this case can't occur.
        product.is_available = any(
            InventoryMovement.current_stock(product, variant=v) > 0
            for v in product.variants.all()
        )
        product.save(update_fields=['is_available'])

    else:
        product.is_available = InventoryMovement.current_stock(product) > 0
        product.save(update_fields=['is_available'])