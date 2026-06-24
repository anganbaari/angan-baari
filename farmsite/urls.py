from django.contrib import admin
from django.urls import path, include
from django.contrib.sitemaps.views import sitemap
from shop.sitemaps import StaticViewSitemap, ProductSitemap

sitemaps = {
    'static': StaticViewSitemap,
    'products': ProductSitemap,
}

handler404 = 'shop.views.error_404'
handler500 = 'shop.views.error_500'

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('shop.urls')),
    path('account/', include('shop.auth_urls')),
    path('sitemap.xml', sitemap, {'sitemaps': sitemaps},
         name='django.contrib.sitemaps.views.sitemap'),
]