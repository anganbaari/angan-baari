from django.urls import path

from . import views

urlpatterns = [
    path('products/', views.ProductListView.as_view(), name='v1_product_list'),
    path('products/<int:pk>/', views.ProductDetailView.as_view(), name='v1_product_detail'),
    path('categories/', views.CategoryListView.as_view(), name='v1_category_list'),
    path('inventory/movements/', views.InventoryMovementListView.as_view(), name='v1_inventory_movement_list'),
    path('sales/', views.POSSaleView.as_view(), name='v1_sale_list_create'),
    path('orders/', views.OrderCreateView.as_view(), name='v1_order_create'),
    path('orders/<int:pk>/', views.OrderDetailView.as_view(), name='v1_order_detail'),
]
