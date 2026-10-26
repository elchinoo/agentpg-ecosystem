import { Page, expect } from '@playwright/test';
import type { AppInstance } from '../fleet';

/**
 * Sign in through the API. Browser-style writes need the app's CSRF header (named in <html data-csrf>), which the
 * shared kitLoginViaApi does not send, so this reads the name from the app's index page first.
 */
export async function loginViaApi(page: Page, app: AppInstance): Promise<void> {
  const html = await (await page.context().request.get(app.url + '/')).text();
  const name = html.match(/data-csrf="([^"]+)"/)?.[1];
  const r = await page.context().request.post(app.url + '/api/login', {
    data: { password: app.adminPassword },
    headers: name ? { [name]: '1' } : {},
  });
  expect(r.ok(), `sign in to ${app.name}`).toBeTruthy();
}
