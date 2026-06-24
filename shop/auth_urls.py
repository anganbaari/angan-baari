from django.urls import path
from . import auth_views

urlpatterns = [
    path('signup/', auth_views.signup, name='signup'),
    path('login/', auth_views.login_view, name='login'),
    path('logout/', auth_views.logout_view, name='logout'),
    path('profile/', auth_views.profile, name='profile'),
    path('forgot-password/', auth_views.forgot_password, name='forgot_password'),
    path('reset-password/<str:token>/', auth_views.reset_password, name='reset_password'),
]