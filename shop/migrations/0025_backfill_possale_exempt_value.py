from django.db import migrations


def backfill_exempt_value(apps, schema_editor):
    """Every sale created before this migration predates VAT scaffolding
    entirely — VAT was never enabled, so under the stated rule (VAT
    disabled -> exempt_value = full total, taxable_value/vat_amount = 0)
    each one's exempt_value should equal its own total_amount, not the 0
    the new column defaulted to. taxable_value/vat_amount are correctly
    left at 0."""
    POSSale = apps.get_model('shop', 'POSSale')
    for sale in POSSale.objects.filter(exempt_value=0, taxable_value=0, vat_amount=0):
        sale.exempt_value = sale.total_amount
        sale.save(update_fields=['exempt_value'])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('shop', '0024_pos_credit_vat'),
    ]

    operations = [
        migrations.RunPython(backfill_exempt_value, noop_reverse),
    ]
