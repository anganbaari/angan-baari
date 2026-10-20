# Frontend Lessons

## Font Awesome CDN incident → inline SVG

The site uses **inline SVG for icons, not the Font Awesome CDN**. This is a lesson learned from a real incident (the CDN silently failed in production before), not a style preference — don't "simplify" by switching back to a CDN-hosted icon font. See [[Frontend-Conventions]] for the full set of current frontend rules.

## Test "element is absent" by the rendered tag, not the CSS class name

More than once, a test asserting some markup is **absent** (e.g.
`assertNotIn('refund-slip-links-wrap', content)`) false-failed because the
bare class/selector name is **always** present in the page's own
`<style>` block, regardless of whether the element itself ever renders.
The fix each time was the same: assert on the actual opening tag instead
(e.g. `'<div class="refund-slip-links-wrap">'`), or on a string that only
appears in the rendered content, not the CSS. Found repeatedly across the
receipt (`PosReceiptViewTests`) and refund-link-reachability
(`api/tests_refunds.py`) test suites — worth checking for every time a
new "X is hidden/skipped" test is written against one of these
self-contained `<style>`-block pages. See [[Refunds]],
[[Receipts-and-Pricing]].
