import type { AppInstance } from '../fleet';

/**
 * A tiny Node-side client for the kit's cookie session (setup/login) with the CSRF header the app announces
 * in <html data-csrf>. Used by harness code and specs to prepare data through the app's own admin API.
 * Talks to the LOCAL app only (the URL comes from the AppInstance the harness started).
 */
export async function adminClient(app: AppInstance, csrfHeader: string) {
  let cookie = '';
  const grab = (r: Response) => {
    const sc = r.headers.getSetCookie?.() ?? [];
    if (sc.length) cookie = sc.map((c) => c.split(';')[0]).join('; ');
  };
  if (!app.extra.setupDone) {
    const r = await fetch(app.url + '/api/setup', {
      method: 'POST',
      headers: { 'content-type': 'application/json', [csrfHeader]: '1' },
      body: JSON.stringify({ code: app.extra.setupCode, password: app.adminPassword }),
    });
    if (!r.ok) throw new Error(`${app.name} setup failed: HTTP ${r.status}`);
    app.extra.setupDone = true;
    grab(r);
  } else {
    const r = await fetch(app.url + '/api/login', {
      method: 'POST',
      headers: { 'content-type': 'application/json', [csrfHeader]: '1' },
      body: JSON.stringify({ password: app.adminPassword }),
    });
    if (!r.ok) throw new Error(`${app.name} login failed: HTTP ${r.status}`);
    grab(r);
  }
  return async function call<T = any>(method: string, path: string, body?: unknown, expect = 200): Promise<T> {
    const r = await fetch(app.url + path, {
      method,
      headers: { 'content-type': 'application/json', [csrfHeader]: '1', cookie },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const text = await r.text();
    if (r.status >= 400 || (expect && r.status !== expect && r.status >= 300)) throw new Error(`${method} ${path} -> ${r.status}: ${text.slice(0, 300)}`);
    return (text ? JSON.parse(text) : {}) as T;
  };
}

/** Sign a browser context in through the API, with the CSRF header the app requires (cookie lands in the context). */
export async function loginViaApi(page: import('@playwright/test').Page, app: AppInstance, csrfHeader: string) {
  const r = await page.context().request.post(app.url + '/api/login', {
    data: { password: app.adminPassword },
    headers: { [csrfHeader]: '1' },
  });
  if (!r.ok()) throw new Error(`sign in to ${app.name} failed: HTTP ${r.status()}`);
}
