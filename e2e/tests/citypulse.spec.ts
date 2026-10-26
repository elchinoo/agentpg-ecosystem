import { test, expect } from '../harness/fixtures';
import { kitSetupViaUi } from '../harness/ui';
import { loginViaApi as kitLoginViaApi } from '../harness/helpers/citypulse';
import type { AppInstance } from '../harness/fleet';

// Known, documented in findings/citypulse.md: horizontally scrollable table wrappers are not keyboard focusable.
const SCROLL = ['scrollable-region-focusable'];

test.describe.configure({ mode: 'serial' });

let cp: AppInstance;
let sim: AppInstance;
let city = '';
let ingestToken = '';

test.beforeAll(async ({ fleet }) => {
  cp = await fleet.get('citypulse');
  sim = await fleet.get('cpsim');
});

test('an administrator sets CityPulse up and creates an ingest token', async ({ page, snap }) => {
  await page.goto(cp.url + '/');
  await expect(page.getByRole('heading', { name: 'Set up CityPulse' })).toBeVisible();
  await snap(page, 'citypulse', 'setup', { phone: true });
  await kitSetupViaUi(page, cp);
  await expect(page.locator('#nav')).toBeVisible();
  await page.goto(cp.url + '/#settings');
  await page.locator('#tk-name').fill('e2e simulator');
  await page.locator('#tk-scope').selectOption('ingest');
  await page.getByRole('button', { name: 'Create token' }).click();
  const code = page.locator('.codewrap code').first();
  await expect(code).toBeVisible();
  ingestToken = (await code.textContent())!.trim();
  expect(ingestToken.length).toBeGreaterThan(20);
});

test('the simulator is set up and holds the ingest credential', async ({ page, snap }) => {
  await page.goto(sim.url + '/');
  await expect(page.getByRole('heading', { name: 'Set up CityPulse Simulator' })).toBeVisible();
  await kitSetupViaUi(page, sim);
  await expect(page.locator('#nav')).toBeVisible();
  await page.goto(sim.url + '/#settings');
  const field = page.getByLabel('New value for CITYPULSE_INGEST_TOKEN');
  await expect(field).toBeEnabled();
  await field.fill(ingestToken);
  await field.locator('xpath=ancestor::tr').getByRole('button', { name: 'Save' }).click();
  await expect(page.locator('#toast')).toContainText('Saved');
  await snap(page, 'cpsim', 'settings', { knownA11y: ['color-contrast'] });
});

test('a generated city streams into CityPulse and fills the heatmap', async ({ page, snap }) => {
  await kitLoginViaApi(page, sim);
  await kitLoginViaApi(page, cp);
  await page.goto(sim.url + '/#cities');
  await page.locator('#gen-size').selectOption('small');
  await page.getByRole('button', { name: 'Generate demo cities' }).click();
  const first = page.locator('#gen-summary tbody tr').first().locator('td').first();
  await expect(first).toBeVisible();
  city = (await first.textContent())!.trim();
  expect(city).toMatch(/^[a-z0-9][a-z0-9-]*$/);
  await snap(page, 'cpsim', 'cities-generated', { knownA11y: SCROLL });

  await page.goto(`${sim.url}/#cities/${city}`);
  await expect(page.getByRole('heading', { name: city, level: 1 })).toBeVisible();
  await page.locator('#c-url').fill(cp.url);
  await page.getByRole('button', { name: 'Save settings' }).click();
  await expect(page.locator('#c-url')).toHaveValue(cp.url);
  await page.getByRole('button', { name: 'Start city' }).click();
  await expect(page.getByText('Running: changes are locked until you stop it.')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Delivery to CityPulse' })).toBeVisible();
  // readings are really delivered
  await expect(page.locator('dt', { hasText: 'Readings sent' }).locator('xpath=following-sibling::dd[1]')).not.toHaveText('0', { timeout: 60_000 });
  await snap(page, 'cpsim', 'city-running', { phone: true, knownA11y: SCROLL });

  await page.goto(cp.url + '/#overview');
  const cells = page.locator('.heat button[aria-label*="healthy"]');
  await expect(cells.first()).toBeVisible({ timeout: 60_000 });
  expect(await cells.count()).toBeGreaterThan(3);
  await expect(page.locator('.status-banner')).toBeVisible();
  await snap(page, 'citypulse', 'overview', { phone: true, knownA11y: SCROLL });
});

test('pressing BURST changes the simulated city status', async ({ page, snap }) => {
  await kitLoginViaApi(page, sim);
  await kitLoginViaApi(page, cp);
  await page.goto(`${sim.url}/#cities/${city}`);
  const boost = page.locator('dt', { hasText: 'City-wide boost' }).locator('xpath=following-sibling::dd[1]');
  await expect(boost).toHaveText('off');
  await page.getByRole('button', { name: 'BURST' }).click();
  await expect(page.locator('#toast')).toContainText('Burst started');
  await expect(boost).toContainText('s left', { timeout: 15_000 });
  await snap(page, 'cpsim', 'city-burst', { knownA11y: SCROLL });
  await page.goto(cp.url + '/#overview');
  await expect(page.locator('.status-banner')).toBeVisible();
});
