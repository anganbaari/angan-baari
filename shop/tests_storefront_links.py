"""Storefront links switch (Phase 1, links only): {% storefront_link %}
(shop/templatetags/storefront_tags.py) and its STOREFRONT_URL context
processor (shop/context_processors.py). Kept separate from shop/tests.py
for the same reason tests_bs_calendar.py/tests_crop_batches.py are.
"""
from decimal import Decimal

from django.test import TestCase, override_settings

from shop.models import Category, Product
from shop.templatetags.storefront_tags import storefront_link


class StorefrontLinkTagTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(name='Dry Fruits')
        self.product = Product.objects.create(
            name='Test Almonds', slug='test-almonds', category=self.category,
            description='test', price=Decimal('100.00'), pricing_mode='fixed_quantity',
        )

    @override_settings(STOREFRONT_URL='')
    def test_links_are_the_existing_django_pages_when_unset(self):
        self.assertEqual(storefront_link('shop'), '/shop/')
        self.assertEqual(storefront_link('cart'), '/cart/')
        self.assertEqual(storefront_link('checkout'), '/checkout/')
        self.assertEqual(storefront_link('login'), '/account/login/')
        self.assertEqual(storefront_link('signup'), '/account/signup/')
        self.assertEqual(storefront_link('profile'), '/account/profile/')
        self.assertEqual(storefront_link('category', self.category.id), f'/shop/?cat={self.category.id}')
        self.assertEqual(storefront_link('product', self.product.slug), f'/products/{self.product.slug}/')

    @override_settings(STOREFRONT_URL='https://angan-baari-shop.vercel.app')
    def test_links_point_at_the_storefront_when_set(self):
        base = 'https://angan-baari-shop.vercel.app'
        self.assertEqual(storefront_link('shop'), base + '/')
        self.assertEqual(storefront_link('cart'), base + '/cart')
        self.assertEqual(storefront_link('checkout'), base + '/checkout')
        self.assertEqual(storefront_link('login'), base + '/login')
        self.assertEqual(storefront_link('signup'), base + '/signup')
        self.assertEqual(storefront_link('profile'), base + '/profile')
        self.assertEqual(storefront_link('category', self.category.id), f'{base}/?cat={self.category.id}')
        self.assertEqual(storefront_link('product', self.product.slug), f'{base}/product/{self.product.slug}')
