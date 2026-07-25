import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    # Update this dependency to match your actual latest migration filename
    # if it's not 0014_productorder_cart_snapshot.
    dependencies = [
        ('shop', '0014_productorder_cart_snapshot'),
    ]

    operations = [
        migrations.AddField(
            model_name='wishlist',
            name='variant',
            field=models.ForeignKey(
                blank=True,
                help_text='Only used for "Fixed weight" products (goat/chicken) — which specific '
                           'size/animal was picked. Left blank for every other pricing mode.',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='wishlisted_by',
                to='shop.productvariant',
            ),
        ),
    ]
