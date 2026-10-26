import type { AppInstance, StartCtx } from '../fleet';
import { baseEnv, cli, extractSetupCode, runOnce, startProcess } from '../proc';
import { freePort, adminPassword, waitFor } from '../util';

/**
 * ShopFront, standalone. It talks to the LOCAL PayBroker of the fleet (started on demand); the PayBroker keys are
 * entered later through ShopFront's Settings (vault), not through the environment.
 */
export async function start(ctx: StartCtx): Promise<AppInstance> {
  const pb = await ctx.fleet.get('paybroker');
  const llm = await ctx.fakeLlm();
  const port = await freePort();
  const url = `http://127.0.0.1:${port}`;
  const env = {
    ...baseEnv(ctx.workDir),
    DATABASE_URL: ctx.newDb(),
    PORT: String(port),
    SHOPFRONT_PUBLIC_URL: url,
    SHOPFRONT_ALLOW_PRIVATE_URLS: 'true', // PayBroker and the fake model are on loopback
    LLM_BASE_URL: llm.url,
    LLM_API_KEY: 'fake-key-for-e2e',
    LLM_CHAT_MODEL: 'fake-chat',
    EMBEDDING_MODEL: 'fake-embed',
    EMBEDDING_DIM: '768',
  };
  const bin = cli('shopfront', 'shopfront');
  await runOnce(bin, ['migrate'], env, ctx.workDir);
  const running = await startProcess({ name: 'shopfront', cmd: bin, args: ['serve'], env, cwd: ctx.workDir, url });
  const code = await waitFor('shopfront setup code', async () => extractSetupCode(running.logs()), 15_000);
  return { name: 'shopfront', url, adminPassword: adminPassword(), extra: { setupCode: code, setupDone: false, paybroker: pb.url }, running: [running] };
}
