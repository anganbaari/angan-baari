# Frontend Conventions

- **Honeycomb hexagon navigation** (`.hc-cell`, `.hc-nav`, `.hc-cta`) is current across all pages **except `profile.html`** (known gap — still old pill-style `.nav-links`, see [[Roadmap]]).
- **Inline SVG for icons**, not Font Awesome CDN — see [[Frontend-Lessons]] for why.
- **`path()` clip-paths, not `polygon()`**, for hexagon shapes.
- **Background hexagon strips sized at runtime via JS** (`fitStripBg()`, `fitMobileBg()`), not static CSS — intentional.
- **CSS variables:** `--moss`, `--gold`, `--forest`, `--cream`, `--mist`, `--text-muted`.
- **Fonts:** Cormorant Garamond (`--font-display`), DM Sans (`--font-body`), Cinzel (`--font-accent`).
- **Cache-busting:** append `?v=N` to static assets when editing CSS/JS.
