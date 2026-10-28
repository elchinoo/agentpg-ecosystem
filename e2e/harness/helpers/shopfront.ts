import { execFileSync } from 'node:child_process';
import * as path from 'node:path';
import { PG_BIN } from '../util';

/**
 * Make a customer look established to PayBroker. ShopFront derives the account age from the e-mail's first PAID order. By default
 * (SHOPFRONT_NEW_GUEST_AGE=omit) a guest it has never seen pay is sent with no account age, so PayBroker assumes an established account;
 * only in mode `zero` does a first-time buyer score as a "new account" (about 600: 3-D Secure). Back-dating the paid order in the
 * run's THROWAWAY database makes the age real (30 days), so a later purchase takes the plain approval path whatever the mode.
 */
export function backdateCustomer(email: string, days = 30) {
  const port = process.env.E2E_PG_PORT;
  if (!port) throw new Error('E2E_PG_PORT missing');
  execFileSync(
    path.join(PG_BIN, 'psql'),
    ['-h', '127.0.0.1', '-p', port, '-U', 'postgres', '-d', 'e2e_shopfront', '-v', 'ON_ERROR_STOP=1', '-c', `UPDATE shop_order SET created_at = created_at - interval '${Number(days)} days', paid_at = paid_at - interval '${Number(days)} days' WHERE lower(email) = '${email.toLowerCase().replace(/'/g, "''")}'`],
    { env: { PATH: '/usr/bin:/bin', LANG: 'C' }, stdio: 'pipe' },
  );
}
