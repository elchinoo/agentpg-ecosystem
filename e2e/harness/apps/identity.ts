import type { AppInstance, StartCtx } from '../fleet';

export async function start(_ctx: StartCtx): Promise<AppInstance> {
  throw new Error('identity starter not written yet');
}
