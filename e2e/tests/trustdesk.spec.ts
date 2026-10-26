import { test, expect } from '../harness/fixtures';
import { kitSetupViaUi, kitLoginViaApi } from '../harness/ui';
import type { AppInstance } from '../harness/fleet';

test.describe.configure({ mode: 'serial' });

let td: AppInstance;
test.beforeAll(async ({ fleet }) => {
  td = await fleet.get('trustdesk');
});

const CSRF = { 'X-TrustDesk-CSRF': '1' };
const iso = (daysAhead: number) => new Date(Date.now() + daysAhead * 86_400_000).toISOString().slice(0, 10);

test('an administrator sets TrustDesk up and loads the demo policies', async ({ page, snap }) => {
  await page.goto(td.url + '/');
  await expect(page.getByRole('heading', { name: 'Set up TrustDesk' })).toBeVisible();
  await snap(page, 'trustdesk', 'setup', { phone: true });
  await kitSetupViaUi(page, td);
  await expect(page.getByRole('heading', { name: 'Requests', level: 1 })).toBeVisible();
  await page.goto(td.url + '/#documents');
  await page.getByRole('button', { name: 'Load demo policies' }).click();
  await expect(page.locator('#doc-body tr').first()).toBeVisible({ timeout: 60_000 });
  await snap(page, 'trustdesk', 'documents', { knownA11y: ['label-content-name-mismatch'] });
});

test('an employee request arrives through the client API and the approver follows the AI advice', async ({ page, snap, fleet }) => {
  await kitLoginViaApi(page, td);
  // A client-scope token, as an HR application would hold it (created over the browser session).
  const tok = await page.context().request.post(td.url + '/api/tokens', { data: { name: 'e2e hr app', scope: 'client' }, headers: CSRF });
  expect(tok.ok()).toBeTruthy();
  const clientToken: string = (await tok.json()).token;
  const auth = { authorization: `Bearer ${clientToken}`, 'content-type': 'application/json' };

  const emp = await fetch(td.url + '/api/v1/employees', {
    method: 'POST', headers: auth,
    body: JSON.stringify({ employee_no: 'E2E-1', name: 'Alex Example', email: 'alex.e2e@example.com', department: 'Sales', title: 'Account Executive', country: 'US', hire_date: '2022-03-18' }),
  });
  expect(emp.status).toBe(201);
  const req = await fetch(td.url + '/api/v1/requests', {
    method: 'POST', headers: { ...auth, 'Idempotency-Key': 'e2e-summer' },
    body: JSON.stringify({ employee_no: 'E2E-1', type: 'vacation', title: 'Summer break (e2e)', description: 'Five days at the coast', start_date: iso(60), end_date: iso(64) }),
  });
  expect(req.status).toBe(201);
  const ref: string = (await req.json()).ref;

  await page.goto(td.url + '/#requests');
  const row = page.locator('#req-body tr', { hasText: 'Summer break (e2e)' });
  // The background pre-validation finishes: the row shows the AI advice and "Needs decision".
  await expect(row).toBeVisible();
  await row.getByRole('button', { name: ref }).click();
  const review = page.locator('#review');
  await expect(review.getByRole('heading', { name: 'AI pre-validation' })).toBeVisible();
  await expect(review.getByText('Quote found word for word in the document')).toBeVisible({ timeout: 30_000 });
  await expect(review.locator('blockquote').first()).not.toBeEmpty();
  expect((await fleet.fakeLlm()).calls.some((c) => c.path.endsWith('/chat/completions'))).toBeTruthy();
  await snap(page, 'trustdesk', 'review-ai-advice', { phone: true, knownA11y: ['color-contrast'] });

  await review.getByRole('button', { name: 'Approve', exact: true }).click();
  // The client application sees the decision, but never the advice.
  const get = async () => (await fetch(td.url + `/api/v1/requests/${ref}`, { headers: auth })).json();
  await expect.poll(async () => (await get()).status).toBe('approved');
  expect((await get()).validation).toBeUndefined();
  await expect(review).toBeHidden();
  await page.locator('#req-status').selectOption('approved');
  await expect(page.locator('#req-body tr', { hasText: 'Summer break (e2e)' })).toBeVisible();
});

test('the validation setting a person is typing survives 15 s of page timers', async ({ page, snap }) => {
  await kitLoginViaApi(page, td);
  await page.clock.install();
  await page.goto(td.url + '/#settings');
  const rate = page.locator('#val-backlog');
  await expect(rate).toBeVisible();
  await rate.fill('7');
  await page.clock.runFor(16_000); // the counts poll fires (15 s); the form must not be rebuilt
  await expect(rate).toHaveValue('7');
  await expect(page.locator('#val-waiting')).toBeVisible();
  await page.getByRole('button', { name: 'Save', exact: true }).click();
  await expect(page.getByText('Saved.', { exact: true })).toBeVisible();
  await expect(rate).toHaveValue('7');
  await snap(page, 'trustdesk', 'settings', {});
});

test('deleting a provider account asks to type its name; cancel keeps it, confirm removes it', async ({ page, snap }) => {
  await kitLoginViaApi(page, td);
  const made = await page.context().request.post(td.url + '/api/accounts', {
    headers: CSRF,
    data: { name: 'Throwaway model', provider: 'custom', base_url: td.extra.llmUrl, api_key: 'fake-key-for-e2e', chat_model: 'fake-chat' },
  });
  expect(made.ok()).toBeTruthy();
  await page.goto(td.url + '/#settings');
  const card = page.locator('article.acct', { hasText: 'Throwaway model' });
  await expect(card).toBeVisible();

  await card.getByRole('button', { name: 'Delete' }).click();
  const dialog = page.getByRole('alertdialog', { name: 'Delete account' });
  const confirm = dialog.getByRole('button', { name: 'Delete', exact: true });
  await expect(confirm).toBeDisabled();
  await dialog.getByRole('textbox').fill('Throwaway');
  await expect(confirm).toBeDisabled();
  await snap(page, 'trustdesk', 'delete-account-dialog', {});
  await dialog.getByRole('button', { name: 'Cancel' }).click();
  await expect(dialog).toBeHidden();
  await expect(card).toBeVisible();

  await card.getByRole('button', { name: 'Delete' }).click();
  await dialog.getByRole('textbox').fill('Throwaway model');
  await expect(confirm).toBeEnabled();
  await confirm.click();
  await expect(card).toHaveCount(0);
});
