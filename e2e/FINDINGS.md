# Findings from the end-to-end suite

Real problems the suite exposed. The apps were not changed (other repositories are out of scope for this suite). Each item is covered by a test: a `test.fail()` test turns red as soon as the bug is fixed, and accessibility items are listed as `knownA11y` in the spec of the screen concerned. The complete axe list of every run is in `reports/a11y.jsonl` (git-ignored; `npm run a11y` summarises it).

## TrustDesk

### F-TD-1  Accessibility (axe)
- serious `label-content-name-mismatch`: the Documents drop zone (`#dropzone`, `role="button"` with `aria-label="Upload documents"` while the visible text is "Drop files here or click to browse"). Screen: `screenshots/trustdesk/documents-light-desktop.png` (listed as known in the spec).
- serious `color-contrast`: the `.alt` badge in Settings and the green `.badge.ok` citation badge in the review window (light and dark). Screens: `settings-*`, `review-ai-advice-*`, `delete-account-dialog-*`. Probably the shared kit badge colours (same rule fires in PayBroker, ShopFront, llm-gateway, identity).
- minor `empty-table-header`: the last (action) column header of the documents table and of the Settings API tokens table.

## PayBroker

### F-PB-1  Confirm dialog inside a modal dialog cannot be confirmed (kit bug, also in ShopFront)
- App: PayBroker (shared agentpg-kit `confirmDialog`, `static/ui.js`).
- Steps: Merchants > open a merchant > create a key > press Revoke on it > press "Revoke" in the confirmation.
- Expected: the key is revoked.
- Actual: nothing happens, with a real mouse click or Enter. The confirmation is a `popover="manual"` element appended to `<body>`; while the merchant `<dialog>` is open with `showModal()`, Chrome treats everything outside the dialog as inert, so the confirm button never receives the event (`elementFromPoint` hits the dialog's content underneath). Same for "Record a chargeback" (prompt dialog) in the payment window.
- Evidence: `tests/paybroker.spec.ts` "revoking an API key can be confirmed inside the merchant window" (marked `test.fail`, so it turns red when the bug is fixed); Chrome 155.
- Suggested fix: render the confirm inside the open dialog (append to `document.querySelector('dialog[open]') ?? body`) or make it a `<dialog>` shown with `showModal()` after the first.

### F-PB-2  Accessibility (axe)
- serious `color-contrast`: green status badge text (`.badge.ok`, "Authorized", "active") in light mode on Merchants, payment window, Settings. Screens: `screenshots/paybroker/payment-detail-light-desktop.png`, `merchant-keys-light-desktop.png`, `settings-light-desktop.png`. The tests list `color-contrast` as known on those screens.
- moderate `heading-order` (h3 straight after the page's h1/none) on Overview and Cases.
- minor `empty-table-header` (an action column with an empty `<th>`) on Merchants (keys table) and Settings (accounts).

## ShopFront (with PayBroker)

### F-SF-1  Confirm dialog inside the order window cannot be confirmed (same kit bug as F-PB-1)
- App: ShopFront back office (`/admin`, shared agentpg-kit `confirmDialog`).
- Steps: sign in, Orders, open a paid order, press Refund, press "Refund payment" in the confirmation.
- Expected: the order becomes Refunded.
- Actual: nothing happens (mouse and keyboard); the confirmation popover is inert while the order `<dialog>` is modal. Capture/void/cancel use the same confirmation and have the same problem; refunds are impossible from the UI.
- Evidence: `tests/shopfront.spec.ts` "back office: a refund can be confirmed in the order dialog" (`test.fail`). The refund path itself works: the next test refunds through the admin API (`POST /api/orders/{n}/refund`) and the UI then shows Refunded with the payment trail.

### F-SF-2  A first-time shopper can never get a plain approval (design observation)
- Steps: a new e-mail buys with `4242 4242 4242 4242` (any speed).
- Expected (from the task story): order paid at once.
- Actual: ShopFront derives `account_age_hours` from the e-mail's first order, so a first order is age 0: PayBroker scores about 600 (`NEW_ACCOUNT`, plus the odd reason `MANY_CARDS_ON_ACCOUNT` although the feature `distinct_cards_used_7d` is 0) and challenges with 3-D Secure; if the checkout is also fast (`FAST_CHECKOUT`, as with automation) the score is 836 and the payment is declined as fraud ("Payment declined ... for security reasons"). Hours must pass before the same e-mail counts as established.
- Consequence for the suite: the first-time story goes through PayBroker's hosted 3-D Secure page; the plain-approval story back-dates that customer's first order in the run's throwaway database (`harness/helpers/shopfront.ts`); checkout speed is simulated with Playwright's clock (no sleeps).
- Worth a look: a legitimate guest checkout that is merely quick is declined outright at score 836.

### F-SF-3  Duplicate element id `co-error` on the checkout page
- Both the form error and the card error use `id="co-error"` (`checkout.js` and `card.js`). Not flagged by the current axe rules, but `aria-describedby`/label lookups by id are ambiguous.

### F-SF-4  Accessibility (axe)
- serious `color-contrast`: status badges (`Paid`, risk band) in light mode on the order page and the back-office order window. Listed as known on those screens.

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

## llm-gateway (a11y, none functional)

All found by axe on real Chrome, Playwright suite `tests/llmgw.spec.ts`; the specs list the rule ids in `knownA11y` so the run is green and the findings stay visible.

| # | Impact | Rule | Where | Steps / actual | Expected |
|---|---|---|---|---|---|
| 1 | serious | color-contrast | `.badge.ok` ("enabled"/"active") on Models and a client's detail, light theme | Open #models after setup; the green badge text is below 4.5:1 | AA contrast. Probably the kit badge colour (shared with identity). Evidence: `screenshots/llmgw/models-light-desktop.png` |
| 2 | serious | scrollable-region-focusable | Overview, phone width (390), the clients table card `#view-overview > .flush.card` | Resize to 390 wide after traffic exists; the card scrolls horizontally but cannot take keyboard focus | `tabindex="0"` plus an accessible name on the scroll container. Evidence: `screenshots/llmgw/overview-light-phone.png` |
| 3 | moderate/minor | heading-order, empty-table-header | h3 right after h1; actions column header is empty | not failing the suite |  |

## identity (a11y, none functional)

Found by axe in `tests/identity.spec.ts`; rule ids are in `knownA11y` so the run stays green.

| # | Impact | Rule | Where | Steps / actual | Expected |
|---|---|---|---|---|---|
| 1 | critical | select-name | Admin > Groups, the per-row "app" `<select>` of "Grant a role to the whole group" | Create a group; the select in its row has no label or aria-label (the role input has a placeholder only) | An accessible name such as `aria-label="App for group finance"`. Evidence: `screenshots/identity/groups-light-desktop.png` |
| 2 | serious | color-contrast | `.badge` ("active") on Apps, light theme | Register an app and open Apps | AA contrast (same kit badge as llm-gateway) |
| 3 | serious | scrollable-region-focusable | Overview at phone width: a `<pre>` code block scrolls but is not keyboard focusable | Open /admin at 390 wide | `tabindex="0"` on the scrollable `<pre>`. Evidence: `screenshots/identity/overview-light-phone.png` |
| 4 | moderate/minor | heading-order, empty-table-header | h3 after h1; empty action column headers | not failing the suite |  |

