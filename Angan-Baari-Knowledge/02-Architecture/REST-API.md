# REST API

This project has exactly **one** legitimate REST API boundary: ABMS ↔ Django, at `/api/inventory/movements/` (token-authed, CORS-allowed for `https://angan-baari.web.app`). Full detail in [[ABMS-Integration]].

**There is no REST API layer between POS and the website.** They're the same Django project, same database, same process — don't propose one. See [[Architecture-Decisions]].
