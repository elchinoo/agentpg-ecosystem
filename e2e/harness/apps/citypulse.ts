import type { AppInstance, StartCtx } from '../fleet';
import { baseEnv, cli, extractSetupCode, startProcess } from '../proc';
import { freePort, adminPassword, waitFor } from '../util';

/**
 * CityPulse in role `api` (no built-in simulator: readings come from CityPulse Simulator), standalone (no OIDC).
 * `citypulse start` migrates, then serves. First-run setup is left to the spec (UI) or to `setupViaApi`.
 */
export async function start(ctx: StartCtx): Promise<AppInstance> {
  const port = await freePort();
  const env = {
    ...baseEnv(ctx.workDir),
    DATABASE_URL: ctx.newDb(),
    PORT: String(port),
    CITYPULSE_ROLE: 'api',
  };
  const url = `http://127.0.0.1:${port}`;
  const running = await startProcess({ name: 'citypulse', cmd: cli('CityPulse', 'citypulse'), args: ['start'], env, cwd: ctx.workDir, url });
  const code = await waitFor('citypulse setup code', async () => extractSetupCode(running.logs()), 15_000);
  return {
    name: 'citypulse',
    url,
    adminPassword: adminPassword(),
    extra: { setupCode: code, setupDone: false },
    running: [running],
  };
}

export async function setupViaApi(app: AppInstance): Promise<void> {
  if (app.extra.setupDone) return;
  const r = await fetch(app.url + '/api/setup', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ code: app.extra.setupCode, password: app.adminPassword }),
  });
  if (!r.ok) throw new Error(`citypulse setup failed: HTTP ${r.status}`);
  app.extra.setupDone = true;
}
