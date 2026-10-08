from django.urls import path

from . import costs_views

urlpatterns = [
    path('sync/', costs_views.CostSyncView.as_view(), name='api_costs_sync'),
    path('batches/sync/', costs_views.CropBatchSyncView.as_view(), name='api_costs_batches_sync'),
]
