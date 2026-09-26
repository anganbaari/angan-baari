# System Architecture

Two systems, one legitimate boundary between them.

```
┌─────────────────────────────┐      /api/inventory/movements/      ┌──────────────────────┐
│   This Django project        │◄────────(token-authed, CORS)────────│   ABMS (Firebase PWA)  │
│   anganbaari.pythonanywhere  │                                      │   angan-baari.web.app  │
│   .com                       │                                      │   (separate codebase,   │
│   — website + admin + POS —  │                                      │   not in this repo)     │
└─────────────────────────────┘                                      └──────────────────────┘
        ▲              ▲
        │              │
   website cart    POS terminal
   (session-based) (staff login,
                    vanilla JS)
```

**Key rule: no REST API layer between POS and the website.** Same Django project, same database, same process. Both funnel through the shared helper `create_inventory_movements_from_snapshot()` in `shop/views.py` (two modes — see [[Inventory-Rules]]).

The *only* legitimate REST API boundary in this project is ABMS↔Django, because ABMS is a genuinely separate hosted system (Firebase) with no other way to reach this database. See [[ABMS-Integration]].

Full rationale for these decisions: [[Architecture-Decisions]].
