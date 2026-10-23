"""shop/middleware.py's OnDemandBackupMiddleware -- the opportunistic
daily backup that runs from inside an ordinary staff /pos/ or /admin/
request on PythonAnywhere's free tier (no scheduled tasks there). Kept
separate from shop/tests_backup_db.py for the same reason
tests_bs_calendar.py/tests_crop_batches.py are: this tests the
middleware's own eligibility/throttle/freshness logic, not backup_db's
own backup logic (already covered there), which this middleware calls
via call_command('backup_db') and never reimplements.

Uses a temp BACKUP_DIR and a fake django.utils.timezone.now() (patched
only around the request itself, never around force_login/setUp) so
"the newest backup is more/less than 20 hours old" can be tested without
waiting on a real clock.
"""
import shutil
import sqlite3
import tempfile
from datetime import datetime, timezone as dt_timezone
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.utils import timezone

import shop.middleware as backup_middleware

FAKE_NOW = datetime(2026, 6, 15, 10, 0, tzinfo=dt_timezone.utc)


class OnDemandBackupMiddlewareTests(TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)
        self.backup_dir = Path(self.tmp_dir) / 'backups'
        self.backup_dir.mkdir()
        self.db_path = Path(self.tmp_dir) / 'source.sqlite3'

        conn = sqlite3.connect(str(self.db_path))
        conn.execute('CREATE TABLE shop_product (id INTEGER PRIMARY KEY, name TEXT)')
        conn.execute("INSERT INTO shop_product (name) VALUES ('Mango')")
        conn.commit()
        conn.close()

        # Module-level throttle/dedup state persists across tests in the
        # same process -- reset it so each test starts as a "fresh worker".
        backup_middleware._last_check_at = 0
        backup_middleware._last_size_warning_date = None

        self.staff = User.objects.create_user(
            'staffer', password='pw', is_staff=True, is_superuser=True,
        )
        self.plain = User.objects.create_user('plain', password='pw')

        override = override_settings(
            BACKUP_DIR=str(self.backup_dir),
            DATABASES={'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': str(self.db_path)}},
        )
        override.enable()
        self.addCleanup(override.disable)

        patcher = patch('shop.management.commands.backup_db.send_telegram')
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher2 = patch('shop.middleware.send_telegram')
        patcher2.start()
        self.addCleanup(patcher2.stop)

    def _backups(self):
        return list(self.backup_dir.glob('*.sqlite3.gz'))

    def test_staff_get_with_no_backup_creates_one(self):
        self.client.force_login(self.staff)
        with patch('django.utils.timezone.now', return_value=FAKE_NOW):
            response = self.client.get('/admin/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self._backups()), 1)

    def test_staff_get_with_fresh_backup_does_not_create_another(self):
        now_local = timezone.localtime(FAKE_NOW, backup_middleware.KATHMANDU).replace(tzinfo=None)
        fresh_name = f'angan-baari-{now_local.strftime("%Y%m%d-%H%M")}.sqlite3.gz'
        (self.backup_dir / fresh_name).write_bytes(b'x')

        self.client.force_login(self.staff)
        with patch('django.utils.timezone.now', return_value=FAKE_NOW):
            response = self.client.get('/admin/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self._backups()), 1)  # still just the pre-existing one

    def test_anonymous_request_never_triggers_a_backup(self):
        with patch('django.utils.timezone.now', return_value=FAKE_NOW):
            self.client.get('/admin/')
        self.assertEqual(len(self._backups()), 0)

    def test_non_staff_request_never_triggers_a_backup(self):
        self.client.force_login(self.plain)
        with patch('django.utils.timezone.now', return_value=FAKE_NOW):
            self.client.get('/admin/')
        self.assertEqual(len(self._backups()), 0)

    def test_failing_backup_does_not_raise_and_page_still_returns(self):
        self.client.force_login(self.staff)
        with patch('django.utils.timezone.now', return_value=FAKE_NOW), \
             patch('shop.middleware.call_command', side_effect=RuntimeError('boom')):
            response = self.client.get('/admin/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self._backups()), 0)
