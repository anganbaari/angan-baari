"""Restore-check: decompresses the newest backup (or a given file) into
a temporary directory, opens that temporary copy read-only, runs PRAGMA
integrity_check, and prints row counts for the key tables. Never touches
the live database -- the only file this command ever reads is the backup
itself (or its own temp-directory copy of it), and that temp directory
is removed again once the command exits.

Usage:
    python manage.py restore_check
    python manage.py restore_check /path/to/angan-baari-20261022-0300.sqlite3.gz
"""
import gzip
import shutil
import sqlite3
import tempfile
from pathlib import Path

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError

from shop.models import (
    CreditTransaction, InventoryMovement, POSSale, POSSaleLine,
    Product, ProductOrder, ProductVariant, Refund,
)

from .backup_db import backup_dir, list_backups, sqlite_uri

# The "key tables" the task asked for, as model classes so the real
# table name is read from each model's own _meta.db_table (e.g. User's
# is auth_user, not shop_user) rather than guessed/hardcoded.
KEY_TABLE_MODELS = [
    Product, ProductVariant, POSSale, POSSaleLine, Refund,
    InventoryMovement, ProductOrder, CreditTransaction, User,
]


class Command(BaseCommand):
    help = (
        'Read-only restorability check for a backup_db backup: integrity_check '
        'plus key-table row counts. Never touches the live database.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            'path', nargs='?', default=None,
            help='A specific .sqlite3.gz backup file; defaults to the newest one in BACKUP_DIR.',
        )

    def handle(self, *args, **options):
        source = self._resolve_source(options['path'])
        self.stdout.write(f'Checking {source}')

        with tempfile.TemporaryDirectory() as tmp_dir:
            decompressed = Path(tmp_dir) / 'restore_check.sqlite3'
            with gzip.open(source, 'rb') as f_in, open(decompressed, 'wb') as f_out:
                shutil.copyfileobj(f_in, f_out)

            conn = sqlite3.connect(sqlite_uri(decompressed, 'ro'), uri=True)
            try:
                result = conn.execute('PRAGMA integrity_check').fetchone()[0]
                self.stdout.write(f'integrity_check: {result}')
                if result != 'ok':
                    raise CommandError(f'Backup failed integrity_check: {result}')

                self.stdout.write('')
                self.stdout.write(self.style.MIGRATE_HEADING('Row counts:'))
                for model in KEY_TABLE_MODELS:
                    table = model._meta.db_table
                    try:
                        count = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                    except sqlite3.OperationalError as exc:
                        count = f'ERROR ({exc})'
                    self.stdout.write(f'  {model.__name__:20s} ({table}): {count}')
            finally:
                conn.close()

    def _resolve_source(self, path):
        if path:
            resolved = Path(path).expanduser()
            if not resolved.exists():
                raise CommandError(f'No such file: {resolved}')
            return resolved
        backups = list_backups(backup_dir())
        if not backups:
            raise CommandError(f'No backups found in {backup_dir()}')
        return backups[-1][0]  # list_backups is oldest-first; the last one is newest
