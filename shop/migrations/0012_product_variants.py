from django.db import migrations, models


class Migration(migrations.Migration):

    # If you already ran the PREVIOUS version of this same-named file (the one
    # with variant_group), delete this migration file, run:
    #   python manage.py migrate shop 0011
    # to unapply it first, then drop this corrected version in and migrate again.
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
        migrations.AlterField(
            model_name='product',
            name='fixed_weight',
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                help_text='FALLBACK ONLY — used if you add no weight rows below. Once you add at least one '
                           'row in "Weight variants", that takes over and this field is ignored.',
                max_digits=6,
                null=True,
            ),
        ),
    ]