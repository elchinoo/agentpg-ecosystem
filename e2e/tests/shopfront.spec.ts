import { test, expect } from '../harness/fixtures';
import { adminClient } from '../harness/helpers/kitadmin';
import type { AppInstance } from '../harness/fleet';
import type { Page } from '@playwright/test';
import { backdateCustomer } from '../harness/helpers/shopfront';

const SHOPPER = 'pat.shopper@example.com';

/**
 * The cross-app story: a customer buys from ShopFront and pays through PayBroker (both LOCAL, both started by the harness).
 * Keys are typed only into the local shop.
 */
test.describe.configure({ mode: 'serial' });

// Real finding (FINDINGS.md): status badges (green/amber) fail WCAG AA contrast in the kit's CSS.
const BADGE = ['color-contrast'];

let shop: AppInstance;
let pb: AppInstance;

test.beforeAll(async ({ fleet }) => {
  shop = await fleet.get('shopfront');
  pb = await fleet.get('paybroker');
});

async function shopAdminLogin(page: Page) {
  const r = await page.context().request.post(shop.url + '/api/login', { data: { password: shop.adminPassword }, headers: { 'X-ShopFront-CSRF': '1' } });
  expect(r.ok()).toBeTruthy();
}

async function buy(page: Page, card: string, opts: { query?: string } = {}) {
  // A person takes a while at checkout; automation does not, and PayBroker (rightly) flags FAST_CHECKOUT.
  // Move the page's clock (performance.now) forward instead of sleeping.
  await page.clock.install();
  await page.goto(shop.url + '/');
  await page.locator('#search-q').fill(opts.query ?? 'coffee');
  await page.getByRole('button', { name: 'Search' }).click();
  await expect(page.getByRole('heading', { name: /Results for/ })).toBeVisible();
  await page.locator('#results a').first().click();
  await page.getByRole('button', { name: 'Add to cart' }).click();
  await page.locator('#cart-link').click();
  await expect(page.getByRole('heading', { name: /cart/i }).first()).toBeVisible();
  await page.getByRole('link', { name: /checkout/i }).first().click();
  await expect(page.getByRole('heading', { name: 'Checkout' })).toBeVisible();
  await page.locator('#co-email').fill(SHOPPER);
  await page.locator('#co-name').fill('Pat Shopper');
  await page.locator('#co-line1').fill('12 Test Street');
  await page.locator('#co-city').fill('Springfield');
  await page.locator('#co-region').fill('IL');
  await page.locator('#co-postal').fill('62701');
  await page.locator('#co-number').fill(card);
  await page.locator('#co-expiry').fill('12 / 35');
  await page.locator('#co-cvc').fill('123');
  await page.clock.fastForward(45_000);
}

test('the shop is set up and connected to PayBroker', async ({ page, snap }) => {
  // PayBroker side: merchant for this shop (origin + webhook), keys. Prepared through PayBroker's own admin API.
  const call = await adminClient(pb, 'X-PayBroker-CSRF');
  const m = await call('POST', '/api/merchants', { name: 'ShopFront e2e', allowed_origins: [shop.url] }, 201);
  const wh = await call('PUT', `/api/merchants/${m.id}/webhook`, { url: `${shop.url}/webhooks/paybroker` });
  const sk = (await call('POST', `/api/merchants/${m.id}/keys`, { kind: 'secret', name: 'shop server' }, 201)).key;
  const pk = (await call('POST', `/api/merchants/${m.id}/keys`, { kind: 'publishable', name: 'shop browser' }, 201)).key;

  // ShopFront side, in the browser.
  await page.goto(shop.url + '/admin');
  await expect(page.getByRole('heading', { name: 'Set up ShopFront' })).toBeVisible();
  await snap(page, 'shopfront', 'admin-setup');
  await page.locator('#setup-code').fill(shop.extra.setupCode);
  await page.locator('#setup-pw').fill(shop.adminPassword);
  await page.locator('#setup-pw2').fill(shop.adminPassword);
  await page.getByRole('button', { name: 'Create admin password' }).click();
  shop.extra.setupDone = true;
  await page.goto(shop.url + '/admin#settings');
  await page.locator('#pb-url').fill(pb.url);
  await page.locator('#pb-pk').fill(pk);
  await page.locator('#pb-sk').fill(sk);
  await page.locator('#pb-wh').fill(wh.signing_secret);
  await page.getByRole('button', { name: 'Save connection' }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Saved' })).toBeVisible();
  await page.getByRole('button', { name: 'Test connection' }).click();
  await expect(page.locator('#pb-test-result').getByRole('heading', { name: 'Connection works' })).toBeVisible();
  await snap(page, 'shopfront', 'admin-settings', { knownA11y: BADGE });
});

test('a first-time customer is sent through PayBroker\'s 3-D Secure page and comes back paid', async ({ page, snap }) => {
  // PayBroker treats a customer with no history as a new account (guest first order: score ~600 = challenge).
  await buy(page, '4000 0025 0000 3155');
  await snap(page, 'shopfront', 'checkout', { phone: true });
  await page.locator('#co-pay').click();
  await expect(page).toHaveURL(new RegExp(pb.url.replace(/[.]/g, '\\.') + '/3ds/'));
  await expect(page.getByRole('heading', { name: 'Verify this payment' })).toBeVisible();
  await snap(page, 'shopfront', 'three-d-secure', { knownA11y: BADGE });
  await page.getByRole('button', { name: /Approve/ }).click();
  await expect(page).toHaveURL(/\/order\//);
  await expect(page.locator('#order-badge')).toHaveText(/paid/i);
  await snap(page, 'shopfront', 'order-paid', { phone: true, knownA11y: BADGE });
});

test('a returning customer pays with a test card and the order is paid at once', async ({ page }) => {
  backdateCustomer(SHOPPER, 30);
  await buy(page, '4242 4242 4242 4242', { query: 'notebook' });
  await page.locator('#co-pay').click();
  await expect(page).toHaveURL(/\/order\//);
  await expect(page.locator('#order-badge')).toHaveText(/paid/i);
});

test('back office: the order shows the PayBroker payment trail', async ({ page, snap }) => {
  await shopAdminLogin(page);
  await page.goto(shop.url + '/admin#orders');
  await page.locator('#view-orders tbody tr').first().click();
  const dlg = page.locator('dialog[open]');
  await expect(dlg.getByRole('heading', { name: 'Payment trail (from PayBroker)' })).toBeVisible();
  await expect(dlg.locator('#od-payment')).toContainText(/pay_[a-z0-9]+/);
  await expect(dlg.locator('#od-payment')).toContainText('Fraud assessment');
  await expect(dlg.locator('#od-events')).toContainText('paid');
  await snap(page, 'shopfront', 'admin-order', { knownA11y: BADGE });
});

// REAL BUG (FINDINGS.md, "confirm dialog inside a modal dialog is dead"): the kit's confirmDialog is a popover appended to
// <body>; while the order <dialog> is open modally, Chrome treats everything outside it as inert, so neither a mouse click nor Enter
// reaches "Refund payment". The intended behaviour is asserted here; the test is expected to fail until the kit is fixed.
test('back office: a refund can be confirmed in the order dialog', async ({ page }) => {
  test.fail(true, 'confirmDialog popover is inert while the order <dialog> is modal (kit bug, see FINDINGS.md)');
  await shopAdminLogin(page);
  await page.goto(shop.url + '/admin#orders');
  await page.locator('#view-orders tbody tr').first().click();
  const dlg = page.locator('dialog[open]');
  await dlg.locator('#od-refund-btn').click();
  const box = (await page.getByRole('button', { name: 'Refund payment' }).boundingBox())!;
  await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
  await expect(dlg.locator('h2 .badge')).toHaveText(/refunded/i, { timeout: 3000 });
});

test('back office: the refund goes through to PayBroker (via the admin API, since the UI confirm is blocked)', async ({ page }) => {
  await shopAdminLogin(page);
  await page.goto(shop.url + '/admin#orders');
  await page.locator('#view-orders tbody tr').first().click();
  const dlg = page.locator('dialog[open]');
  const number = (await dlg.locator('h2').first().innerText()).match(/SF-\d+/)![0];
  const r = await page.context().request.post(`${shop.url}/api/orders/${number}/refund`, { data: {}, headers: { 'X-ShopFront-CSRF': '1' } });
  expect(r.ok()).toBeTruthy();
  await dlg.locator('#od-refresh').click();
  await expect(dlg.locator('h2 .badge')).toHaveText(/refunded/i);
  await expect(dlg.locator('#od-payment')).toContainText('Refunded');
});

test('a declined card shows a friendly message and keeps the order for another try', async ({ page, snap }) => {
  await buy(page, '4000 0000 0000 0002');
  await page.locator('#co-pay').click();
  const out = page.locator('#co-outcome');
  await expect(out).toContainText(/Payment (declined|not completed)/);
  await expect(out).toContainText(/Enter another card/);
  await expect(page).toHaveURL(/\/checkout/);
  await snap(page, 'shopfront', 'declined', { knownA11y: BADGE });
});
