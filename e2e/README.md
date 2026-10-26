# End-to-end suite

A real-browser suite for the fleet. [Playwright](https://playwright.dev) (TypeScript) drives each app in the machine's installed
**Google Chrome**, walks the main user stories, and fails on any browser console error, page error or failed request
(except an explicit allowlist) and on any **serious or critical accessibility violation** ([axe](https://github.com/dequelabs/axe-core-npm)).
It also saves screenshots of the key screens (light and dark, desktop 1440x900 and phone 390x844) for the READMEs.

## Safety rule

Tests run **only against local apps on 127.0.0.1** that the harness starts itself, with a throwaway Postgres, generated test
credentials and nothing from your real environment:

- one Postgres 17 cluster per run (with pgvector and pg_trgm), `trust` auth on 127.0.0.1, no unix socket, a free port, one database
  per app, in a unique directory under the scratch folder; it is stopped and deleted in global teardown, also when tests fail or you press Ctrl-C;
- each app runs from its sibling repository with that repository's own `.venv`, in a private working directory (so no `.env` or key file is read),
  with an explicit environment (`DATABASE_URL`, free `PORT`, per-run generated secrets, standalone mode with no OIDC);
- admin passwords are random per run, held in memory and typed only into these local apps; the one-time setup code is read from the process output;
- model calls go to a fake OpenAI-compatible server the harness starts (canned chat and embedding answers);
- the suite never reads `../.ecosystem/state.json`, `../keys` or any secret file, and never talks to the live fleet.

## Run it

Prerequisites: Node 22+, Google Chrome installed, Postgres 17 binaries with pgvector (default `/Users/charly.batista/app/bin`,
override with `E2E_PG_BIN`), and the sibling repositories next to `ecosystem/` each with a `.venv`
(`uv venv && uv pip install -e ".[dev]"`): `agentpg-kit`, `trustdesk`, `paybroker`, `pagerlens`, `CityPulse`, `CityPulseSimulator`,
`llm-gateway`, `identity`, `shopfront`. No browser is downloaded.

```bash
cd e2e
npm ci
npx playwright test                       # everything (about 2 minutes)
npx playwright test tests/trustdesk.spec.ts   # one app
npx playwright test -g "refund"           # by title
HEADED=1 npx playwright test tests/shopfront.spec.ts --workers=1   # watch it in a visible Chrome
npx playwright show-report                # HTML report (traces of failed tests are attached)
npm run a11y                              # every accessibility finding of the last run, grouped
```

Environment knobs: `E2E_PG_BIN` (Postgres binaries), `E2E_REPOS_DIR` (parent of the sibling repos, default the parent of `ecosystem/`),
`E2E_SCRATCH` (where throwaway clusters live), `HEADED=1`.

## What is covered

| Spec | Story |
|---|---|
| `trustdesk` | setup and sign-in, demo policies; an employee request through the client API appears, the AI pre-validation (fake model) shows advice with the quoted passage, the approver approves; the validation setting survives 15 s of timers; delete-account confirm (type the name, cancel, confirm) |
| `paybroker` | setup; merchant, keys, allowed origins; a test payment through the API shows in Payments with the decision explanation; a risky payment is declined and opens a case; AI triage limits survive a refresh |
| `pagerlens` | setup and seed; Lab: break a scenario, investigate with the Rules method, hypotheses and evidence, mark resolved; retire-service typed name survives the 10 s refresh |
| `citypulse` | CityPulse and the Simulator set up; a generated city streams into CityPulse, the Overview heatmap fills; BURST changes the status |
| `llmgw` | setup; alias on the echo provider; client and key; a `/v1/chat/completions` call shows as usage on the dashboard |
| `identity` | setup; register an app; create a group, a user and a role in the UI; OIDC discovery and JWKS are reachable |
| `shopfront` (with PayBroker) | PayBroker merchant (allowed origin = the shop, webhook = the shop), keys typed into the local shop; search, cart, checkout with a PayBroker test card tokenized from the browser (CORS); order paid; back office shows the payment trail; refund; 3-D Secure through PayBroker's hosted page; a declined card shows the friendly message |

Each run prints the agentpg-kit version every app venv imports (`[e2e] <repo>: agentpg-kit X editable`); install the sibling kit editable in each venv (`uv pip install -e ../agentpg-kit`) so the run tests the kit you mean to.

Real bugs the suite found are marked `test.fail()` (they turn red once fixed) and described in [FINDINGS.md](FINDINGS.md).

## Layout

```
harness/
  pg.ts, global-setup.ts   throwaway Postgres per run, torn down in global teardown
  proc.ts                  start/stop processes, runOnce, setup-code extraction, explicit env
  fleet.ts                 starts apps on demand within a worker, stops them at the end
  fakellm.ts               fake OpenAI-compatible server
  fixtures.ts              test fixtures: console/network guard, `snap` (screenshots + axe), `allow`, `fleet`
  ui.ts, helpers/          shared sign-in helpers (kit setup card, CSRF header)
  apps/<app>.ts            one starter per app: migrate, seed, serve, setup code, /readyz
tests/<app>.spec.ts        one spec per app (serial stories)
screenshots/<app>/         generated on every run; only a curated set is committed
reports/a11y.jsonl         every axe finding of the run (git-ignored); `npm run a11y`
```

## Writing a spec

```ts
import { test, expect } from '../harness/fixtures';
test.describe.configure({ mode: 'serial' });
let app: AppInstance;
test.beforeAll(async ({ fleet }) => { app = await fleet.get('trustdesk'); });
test('story', async ({ page, snap }) => {
  await page.goto(app.url + '/');
  await snap(page, 'trustdesk', 'setup', { phone: true });   // screenshots light+dark (+phone) and the axe audit
});
```

- **Console and network guard:** any `console.error`, uncaught page error, failed request or HTTP status 400+ fails the test. Allow something deliberate with
  `test.use({ allow: [/HTTP 401 GET .*\/api\/session/] })` and say why.
- **Accessibility:** `snap` runs axe in light and dark; serious and critical violations fail unless the rule id is in `knownA11y` (which must be documented in FINDINGS.md).
  Everything found, whatever the impact, is appended to `reports/a11y.jsonl`.
- **No sleeps:** use web-first assertions. For "survives N seconds of timers" stories use `await page.clock.install()` before `goto` and `page.clock.runFor(ms)`.

## Adding an app

1. `harness/apps/<app>.ts` exporting `start(ctx)`: build the env explicitly from `baseEnv(ctx.workDir)`, `ctx.newDb()` for a database, a `freePort()`;
   run migrate/seed with `runOnce`, serve with `startProcess` (waits for `/readyz`), read the setup code with `extractSetupCode`; return an `AppInstance`.
2. Add the name to `AppName` in `harness/fleet.ts` and the starter to `harness/apps/index.ts`.
3. `tests/<app>.spec.ts` as above. If the app needs another app (as ShopFront needs PayBroker), call `ctx.fleet.get(...)` in the starter.

## Screenshots

Each run writes `screenshots/<app>/<screen>-<light|dark>-<desktop|phone>.png`. The folder is git-ignored except `.gitkeep`; a small curated set
is committed with `git add -f` for use in READMEs. Take care that a screen never shows a generated key: use the `hide` option of `snap`.

## CI

`.github/workflows/e2e.yml` (manual `workflow_dispatch` and nightly) checks out all sibling repositories into the layout above, installs them with uv,
installs Postgres 17 + pgvector and Chrome on the runner, runs the suite, and uploads the Playwright report and screenshots as artifacts.
Private repositories need the secret `E2E_REPOS_TOKEN`. The workflow has not been run on GitHub yet.
