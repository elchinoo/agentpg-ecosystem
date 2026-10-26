import { Page, expect } from '@playwright/test';
import type { AppInstance } from '../fleet';

/** API sign-in for apps whose login needs the CSRF-style header (name is in <html data-csrf>). */
export async function loginWithCsrf(page: Page, app: AppInstance, header: string) {
  const r = await page.context().request.post(app.url + '/api/login', {
    data: { password: app.adminPassword },
    headers: { [header]: '1' },
  });
  expect(r.ok(), `sign in to ${app.name}`).toBeTruthy();
}
