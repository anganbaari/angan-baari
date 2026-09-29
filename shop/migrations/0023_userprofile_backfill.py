from django.db import migrations


def create_profiles_for_existing_staff(apps, schema_editor):
    """Backfills a UserProfile for every is_staff account that predates this
    model. Role is derived from is_superuser rather than hardcoded to the 2
    accounts that happen to exist today (anganbaari@gmail.com,
    nikesharma456@gmail.com — both superusers, so both land on 'admin'
    either way) so this migration behaves correctly if it's ever run against
    a database with a different staff roster (e.g. a fresh deploy). pin_hash
    is left blank — PINs are set afterward through the admin."""
    User = apps.get_model('auth', 'User')
    UserProfile = apps.get_model('shop', 'UserProfile')

    for user in User.objects.filter(is_staff=True):
        UserProfile.objects.get_or_create(
            user=user,
            defaults={'role': 'admin' if user.is_superuser else 'cashier'},
        )


def noop_reverse(apps, schema_editor):
    """Deliberately a no-op, not a delete: reversing this migration should
    undo the schema (handled by the CreateModel migration's own reverse),
    not silently destroy PINs/roles someone may have since set through the
    admin."""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('shop', '0022_userprofile'),
    ]

    operations = [
        migrations.RunPython(create_profiles_for_existing_staff, noop_reverse),
    ]
