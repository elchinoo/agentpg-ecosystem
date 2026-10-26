import * as fs from 'node:fs';
import * as path from 'node:path';
import { execFileSync } from 'node:child_process';
import { startCluster, Cluster } from './pg';
import { REPOS_DIR } from './util';

/** Print the agentpg-kit version each app virtualenv really imports (the apps share one UI kit). */
function logKitVersions() {
  for (const repo of ['trustdesk', 'paybroker', 'pagerlens', 'CityPulse', 'CityPulseSimulator', 'llm-gateway', 'identity', 'shopfront']) {
    const py = path.join(REPOS_DIR, repo, '.venv', 'bin', 'python');
    try {
      const v = execFileSync(py, ['-c', 'import importlib.metadata as m,apgkit,os;print(m.version("agentpg-kit"), "editable" if "site-packages" not in apgkit.__file__ else "installed")'], { env: { PATH: '/usr/bin:/bin' }, encoding: 'utf8' }).trim();
      console.log(`[e2e] ${repo}: agentpg-kit ${v}`);
    } catch {
      console.log(`[e2e] ${repo}: agentpg-kit version unknown (no .venv?)`);
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
  logKitVersions();
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
