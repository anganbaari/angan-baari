from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('shop', '0013_productvariant'),
    ]

    operations = [
        migrations.AddField(
            model_name='productorder',
            name='cart_snapshot',
            field=models.JSONField(
                blank=True,
                help_text='Structured line items (product id, weight, qty) captured at checkout time, '
                           'used by the "Reorder" button. Orders placed before this field existed have '
                           'no snapshot, so they won\'t show a working Reorder button.',
                null=True,
            ),
        ),
    ]
