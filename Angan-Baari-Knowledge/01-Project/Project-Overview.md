# Project Overview

Angan Baari (आँगन बारी) is an organic farm e-commerce site + POS system for a real working farm in Bhulka Danda, Rupandehi, Nepal.

**Sells:** fruits (mango, lychee, papaya, banana, jackfruit), honey, pickles, and livestock (live goats and chickens — no butchered/meat shop).

**Farm also has:** beehives, water harvesting infrastructure, vermicomposting.

## Two separate systems

1. **This Django project** — e-commerce site + admin + POS, hosted on PythonAnywhere at `anganbaari.pythonanywhere.com`.
2. **ABMS** (आँगन बारी) — a *separate* standalone farm-management PWA on Firebase/Firestore, deployed at `angan-baari.web.app`. Different codebase, not in this repo. See [[ABMS]].

Do not conflate the two. See [[System-Architecture]] for how they connect.

## Stack summary

Django + DRF, SQLite (locally and in production), PythonAnywhere hosting, server-rendered templates (no React/Next.js), ImageKit for images, Resend for email, Telegram bot for admin notifications. Details in [[Django]].
