import { spawn, ChildProcess } from 'node:child_process';
import * as fs from 'node:fs';
import * as path from 'node:path';
import { REPOS_DIR, waitFor } from './util';

export interface Running {
  name: string;
  url: string;
  proc: ChildProcess;
  /** Everything the process printed so far (stdout + stderr). Treat as sensitive: never print it in reports. */
  logs(): string;
  stop(): Promise<void>;
}

/** The only variables an app ever sees besides the ones a spec passes explicitly. Nothing from the real environment. */
export function baseEnv(home: string): Record<string, string> {
  return {
    PATH: '/usr/bin:/bin',
    HOME: home,
    TMPDIR: home,
    LANG: 'en_US.UTF-8',
    PYTHONUNBUFFERED: '1',
    PYTHONDONTWRITEBYTECODE: '1',
    NO_COLOR: '1',
    HOST: '127.0.0.1',
  };
}

/** Path of a console script in a sibling repository's own virtualenv. */
export function cli(repo: string, script: string): string {
  const p = path.join(REPOS_DIR, repo, '.venv', 'bin', script);
  if (!fs.existsSync(p)) throw new Error(`${p} not found: create the repository's .venv first (uv venv && uv pip install -e ".[dev]")`);
  return p;
}

/** Run a short command to completion (migrate, seed...). Rejects with the output on a non-zero exit. */
export function runOnce(cmd: string, args: string[], env: Record<string, string>, cwd: string, timeoutMs = 120_000): Promise<string> {
  return new Promise((resolve, reject) => {
    const p = spawn(cmd, args, { env, cwd, stdio: ['ignore', 'pipe', 'pipe'] });
    let out = '';
    p.stdout.on('data', (d) => (out += d));
    p.stderr.on('data', (d) => (out += d));
    const t = setTimeout(() => {
      p.kill('SIGKILL');
      reject(new Error(`${path.basename(cmd)} ${args.join(' ')} timed out\n${redact(out)}`));
    }, timeoutMs);
    p.on('exit', (code) => {
      clearTimeout(t);
      code === 0 ? resolve(out) : reject(new Error(`${path.basename(cmd)} ${args.join(' ')} exited ${code}\n${redact(out)}`));
    });
  });
}

/** Keep generated secrets out of error messages and reports. */
export function redact(s: string): string {
  return s.replace(/(setup code[^\n]*\n\s*)\S+/gi, '$1<redacted>');
}

export async function startProcess(opts: {
  name: string;
  cmd: string;
  args: string[];
  env: Record<string, string>;
  cwd: string;
  url: string;
  ready?: string; // path polled until it answers 2xx; default /readyz
  readyTimeoutMs?: number;
}): Promise<Running> {
  const p = spawn(opts.cmd, opts.args, { env: opts.env, cwd: opts.cwd, stdio: ['ignore', 'pipe', 'pipe'], detached: true });
  let buf = '';
  p.stdout!.on('data', (d) => (buf += d));
  p.stderr!.on('data', (d) => (buf += d));
  let exited: number | null | undefined;
  p.on('exit', (c) => (exited = c ?? -1));
  const running: Running = {
    name: opts.name,
    url: opts.url,
    proc: p,
    logs: () => buf,
    async stop() {
      if (exited !== undefined) return;
      try {
        process.kill(-p.pid!, 'SIGTERM');
      } catch {}
      const end = Date.now() + 8000;
      while (exited === undefined && Date.now() < end) await new Promise((r) => setTimeout(r, 100));
      if (exited === undefined) {
        try {
          process.kill(-p.pid!, 'SIGKILL');
        } catch {}
      }
    },
  };
  try {
    await waitFor(
      `${opts.name} ${opts.ready ?? '/readyz'}`,
      async () => {
        if (exited !== undefined) throw new Error(`${opts.name} exited early (${exited})\n${redact(buf).slice(-3000)}`);
        try {
          const r = await fetch(opts.url + (opts.ready ?? '/readyz'));
          return r.ok;
        } catch {
          return false;
        }
      },
      opts.readyTimeoutMs ?? 90_000,
    );
  } catch (e) {
    await running.stop();
    throw e;
  }
  return running;
}

/** The first-run setup code printed to the console, e.g. `    XXXX-XXXX-XXXX` on a line of its own. */
export function extractSetupCode(logs: string): string | undefined {
  const dashed = logs.match(/^[ \t]{2,}([A-Za-z0-9]{3,8}(?:-[A-Za-z0-9]{3,8}){1,4})[ \t]*$/m);
  if (dashed) return dashed[1];
  return logs.match(/setup[ -]code[^\n]*?[:=]\s*([A-Za-z0-9_-]{6,})/i)?.[1];
}
