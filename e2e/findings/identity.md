## identity (a11y, none functional)

Found by axe in `tests/identity.spec.ts`; rule ids are in `knownA11y` so the run stays green.

| # | Impact | Rule | Where | Steps / actual | Expected |
|---|---|---|---|---|---|
| 1 | critical | select-name | Admin > Groups, the per-row "app" `<select>` of "Grant a role to the whole group" | Create a group; the select in its row has no label or aria-label (the role input has a placeholder only) | An accessible name such as `aria-label="App for group finance"`. Evidence: `screenshots/identity/groups-light-desktop.png` |
| 2 | serious | color-contrast | `.badge` ("active") on Apps, light theme | Register an app and open Apps | AA contrast (same kit badge as llm-gateway) |
| 3 | serious | scrollable-region-focusable | Overview at phone width: a `<pre>` code block scrolls but is not keyboard focusable | Open /admin at 390 wide | `tabindex="0"` on the scrollable `<pre>`. Evidence: `screenshots/identity/overview-light-phone.png` |
| 4 | moderate/minor | heading-order, empty-table-header | h3 after h1; empty action column headers | not failing the suite |  |
