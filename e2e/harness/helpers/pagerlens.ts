import { Page, expect } from '@playwright/test';
import type { AppInstance } from '../fleet';

/** API sign-in for PagerLens: state-changing calls (login included) need the app's CSRF header. */
export async function loginViaApi(page: Page, app: AppInstance) {
  const r = await page.context().request.post(app.url + '/api/login', {
    data: { password: app.adminPassword },
    headers: { 'X-PagerLens-CSRF': '1' },
  });
  expect(r.ok(), 'sign in to pagerlens').toBeTruthy();
}
