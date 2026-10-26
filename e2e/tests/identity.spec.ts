import { test, expect } from '../harness/fixtures';
import { kitSetupViaUi } from '../harness/ui';
import { loginWithCsrf } from '../harness/helpers/login';
import type { AppInstance } from '../harness/fleet';

test.describe.configure({ mode: 'serial' });

let idp: AppInstance;
test.beforeAll(async ({ fleet }) => {
  idp = await fleet.get('identity');
});

const signIn = (page: import('@playwright/test').Page) => loginWithCsrf(page, idp, 'X-Identity-CSRF');

test('an administrator sets the identity provider up', async ({ page, snap }) => {
  await page.goto(idp.url + '/admin');
  await expect(page.getByRole('heading', { name: 'Set up Identity' })).toBeVisible();
  await snap(page, 'identity', 'setup');
  await page.locator('#setup-code').fill(idp.extra.setupCode);
  await page.locator('#setup-pw').fill(idp.adminPassword);
  await page.locator('#setup-pw2').fill(idp.adminPassword);
  await page.getByRole('button', { name: 'Create admin password' }).click();
  await expect(page.getByRole('heading', { name: 'Overview', level: 1 })).toBeVisible();
  await snap(page, 'identity', 'overview', { phone: true, knownA11y: ['scrollable-region-focusable'] });
});

test('they register an app that defines a role', async ({ page, snap }) => {
  await signIn(page);
  await page.goto(idp.url + '/admin#apps');
  await expect(page.getByRole('heading', { name: 'Apps', level: 1 })).toBeVisible();
  await page.locator('input[name=client_id]').fill('demoapp');
  await page.locator('input[name=name]').fill('Demo App');
  await page.locator('select[name=kind]').selectOption('web');
  await page.locator('input[name=url]').fill('http://127.0.0.1:9999');
  await page.locator('input[name=redirects]').fill('http://127.0.0.1:9999/auth/callback');
  await page.locator('input[name=roles]').fill('admin, approver');
  await page.getByRole('button', { name: 'Register' }).click();
  await expect(page.getByRole('heading', { name: 'Secret for demoapp' })).toBeVisible();
  await page.getByRole('button', { name: 'Done' }).click();
  await expect(page.getByRole('cell', { name: /Demo App/ })).toBeVisible();
  await snap(page, 'identity', 'apps', { knownA11y: ['color-contrast'] });
});

test('they create a group, a user in it, and grant the user a role', async ({ page, snap }) => {
  await signIn(page);
  await page.goto(idp.url + '/admin#groups');
  await page.getByLabel('New group').fill('finance');
  await page.getByRole('button', { name: 'Create' }).click();
  await expect(page.getByRole('cell', { name: 'finance', exact: true })).toBeVisible();
  await snap(page, 'identity', 'groups', { knownA11y: ['color-contrast', 'select-name', 'label'] });

  await page.goto(idp.url + '/admin#users');
  await page.locator('input[name=username]').fill('alice');
  await page.locator('input[name=email]').fill('alice@example.test');
  await page.locator('input[name=name]').fill('Alice Example');
  await page.locator('input[name=groups]').fill('finance');
  await page.getByRole('button', { name: 'Add', exact: true }).click();
  const row = page.getByRole('row', { name: /alice/ });
  await expect(row).toContainText('finance');
  await row.getByRole('button', { name: 'Roles' }).click();
  await expect(page.getByRole('heading', { name: 'alice', level: 1 })).toBeVisible();
  await page.getByPlaceholder('role name, e.g. approver').fill('approver');
  await page.getByRole('button', { name: 'Grant', exact: true }).click();
  await expect(page.getByRole('row', { name: /demoapp\s+approver/ })).toBeVisible();
  await snap(page, 'identity', 'user-roles', { knownA11y: ['color-contrast'] });

  // a role for the whole group
  await page.goto(idp.url + '/admin#groups');
  const g = page.getByRole('row', { name: /finance/ });
  await g.getByPlaceholder('role').fill('admin');
  await g.getByRole('button', { name: 'Grant' }).click();
  await expect(page.locator('#toast')).toContainText('Granted');
});

test('OIDC discovery and the signing keys are reachable', async ({ request }) => {
  const d = await request.get(idp.url + '/.well-known/openid-configuration');
  expect(d.ok()).toBeTruthy();
  const doc = await d.json();
  expect(doc.issuer).toBe(idp.url);
  for (const k of ['authorization_endpoint', 'token_endpoint', 'jwks_uri']) expect(doc[k], k).toContain(idp.url);
  const j = await request.get(doc.jwks_uri);
  expect(j.ok()).toBeTruthy();
  const jwks = await j.json();
  expect(jwks.keys.length).toBeGreaterThan(0);
  expect(jwks.keys[0].kty).toBe('RSA');
});
