from django.urls import path
from . import views

urlpatterns = [
    path('', views.home, name='home'),
    path('contact/', views.contact, name='contact'),
    path('order/', views.place_order, name='order'),
    path('newsletter/', views.newsletter_signup, name='newsletter'),
]