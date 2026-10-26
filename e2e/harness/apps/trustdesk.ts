import * as path from 'node:path';
import type { AppInstance, StartCtx } from '../fleet';
import { baseEnv, cli, extractSetupCode, runOnce, startProcess } from '../proc';
import { freePort, adminPassword, waitFor } from '../util';

/**
 * TrustDesk, standalone (no OIDC): migrate, serve on a free port, LLM env pointed at the harness's fake
 * OpenAI-compatible server. First-run setup is left to the spec (UI) or to `trustdesk-setup` below.
 */
export async function start(ctx: StartCtx): Promise<AppInstance> {
  const llm = await ctx.fakeLlm();
  const port = await freePort();
  const env = {
    ...baseEnv(ctx.workDir),
    DATABASE_URL: ctx.newDb(),
    PORT: String(port),
    LLM_BASE_URL: llm.url,
    LLM_API_KEY: 'fake-key-for-e2e',
    LLM_CHAT_MODEL: 'fake-chat',
    EMBEDDING_MODEL: 'fake-embed',
    EMBEDDING_DIM: '768',
    TRUSTDESK_ALLOW_PRIVATE_LLM_URLS: 'true', // the fake model server is on loopback
  };
  const bin = cli('trustdesk', 'trustdesk');
  await runOnce(bin, ['migrate'], env, ctx.workDir);
  const url = `http://127.0.0.1:${port}`;
  const running = await startProcess({ name: 'trustdesk', cmd: bin, args: ['serve'], env, cwd: ctx.workDir, url });
  const code = await waitFor('trustdesk setup code', async () => extractSetupCode(running.logs()), 15_000);
  return {
    name: 'trustdesk',
    url,
    adminPassword: adminPassword(),
    extra: { setupCode: code, setupDone: false, llmUrl: llm.url },
    running: [running],
  };
}

/** Complete the first-run setup through the API and return the session cookie header value. */
export async function setupViaApi(app: AppInstance): Promise<void> {
  if (app.extra.setupDone) return;
  const r = await fetch(app.url + '/api/setup', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ code: app.extra.setupCode, password: app.adminPassword }),
  });
  if (!r.ok) throw new Error(`trustdesk setup failed: HTTP ${r.status}`);
  app.extra.setupDone = true;
}
