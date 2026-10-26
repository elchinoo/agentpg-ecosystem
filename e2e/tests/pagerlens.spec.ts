import { test, expect } from '../harness/fixtures';
import { kitSetupViaUi } from '../harness/ui';
import { loginViaApi as kitLoginViaApi } from '../harness/helpers/pagerlens';
import type { AppInstance } from '../harness/fleet';

test.describe.configure({ mode: 'serial' });

let pl: AppInstance;
test.beforeAll(async ({ fleet }) => {
  pl = await fleet.get('pagerlens');
});

test('an administrator sets PagerLens up and sees the seeded fleet', async ({ page, snap }) => {
  await page.goto(pl.url + '/');
  await expect(page.getByRole('heading', { name: 'Set up PagerLens' })).toBeVisible();
  await snap(page, 'pagerlens', 'setup', { phone: true });
  await kitSetupViaUi(page, pl);
  await expect(page.locator('#view-dashboard')).toContainText('checkout-api');
  await snap(page, 'pagerlens', 'dashboard', { phone: true });
});

test('break a scenario in the Lab, investigate with Rules, read hypotheses and evidence, mark resolved', async ({ page, snap }) => {
  await kitLoginViaApi(page, pl);
  await page.goto(pl.url + '/#lab');
  await expect(page.getByRole('heading', { name: 'Lab', level: 1 })).toBeVisible();
  await snap(page, 'pagerlens', 'lab');
  const card = page.locator('article', { hasText: 'Bad release breaks checkout' });
  await card.getByRole('button', { name: 'Break it' }).click();
  await page.getByRole('button', { name: 'Continue' }).click();
  await expect(page).toHaveURL(/#incidents\/\d+/);

  await page.getByLabel('Method').selectOption('rules');
  await page.getByRole('button', { name: 'Investigate', exact: true }).click();
  const top = page.locator('section', { has: page.getByRole('heading', { name: 'Hypotheses' }) }).locator('h3').first();
  await expect(top).toContainText('1.', { timeout: 30_000 });
  await expect(top).toContainText(/Release .* introduced the failure/);
  const evidence = page.getByRole('button', { name: /^evidence #\d+/ }).first();
  await expect(evidence).toBeVisible();
  await evidence.click();
  await expect(page.getByRole('button', { name: 'Close' })).toBeVisible();
  await snap(page, 'pagerlens', 'investigation', { phone: true });

  await page.getByLabel('Real cause').selectOption('bad_deploy');
  await page.getByRole('button', { name: 'Mark resolved' }).click();
  await expect(page.getByText('Resolved', { exact: false }).first()).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Learning check' })).toBeVisible();
});

test('the retire-service confirmation keeps what was typed through the 10 s refresh', async ({ page, snap }) => {
  await page.clock.install();
  await kitLoginViaApi(page, pl);
  await page.goto(pl.url + '/#service/search-service');
  await page.locator('#retire-details summary').click();
  const typed = page.locator('#retire-confirm');
  const retire = page.getByRole('button', { name: 'Retire service', exact: true });
  await typed.fill('search-serv');
  await page.clock.runFor(12_000); // the page redraws its content every 10 s
  await expect(typed).toHaveValue('search-serv');
  await expect(retire).toBeDisabled();
  await typed.fill('search-service');
  await expect(retire).toBeEnabled();
  await snap(page, 'pagerlens', 'retire-service');
  await retire.click();
  await expect(page).toHaveURL(/#dashboard/);
});
