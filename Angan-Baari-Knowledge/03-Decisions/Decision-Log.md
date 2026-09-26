# Decision Log

Dated entries, newest first. For the standing list of already-settled architecture decisions (not dated, treated as constant), see [[Architecture-Decisions]].

## Promotion rule (how something ends up here vs. elsewhere)

```
Temporary/session detail          → claude-mem (automatic, local, ~/.claude-mem)
Repeatedly useful project knowledge → this vault (Obsidian)
Stable instruction/constraint      → CLAUDE.md
```

Before adding something to `CLAUDE.md`, ask: *"Would this still be useful and correct for Claude in most future sessions?"* If no, it belongs here or in claude-mem instead. `CLAUDE.md` stays short and operational — it is not a dumping ground for session history.

---

## 2026-09-27 — Set up long-term knowledge system

Installed `claude-mem` v13.27.1 (local-only, `--provider host` pointed at an already-running local Ollama instance — no cloud account, no data leaves the machine) and created this Obsidian-compatible knowledge vault. `CLAUDE.md` was read in full and left unmodified in content; durable knowledge from it was reorganized (not copied verbatim) into this vault's sections. See the project's own conversation history for full rationale if needed — this entry exists so the *decision* (not the play-by-play) is captured here.
