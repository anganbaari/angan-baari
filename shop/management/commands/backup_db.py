"""Daily SQLite backup.

Makes a consistent copy of the live database using sqlite3's own online
backup API (Connection.backup) -- not a plain file copy, which could
capture a half-written page while the site is serving requests -- runs
PRAGMA integrity_check on that copy, gzip-compresses it to BACKUP_DIR
(farmsite/settings.py, default ~/backups), and rotates old backups
(newest 14 overall + newest 8 Sunday ones kept). Sends one short
Telegram message either way via the existing send_telegram() helper
(shop/emails.py) -- its own raise_on_failure=False default means a
Telegram outage never fails the backup itself.

Usage: python manage.py backup_db
Intended to run once a day from a PythonAnywhere scheduled task -- see
CLAUDE.md for the exact command line and restore steps.
"""
import gzip
import re
import shutil
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from shop.emails import send_telegram

# angan-baari-YYYYMMDD-HHMM.sqlite3.gz -- the one naming convention both
# this command and restore_check (shop/management/commands/
# restore_check.py) rely on to find/sort backups.
FILENAME_RE = re.compile(r'^angan-baari-(\d{8}-\d{4})\.sqlite3\.gz$')
DAILY_KEEP = 14
WEEKLY_KEEP = 8


def backup_dir():
    """BACKUP_DIR if set, else ~/backups -- expanded here (not stored
    expanded in settings), so this still behaves correctly if HOME
    changes between processes on the same deploy."""
    configured = getattr(settings, 'BACKUP_DIR', '') or '~/backups'
    return Path(configured).expanduser()


def list_backups(directory):
    """[(path, datetime), ...] for every file in `directory` matching
    this command's own naming convention, oldest first. Anything else in
    that directory (a stray file, a different naming scheme) is ignored,
    not deleted."""
    found = []
    if not directory.exists():
        return found
    for path in directory.iterdir():
        match = FILENAME_RE.match(path.name)
        if match:
            found.append((path, datetime.strptime(match.group(1), '%Y%m%d-%H%M')))
    found.sort(key=lambda pair: pair[1])
    return found


def apply_retention(directory):
    """Deletes every backup in `directory` except the newest DAILY_KEEP
    overall and the newest WEEKLY_KEEP Sunday ones (a file matching both
    is still only ever one file on disk -- keep is a set, not a count).
    Returns how many files remain."""
    backups = list_backups(directory)
    newest_first = list(reversed(backups))
    keep = {path for path, _ in newest_first[:DAILY_KEEP]}
    sundays = [(path, dt) for path, dt in newest_first if dt.weekday() == 6]  # Monday=0 .. Sunday=6
    keep.update(path for path, _ in sundays[:WEEKLY_KEEP])
    for path, _ in backups:
        if path not in keep:
            path.unlink()
    return len(keep)


def sqlite_uri(path, mode):
    # as_posix() so this also works on Windows (local dev) -- sqlite3's
    # URI filenames want forward slashes regardless of OS, and a plain
    # str() of a WindowsPath keeps its backslashes. Shared with
    # restore_check.py (shop/management/commands/restore_check.py), the
    # only other place that needs a read-only sqlite URI.
    return f'file:{Path(path).resolve().as_posix()}?mode={mode}'


def _consistent_copy(source_path, dest_path):
    # mode=ro on the SOURCE -- this command must never be the thing that
    # writes to the live database file, backup or not.
    source = sqlite3.connect(sqlite_uri(source_path, 'ro'), uri=True)
    dest = sqlite3.connect(str(dest_path))
    try:
        source.backup(dest)
    finally:
        dest.close()
        source.close()


def _integrity_check(path):
    conn = sqlite3.connect(sqlite_uri(path, 'ro'), uri=True)
    try:
        return conn.execute('PRAGMA integrity_check').fetchone()[0]
    finally:
        conn.close()


def _gzip_to(source_path, dest_path):
    with open(source_path, 'rb') as f_in, gzip.open(dest_path, 'wb') as f_out:
        shutil.copyfileobj(f_in, f_out)


def human_size(num_bytes):
    size = float(num_bytes)
    for unit in ('B', 'KB', 'MB', 'GB'):
        if size < 1024:
            return f'{size:.1f}{unit}'
        size /= 1024
    return f'{size:.1f}TB'


def run_backup(db_path, directory):
    """The actual backup, independent of Django's own DATABASES setting
    so it can be exercised directly against an arbitrary sqlite file --
    the Django test database is in-memory, not a real file on disk, so
    the management command can't be run end-to-end the normal way in a
    test (see shop/tests_backup_db.py). Returns (kept_count, size_bytes,
    dest_filename). Raises CommandError on any failure; nothing is left
    behind in `directory` in that case (the gzip destination is only
    written after the integrity check on the uncompressed copy passes)."""
    db_path = Path(db_path)
    if not db_path.exists():
        raise CommandError(f'Live database not found at {db_path}')

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)

    now_local = timezone.localtime(timezone.now(), ZoneInfo('Asia/Kathmandu'))
    dest_name = f'angan-baari-{now_local.strftime("%Y%m%d-%H%M")}.sqlite3.gz'
    dest_path = directory / dest_name

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_copy = Path(tmp_dir) / 'backup.sqlite3'
        try:
            _consistent_copy(db_path, tmp_copy)
            result = _integrity_check(tmp_copy)
        except sqlite3.Error as exc:
            # Raised as CommandError, same as the explicit "not ok" case
            # below, so every failure mode of this function (corrupt
            # source file, a locked/missing file, a real integrity
            # mismatch) surfaces as one consistent exception type to
            # whatever's calling run_backup() -- Command.handle() below,
            # or a test calling it directly.
            raise CommandError(f'Could not read/copy the live database: {exc}')
        if result != 'ok':
            raise CommandError(f'Integrity check failed on the backup copy: {result}')
        _gzip_to(tmp_copy, dest_path)

    size = dest_path.stat().st_size
    kept = apply_retention(directory)
    return kept, size, dest_name


class Command(BaseCommand):
    help = 'Back up the live SQLite database: consistent copy, integrity check, gzip, rotate, Telegram alert.'

    def handle(self, *args, **options):
        try:
            kept, size, dest_name = run_backup(settings.DATABASES['default']['NAME'], backup_dir())
        except Exception as exc:
            send_telegram(f'❌ Daily backup FAILED: {exc}')
            raise CommandError(str(exc))

        send_telegram(
            f'✅ Daily backup OK: {dest_name} ({human_size(size)}). {kept} backups kept.'
        )
        self.stdout.write(self.style.SUCCESS(
            f'Backup OK: {dest_name} ({human_size(size)}), {kept} backups kept.'
        ))
