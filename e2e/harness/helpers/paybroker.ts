/** Node-side PayBroker API helpers for specs (local PayBroker only; keys are the run's throwaway ones). */
export async function tokenize(pbUrl: string, pk: string, number: string): Promise<string> {
  const r = await fetch(pbUrl + '/v1/vault/tokens', {
    method: 'POST',
    headers: { authorization: 'Bearer ' + pk, 'content-type': 'application/json' },
    body: JSON.stringify({ number, exp_month: 12, exp_year: 2035, cvc: '123' }),
  });
  const j: any = await r.json();
  if (!j.token) throw new Error('tokenize failed: ' + r.status);
  return j.token;
}

export async function authorize(pbUrl: string, sk: string, body: Record<string, unknown>): Promise<{ status: number; json: any }> {
  const r = await fetch(pbUrl + '/v1/payments/authorize', {
    method: 'POST',
    headers: { authorization: 'Bearer ' + sk, 'content-type': 'application/json' },
    body: JSON.stringify({ currency: 'USD', ...body }),
  });
  return { status: r.status, json: await r.json() };
}

export const SAFE_CUSTOMER = { id: 'cus_e2e_1', email: 'pat@example.com', ip_address: '198.51.100.20', account_age_hours: 4000 };
export const RISKY = {
  customer: { id: 'cus_e2e_risky', email: 'x@example.com', ip_address: '203.0.113.9', account_age_hours: 1 },
  telemetry: { device_fingerprint: 'dev_e2e_z', checkout_duration_ms: 900, pasted_card_number: true },
  shipping_address: { country: 'NG' },
};
