import type { AppInstance, StartCtx } from '../fleet';
import { baseEnv, cli, extractSetupCode, runOnce, startProcess } from '../proc';
import { freePort, adminPassword, waitFor } from '../util';

/**
 * PagerLens, standalone (no OIDC), demo embedder (no LLM env: the Rules method needs no model).
 * migrate + seed (demo fleet, runbooks, healthy baseline) then serve; first-run setup is left to the spec.
 */
export async function start(ctx: StartCtx): Promise<AppInstance> {
  const port = await freePort();
  const env = { ...baseEnv(ctx.workDir), DATABASE_URL: ctx.newDb(), PORT: String(port) };
  const bin = cli('pagerlens', 'pagerlens');
  await runOnce(bin, ['migrate'], env, ctx.workDir);
  await runOnce(bin, ['seed'], env, ctx.workDir);
  const url = `http://127.0.0.1:${port}`;
  const running = await startProcess({ name: 'pagerlens', cmd: bin, args: ['serve'], env, cwd: ctx.workDir, url });
  const code = await waitFor('pagerlens setup code', async () => extractSetupCode(running.logs()), 15_000);
  return { name: 'pagerlens', url, adminPassword: adminPassword(), extra: { setupCode: code, setupDone: false }, running: [running] };
}

/** Complete first-run setup through the API (for specs of other apps that need a signed-in PagerLens). */
export async function setupViaApi(app: AppInstance): Promise<void> {
  if (app.extra.setupDone) return;
  const r = await fetch(app.url + '/api/setup', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ code: app.extra.setupCode, password: app.adminPassword }),
  });
  if (!r.ok) throw new Error(`pagerlens setup failed: HTTP ${r.status}`);
  app.extra.setupDone = true;
}
