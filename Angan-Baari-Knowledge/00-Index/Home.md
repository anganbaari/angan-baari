# Angan Baari — Project Knowledge Vault

Human-readable, long-term project knowledge for the Angan Baari (आँगन बारी) Django e-commerce + POS project. This vault holds durable knowledge that's too detailed for `CLAUDE.md` but more permanent than session chatter.

**Division of labor between the three knowledge systems:**
- **`CLAUDE.md`** (project root) — stable instructions and constraints Claude should follow in essentially every session. Kept short and operational.
- **This vault** — human-readable reference: architecture explanations, decision rationale, deployment procedures, bug history. Git-friendly Markdown.
- **claude-mem** (`~/.claude-mem/`, local-only) — automatic, per-session observational memory. Not version-controlled, not meant to be read directly.

See `03-Decisions/Decision-Log.md` for the promotion rule between these three.

## Sections

- [[Project-Overview]] · [[Current-State]] · [[Roadmap]] — `01-Project/`
- [[System-Architecture]] · [[Django]] · [[ABMS]] · [[POS]] · [[Inventory]] · [[REST-API]] — `02-Architecture/`
- [[Architecture-Decisions]] · [[Decision-Log]] — `03-Decisions/`
- [[Reports-Dashboard]] — `04-Features/`
- [[Inventory-Rules]] — `05-Inventory/`
- [[ABMS-Integration]] — `06-API/`
- [[Deployment-Workflow]] — `07-Deployment/`
- [[Known-Bugs]] · [[Frontend-Lessons]] — `08-Bugs-and-Lessons/`
- `09-Session-Notes/` — dated notes worth keeping beyond claude-mem's automatic capture
- [[Key-Files]] · [[Frontend-Conventions]] — `10-Reference/`
