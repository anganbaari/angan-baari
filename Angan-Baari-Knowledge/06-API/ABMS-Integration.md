# ABMS Integration

The only legitimate REST API boundary in this project. See [[REST-API]] and [[ABMS]] for why it's the sole exception to "no REST layer between subsystems."

- **Endpoint:** `/api/inventory/movements/`
- **Auth:** token-authed
- **CORS:** allowed for `https://angan-baari.web.app`
- **Why it exists:** ABMS is a genuinely separate hosted system (Firebase/Firestore), with no other way to reach this Django project's database.

## What's bridged today vs. not

Not yet connected (see [[Roadmap]] for full list):
- Local Egg / Vermicompost inventory bridge (ABMS's `productionLog` / `vermiOut`) — planned to follow the same pattern as the existing harvest bridge.
- Goat/Chicken sales bridge — blocked on ABMS needing a live variant-picker UI so the operator selects the specific animal sold.
