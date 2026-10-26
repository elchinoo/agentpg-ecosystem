import { test as base, expect, BrowserContext, Page, TestInfo } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import * as fs from 'node:fs';
import * as path from 'node:path';
import { Fleet } from './fleet';

export { expect };

const ROOT = path.resolve(__dirname, '..');
export const SHOT_DIR = path.join(ROOT, 'screenshots');
const A11Y_LOG = path.join(ROOT, 'reports', 'a11y.jsonl');

/** Problems that never count (browser noise, not app behaviour). */
const DEFAULT_ALLOW: RegExp[] = [/net::ERR_ABORTED/];

export type Theme = 'light' | 'dark';

export interface A11yFinding {
  app: string;
  screen: string;
  theme: Theme;
  viewport: 'desktop' | 'phone';
  id: string;
  impact: string;
  help: string;
  nodes: number;
  targets: string[];
}

/** Console errors, page errors and failed requests seen by a page; filtered through the allowlist. */
function attachGuard(context: BrowserContext, allow: RegExp[]) {
  const seen: string[] = [];
  const keep = (s: string) => !allow.some((r) => r.test(s)) && !DEFAULT_ALLOW.some((r) => r.test(s));
  const watch = (page: Page) => {
    page.on('console', (m) => {
      if (m.type() === 'error') {
        const s = `console.error: ${m.text()} (${m.location().url})`;
        if (keep(s)) seen.push(s);
      }
    });
    page.on('pageerror', (e) => {
      const s = `pageerror: ${e.message}`;
      if (keep(s)) seen.push(s);
    });
    page.on('response', (r) => {
      if (r.status() >= 400) {
        const s = `HTTP ${r.status()} ${r.request().method()} ${r.url()}`;
        if (keep(s)) seen.push(s);
      }
    });
    page.on('requestfailed', (r) => {
      const s = `request failed: ${r.method()} ${r.url()} ${r.failure()?.errorText}`;
      if (keep(s)) seen.push(s);
    });
  };
  context.on('page', watch);
  return seen;
}

async function setTheme(page: Page, theme: Theme) {
  await page.emulateMedia({ colorScheme: theme });
  // Apps that keep a manual choice in storage/attribute: follow the emulated scheme.
  await page.evaluate((t) => {
    const el = document.documentElement;
    if (el.hasAttribute('data-theme')) el.setAttribute('data-theme', t);
  }, theme);
}

export interface ShotOptions {
  /** Also take the phone (390x844) pair. Use for the 2 or 3 key screens of an app. */
  phone?: boolean;
  /** Axe rule ids known to fail on this screen (documented in FINDINGS.md): reported, not failing. */
  knownA11y?: string[];
  /** Skip the accessibility audit (screenshot only). */
  noAudit?: boolean;
  /** CSS selectors masked/hidden before capture (volatile content). */
  hide?: string[];
}

export interface Fixtures {
  /** Extra allowlist (regexes against "console.error: ...", "HTTP 404 GET url", "request failed: ...") for this spec. */
  allow: RegExp[];
  fleet: Fleet;
  /** Screenshot (light and dark, desktop; phone optional) + accessibility audit of the page as it is now. */
  snap: (page: Page, app: string, name: string, opts?: ShotOptions) => Promise<void>;
}

export const test = base.extend<Fixtures, { _fleet: Fleet }>({
  allow: [[], { option: true }],
  _fleet: [
    async ({}, use) => {
      const f = new Fleet();
      await use(f);
      await f.stopAll();
    },
    { scope: 'worker' },
  ],
  fleet: async ({ _fleet }, use) => use(_fleet),
  context: async ({ context, allow }, use, testInfo) => {
    const seen = attachGuard(context, allow);
    await use(context);
    if (testInfo.status === 'passed' || testInfo.status === 'failed') {
      expect.soft(seen, 'browser console errors / failed requests').toEqual([]);
    }
  },
  snap: async ({}, use, testInfo) => {
    await use(async (page, app, name, opts = {}) => {
      const dir = path.join(SHOT_DIR, app);
      fs.mkdirSync(dir, { recursive: true });
      const views: { vp: 'desktop' | 'phone'; size: { width: number; height: number } }[] = [{ vp: 'desktop', size: { width: 1440, height: 900 } }];
      if (opts.phone) views.push({ vp: 'phone', size: { width: 390, height: 844 } });
      const original = page.viewportSize() ?? { width: 1440, height: 900 };
      const findings: A11yFinding[] = [];
      for (const { vp, size } of views) {
        await page.setViewportSize(size);
        for (const theme of ['light', 'dark'] as Theme[]) {
          await setTheme(page, theme);
          await page.waitForTimeout(150); // let CSS transitions settle; screenshots only, never assertions
          const style = opts.hide?.length ? await page.addStyleTag({ content: `${opts.hide.join(',')}{visibility:hidden !important}` }) : undefined;
          await page.screenshot({ path: path.join(dir, `${name}-${theme}-${vp}.png`), fullPage: true, animations: 'disabled' });
          await style?.evaluate((n) => n.remove());
          if (!opts.noAudit) findings.push(...(await audit(page, app, name, theme, vp)));
        }
      }
      await setTheme(page, 'light');
      await page.setViewportSize(original);
      const known = new Set(opts.knownA11y ?? []);
      const bad = findings.filter((f) => (f.impact === 'serious' || f.impact === 'critical') && !known.has(f.id));
      expect.soft(
        bad.map((f) => `${f.impact} ${f.id} [${f.theme}/${f.viewport}] x${f.nodes}: ${f.help} -> ${f.targets.join(' | ')}`),
        `accessibility (serious/critical) on ${app}/${name}`,
      ).toEqual([]);
    });
  },
});

async function audit(page: Page, app: string, screen: string, theme: Theme, viewport: 'desktop' | 'phone'): Promise<A11yFinding[]> {
  const res = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'best-practice']).analyze();
  const out: A11yFinding[] = res.violations.map((v) => ({
    app,
    screen,
    theme,
    viewport,
    id: v.id,
    impact: v.impact ?? 'minor',
    help: v.help,
    nodes: v.nodes.length,
    targets: v.nodes.slice(0, 3).map((n) => n.target.join(' ')),
  }));
  fs.mkdirSync(path.dirname(A11Y_LOG), { recursive: true });
  for (const f of out) fs.appendFileSync(A11Y_LOG, JSON.stringify(f) + '\n');
  return out;
}

/** Wait the given time in the page's clock without sleeping the test (used for the "survives N seconds of timers" stories). */
export async function throughTimers(page: Page, ms: number) {
  await page.clock.runFor(ms);
}

export type { TestInfo };
