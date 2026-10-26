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
