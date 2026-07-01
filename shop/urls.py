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
    path('shop/', views.shop, name='shop'),
    path('offers/', views.offers, name='offers'),
    path('cart/', views.cart_view, name='cart'),
    path('cart/add/<int:product_id>/', views.add_to_cart, name='add_to_cart'),
    path('cart/add-offer/<int:product_id>/', views.add_to_cart_offer, name='add_to_cart_offer'),
    path('cart/remove/<int:product_id>/', views.remove_from_cart, name='remove_from_cart'),
    path('cart/update/<int:product_id>/', views.update_cart, name='update_cart'),
    path('cart/save-for-later/<int:product_id>/', views.toggle_save_for_later, name='toggle_save_for_later'),
    path('cart/move-to-cart/<int:product_id>/', views.move_to_cart, name='move_to_cart'),
    path('cart/remove-saved/<int:product_id>/', views.remove_saved, name='remove_saved'),
    path('checkout/', views.checkout, name='checkout'),
]