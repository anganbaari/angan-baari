"""Daily database backup + restore check
(shop/management/commands/backup_db.py / restore_check.py). Kept
separate from shop/tests.py for the same reason tests_bs_calendar.py/
tests_crop_batches.py are.

run_backup()/apply_retention() are tested directly against a real,
separate temp sqlite file rather than through `call_command('backup_db')`
-- the Django test database is in-memory (not a real file on disk), so
the management command itself (which reads settings.DATABASES['default']
['NAME']) can't be exercised end-to-end against it. See run_backup()'s
own docstring.
"""
import gzip
import shutil
import sqlite3
import tempfile
from datetime import datetime
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from shop.management.commands.backup_db import apply_retention, run_backup


class BackupDbTests(TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)
        self.db_path = Path(self.tmp_dir) / 'source.sqlite3'
        self.backup_dir = Path(self.tmp_dir) / 'backups'

        conn = sqlite3.connect(str(self.db_path))
        conn.execute('CREATE TABLE shop_product (id INTEGER PRIMARY KEY, name TEXT)')
        conn.execute("INSERT INTO shop_product (name) VALUES ('Mango')")
        conn.execute("INSERT INTO shop_product (name) VALUES ('Honey')")
        conn.commit()
        conn.close()

    def test_backup_creates_a_valid_restorable_file(self):
        with patch('shop.management.commands.backup_db.send_telegram'):
            kept, size, name = run_backup(self.db_path, self.backup_dir)

        self.assertEqual(kept, 1)
        self.assertTrue(size > 0)
        backup_path = self.backup_dir / name
        self.assertTrue(backup_path.exists())
        self.assertTrue(name.startswith('angan-baari-'))
        self.assertTrue(name.endswith('.sqlite3.gz'))

        # Decompress and verify it's genuinely restorable, not just a
        # file that happens to exist -- same steps restore_check.py
        # itself takes.
        decompressed = Path(self.tmp_dir) / 'restored.sqlite3'
        with gzip.open(backup_path, 'rb') as f_in, open(decompressed, 'wb') as f_out:
            shutil.copyfileobj(f_in, f_out)
        conn = sqlite3.connect(str(decompressed))
        try:
            self.assertEqual(conn.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM shop_product').fetchone()[0], 2)
        finally:
            conn.close()

    def test_corrupt_database_aborts_without_writing_a_backup_file(self):
        """A file that exists but isn't a valid sqlite database (the
        simplest stand-in for "the live db is somehow corrupt") must
        raise, and must not leave a gzip file behind -- run_backup()
        only compresses to the destination after the integrity check on
        the uncompressed copy has already passed."""
        corrupt_path = Path(self.tmp_dir) / 'corrupt.sqlite3'
        corrupt_path.write_bytes(b'this is not a sqlite database')

        with self.assertRaises(CommandError):
            run_backup(corrupt_path, self.backup_dir)
        self.assertEqual(list(self.backup_dir.glob('*.sqlite3.gz')), [])

    def test_backup_db_command_sends_a_telegram_message_on_success(self):
        with patch('shop.management.commands.backup_db.backup_dir', return_value=self.backup_dir), \
             patch('shop.management.commands.backup_db.settings') as mock_settings, \
             patch('shop.management.commands.backup_db.send_telegram') as mock_telegram:
            mock_settings.DATABASES = {'default': {'NAME': str(self.db_path)}}
            out = StringIO()
            call_command('backup_db', stdout=out)

        mock_telegram.assert_called_once()
        self.assertIn('Daily backup OK', mock_telegram.call_args[0][0])
        self.assertIn('Backup OK', out.getvalue())

    def test_retention_keeps_newest_14_plus_newest_8_sundays(self):
        self.backup_dir.mkdir(parents=True)
        # 30 consecutive daily backups ending today (a mix of Sundays and
        # non-Sundays, same as real usage) -- 30 is comfortably more than
        # 14 + 8, so both limits actually bind in this test.
        from datetime import timedelta
        start = datetime(2026, 1, 1, 3, 0)  # a Thursday
        all_dates = [start + timedelta(days=i) for i in range(30)]
        for dt in all_dates:
            name = f'angan-baari-{dt.strftime("%Y%m%d-%H%M")}.sqlite3.gz'
            (self.backup_dir / name).write_bytes(b'x')
        # A file that doesn't match the naming convention must be left
        # alone entirely -- never deleted, never counted.
        (self.backup_dir / 'not-a-backup.txt').write_text('leave me alone')

        kept = apply_retention(self.backup_dir)

        newest_first = sorted(all_dates, reverse=True)
        expected_daily = set(newest_first[:14])
        sundays_newest_first = [d for d in newest_first if d.weekday() == 6]
        expected_weekly = set(sundays_newest_first[:8])
        expected_kept_dates = expected_daily | expected_weekly

        remaining = {
            datetime.strptime(p.name[len('angan-baari-'):-len('.sqlite3.gz')], '%Y%m%d-%H%M')
            for p in self.backup_dir.glob('angan-baari-*.sqlite3.gz')
        }
        self.assertEqual(remaining, expected_kept_dates)
        self.assertEqual(kept, len(expected_kept_dates))
        self.assertTrue((self.backup_dir / 'not-a-backup.txt').exists())
