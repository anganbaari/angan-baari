from django.apps import AppConfig


class ShopConfig(AppConfig):
    name = 'shop'

    def ready(self):
        import shop.signals  # noqa: F401 — connects the InventoryMovement signal