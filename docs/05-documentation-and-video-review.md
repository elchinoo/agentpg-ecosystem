# Documentation and video scripts: review index

Status 2026-10-27. Nothing here is committed or pushed. Each project has `docs/GUIDE.md` (the one document for that project) and `docs/VIDEO-SCRIPT.md` (scene table: timestamp, on-screen action, narration, callouts, Playwright recording notes). No video has been recorded yet.

| Project | Guide | Script | Video subject | Length | Checked by running? |
|---|---|---|---|---|---|
| agentpg-kit | [GUIDE](../../agentpg-kit/docs/GUIDE.md) | [SCRIPT](../../agentpg-kit/docs/VIDEO-SCRIPT.md) | Build a new app in 10 minutes (uses the new `docs/examples/notes` app) | 6:44 | Yes: tutorial app run in a browser, 67 tests |
| identity | [GUIDE](../../identity/docs/GUIDE.md) | [SCRIPT](../../identity/docs/VIDEO-SCRIPT.md) | Own OIDC provider: sign-in, roles, service tokens, key rotation | 6:40 | Yes: sign-in flows, tokens, 40 tests |
| llm-gateway | [GUIDE](../../llm-gateway/docs/GUIDE.md) | [SCRIPT](../../llm-gateway/docs/VIDEO-SCRIPT.md) | Aliases, clients, budgets, failover | 6:30 | No (read from code) |
| TrustDesk | [GUIDE](../../trustdesk/docs/GUIDE.md) | [SCRIPT](../../trustdesk/docs/VIDEO-SCRIPT.md) | Requests, AI pre-validation, reports | 6:25 | Partly (97 tests, no UI run) |
| PagerLens | [GUIDE](../../pagerlens/docs/GUIDE.md) | [SCRIPT](../../pagerlens/docs/VIDEO-SCRIPT.md) | Incident triage | 6:00 | No |
| CityPulse | [GUIDE](../../CityPulse/docs/GUIDE.md) | [SCRIPT](../../CityPulse/docs/VIDEO-SCRIPT.md) | City sensor data, bursts, heatmap | 6:30 | No |
| CityPulse Simulator | [GUIDE](../../CityPulseSimulator/docs/GUIDE.md) | [SCRIPT](../../CityPulseSimulator/docs/VIDEO-SCRIPT.md) | Running simulated cities and workloads | 6:30 | No |
| PayBroker | [GUIDE](../../paybroker/docs/GUIDE.md) | [SCRIPT](../../paybroker/docs/VIDEO-SCRIPT.md) | Payments, 3-tier fraud scoring, 3DS, webhooks | 6:30 | No |
| ShopFront | [GUIDE](../../shopfront/docs/GUIDE.md) | [SCRIPT](../../shopfront/docs/VIDEO-SCRIPT.md) | Shop, checkout through PayBroker, assistant | ~6:00 | No |
| ecosystem / ecoctl | [GUIDE](GUIDE.md) | [SCRIPT](VIDEO-SCRIPT.md) | From zero to a running fleet, and how the apps interact | 7:15 | No |

## Fix list found while writing (not yet fixed)

Docs that disagree with code:
- PayBroker `API.md` and README sizing paragraph say triage cap 500; code is 120. Webhook retry text says about a day; code retries 30 s, 5 min, 30 min, 2 h, 6 h (about 8.6 h).
- ShopFront README claims `PG*` variables and a simulator link that the code does not have.
- CityPulse Simulator README names kit v0.2.0; it needs >= 0.2.5.
- `ecosystem/docs/DEPLOY.md`: stale counts and 768 vs 1536 (fixed 2026-10-27).
- identity Makefile comment says port 5435; command uses 5436. TrustDesk `pyproject` description is out of date.

Behaviour gaps:
- TrustDesk: the Ask tab is visible to approvers but its routes need admin; `kb:write` is declared and checked nowhere.
- PagerLens: per-account embedding models in Settings are ignored by retrieval; the alert API accepts an `investigate` field it ignores.
- identity: `IDENTITY_ALLOW_PRIVATE_URLS` and `IDENTITY_POOL_MAX` have no effect; no `db_up` metric; `auditor` role unusable; a non-UTF-8 database breaks admin login.
- Kit and all apps: a changed `<PREFIX>_ENCRYPTION_KEY` after the vault exists breaks auto-unlock (the kit only wraps once). Fix once in agentpg-kit.
- agentpg-kit: the `mcp` extra and "MCP helper" description have no code behind them.
- cpsim: no `llm: true`, so no gateway client for its `llmgw` workload; `reset-admin` leaves unreadable saved credentials; the PagerLens self-report ignores `CPSIM_ALLOW_HTTP_TARGETS`.
- ecoctl: `extensions: [aidb]` was rejected by the validator (fixed 2026-10-27; on 2026-10-28 the allow-list became exactly the set the platform installs, probed: `pgfs`, `anon`, `pgcrypto`, `hstore` and `uuid-ossp` are no longer accepted, refused and unusable ones say why; `apply` now adds missing extensions to existing instances).

Script risks to rehearse before recording:
- PagerLens: `bad_deploy` ranking and the "similar past incidents" result were never run; no live AI investigation is shown.
- TrustDesk: depends on live model output; the offered stand-in model does not exist in the repo.
- PayBroker: the risky-payment payload (expects 403 NEW_ACCOUNT) may shift with earlier traffic.
- llm-gateway: the 40-token budget tripping on the third request is from arithmetic, not a run.
- Selectors in all Playwright notes come from reading source except identity and the kit tutorial, which were driven.

Stray file: `ecosystem/.ecosystem/aidb-spike.dbkey` (git-ignored secret for the empty spike instance).
