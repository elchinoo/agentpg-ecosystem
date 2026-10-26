import { defineConfig } from '@playwright/test';

/**
 * Real Chrome (the machine's installed Google Chrome, no browser download) against LOCAL apps on 127.0.0.1.
 * One worker: specs start the apps they need from harness/apps, on free ports, with throwaway databases.
 */
export default defineConfig({
  testDir: './tests',
  globalSetup: './harness/global-setup.ts',
  workers: 1,
  fullyParallel: false,
  retries: 0,
  timeout: 90_000,
  expect: { timeout: 10_000 },
  reporter: [['list'], ['html', { open: 'never' }], ['json', { outputFile: 'reports/results.json' }]],
  use: {
    channel: 'chrome',
    headless: !process.env.HEADED,
    viewport: { width: 1440, height: 900 },
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'off',
    actionTimeout: 15_000,
    navigationTimeout: 30_000,
  },
});
