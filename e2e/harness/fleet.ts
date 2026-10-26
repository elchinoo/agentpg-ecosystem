import * as fs from 'node:fs';
import * as path from 'node:path';
import { clusterFromEnv } from './pg';
import { Running } from './proc';
import { FakeLlm, startFakeLlm } from './fakellm';
import { starters } from './apps';

export interface AppInstance {
  name: string;
  /** http://127.0.0.1:<port> */
  url: string;
  /** Throwaway admin password, generated in memory for this run and only ever typed into this local app. */
  adminPassword: string;
  /** App specific extras (tokens, keys, ids) a spec may need. Generated at run time. */
  extra: Record<string, any>;
  running: Running[];
  /** Cleanup beyond stopping the processes. */
  onStop?: () => Promise<void> | void;
}

export interface StartCtx {
  name: string;
  /** Private working directory of this app (becomes the process cwd, so no stray .env or key file is ever read). */
  workDir: string;
  /** Create a database on the run's throwaway cluster and return its libpq URL. */
  newDb(): string;
  fakeLlm(): Promise<FakeLlm>;
  /** Start (or reuse) another app of the fleet. */
  fleet: Fleet;
}

export type AppName = 'trustdesk' | 'paybroker' | 'pagerlens' | 'citypulse' | 'cpsim' | 'llmgw' | 'identity' | 'shopfront';

/** Starts apps on demand within one worker and stops everything when the worker ends. */
export class Fleet {
  private apps = new Map<string, Promise<AppInstance>>();
  private llm?: Promise<FakeLlm>;
  private runDir = process.env.E2E_RUN_DIR || '';

  get(name: AppName): Promise<AppInstance> {
    let p = this.apps.get(name);
    if (!p) {
      p = this.start(name);
      this.apps.set(name, p);
    }
    return p;
  }

  private async start(name: AppName): Promise<AppInstance> {
    if (!this.runDir) throw new Error('E2E_RUN_DIR missing: global setup did not run');
    const cluster = clusterFromEnv();
    const workDir = path.join(this.runDir, `app-${name}`);
    fs.mkdirSync(workDir, { recursive: true });
    const starter = starters[name];
    if (!starter) throw new Error(`no starter registered for ${name}`);
    return starter({
      name,
      workDir,
      newDb: () => {
        // `e2e_<app>` normally (helpers refer to it); a restarted worker meets the old database on the same cluster and gets a suffixed one.
        let db = `e2e_${name}`;
        try {
          cluster.createDb(db);
        } catch {
          db = `e2e_${name}_${Math.random().toString(36).slice(2, 8)}`;
          cluster.createDb(db);
        }
        return cluster.url(db);
      },
      fakeLlm: () => (this.llm ??= startFakeLlm()),
      fleet: this,
    });
  }

  fakeLlm(): Promise<FakeLlm> {
    return (this.llm ??= startFakeLlm());
  }

  async stopAll(): Promise<void> {
    for (const p of this.apps.values()) {
      try {
        const a = await p;
        for (const r of a.running.reverse()) await r.stop();
        await a.onStop?.();
      } catch {}
    }
    this.apps.clear();
    if (this.llm) {
      try {
        await (await this.llm).stop();
      } catch {}
      this.llm = undefined;
    }
  }
}
