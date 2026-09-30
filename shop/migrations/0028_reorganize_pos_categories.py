from django.db import migrations


def reorganize_categories(apps, schema_editor):
    Category = apps.get_model('shop', 'Category')
    Product = apps.get_model('shop', 'Product')

    # Rename + reorder the existing top-level categories that already
    # exist and just need a new name/position to match the POS mockup's
    # category row.
    renames = [
        ('Fruits', 'फलफूल', 1),
        ('Vegetables', 'तरकारी', 2),
        ('Pickles', 'अचार', 3),
        ('Honey', 'मह', 4),
        ('Compost', 'मल र दाना', 7),
    ]
    for old_name, new_name, order in renames:
        Category.objects.filter(name=old_name, parent__isnull=True).update(name=new_name, order=order)

    # "Dairy Product" is new -- absorbs every product currently under
    # Animals (Goats) and Chickens (Local Chickens, Local Egg), then
    # those two now-empty categories are removed. Product.category is
    # SET_NULL on delete, so this can't cascade-delete any products.
    dairy, _ = Category.objects.get_or_create(name='Dairy Product', parent=None, defaults={'order': 5})
    if dairy.order != 5:
        dairy.order = 5
        dairy.save(update_fields=['order'])

    for old_name in ('Animals', 'Chickens'):
        old_cat = Category.objects.filter(name=old_name, parent__isnull=True).first()
        if old_cat:
            Product.objects.filter(category=old_cat).update(category=dairy)
            old_cat.delete()

    # New, empty category -- products get added to it later via the admin.
    Category.objects.get_or_create(name='मसला र Dry Fruits', parent=None, defaults={'order': 6})


def reverse_reorganize_categories(apps, schema_editor):
    # Best-effort reverse: restores the renamed categories' old English
    # names/order. Cannot restore the original Animals/Chickens split for
    # products that were moved into "Dairy Product" -- there's no record
    # of which product came from which, so those products are left on
    # "Dairy Product" (or wherever they are by the time this runs) rather
    # than guessed back into two categories.
    Category = apps.get_model('shop', 'Category')

    reverse_renames = [
        ('फलफूल', 'Fruits', 1),
        ('तरकारी', 'Vegetables', 2),
        ('अचार', 'Pickles', 6),
        ('मह', 'Honey', 3),
        ('मल र दाना', 'Compost', 1),
    ]
    for new_name, old_name, order in reverse_renames:
        Category.objects.filter(name=new_name, parent__isnull=True).update(name=old_name, order=order)

    Category.objects.filter(name='मसला र Dry Fruits', parent__isnull=True).delete()
    # "Dairy Product" itself is left in place on reverse (deleting it
    # would just orphan its products to NULL, which isn't a real revert
    # either) -- remove it by hand in the admin if the reverse is ever run.


class Migration(migrations.Migration):

    dependencies = [
        ('shop', '0027_product_weight_entry_mode_alter_product_weight_step'),
    ]

    operations = [
        migrations.RunPython(reorganize_categories, reverse_reorganize_categories),
    ]
