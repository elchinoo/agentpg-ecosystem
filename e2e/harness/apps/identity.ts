import type { AppInstance, StartCtx } from '../fleet';
import { baseEnv, cli, extractSetupCode, runOnce, startProcess } from '../proc';
import { adminPassword, freePort, secret, waitFor } from '../util';

/** Identity provider, standalone admin (local password): migrate, serve on a free port; issuer = its own loopback URL. */
export async function start(ctx: StartCtx): Promise<AppInstance> {
  const port = await freePort();
  const url = `http://127.0.0.1:${port}`;
  const env = {
    ...baseEnv(ctx.workDir),
    DATABASE_URL: ctx.newDb(),
    PORT: String(port),
    IDENTITY_PUBLIC_URL: url,
    IDENTITY_ENCRYPTION_KEY: secret(32), // generated per run, unlocks the vault holding the signing keys
  };
  const bin = cli('identity', 'identity');
  await runOnce(bin, ['migrate'], env, ctx.workDir);
  const running = await startProcess({ name: 'identity', cmd: bin, args: ['serve'], env, cwd: ctx.workDir, url });
  const code = await waitFor('identity setup code', async () => extractSetupCode(running.logs()), 15_000);
  return { name: 'identity', url, adminPassword: adminPassword(), extra: { setupCode: code, setupDone: false }, running: [running] };
}
