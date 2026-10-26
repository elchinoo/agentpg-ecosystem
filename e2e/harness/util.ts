import * as net from 'node:net';
import * as crypto from 'node:crypto';
import * as path from 'node:path';

/** Directory that holds the sibling repositories (default: the parent of the ecosystem repo). */
export const REPOS_DIR = process.env.E2E_REPOS_DIR || path.resolve(__dirname, '..', '..', '..');
export const PG_BIN = process.env.E2E_PG_BIN || '/Users/charly.batista/app/bin';
export const SCRATCH_ROOT =
  process.env.E2E_SCRATCH ||
  '/private/tmp/claude-501/-Users-charly-batista-proj-edb-dev-cloud/97a03695-06aa-4b09-b814-b77ba9a34fc2/scratchpad';

/** Ask the OS for a free TCP port on 127.0.0.1. */
export function freePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.unref();
    srv.on('error', reject);
    srv.listen(0, '127.0.0.1', () => {
      const { port } = srv.address() as net.AddressInfo;
      srv.close(() => resolve(port));
    });
  });
}

/** A random throwaway secret, kept in memory only. */
export function secret(bytes = 18): string {
  return crypto.randomBytes(bytes).toString('base64url');
}

/** A throwaway admin password that satisfies "12+ characters". */
export function adminPassword(): string {
  return `Pw-${secret(14)}-9`;
}

export function fernetKey(): string {
  return crypto.randomBytes(32).toString('base64url') + '=';
}

export const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export async function waitFor<T>(
  what: string,
  fn: () => Promise<T | undefined | false | null>,
  timeoutMs = 60_000,
  intervalMs = 250,
): Promise<T> {
  const end = Date.now() + timeoutMs;
  let last: unknown;
  while (Date.now() < end) {
    try {
      const v = await fn();
      if (v) return v;
    } catch (e) {
      last = e;
    }
    await sleep(intervalMs);
  }
  throw new Error(`timed out waiting for ${what}${last ? `: ${String(last)}` : ''}`);
}
