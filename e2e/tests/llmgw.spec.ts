import { test, expect } from '../harness/fixtures';
import { kitSetupViaUi } from '../harness/ui';
import { loginWithCsrf } from '../harness/helpers/login';
import type { AppInstance } from '../harness/fleet';

test.describe.configure({ mode: 'serial' });

let gw: AppInstance;
let clientKey = '';
test.beforeAll(async ({ fleet }) => {
  gw = await fleet.get('llmgw');
});

test('an administrator sets the gateway up', async ({ page, snap }) => {
  await page.goto(gw.url + '/');
  await expect(page.getByRole('heading', { name: 'Set up the LLM gateway' })).toBeVisible();
  await snap(page, 'llmgw', 'setup');
  await kitSetupViaUi(page, gw);
  await expect(page.getByRole('heading', { name: 'Overview', level: 1 })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'No traffic yet' })).toBeVisible();
  await snap(page, 'llmgw', 'overview-empty');
});

test('they add a model alias on the built-in echo provider', async ({ page, snap }) => {
  await loginWithCsrf(page, gw, 'X-LLMGW-CSRF');
  await page.goto(gw.url + '/#models');
  await expect(page.getByRole('heading', { name: 'Models', level: 1 })).toBeVisible();
  await page.getByRole('button', { name: 'New model alias' }).click();
  const dialog = page.locator('#form');
  await dialog.locator('#al-name').fill('e2e-chat');
  await dialog.locator('#al-desc').fill('Alias created by the e2e suite');
  await dialog.getByLabel('Provider for target 1').selectOption({ label: 'echo (built-in)' }).catch(async () => {
    await dialog.getByLabel('Provider for target 1').selectOption({ index: 0 });
  });
  await dialog.getByLabel('Upstream model for target 1').fill('echo');
  await dialog.getByRole('button', { name: 'Save' }).click();
  await expect(page.getByRole('cell', { name: 'e2e-chat' })).toBeVisible();
  await snap(page, 'llmgw', 'models', {});
});

test('they create a client and receive a key', async ({ page, snap }) => {
  await loginWithCsrf(page, gw, 'X-LLMGW-CSRF');
  await page.goto(gw.url + '/#clients');
  await page.getByRole('button', { name: 'New client' }).click();
  const dialog = page.locator('#form');
  await dialog.locator('#c-name').fill('e2e-app');
  await dialog.getByRole('button', { name: 'Create client and issue a key' }).click();
  const key = page.locator('#detail pre.code code').first();
  await expect(key).toHaveText(/^lg_/);
  clientKey = (await key.textContent())!.trim();
  await snap(page, 'llmgw', 'client-created', { hide: ['#detail pre.code code'] });
});

test('the app calls the gateway with its key and the usage shows on the dashboard', async ({ page, snap }) => {
  const res = await fetch(gw.url + '/v1/chat/completions', {
    method: 'POST',
    headers: { 'content-type': 'application/json', authorization: `Bearer ${clientKey}` },
    body: JSON.stringify({ model: 'e2e-chat', messages: [{ role: 'user', content: 'hello from the e2e suite' }] }),
  });
  expect(res.status).toBe(200);
  const body = await res.json();
  expect(body.model).toBe('e2e-chat');
  expect(body.choices[0].message.content).toBeTruthy();

  await loginWithCsrf(page, gw, 'X-LLMGW-CSRF');
  await page.goto(gw.url + '/#overview');
  await expect(async () => {
    await page.reload();
    await expect(page.getByRole('heading', { name: 'Top models' })).toBeVisible({ timeout: 2000 });
  }).toPass({ timeout: 30_000 });
  await expect(page.getByRole('cell', { name: 'e2e-app', exact: true })).toBeVisible();
  await expect(page.locator('.stats .stat').first().locator('strong')).toHaveText('1');
  await snap(page, 'llmgw', 'overview', { phone: true });
});
