"""Read-only sanity check for cost-centre key hygiene across the three
places a cost-centre string lives (CostCentreProduct.cost_centre --
the admin-maintained Django-side mapping -- and CostEntry.cost_centre/
CropBatch.cost_centre -- both mirrored verbatim from whatever key ABMS
happens to send). Never writes anything; existing rows are never
auto-fixed (see CostCentreProduct.cost_centre's own validator and
CLAUDE.md's "additive only" convention) -- this command only tells a
human what, if anything, needs fixing by hand in Admin.

Usage: python manage.py check_cost_centre_keys
"""
from django.core.management.base import BaseCommand

from shop.models import CostCentreProduct, CostEntry, CropBatch
from shop.models import COST_CENTRE_SLUG_VALIDATOR


class Command(BaseCommand):
    help = (
        'Read-only: prints every CostCentreProduct row whose cost_centre key is not slug form, '
        'and every distinct cost_centre used by CostEntry/CropBatch that has no CostCentreProduct row at all.'
    )

    def handle(self, *args, **options):
        self._check_non_slug_mappings()
        self.stdout.write('')
        self._check_unmapped_centres()

    def _check_non_slug_mappings(self):
        self.stdout.write(self.style.MIGRATE_HEADING('CostCentreProduct rows not in slug form:'))
        bad_rows = [
            row for row in CostCentreProduct.objects.select_related('product').order_by('cost_centre')
            if not COST_CENTRE_SLUG_VALIDATOR.regex.match(row.cost_centre)
        ]
        if not bad_rows:
            self.stdout.write('  (none)')
            return
        for row in bad_rows:
            self.stdout.write(f'  {row.cost_centre!r} -> {row.product.name} (CostCentreProduct id={row.id})')

    def _check_unmapped_centres(self):
        self.stdout.write(self.style.MIGRATE_HEADING(
            'Cost centres used in CostEntry/CropBatch with no CostCentreProduct row:'
        ))
        used_centres = set(CostEntry.objects.values_list('cost_centre', flat=True).distinct())
        used_centres |= set(CropBatch.objects.values_list('cost_centre', flat=True).distinct())
        mapped_centres = set(CostCentreProduct.objects.values_list('cost_centre', flat=True).distinct())
        unmapped = sorted(used_centres - mapped_centres)
        if not unmapped:
            self.stdout.write('  (none)')
            return
        for centre in unmapped:
            self.stdout.write(f'  {centre!r}')
