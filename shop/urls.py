from django.urls import path
from . import views

urlpatterns = [
    path('', views.home, name='home'),
    path('contact/', views.contact, name='contact'),
    path('order/', views.place_order, name='order'),
    path('newsletter/', views.newsletter_signup, name='newsletter'),
    path('cancel/<str:token>/', views.cancel_order, name='cancel_order'),
    path('products/<slug:slug>/', views.product_detail, name='product_detail'),
    path('products/<slug:slug>/review/', views.submit_review, name='submit_review'),
]