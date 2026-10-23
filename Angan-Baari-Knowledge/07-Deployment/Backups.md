# Backups

Two management commands plus a middleware, working together because
PythonAnywhere's free tier has no scheduled-task feature at all.

## The commands

`python manage.py backup_db`
(`shop/management/commands/backup_db.py`):

1. A **consistent copy** of the live SQLite database via sqlite3's own
   online backup API (`Connection.backup`) — not a plain file copy,
   which could capture a half-written page mid-request.
2. `PRAGMA integrity_check` on that copy.
3. Gzip-compress to `BACKUP_DIR` (env var, default `~/backups`) as
   `angan-baari-YYYYMMDD-HHMM.sqlite3.gz` (Asia/Kathmandu local time).
4. Retention: newest 14 backups overall **plus** newest 8 Sunday ones
   kept (a file matching both is still just one file — "keep" is a set).
5. One Telegram message either way (`send_telegram()`, `shop/emails.py`)
   — success names the file/size/kept-count, failure names the error.

`python manage.py restore_check [path]`
(`shop/management/commands/restore_check.py`) — decompresses a backup
(the newest one in `BACKUP_DIR` if no path given) into a temp directory,
runs `PRAGMA integrity_check`, and prints row counts for the key tables
(`Product`, `ProductVariant`, `POSSale`, `POSSaleLine`, `Refund`,
`InventoryMovement`, `ProductOrder`, `CreditTransaction`, `User`).
**Never touches the live database** — the only files it ever reads are
the backup itself and its own temp-directory copy, both cleaned up on
exit. Safe to run at any time, as often as you like.

## Why there's a middleware at all

PythonAnywhere's free tier has no scheduled tasks — the thing that would
normally run `backup_db` once a day on a timer simply doesn't exist
there. Rather than require a staff member to SSH in and run the command
by hand every day (which would eventually get forgotten), 
`shop/middleware.py`'s `OnDemandBackupMiddleware` opportunistically runs
the exact same `backup_db` command from inside an ordinary staff page
request — `/pos/` and `/admin/` are both staff-only already and get
requested constantly through a normal working day.

Mechanics (registered right after `AuthenticationMiddleware` in
`MIDDLEWARE`, so `request.user` is already resolved):

- Fires only for an **authenticated staff** `GET` to a path starting
  with `/pos/` or `/admin/`.
- Checks the newest file in `BACKUP_DIR` — if it's under 20 hours old,
  does nothing. This check itself is throttled to once per 10 minutes
  per worker process (a module-level timestamp), so it isn't re-reading
  the directory on every single staff page load.
- Runs `call_command('backup_db')` **synchronously, inline in the
  request** — deliberately **not** a background thread, since uwsgi on
  PythonAnywhere's free tier may not run threads reliably. The one
  request that triggers a backup is a little slower (see timing below);
  nothing else is affected.
- An atomic lock file (`.backup_on_demand.lock` in `BACKUP_DIR`,
  `O_CREAT|O_EXCL`) stops two simultaneous staff requests from both
  starting a backup. A lock older than 10 minutes is treated as
  abandoned (a worker that crashed mid-backup) and cleared before one
  retry, so a single stuck lock can't wedge every future request
  forever.
- If the live database exceeds 100MB, the backup is skipped entirely —
  an in-request synchronous backup that size would be too slow/risky —
  logged, with one Telegram warning per calendar day (not spammed every
  10-minute check). **If this ever actually fires, it means a real
  scheduled task is needed instead of this middleware.**
- Everything is wrapped in try/except, at two levels (the backup logic
  itself, and the middleware's own `__call__`) — a backup problem must
  never raise to the user or slow the page beyond the backup itself. A
  failure is reported the same way `backup_db`'s own failure always is:
  one Telegram message.
- `BACKUP_ON_DEMAND` setting (env-driven, default `True`, accepts
  `1`/`true`/`yes`) turns the whole thing off — e.g. once a real
  scheduled task exists somewhere and this would just be redundant.

**The real-world consequence worth remembering**: the production backup
depends on a staff member actually opening `/pos/` or `/admin/` at least
once every 20 hours. A day with zero staff logins is a day with no new
backup — there's no cron job underneath this catching that case.

**Timing** (measured 2026-10-23 against the live ~1MB `db.sqlite3`):
about **0.22 seconds** for a full backup. This will grow with database
size, which is exactly why the 100MB skip threshold exists — well before
"inline in a request" stops being a reasonable place to run this at all.

## Restoring from a backup

On a PythonAnywhere Bash console, with the project's venv active:

```bash
cd ~/angan-baari
source venv/bin/activate   # if this console doesn't already have it active

# 1. VERIFY FIRST, before touching the live database at all. Read-only.
python manage.py restore_check                                          # newest backup in BACKUP_DIR
python manage.py restore_check ~/backups/angan-baari-20261022-0300.sqlite3.gz   # or a specific one

# 2. Only if integrity_check says "ok" and the row counts look right:
#    keep the current (possibly broken) db.sqlite3 around just in case,
#    then swap in the backup.
cp db.sqlite3 db.sqlite3.bak-$(date +%Y%m%d-%H%M)
gunzip -c ~/backups/angan-baari-20261022-0300.sqlite3.gz > db.sqlite3

# 3. Web tab → Reload, so the running WSGI process picks up the restored file.
```

Don't skip step 1 — `restore_check` is read-only and exists specifically
so a bad backup is caught *before* it overwrites a working database, not
after.

## The off-server-copy gap

`~/backups` lives on the **same PythonAnywhere account** as the live
site. These backups protect against database corruption or a bad
migration — they do **not** protect against losing the PythonAnywhere
account itself (a lapsed payment, a suspended account, anything that
takes the whole account away at once). That scenario takes every backup
in `~/backups` down with it.

There is no automation for this yet. The accepted mitigation is a
**manual habit**: download the newest backup file to a separate
machine or cloud storage roughly **once a month** — via PythonAnywhere's
Files tab, or `scp`/`rsync` from a machine with SSH access to the
account. This is intentionally a human habit, not a missing feature to
build — same spirit as occasionally actually looking at `restore_check`
output rather than trusting backups blindly forever.
