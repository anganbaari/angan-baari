"""On-demand daily backup -- PythonAnywhere's free tier has no scheduled
tasks, so instead of (or alongside) the cron-driven `python manage.py
backup_db` (shop/management/commands/backup_db.py, used wherever
scheduled tasks ARE available), OnDemandBackupMiddleware opportunistically
runs that exact same command once a day, synchronously, inside an
ordinary staff page request. /pos/ and /admin/ are both staff-only
already and get requested constantly through a normal working day, so
this reliably fires at least once daily without needing any
infrastructure beyond what's already there.

Deliberately NOT a background thread: uwsgi on PythonAnywhere's free
tier may not run threads reliably, so "synchronous, inside the request
that happened to trigger it" is the only safe option here -- the one
request that triggers a backup is a little slower, not the whole site,
and every other request is cheap (see CHECK_INTERVAL_SECONDS below).

Reuses backup_db's own code and behaviour completely, via
django.core.management.call_command('backup_db') -- not a reimplementation
of the copy/integrity-check/gzip/rotate/Telegram logic, and not even a
direct call to that command's run_backup() helper, specifically so this
middleware can never drift from what the "real" scheduled-task command
does (same success/failure Telegram message, same retention, same
BACKUP_DIR). See shop/tests_middleware.py for the test coverage this task
asked for.
"""
import logging
import os
import time
from pathlib import Path
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.management import call_command
from django.utils import timezone

from shop.emails import send_telegram
from shop.management.commands.backup_db import backup_dir, list_backups

logger = logging.getLogger(__name__)

CHECK_INTERVAL_SECONDS = 10 * 60  # how often THIS PROCESS re-checks the backup folder at all
STALE_THRESHOLD_SECONDS = 20 * 60 * 60  # how old the newest backup must be before another one runs
LOCK_MAX_AGE_SECONDS = 10 * 60  # a lock file older than this is treated as abandoned, not "in progress"
MAX_DB_SIZE_BYTES = 100 * 1024 * 1024  # past this, an in-request backup is too slow/risky -- needs backup_db on a real scheduled task instead
LOCK_FILENAME = '.backup_on_demand.lock'
KATHMANDU = ZoneInfo('Asia/Kathmandu')

# Per-process, not per-request or persisted anywhere -- avoids touching
# the filesystem on every single staff page load (see
# CHECK_INTERVAL_SECONDS). A freshly started worker process has both at
# their zero/None default, so it always checks on its own first eligible
# request rather than waiting out whatever another worker's clock
# happens to be at -- harmless (worst case, two workers' first requests
# both check at once, which the lock file below already handles safely).
_last_check_at = 0
_last_size_warning_date = None


def backup_on_demand_enabled():
    # farmsite/settings.py parses the BACKUP_ON_DEMAND env var; reading it
    # from settings (not os.getenv directly) is what lets tests toggle it
    # with Django's own override_settings.
    return getattr(settings, 'BACKUP_ON_DEMAND', True)


def _acquire_lock(directory):
    """Atomic lock-file creation (O_CREAT|O_EXCL -- fails if the file
    already exists) so two simultaneous requests can't both start a
    backup. A lock older than LOCK_MAX_AGE_SECONDS is assumed abandoned
    (the worker that created it crashed or was killed mid-backup) and is
    cleared before one retry, so a single stuck lock can't wedge every
    future request indefinitely. Returns the lock Path on success, None
    if someone else currently holds it."""
    lock_path = directory / LOCK_FILENAME
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
        return lock_path
    except FileExistsError:
        try:
            age = time.time() - lock_path.stat().st_mtime
        except OSError:
            return None
        if age <= LOCK_MAX_AGE_SECONDS:
            return None
        try:
            lock_path.unlink()
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            return lock_path
        except OSError:
            return None


def _release_lock(lock_path):
    try:
        lock_path.unlink()
    except OSError:
        pass


def _warn_once_per_day_about_size(size_bytes):
    global _last_size_warning_date
    today = timezone.localtime(timezone.now(), KATHMANDU).date()
    logger.warning('on-demand backup skipped: database is %.1f MB (over the 100 MB limit)', size_bytes / 1024 / 1024)
    if _last_size_warning_date == today:
        return
    _last_size_warning_date = today
    send_telegram(
        f'⚠️ On-demand backup skipped: database is '
        f'{size_bytes / 1024 / 1024:.1f} MB (over the 100 MB on-demand limit). '
        f'Set up a real scheduled task for backup_db.'
    )


def maybe_run_backup():
    """Called from OnDemandBackupMiddleware for an eligible request. Does
    nothing (cheaply) unless it's actually time to check, and does
    nothing risky even then -- see the module docstring. Never raises;
    the one caller already wraps this in try/except too, belt-and-braces,
    since "never break or slow the page beyond the backup itself" is the
    whole point of this function existing."""
    global _last_check_at

    if not backup_on_demand_enabled():
        return

    now = time.time()
    if now - _last_check_at < CHECK_INTERVAL_SECONDS:
        return
    _last_check_at = now

    directory = backup_dir()
    directory.mkdir(parents=True, exist_ok=True)

    backups = list_backups(directory)
    if backups:
        newest_path, newest_dt = backups[-1]
        now_local = timezone.localtime(timezone.now(), KATHMANDU).replace(tzinfo=None)
        age_seconds = (now_local - newest_dt).total_seconds()
        if age_seconds < STALE_THRESHOLD_SECONDS:
            return  # fresh enough -- nothing to do

    db_path = Path(settings.DATABASES['default']['NAME'])
    try:
        db_size = db_path.stat().st_size
    except OSError:
        return  # no live database file to back up (e.g. a non-sqlite backend) -- nothing this middleware can do
    if db_size > MAX_DB_SIZE_BYTES:
        _warn_once_per_day_about_size(db_size)
        return

    lock_path = _acquire_lock(directory)
    if lock_path is None:
        return  # another request is already backing up right now

    try:
        call_command('backup_db')
    except Exception:
        # backup_db's own Command.handle() already sends a Telegram
        # failure message and raises CommandError -- nothing more to
        # report here, just make sure it goes no further than this
        # function (the caller logs it; see OnDemandBackupMiddleware).
        logger.exception('on-demand backup_db run failed')
    finally:
        _release_lock(lock_path)


class OnDemandBackupMiddleware:
    """GET /pos/* or /admin/* from an authenticated staff user triggers
    the opportunistic daily-backup check above. Registered AFTER
    AuthenticationMiddleware in MIDDLEWARE (farmsite/settings.py) so
    request.user is already resolved by the time this runs. Runs after
    get_response() -- the ordinary page response is already fully built
    either way (middleware can't stream a partial response out from
    under itself), so there's no benefit to checking first, and this
    order means a request that's about to fail for unrelated reasons
    never wastes time on a backup it was never going to need anyway."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        try:
            if (
                request.method == 'GET'
                and (request.path.startswith('/pos/') or request.path.startswith('/admin/'))
                and getattr(request, 'user', None) is not None
                and request.user.is_authenticated
                and request.user.is_staff
            ):
                maybe_run_backup()
        except Exception:
            # Must never break or slow the page beyond the backup itself
            # -- this except is the final backstop on top of
            # maybe_run_backup()'s own internal try/except-shaped control
            # flow, in case something unexpected (a settings/import-time
            # issue, a filesystem error outside the paths already
            # handled) slips through.
            logger.exception('OnDemandBackupMiddleware failed')
        return response
