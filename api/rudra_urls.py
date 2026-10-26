from django.urls import path

from . import rudra_views

urlpatterns = [
    path('sales/summary/', rudra_views.RudraSalesSummaryView.as_view(), name='rudra_sales_summary'),
    path('sales/payments/', rudra_views.RudraPaymentsView.as_view(), name='rudra_sales_payments'),
    path('sales/products/', rudra_views.RudraProductsView.as_view(), name='rudra_sales_products'),
    path('pl/', rudra_views.RudraPLView.as_view(), name='rudra_pl'),
]
