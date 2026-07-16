from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('shop', '0011_product_pricing_mode'),
    ]

    operations = [
        migrations.AddField(
            model_name='product',
            name='weight_unit_label',
            field=models.CharField(
                default='kg',
                help_text='Unit shown next to the weight/quantity stepper for "Variable weight" products, '
                           'e.g. "kg" for fruit, "dozen" for banana. The price field is always per ONE of this unit.',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='product',
            name='variant_group',
            field=models.CharField(
                blank=True,
                help_text='Only used for "Fixed weight" products (goat, chicken). Give all size listings of '
                           'the same animal the exact same text here (e.g. "goat", "chicken") to group them '
                           'together as one card with a size-picker, instead of separate cards.',
                max_length=100,
            ),
        ),
    ]
