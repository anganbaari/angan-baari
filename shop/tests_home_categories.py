"""Home page: category sections built from the database (shop/views.py's
home()). Kept separate from shop/tests.py for the same reason
tests_bs_calendar.py/tests_crop_batches.py are.
"""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from shop.models import Category, Product
from shop.views import HOME_EXCLUDED_CATEGORIES


class HomeCategorySectionsTests(TestCase):
    def make_product(self, name, slug, category, **kwargs):
        defaults = dict(
            description='test', price=Decimal('50.00'), pricing_mode='fixed_quantity',
            is_available=True, main_image='products/placeholder.jpg',
        )
        defaults.update(kwargs)
        return Product.objects.create(name=name, slug=slug, category=category, **defaults)

    def test_top_level_category_with_a_product_renders_its_own_section(self):
        cat = Category.objects.create(name='Dry Fruits Test Category', order=50)
        self.make_product('Test Cashews', 'test-cashews', cat)

        response = self.client.get(reverse('home'))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn(f'data-cat="cat-{cat.id}"', content)
        self.assertIn('Test Cashews', content)
        self.assertIn('Dry Fruits Test Category', content)

    def test_empty_top_level_category_renders_no_section_and_no_tab(self):
        cat = Category.objects.create(name='Empty Test Category', order=51)

        response = self.client.get(reverse('home'))
        content = response.content.decode()
        self.assertNotIn(f'data-cat="cat-{cat.id}"', content)
        self.assertNotIn('Empty Test Category', content)

    def test_fruits_honey_pickles_are_not_duplicated(self):
        """HOME_EXCLUDED_CATEGORIES (shop/views.py) excludes the real
        stored category names -- फलफूल/मह/अचार -- from the database-driven
        loop, since those are already covered by the hand-written sections
        earlier on the page. A product added to one of those categories
        must not also trigger a second, DB-driven section/tab for it."""
        # These rows aren't created by any migration (the real ones were
        # entered through the admin on the live site) -- get_or_create so
        # this test is self-contained against a fresh test database.
        cats = []
        for i, name in enumerate(HOME_EXCLUDED_CATEGORIES):
            cat, _ = Category.objects.get_or_create(name=name, parent=None)
            cats.append(cat)
            self.make_product(f'Test product for {name}', f'test-product-{i}', cat)

        response = self.client.get(reverse('home'))
        content = response.content.decode()
        for cat in cats:
            self.assertNotIn(f'data-cat="cat-{cat.id}"', content)

    def test_fixed_weight_product_shows_no_price_line(self):
        """Step 2, item 8: the hand-written Goats/Free-range Hens cards
        this feature replaces showed no price at all (the real price
        lives per-animal on ProductVariant.price_override, not a single
        Product.price) -- the database-driven cards match that, rather
        than showing a misleading flat product.price for a fixed_weight
        product."""
        cat = Category.objects.create(name='Livestock Test Category', order=52)
        self.make_product(
            'Test Goat', 'test-goat', cat, pricing_mode='fixed_weight', price=Decimal('0.00'),
        )

        response = self.client.get(reverse('home'))
        content = response.content.decode()
        self.assertIn('Test Goat', content)
        card_start = content.index('Test Goat')
        card_chunk = content[card_start:card_start + 600]
        self.assertNotIn('shop-price', card_chunk)

    def test_card_shows_tagline_or_else_truncated_description(self):
        """Short-punchline-cards task: Product.tagline, when set, is shown
        as-is; when blank, the card falls back to the description's first
        sentence cut to ~90 chars (first_sentence|truncatechars:90,
        shop/templatetags/storefront_tags.py) instead."""
        cat = Category.objects.create(name='Punchline Test Category', order=53)
        self.make_product(
            'Test Tagline Product', 'test-tagline-product', cat,
            tagline='Short and punchy.',
            description='This should never appear because the tagline wins. Second sentence.',
        )
        long_sentence = 'A' * 120 + '.'
        self.make_product(
            'Test Fallback Product', 'test-fallback-product', cat,
            tagline='', description=f'{long_sentence} This second sentence must not appear either.',
        )

        response = self.client.get(reverse('home'))
        content = response.content.decode()

        self.assertIn('Short and punchy.', content)
        self.assertNotIn('This should never appear', content)

        self.assertIn('A' * 89 + '…', content)
        self.assertNotIn('This second sentence must not appear', content)
