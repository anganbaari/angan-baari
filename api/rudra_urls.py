from django.urls import path

from . import rudra_views

urlpatterns = [
    path('sales/summary/', rudra_views.RudraSalesSummaryView.as_view(), name='rudra_sales_summary'),
    path('sales/payments/', rudra_views.RudraPaymentsView.as_view(), name='rudra_sales_payments'),
    path('sales/products/', rudra_views.RudraProductsView.as_view(), name='rudra_sales_products'),
    path('stock/low/', rudra_views.RudraLowStockView.as_view(), name='rudra_low_stock'),
    path('orders/pending/', rudra_views.RudraPendingOrdersView.as_view(), name='rudra_pending_orders'),
    path('pl/', rudra_views.RudraPLView.as_view(), name='rudra_pl'),
]
