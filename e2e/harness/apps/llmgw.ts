import type { AppInstance, StartCtx } from '../fleet';
import { baseEnv, cli, extractSetupCode, runOnce, startProcess } from '../proc';
import { adminPassword, freePort, waitFor } from '../util';

/** LLM gateway, standalone: migrate, serve on a free port. Only the built-in echo provider is used (no network, no keys). */
export async function start(ctx: StartCtx): Promise<AppInstance> {
  const port = await freePort();
  const url = `http://127.0.0.1:${port}`;
  const env = { ...baseEnv(ctx.workDir), DATABASE_URL: ctx.newDb(), PORT: String(port), LLMGW_PUBLIC_URL: url };
  const bin = cli('llm-gateway', 'llmgw');
  await runOnce(bin, ['migrate'], env, ctx.workDir);
  const running = await startProcess({ name: 'llmgw', cmd: bin, args: ['serve'], env, cwd: ctx.workDir, url });
  const code = await waitFor('llmgw setup code', async () => extractSetupCode(running.logs()), 15_000);
  return { name: 'llmgw', url, adminPassword: adminPassword(), extra: { setupCode: code, setupDone: false }, running: [running] };
}
