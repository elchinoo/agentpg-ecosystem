## llm-gateway (a11y, none functional)

All found by axe on real Chrome, Playwright suite `tests/llmgw.spec.ts`; the specs list the rule ids in `knownA11y` so the run is green and the findings stay visible.

| # | Impact | Rule | Where | Steps / actual | Expected |
|---|---|---|---|---|---|
| 1 | serious | color-contrast | `.badge.ok` ("enabled"/"active") on Models and a client's detail, light theme | Open #models after setup; the green badge text is below 4.5:1 | AA contrast. Probably the kit badge colour (shared with identity). Evidence: `screenshots/llmgw/models-light-desktop.png` |
| 2 | serious | scrollable-region-focusable | Overview, phone width (390), the clients table card `#view-overview > .flush.card` | Resize to 390 wide after traffic exists; the card scrolls horizontally but cannot take keyboard focus | `tabindex="0"` plus an accessible name on the scroll container. Evidence: `screenshots/llmgw/overview-light-phone.png` |
| 3 | moderate/minor | heading-order, empty-table-header | h3 right after h1; actions column header is empty | not failing the suite |  |
