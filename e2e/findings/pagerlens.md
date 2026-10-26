## PagerLens

No functional bug found: setup, seed, Lab, Rules investigation, evidence, resolve and the retire-service panel (typed name survives the 10 s refresh) all behave as documented.

### Accessibility (axe, wcag2a/aa + best-practice; serious/critical fail the test unless listed as known)

| Screen | Rule | Impact | Where | Status |
|---|---|---|---|---|
| lab (dark, desktop) | color-contrast | serious | `.sev1.badge.sev` severity badge on two scenario cards | known, `knownA11y` in spec |
| investigation (light/dark, desktop/phone) | color-contrast | serious | `.alt` badge and `.stance.supports` labels in hypotheses | known, `knownA11y` in spec |
| investigation (all) | scrollable-region-focusable | serious | `.obs > pre` (raw evidence) and `.flush` table on phones: scrollable but not keyboard reachable | known, `knownA11y` in spec |
| lab | heading-order | moderate | heading levels skip | reported only |

Evidence: `e2e/screenshots/pagerlens/lab-dark-desktop.png`, `investigation-light-desktop.png`, `investigation-light-phone.png`.
Expected: no serious violations; actual: the rows above. Steps: Lab > Break it on "Bad release breaks checkout" > Investigate (Rules) > open "evidence #N".
