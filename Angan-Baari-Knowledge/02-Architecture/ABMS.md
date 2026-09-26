# ABMS

A **separate** standalone farm-management PWA (आँगन बारी), built on Firebase/Firestore, deployed at `angan-baari.web.app`. **Different codebase, not in this repo.**

It talks to this Django project only through the REST API boundary described in [[ABMS-Integration]] — never assume any tighter coupling than that.

Farm-management concerns that live in ABMS (not this repo): harvest logging, `productionLog`, `vermiOut`, and presumably day-to-day farm operations data. The bridge from ABMS into this project's `InventoryMovement` ledger is partial — see [[Roadmap]] for what's not yet connected (egg/vermicompost, goat/chicken sales variant-picker).
