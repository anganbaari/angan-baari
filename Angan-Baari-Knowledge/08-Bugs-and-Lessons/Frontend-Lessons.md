# Frontend Lessons

## Font Awesome CDN incident → inline SVG

The site uses **inline SVG for icons, not the Font Awesome CDN**. This is a lesson learned from a real incident (the CDN silently failed in production before), not a style preference — don't "simplify" by switching back to a CDN-hosted icon font. See [[Frontend-Conventions]] for the full set of current frontend rules.
