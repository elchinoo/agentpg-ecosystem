// Playwright stills (real Chrome, 1920x1080) of local runs: the notes example, the gateway console, the identity console.
import { createRequire } from 'node:module';
const require = createRequire(new URL('../../../agentpg-kit/docs/video/package.json', import.meta.url));
const { chromium } = require('playwright');

const E = process.env;
const browser = await chromium.launch({ channel: 'chrome' });

async function session(base, loginPath, body, headers = {}) {
  const ctx = await browser.newContext({ viewport: { width: 1920, height: 1080 }, colorScheme: 'dark' });
  const r = await ctx.request.post(base + loginPath, { data: body, headers });
  if (!r.ok()) throw new Error(`login failed ${base}${loginPath}: ${r.status()}`);
  return ctx;
}
async function shot(ctx, url, file, { wait = 1200, full = false, pre } = {}) {
  const page = await ctx.newPage();
  await page.goto(url);
  await page.waitForTimeout(wait);
  if (pre) await pre(page);
  await page.screenshot({ path: `${E.SHOTS}/${file}.png`, fullPage: full });
  console.log('shot', file);
  await page.close();
}

// notes: the setup screen first, then a session with one open and one private note
{
  const ctx = await browser.newContext({ viewport: { width: 1920, height: 1080 }, colorScheme: 'dark' });
  await shot(ctx, E.NOTES + '/', 'notes-setup');
  const H = { 'x-notes-csrf': '1' };
  let r = await ctx.request.post(E.NOTES + '/api/setup', { data: { code: E.SETUP, password: 'correct horse battery' }, headers: H });
  if (!r.ok()) throw new Error('notes setup ' + r.status());
  await ctx.request.post(E.NOTES + '/api/notes', { data: { title: 'Shopping', body: 'kettle, teapot', private: false }, headers: H });
  await ctx.request.post(E.NOTES + '/api/notes', { data: { title: 'Launch plan', body: 'stored encrypted by the vault', private: true }, headers: H });
  await shot(ctx, E.NOTES + '/', 'notes-list');
  await ctx.close();
}
// gateway console
{
  const ctx = await session(E.GATEWAY, '/api/login', { api_key: E.GWKEY }, { 'x-llmgw-csrf': '1' });
  for (const t of ['clients', 'models', 'connect']) await shot(ctx, `${E.GATEWAY}/#${t}`, `gw-${t}`);
  await ctx.close();
}
// identity console
{
  const ctx = await session(E.IDENTITY, '/api/login', { api_key: E.IDKEY }, { 'x-identity-csrf': '1' });
  await shot(ctx, `${E.IDENTITY}/admin`, 'identity-admin');
  await shot(ctx, `${E.IDENTITY}/admin#apps`, 'identity-apps');
  await ctx.close();
}
await browser.close();
