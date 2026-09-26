# Current State

*Living document — update as work lands. Last checked: 2026-09-27.*

## In-flight (uncommitted, as of 2026-09-27)

- **`shop/models.py`** — the `django.ce.exceptions` → `django.core.exceptions` typo fix (see [[Known-Bugs]]) is applied locally but **not yet committed, migrated, or deployed**. Confirm it's landed before assuming POS sales work in production.
- **`farmsite/settings.py`** — `ALLOWED_HOSTS` extended to include `127.0.0.1`/`localhost` for local dev testing. Uncommitted.
- **`shop/migrations/0021_possale_client_sale_id.py`** — new migration, untracked. Adds `POSSale.client_sale_id` (see [[POS]]).

## Recently set up (2026-09-27)

- This Obsidian vault, `claude-mem` (local-only, Ollama-backed), as a companion to `CLAUDE.md`. See `03-Decisions/Decision-Log.md`.
- `.claude/` and `.agents/` skill directories exist from Claude Code skill installs (`skill-creator`, `find-skills`) — not application code, don't confuse with project structure.

## Not yet true

Anything in [[Roadmap]] is explicitly **not built**. Don't assume deferred work has been picked up without checking the actual code first.
