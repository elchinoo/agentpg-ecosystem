import { Page, expect } from '@playwright/test';
import type { AppInstance } from './fleet';

/**
 * First-run setup and sign-in of the shared AgentPG kit UI (TrustDesk, PayBroker, PagerLens, ... all use the same card):
 * #setup-code, #setup-pw, #setup-pw2 and #login-pw.
 */
export async function kitSetupViaUi(page: Page, app: AppInstance) {
  await page.goto(app.url + '/');
  await page.locator('#setup-code').fill(app.extra.setupCode);
  await page.locator('#setup-pw').fill(app.adminPassword);
  await page.locator('#setup-pw2').fill(app.adminPassword);
  await page.getByRole('button', { name: 'Create admin password' }).click();
  app.extra.setupDone = true;
}

export async function kitLoginViaUi(page: Page, app: AppInstance) {
  await page.goto(app.url + '/');
  await page.locator('#login-pw').fill(app.adminPassword);
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
}

/** Sign in without a UI round trip (cookie lands in the page's browser context). Sends the app's CSRF header. */
export async function kitLoginViaApi(page: Page, app: AppInstance, endpoint = '/api/login') {
  const html = await (await page.context().request.get(app.url + '/')).text();
  const name = html.match(/data-csrf="([^"]+)"/)?.[1];
  const r = await page.context().request.post(app.url + endpoint, {
    data: { password: app.adminPassword },
    headers: name ? { [name]: '1' } : {},
  });
  expect(r.ok(), `sign in to ${app.name}`).toBeTruthy();
}

/** The kit's CSRF-style header for state-changing browser calls (read from <html data-csrf>). */
export async function csrfHeader(page: Page): Promise<Record<string, string>> {
  const name = await page.evaluate(() => document.documentElement.getAttribute('data-csrf'));
  return name ? { [name]: '1' } : {};
}
