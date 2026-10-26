import type { AppInstance, StartCtx } from '../fleet';
import { baseEnv, cli, extractSetupCode, startProcess } from '../proc';
import { freePort, adminPassword, waitFor } from '../util';

/**
 * CityPulse Simulator (`cpsim serve` applies its migrations first), standalone. The simulated protocol servers bind to
 * loopback only. Plain http to the local CityPulse is allowed by the app itself (loopback); CPSIM_ALLOW_HTTP_TARGETS is
 * set as well for the workload code path. The ingest credential is entered in Settings > Credentials by the spec.
 */
export async function start(ctx: StartCtx): Promise<AppInstance> {
  const port = await freePort();
  const env = {
    ...baseEnv(ctx.workDir),
    DATABASE_URL: ctx.newDb(),
    PORT: String(port),
    CPSIM_ALLOW_HTTP_TARGETS: 'true',
  };
  const url = `http://127.0.0.1:${port}`;
  const running = await startProcess({ name: 'cpsim', cmd: cli('CityPulseSimulator', 'cpsim'), args: ['serve'], env, cwd: ctx.workDir, url });
  const code = await waitFor('cpsim setup code', async () => extractSetupCode(running.logs()), 15_000);
  return {
    name: 'cpsim',
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
  if (!r.ok) throw new Error(`cpsim setup failed: HTTP ${r.status}`);
  app.extra.setupDone = true;
}
