import { test, expect } from '../harness/fixtures';
import { kitSetupViaUi } from '../harness/ui';
import { loginViaApi } from '../harness/helpers/kitadmin';
import { authorize, tokenize, SAFE_CUSTOMER, RISKY } from '../harness/helpers/paybroker';
import type { AppInstance } from '../harness/fleet';

// Real finding (see FINDINGS.md): the green/amber status badge text fails WCAG AA contrast.
const BADGE = ['color-contrast'];

test.describe.configure({ mode: 'serial' });

let pb: AppInstance;
let merchantId = '';
let sk = '';
let pk = '';
let okPaymentId = '';
let flaggedPaymentId = '';

test.beforeAll(async ({ fleet }) => {
  pb = await fleet.get('paybroker');
});

test('an administrator sets PayBroker up and signs in', async ({ page, snap }) => {
  await page.goto(pb.url + '/');
  await expect(page.getByRole('heading', { name: 'Set up PayBroker' })).toBeVisible();
  await snap(page, 'paybroker', 'setup', { phone: true });
  await kitSetupViaUi(page, pb);
  await expect(page.getByRole('heading', { name: 'Overview' })).toBeVisible();
  await snap(page, 'paybroker', 'overview-empty');
});

test('a merchant gets keys and allowed browser origins', async ({ page, snap }) => {
  await loginViaApi(page, pb, 'X-PayBroker-CSRF');
  await page.goto(pb.url + '/#merchants');
  await page.getByRole('button', { name: 'New merchant' }).click();
  await page.getByLabel('Name').fill('E2E Coffee Roasters');
  await page.getByRole('button', { name: 'Create', exact: true }).click();
  const detail = page.locator('#detail');
  await expect(detail.getByRole('heading', { name: 'E2E Coffee Roasters' })).toBeVisible();
  merchantId = (await detail.locator('p.muted code').first().innerText()).trim();

  // allowed origins form
  await detail.getByLabel('Allowed browser origins').fill('http://127.0.0.1:4555\nhttps://shop.example.test');
  await detail.getByRole('button', { name: 'Save origins' }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Allowed origins saved' })).toBeVisible();

  // keys (shown once)
  for (const kind of ['secret', 'publishable'] as const) {
    await detail.locator('#key-kind').selectOption(kind);
    await detail.locator('#key-name').fill(`e2e ${kind}`);
    await detail.getByRole('button', { name: 'Create key' }).click();
    const code = detail.locator('.codewrap code').last();
    await expect(code).toContainText(kind === 'secret' ? 'sk_test_' : 'pk_test_');
    const v = (await code.innerText()).trim();
    if (kind === 'secret') sk = v; else pk = v;
  }
  await detail.locator('.codewrap code').evaluateAll((els) => els.forEach((e) => (e.textContent = 'key shown once (masked)')));
  await snap(page, 'paybroker', 'merchant-keys', { knownA11y: BADGE });

  // the origins persist across a reload
  await page.reload();
  await page.locator('#view-merchants tbody tr', { hasText: 'E2E Coffee Roasters' }).click();
  await expect(page.locator('#detail').getByLabel('Allowed browser origins')).toHaveValue(/127\.0\.0\.1:4555\s+https:\/\/shop\.example\.test/);
});

test('a test payment is approved and shows up with the decision explained', async ({ page, snap }) => {
  const token = await tokenize(pb.url, pk, '4242424242424242');
  const r = await authorize(pb.url, sk, { idempotency_key: 'e2e-order-1001', amount: 4999, payment_method: { type: 'card', card_token: token }, customer: SAFE_CUSTOMER });
  expect(r.status).toBe(200);
  okPaymentId = r.json.payment_id;
  expect(r.json.fraud_assessment.decision).toBe('APPROVE');

  await loginViaApi(page, pb, 'X-PayBroker-CSRF');
  await page.goto(pb.url + '/#payments');
  await page.locator('#pay-q').fill(okPaymentId);
  const row = page.locator('#pay-body tr', { hasText: okPaymentId });
  await expect(row).toBeVisible();
  await row.getByRole('button', { name: okPaymentId }).click().catch(async () => row.click());
  const detail = page.locator('#detail');
  await expect(detail.getByRole('heading', { name: 'Reasons' }).or(detail.getByText('No risk signals worth reporting.'))).toBeVisible();
  await expect(detail).toContainText(/Fraud engine: \d+ \/ 1000/);
  await snap(page, 'paybroker', 'payment-detail', { phone: true, knownA11y: BADGE });
});

test('a risky payment is declined and opens a fraud case', async ({ page, snap }) => {
  const token = await tokenize(pb.url, pk, '5555555555554444');
  const r = await authorize(pb.url, sk, { idempotency_key: 'e2e-order-risky-1', amount: 450000, payment_method: { type: 'card', card_token: token }, ...RISKY });
  expect(r.status).toBe(403);
  flaggedPaymentId = r.json.error.payment_id;
  expect(r.json.error.fraud_assessment.reasons).toContain('NEW_ACCOUNT');

  await loginViaApi(page, pb, 'X-PayBroker-CSRF');
  await page.goto(pb.url + '/#cases');
  const card = page.locator('#case-list .case', { hasText: flaggedPaymentId });
  await expect(card).toBeVisible();
  await expect(card).toContainText('NEW_ACCOUNT');
  await snap(page, 'paybroker', 'cases', { knownA11y: BADGE });
  await card.getByRole('button', { name: flaggedPaymentId }).click();
  await expect(page.locator('#detail')).toContainText(/Decline/);
});

test('typing in the AI triage limits survives the refresh', async ({ page, snap }) => {
  await loginViaApi(page, pb, 'X-PayBroker-CSRF');
  await page.clock.install();
  await page.goto(pb.url + '/#settings');
  const cap = page.locator('#tr-cap');
  await expect(cap).toBeVisible();
  await cap.fill('1234');
  await page.clock.runFor(16_000); // any timer-driven refresh would fire now
  await page.getByRole('button', { name: 'Refresh usage' }).click();
  await expect(page.locator('#tr-usage')).toContainText('LLM calls in the last 24 hours');
  await expect(cap).toHaveValue('1234');
  await page.locator('form', { has: cap }).getByRole('button', { name: 'Save' }).click();
  await expect(page.locator('#tr-source')).toContainText('Calls per 24 hours: saved here');
  await snap(page, 'paybroker', 'settings', { knownA11y: BADGE });
});

// REAL BUG (FINDINGS.md, "confirm dialog inside a modal dialog is dead"): the kit's confirmDialog is a popover appended to
// <body>. While the merchant <dialog> is open modally Chrome treats everything outside it as inert, so neither the mouse nor Enter
// reaches "Revoke". The intended behaviour is asserted; the test is expected to fail until the kit is fixed.
test('revoking an API key can be confirmed inside the merchant window', async ({ page }) => {
  test.fail(true, 'confirmDialog popover is inert while the merchant <dialog> is modal (kit bug, see FINDINGS.md)');
  await loginViaApi(page, pb, 'X-PayBroker-CSRF');
  await page.goto(pb.url + '/#merchants');
  await page.locator('#view-merchants tbody tr', { hasText: 'E2E Coffee Roasters' }).click();
  const detail = page.locator('#detail');
  await detail.locator('#key-name').fill('to be revoked');
  await detail.getByRole('button', { name: 'Create key' }).click();
  await detail.locator('tbody').getByRole('button', { name: 'Revoke' }).last().click();
  const ok = page.locator('.confirm-box').getByRole('button', { name: 'Revoke' });
  const box = (await ok.boundingBox())!;
  await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
  await expect(detail.locator('tbody td', { hasText: 'revoked' })).toBeVisible({ timeout: 3000 });
});
