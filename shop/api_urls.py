from django.urls import path
from . import api_views

urlpatterns = [
    path('movements/', api_views.InventoryMovementCreateView.as_view(), name='api_inventory_movement_create'),
    path('stock/<int:product_id>/', api_views.CurrentStockView.as_view(), name='api_inventory_stock'),
]