# Django Stack

- Django + Django REST Framework, Python
- **SQLite** locally **and in production** — deliberately deferred Postgres migration until the POS needs concurrent writes. See [[Architecture-Decisions]].
- Hosted on **PythonAnywhere**
- **Server-rendered Django templates only** — no React/Next.js in this project (considered and explicitly rejected)
- **Images:** ImageKit CDN, via `shop/imagekit_storage.py` (custom Storage backend)
- **Email:** Resend API, via `shop/emails.py` — not Django's SMTP backend
- **Admin notifications:** Telegram bot
- GitHub for version control. Local dev on Windows, VS Code, `venv`.

Deployment procedure lives in [[Deployment-Workflow]]. Key file map in [[Key-Files]].
