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
