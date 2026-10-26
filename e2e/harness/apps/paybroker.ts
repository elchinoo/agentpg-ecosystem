import type { AppInstance, StartCtx } from '../fleet';
import { baseEnv, cli, extractSetupCode, runOnce, startProcess } from '../proc';
import { freePort, adminPassword, waitFor } from '../util';

/** PayBroker, standalone (no OIDC), role "all" (API + worker), LLM env pointed at the harness's fake model server. */
export async function start(ctx: StartCtx): Promise<AppInstance> {
  const llm = await ctx.fakeLlm();
  const port = await freePort();
  const url = `http://127.0.0.1:${port}`;
  const env = {
    ...baseEnv(ctx.workDir),
    DATABASE_URL: ctx.newDb(),
    PORT: String(port),
    PAYBROKER_PUBLIC_URL: url,
    PAYBROKER_ALLOW_PRIVATE_URLS: 'true', // the shop's webhook endpoint and the fake model are on loopback
    LLM_BASE_URL: llm.url,
    LLM_API_KEY: 'fake-key-for-e2e',
    LLM_CHAT_MODEL: 'fake-chat',
  };
  const bin = cli('paybroker', 'paybroker');
  await runOnce(bin, ['migrate'], env, ctx.workDir);
  const running = await startProcess({ name: 'paybroker', cmd: bin, args: ['serve'], env, cwd: ctx.workDir, url });
  const code = await waitFor('paybroker setup code', async () => extractSetupCode(running.logs()), 15_000);
  return { name: 'paybroker', url, adminPassword: adminPassword(), extra: { setupCode: code, setupDone: false }, running: [running] };
}
