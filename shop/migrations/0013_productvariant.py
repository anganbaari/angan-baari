import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('shop', '0012_product_variants'),
    ]

    operations = [
        migrations.CreateModel(
            name='ProductVariant',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('weight', models.DecimalField(decimal_places=2, help_text='Actual weight of this specific animal in kg, e.g. 20', max_digits=6)),
                ('price_override', models.DecimalField(blank=True, decimal_places=2, help_text='Leave blank to auto-calculate as product.price x weight. Only set this if THIS particular animal is priced differently than the usual per-kg rate.', max_digits=10, null=True)),
                ('label', models.CharField(blank=True, help_text='Optional note, e.g. "Male, ~1 year old"', max_length=100)),
                ('is_available', models.BooleanField(default=True, help_text='Uncheck once this specific animal is sold, instead of deleting the row.')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('product', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='variants', to='shop.product')),
            ],
            options={
                'ordering': ['weight'],
            },
        ),
    ]
