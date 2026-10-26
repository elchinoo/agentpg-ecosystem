import * as fs from 'node:fs';
import * as path from 'node:path';
import { execFileSync } from 'node:child_process';
import { startCluster, Cluster } from './pg';
import { REPOS_DIR } from './util';

/**
 * Print, per app: the HEAD of its sibling checkout, whether the working tree is dirty, whether its venv runs the app editable from
 * that very checkout (no stale installed copy), and the agentpg-kit version it imports.
 */
function logFleetVersions() {
  for (const repo of ['trustdesk', 'paybroker', 'pagerlens', 'CityPulse', 'CityPulseSimulator', 'llm-gateway', 'identity', 'shopfront']) {
    const dir = path.join(REPOS_DIR, repo);
    const py = path.join(dir, '.venv', 'bin', 'python');
    const env = { PATH: '/usr/bin:/bin' };
    try {
      const head = execFileSync('git', ['-C', dir, 'rev-parse', '--short', 'HEAD'], { env, encoding: 'utf8' }).trim();
      const dirty = execFileSync('git', ['-C', dir, 'status', '--porcelain'], { env, encoding: 'utf8' }).trim() ? ' (dirty tree)' : '';
      const name = (fs.readFileSync(path.join(dir, 'pyproject.toml'), 'utf8').match(/^name\s*=\s*"([^"]+)"/m) || [])[1];
      const code =
        `import importlib.metadata as m,json,apgkit;d=m.distribution(${JSON.stringify(name)});u=json.loads(d.read_text("direct_url.json") or "{}");` +
        `print(("editable" if u.get("dir_info",{}).get("editable") else "NOT EDITABLE"), u.get("url","?").replace("file://",""), "kit", m.version("agentpg-kit"))`;
      const info = execFileSync(py, ['-c', code], { env, encoding: 'utf8' }).trim();
      const stale = info.includes('NOT EDITABLE') || !info.includes(dir) ? '  <-- STALE INSTALL?' : '';
      console.log(`[e2e] ${repo}@${head}${dirty}: ${info}${stale}`);
    } catch {
      console.log(`[e2e] ${repo}: version info unavailable (no .venv?)`);
    }
  }
}

let cluster: Cluster | undefined;

function stopAtExit() {
  try {
    cluster?.stop();
  } catch {}
  cluster = undefined;
}

/** One throwaway Postgres per run. Its address reaches the workers through the environment. */
export default async function globalSetup() {
  logFleetVersions();
  cluster = await startCluster();
  process.env.E2E_PG_PORT = String(cluster.port);
  process.env.E2E_RUN_DIR = cluster.dir;
  fs.rmSync(path.join(__dirname, '..', 'reports'), { recursive: true, force: true });
  fs.mkdirSync(path.join(__dirname, '..', 'reports'), { recursive: true });
  // If the runner is interrupted, still delete the cluster.
  for (const sig of ['SIGINT', 'SIGTERM', 'SIGHUP'] as const) {
    process.once(sig, () => {
      stopAtExit();
      process.exit(130);
    });
  }
  process.once('exit', stopAtExit);
  // Playwright calls the returned function as global teardown, even when tests fail.
  return async () => stopAtExit();
}
