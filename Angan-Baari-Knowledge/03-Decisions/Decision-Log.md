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

## 2026-10-20 — Whole-rupee POS line pricing (line-level rounding, "Option B")

Variable-weight POS cart lines now round to the nearest whole rupee
(`ROUND_HALF_UP`) at the LINE level, inside `price_pos_cart_line()`
(`shop/views.py`) — after any offer discount, before a combo override —
instead of only ever rounding the sale's total at the very end. The
alternative considered (keep rounding order-level only) was rejected
because it left an ordinary single-line sale needing a few paisa of
"round off" absorbed at checkout for no real reason, and made the
printed receipt's own round-off-hiding logic do more work than
necessary. `templates/pos.html`'s JS mirrors this with integer
(paisa × milli-kg) arithmetic specifically to keep the browser and server
in exact agreement — see [[Receipts-and-Pricing]] for the full picture,
including the one path that can still produce a fractional total
afterward (a percentage coupon on an already-whole-rupee subtotal).

## 2026-10-20 — No barcode on the refund slip

The refund slip (`templates/pos_refund_slip.html`) deliberately has no
barcode, unlike the sale receipt. Explicitly asked for as a scope
boundary when the slip was built, not an oversight — a refund slip isn't
re-scanned at a later till the way a sale receipt's barcode might be.
See [[Receipts-and-Pricing]].

## 2026-10-20 — Round-off row hidden on the printed receipt

The printed sale receipt never shows a "Round off" row — it shows what
the customer actually paid (Subtotal/कर स. मूल्य/Total all made to agree
exactly) rather than the pre-rounding arithmetic that produced that
figure. This predates the whole-rupee line-pricing rule above and was
its own deliberate choice; the line-pricing rule just means the gap this
logic has to paper over is usually zero now instead of a few paisa. See
[[Receipts-and-Pricing]].

## 2026-09-27 — Set up long-term knowledge system

Installed `claude-mem` v13.27.1 (local-only, `--provider host` pointed at an already-running local Ollama instance — no cloud account, no data leaves the machine) and created this Obsidian-compatible knowledge vault. `CLAUDE.md` was read in full and left unmodified in content; durable knowledge from it was reorganized (not copied verbatim) into this vault's sections. See the project's own conversation history for full rationale if needed — this entry exists so the *decision* (not the play-by-play) is captured here.
