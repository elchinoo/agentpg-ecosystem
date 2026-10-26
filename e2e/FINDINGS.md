# Findings from the end-to-end suite

Real problems the suite exposed, and what happened to them. The apps are never changed by this suite. Every audited screen
(light and dark, desktop and phone) is checked with axe on every run: serious and critical violations fail the test unless the rule is
listed in that `snap` call's `knownA11y`. `reports/a11y.jsonl` (git-ignored, `npm run a11y` summarises it) lists everything axe
reports, whatever the impact, including minor and moderate findings.

## Current state (run against the sibling checkouts below, agentpg-kit 0.2.5)

31 tests pass, 0 fail, 0 skipped. Checkouts the run used (printed at the start of each run as `[e2e] <repo>@<hash>: editable <path> kit <version>`):
trustdesk 93df925, paybroker ea6dad9, pagerlens 7f143c1, CityPulse 7602ce5, CityPulseSimulator 90b8ab9, llm-gateway 8352ed6, identity 556dcba, shopfront be49fe6 (agentpg-kit 9314d98, tag v0.2.5).

### Open

| App | Rule (impact) | Screen / theme | Selector | Likely cause |
|---|---|---|---|---|
| TrustDesk | label-content-name-mismatch (serious) | Documents, light and dark, desktop | `#dropzone` | `static/index.html`: `<div id="dropzone" role="button" aria-label="Upload documents">` shows "Drop files here or click to browse"; the accessible name must contain the visible text. Drop the `aria-label` or make it start with "Drop files here or click to browse". This is the only `knownA11y` entry left (`tests/trustdesk.spec.ts`). |

No new findings were introduced by the 0.2.5 kit or the app changes (scrollRegion / `role="region"`, heading levels, `.sr-only` table headers): axe reports nothing else on any audited screen, at any impact level.

### Fixed (each verified by a green run; the matching `knownA11y` entries were removed, so a regression now fails)

| Finding | Fixed in |
|---|---|
| Confirm dialogs inside a modal `<dialog>` were dead (PayBroker revoke key; ShopFront refund, capture, void, cancel; kit `confirmDialog` was an inert popover) | agentpg-kit 0.2.5 (native `showModal()` dialogs). Regression tests: PayBroker "revoking an API key can be confirmed inside the merchant window", ShopFront "a refund is confirmed in the order dialog and reaches PayBroker" (were `test.fail()`) |
| Kit badge `color-contrast` (`.badge.ok`, `.alt`, status badges) in TrustDesk, PayBroker, ShopFront, llm-gateway, identity, PagerLens | agentpg-kit 0.2.5 |
| ShopFront: a first-time shopper looked like a brand-new account (account age 0 from the first order; score about 600, 836 with a quick checkout) | shopfront d10747b / be49fe6 (age sent only when known, taken from the first paid order's `paid_at`). The suite's returning-customer story back-dates `paid_at`. |
| ShopFront: duplicate `id="co-error"` on checkout | shopfront d10747b (taken from the commit message; no test asserts it) |
| PagerLens `.sev1.badge.sev` dark contrast; `.obs > pre` and `.flush` not keyboard focusable; heading-order | pagerlens 7f143c1 |
| CityPulse overview scrollable region, empty table headers; CityPulseSimulator `.st.offline` contrast, scrollable regions, heading-order, empty headers | CityPulse 7602ce5, CityPulseSimulator 90b8ab9 |
| llm-gateway overview scrollable region, heading-order, empty table headers | llm-gateway 8352ed6 |
| identity Groups `select` without a name (critical), `pre` scroll region, heading-order, empty table headers | identity 556dcba |
| PayBroker heading-order, empty table headers | paybroker ea6dad9 |
| TrustDesk dark `#cite-1 .badge` contrast, empty table headers | trustdesk 93df925 |

## Notes

- CityPulseSimulator's checkout had one untracked file (`uv.lock`) during the run; nothing else was dirty.
- The "which commit fixed it" mapping above follows from the finding disappearing between the previous run (apps at kit 0.2.4 / earlier commits) and this one; I did not bisect individual commits.
