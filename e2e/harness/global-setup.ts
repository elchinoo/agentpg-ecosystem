import * as fs from 'node:fs';
import * as path from 'node:path';
import { startCluster, Cluster } from './pg';

let cluster: Cluster | undefined;

function stopAtExit() {
  try {
    cluster?.stop();
  } catch {}
  cluster = undefined;
}

/** One throwaway Postgres per run. Its address reaches the workers through the environment. */
export default async function globalSetup() {
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
