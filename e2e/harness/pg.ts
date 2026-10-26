import { execFileSync, spawn, ChildProcess } from 'node:child_process';
import * as fs from 'node:fs';
import * as path from 'node:path';
import { PG_BIN, SCRATCH_ROOT, freePort, secret, waitFor } from './util';

/**
 * One throwaway Postgres 17 cluster per run (pgvector + pg_trgm come with the installation).
 * Trust auth on 127.0.0.1 only, no unix socket, a free port, deleted on stop.
 */
export interface Cluster {
  dir: string;
  port: number;
  /** libpq URL of a database on this cluster. */
  url(db: string): string;
  createDb(name: string): void;
  stop(): void;
}

const bin = (n: string) => path.join(PG_BIN, n);
const PG_ENV = { PATH: `${PG_BIN}:/usr/bin:/bin`, LANG: 'C', LC_ALL: 'C' };

export async function startCluster(): Promise<Cluster> {
  fs.mkdirSync(SCRATCH_ROOT, { recursive: true });
  const dir = fs.mkdtempSync(path.join(SCRATCH_ROOT, `e2e-${secret(4).replace(/[^a-z0-9]/gi, '')}-`));
  const data = path.join(dir, 'pgdata');
  const port = await freePort();
  execFileSync(bin('initdb'), ['-D', data, '-E', 'UTF8', '--locale=C', '-A', 'trust', '-U', 'postgres'], {
    env: PG_ENV,
    stdio: 'pipe',
  });
  const logFile = path.join(dir, 'postgres.log');
  const opts = `-c listen_addresses=127.0.0.1 -c port=${port} -c unix_socket_directories= -c fsync=off -c synchronous_commit=off -c full_page_writes=off -c max_connections=200`;
  const proc: ChildProcess = spawn(bin('postgres'), ['-D', data, ...opts.split(' ')], {
    env: PG_ENV,
    stdio: ['ignore', fs.openSync(logFile, 'a'), fs.openSync(logFile, 'a')],
    detached: false,
  });
  const cluster: Cluster = {
    dir,
    port,
    url: (db) => `postgresql://postgres@127.0.0.1:${port}/${db}`,
    createDb(name) {
      if (!/^[a-z0-9_]+$/.test(name)) throw new Error('bad db name');
      execFileSync(bin('psql'), ['-h', '127.0.0.1', '-p', String(port), '-U', 'postgres', '-d', 'postgres', '-v', 'ON_ERROR_STOP=1', '-c', `CREATE DATABASE ${name} ENCODING 'UTF8' TEMPLATE template0`], { env: PG_ENV, stdio: 'pipe' });
      for (const ext of ['vector', 'pg_trgm']) {
        execFileSync(bin('psql'), ['-h', '127.0.0.1', '-p', String(port), '-U', 'postgres', '-d', name, '-v', 'ON_ERROR_STOP=1', '-c', `CREATE EXTENSION IF NOT EXISTS ${ext}`], { env: PG_ENV, stdio: 'pipe' });
      }
    },
    stop() {
      try {
        proc.kill('SIGINT'); // fast shutdown
      } catch {}
      try {
        execFileSync(bin('pg_ctl'), ['-D', data, 'stop', '-m', 'immediate', '-s'], { env: PG_ENV, stdio: 'pipe', timeout: 15_000 });
      } catch {}
      try {
        proc.kill('SIGKILL');
      } catch {}
      fs.rmSync(dir, { recursive: true, force: true });
    },
  };
  try {
    await waitFor('postgres to accept connections', async () => {
      try {
        execFileSync(bin('pg_isready'), ['-h', '127.0.0.1', '-p', String(port)], { env: PG_ENV, stdio: 'pipe' });
        return true;
      } catch {
        return false;
      }
    }, 30_000);
  } catch (e) {
    cluster.stop();
    throw e;
  }
  return cluster;
}

/** Postgres runs in the global-setup process; its address reaches the workers through these variables. */
export function clusterFromEnv(): { port: number; url(db: string): string; createDb(name: string): void } {
  const port = Number(process.env.E2E_PG_PORT);
  if (!port) throw new Error('global setup did not start the throwaway Postgres (E2E_PG_PORT missing)');
  return {
    port,
    url: (db) => `postgresql://postgres@127.0.0.1:${port}/${db}`,
    createDb(name) {
      if (!/^[a-z0-9_]+$/.test(name)) throw new Error('bad db name');
      execFileSync(bin('psql'), ['-h', '127.0.0.1', '-p', String(port), '-U', 'postgres', '-d', 'postgres', '-v', 'ON_ERROR_STOP=1', '-c', `CREATE DATABASE ${name} ENCODING 'UTF8' TEMPLATE template0`], { env: PG_ENV, stdio: 'pipe' });
      for (const ext of ['vector', 'pg_trgm']) {
        execFileSync(bin('psql'), ['-h', '127.0.0.1', '-p', String(port), '-U', 'postgres', '-d', name, '-v', 'ON_ERROR_STOP=1', '-c', `CREATE EXTENSION IF NOT EXISTS ${ext}`], { env: PG_ENV, stdio: 'pipe' });
      }
    },
  };
}
