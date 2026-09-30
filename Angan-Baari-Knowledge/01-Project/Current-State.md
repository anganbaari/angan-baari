# Current State

*Living document — update as work lands. Last checked: 2026-10-01.*

## Recently landed (as of 2026-10-01, commit 76865d4 + POS Phase C)

- The `django.ce.exceptions` typo (see [[Known-Bugs]]) is fixed and committed — no longer an open item.
- POS Phase A (staff roles + PIN unlock) and Phase B (Customer/credit ledger, split payments, dormant VAT scaffolding) are built, tested, committed, and confirmed on `main`.
- POS Phase C (coupon discounts) added this session: `resolve_pos_coupon()` + coupon fields on `POSSale` + `/api/v1/pos/coupon/validate/` preview endpoint + pos.html coupon UI. 10 new tests, 122/122 passing locally. **Not yet migrated/deployed to PythonAnywhere as of this writing** — migration `shop/0030_possale_coupon_possale_discount_amount.py` needs `migrate` there after the next `git pull`.
- `ProductSellingUnit` (fixed_quantity selling units, e.g. jar sizes) and a shared weight/qty keypad modal with Kg/Gram switching also landed since the doc below was last accurate — check `shop/models.py` directly for the current full model list rather than trusting an older summary.

## Recently set up (2026-09-27)

- This Obsidian vault, `claude-mem` (local-only, Ollama-backed), as a companion to `CLAUDE.md`. See `03-Decisions/Decision-Log.md`.
- `.claude/` and `.agents/` skill directories exist from Claude Code skill installs (`skill-creator`, `find-skills`) — not application code, don't confuse with project structure.

## Not yet true

Anything in [[Roadmap]] is explicitly **not built**. Don't assume deferred work has been picked up without checking the actual code first.
