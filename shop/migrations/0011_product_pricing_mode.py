from django.db import migrations, models


class Migration(migrations.Migration):

    # NOTE: update this to match the actual last migration filename in your
    # shop/migrations/ folder before running (your memory notes mention
    # 0010_coupon_bundleitem as the most recent one — if that's still the
    # latest, this is correct as-is).
    dependencies = [
        ('shop', '0010_coupon_bundleitem'),
    ]

    operations = [
        migrations.AddField(
            model_name='product',
            name='pricing_mode',
            field=models.CharField(
                choices=[
                    ('variable_weight', 'Variable weight — customer picks the weight (fruits, loose pickle)'),
                    ('fixed_quantity', 'Fixed quantity — sold per piece/dozen/jar, no weight (banana, jars)'),
                    ('fixed_weight', 'Fixed weight, locked price — one specific animal (goat, chicken)'),
                ],
                default='fixed_quantity',
                help_text='Controls how this product behaves in the cart.',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='product',
            name='weight_step',
            field=models.DecimalField(
                decimal_places=2,
                default=0.5,
                help_text='Only used for "Variable weight" products. The increment customers can '
                           'adjust the weight by, e.g. 0.50 for fruit (500g steps), 0.25 for pickle jars.',
                max_digits=4,
            ),
        ),
        migrations.AddField(
            model_name='product',
            name='fixed_weight',
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                help_text='Only used for "Fixed weight" products. The actual weight of THIS specific '
                           'animal/item in kg, e.g. 20 for a 20kg goat. Total price = price (per kg) x this weight, '
                           'and the customer cannot change it.',
                max_digits=6,
                null=True,
            ),
        ),
    ]
