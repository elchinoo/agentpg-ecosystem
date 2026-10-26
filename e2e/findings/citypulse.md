## CityPulse and CityPulse Simulator

No functional bug found: setup, ingest token, credential store, city generation, delivery, heatmap and burst all work.
Accessibility findings (axe, real Chrome; screenshots in `screenshots/citypulse/` and `screenshots/cpsim/`):

| App | Screen | Rule | Impact | Detail |
|---|---|---|---|---|
| cpsim | Settings (`cpsim/settings-*`) | color-contrast | serious | `.st.offline` status pill in the Fleet table is below 4.5:1 (light theme, 6 nodes). Known in the spec (`knownA11y`). |
| cpsim | Cities, city page (`cities-generated`, `city-running`, `city-burst`) | scrollable-region-focusable | serious | Table wrappers (`.card.flush`) scroll but cannot take keyboard focus (desktop and phone). Known in the spec. |
| citypulse | Overview (`citypulse/overview-*-phone`) | scrollable-region-focusable | serious | "By sensor type" table wrapper scrolls at 390 px without keyboard access. Known in the spec. |
| citypulse, cpsim | Overview, city page | empty-table-header | minor | Heatmap corner `<th>` and the actions column header are empty. |
| cpsim | City page | heading-order | moderate | h1 followed by h3 cards ("Delivery to CityPulse"). |

Harness note (not an app bug): the shared `kitLoginViaApi` in `harness/ui.ts` posts `/api/login` without the app's CSRF header
(`<html data-csrf>`), which CityPulse and the Simulator answer with 403; `harness/helpers/citypulse.ts` has a variant that sends it.
