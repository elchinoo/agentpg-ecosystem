import type { AppInstance, AppName, StartCtx } from '../fleet';
import { start as trustdesk } from './trustdesk';
import { start as paybroker } from './paybroker';
import { start as pagerlens } from './pagerlens';
import { start as citypulse } from './citypulse';
import { start as cpsim } from './cpsim';
import { start as llmgw } from './llmgw';
import { start as identity } from './identity';
import { start as shopfront } from './shopfront';

/** One starter per app: migrate, seed, serve, complete first-run setup, wait for /readyz. */
export const starters: Record<AppName, (ctx: StartCtx) => Promise<AppInstance>> = {
  trustdesk,
  paybroker,
  pagerlens,
  citypulse,
  cpsim,
  llmgw,
  identity,
  shopfront,
};
