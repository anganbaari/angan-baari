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

## 2026-10-25 — The server is the only price authority

`POST /api/v1/cart/quote/` and `POST /api/v1/orders/` price every line
themselves with `quote_cart_line()` (which reuses `price_pos_cart_line()`)
and ignore any price the client sends; the legacy session cart's
`add_to_cart_offer()` was changed the same way. Alternative considered
and rejected: let the storefront compute and send prices — it can't be
trusted (a tampered request would set its own price), and it would let
the website drift from the POS's rounding. Reusing the POS function means
one rounding rule everywhere. Each order line stores its own
normal/final/discount in `cart_snapshot`, so what the customer was
charged is recorded. See [[Offers-and-Cart-Quote]], [[Orders-and-Profile]].

## 2026-10-25 — Offers visible to everyone, priced only for logged-in customers

The owner's rule: anyone can **see** current offers (`GET /api/v1/offers/`
is public), but only an authenticated customer **gets** the offer price in
the cart and on orders. A guest is quoted the normal price plus a
`login_required_for_offers` flag so the storefront can invite them to log
in. Among several live offers the lowest resulting price wins
(`resolve_automatic_offer()`) — offers are automatic, nobody picks one.
Combo bundles stay display-only (WhatsApp). POS offers are unaffected and
stay operator-chosen. See [[Offers-and-Cart-Quote]].

## 2026-10-25 — "Remove from history" hides an order, it never deletes it

Customers can remove Delivered or Cancelled orders from their profile
list, but this only sets `ProductOrder.hidden_by_customer` (migration
0051). The order, its inventory movements and its admin/report presence
are untouched. Alternative rejected: deleting the order — it would erase
the ledger link behind `InventoryMovement.related_order`, the sales
records and anything the owner needs for a dispute. Pending and Confirmed
orders cannot be hidden (the API returns 400), so a customer can't lose
sight of an order they're still waiting on. See [[Orders-and-Profile]].

## 2026-10-25 — Orders are linked to accounts by `user`, not by email

`ProductOrder.user` (nullable FK, migration 0051) is set only when
`POST /api/v1/orders/` carries a valid token. Profile orders, cancel and
reorder match by `user` alone. Matching by email was rejected: the project
has no email verification, so anyone could type another person's email
into checkout and the real owner would then see (and be able to cancel)
that order — or an attacker could register with a victim's email and read
their history. The cost: guest orders and orders from before the field
existed have `user=None` and never appear in a profile unless an admin
links them by hand. See [[Orders-and-Profile]].

## 2026-10-23 — On-demand backups instead of a scheduled task

PythonAnywhere's free tier has no scheduled-task feature, so the daily
`backup_db` run is triggered opportunistically by
`shop/middleware.py`'s `OnDemandBackupMiddleware` from inside an
ordinary staff `/pos/` or `/admin/` request, instead of a cron job.
Deliberately synchronous, not a background thread — uwsgi on the free
tier may not run threads reliably, so "the one triggering request runs a
little slower" was accepted over any approach that depends on
background execution. Throttled (10 min/process), lock-filed (mutex
across simultaneous requests), and size-capped (100MB, since an
in-request backup past that size would be too slow/risky) — see
[[Backups]] for the full mechanics and the accepted consequence: the
backup depends on a staff member opening a staff page at least once a
day.

## 2026-10-22/23 — Database-driven home page category sections

`templates/index.html`'s category sections (Fruits/Honey/Pickles/
Vegetables hand-written, everything else generated from `Category` rows)
were built to join the **same** shared tab/card grid rather than form a
separate section, after discovering `static/script.js`'s filtering is
one page-wide system, not scoped per section — see
[[Home-Page-Sections]] for the full reasoning. Alongside this,
`Product.tagline` (migration `0050_product_tagline`) was added and every
card's price was replaced with a one-line punchline (tagline, or a
trimmed first sentence of the description) — a deliberate content
change, not a side effect of the database-driven work.

## 2026-10-21 — Storefront links switch (Phase 1, links only, no redirects)

Rather than redirect Django pages to the new Next.js storefront on
Vercel outright, `STOREFRONT_URL` + `{% storefront_link %}`
(`shop/templatetags/storefront_tags.py`) only change the **destination
of specific links** on specific Django pages (Shop/Cart/Checkout/Login/
Signup/Profile/product links), leaving every Django page itself fully
functional either way. Chosen so the switch could be tested and rolled
out incrementally, link by link, without a hard cutover moment where the
old pages stop working. Offers, POS, admin, the dashboard, and two
token-specific routes (`/cancel/<token>/`, the legacy password-reset
flow) were excluded because Vercel has no equivalent for any of them —
see [[Storefront-and-Vercel]] for the full routing table.

## 2026-10-23 — Staying on SQLite; Supabase specifically re-evaluated and rejected

Revisited the standing "Postgres deferred until concurrent writes are
needed" decision specifically against Supabase (free-tier managed
Postgres), since it's a common reason to reconsider. Still rejected, for
two independent reasons: PythonAnywhere's **free tier cannot reach an
external Postgres host at all**, so this would also force a paid-tier
upgrade; and even on a paid tier, a Supabase database physically distant
from the PythonAnywhere server would add network latency to **every**
query, which is a straightforward regression, not a tradeoff, for an app
that currently pays zero network cost per query (SQLite is a local
file). The decision going forward: revisit Postgres only together with
moving Django itself to a host in the same region as the database
(Singapore or Mumbai) — a Postgres migration and a hosting migration
bundled as one decision, not two independent ones.

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
