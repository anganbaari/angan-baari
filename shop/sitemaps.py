from django.contrib.sitemaps import Sitemap
from django.urls import reverse
from shop.models import Product

class StaticViewSitemap(Sitemap):
    priority = 0.9
    changefreq = 'weekly'
    protocol = 'https'

    def items(self):
        return ['home']

    def location(self, item):
        return reverse(item)


class ProductSitemap(Sitemap):
    priority = 0.8
    changefreq = 'weekly'
    protocol = 'https'

    def items(self):
        return Product.objects.filter(is_available=True)

    def lastmod(self, obj):
        return obj.created_at

    def location(self, obj):
        return obj.get_absolute_url()