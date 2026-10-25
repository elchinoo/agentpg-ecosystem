# State of the projects

Report date: 2026-10-25. Written from the repositories' READMEs and code, the live deployment checks done in this project, and the test suites.
Everything marked **verified** was observed (a test passed, an HTTP check returned, a log line was read). Everything marked **not verified** was built but never observed in the real environment.

## 0. Update, 2026-10-25 (evening): what changed since this report was written

First real-browser pass done (Chrome, signed in through the identity provider): TrustDesk, PagerLens, CityPulse, Simulator, PayBroker and the gateway render and work; identity's console was not opened (it needs the admin password).

**Fixed and deployed**
- PayBroker: the AI triage no longer floods the gateway (20 calls a minute, 120 a day, grouping of cases from one device or IP, 429 backoff with a circuit breaker; all four settable in Settings; card on the Settings page verified live). Cause of the earlier budget burn: no throttle, plus the shared client's hidden retries (each failed triage was 4 gateway requests). Data reset done (117,102 payments, 10,312 cases cleared). Per-merchant browser CORS for card tokenization and a signed 3-D Secure return URL added (for ShopFront).
- PagerLens: retire a service, bulk resolve, auto-resolve of recovered incidents, stale badge (verified live: `citypulse-fleet-default` retired).
- Simulator: cities, workloads and company runs that were running resume after a restart (verified live: a recreate logged `resumed city gotham` and `metropolis` with no clicks).
- TrustDesk: the environment account is the default for validation and embeddings, failed validations retry with backoff (limit editable), the demo-policy seeding order bug is fixed.
- CityPulse: durations humanized, status thresholds documented, loading and error states on first paint.
- agentpg-kit 0.2.3 (unlock banner keeps typed text, loading and error helpers); CI in the six app repos now runs the database tests on a pgvector service (it caught a real race in the resume test, fixed).

**Open, found in the browser**
- The model provider answers HTTP 402 to real-size chat requests (gateway: `upstream_misconfigured`); most likely OpenRouter credit or the per-request token ceiling. Until it is fixed, TrustDesk validation, PagerLens AI methods and PayBroker AI triage cannot complete. Needs a look at the provider account.
- AIDB 7.8.0 installs on an AgentPG instance but the instance login role cannot use it (no `aidb_users` grant); needs the platform team.
- PagerLens shows 48 open incidents that are real alerts from the cities' restarts; auto-resolve (30 minutes healthy) should clear them.
- Native browser pop-ups (`confirm`) remain in the apps; a kit helper is being built.
- ShopFront (web shop on PayBroker) is being built.

## 1. The ecosystem in one page

A set of sample applications, all on **Postgres + pgvector**, built to demonstrate **AgentPG** (sandboxes, instances, extensions, scale-to-zero, public HTTPS) and **EDB AIDB**. They share one library, one identity provider and one LLM gateway, and one tool (`ecoctl`) deploys the whole fleet.

```
                         ┌──────────── identity (OIDC: one sign-in, roles per app) ────────────┐
                         │                                                                      │
 CityPulse Simulator ──▶ CityPulse ──alerts/metrics──▶ PagerLens ◀──MCP── your editor / Claude  │
   (cities, companies,      (sensor platform,            (incident investigation, memory)       │
    workloads)               status engine)                    │                                │
        │                                                      ▼                                │
        ├──────────────▶ PayBroker (payments, 3-tier fraud) ──▶ LLM gateway ◀── TrustDesk ──────┘
        └──────────────▶ TrustDesk (policy RAG, request pre-validation)   (keys, aliases, budgets, usage)
                         all built on agentpg-kit (apgkit); all deployed by ecoctl
```

| Project | One-line purpose | Repo | Tests* | Deployed | Version/commit |
|---|---|---|---|---|---|
| **agentpg-kit** | Shared building blocks (`apgkit`): config, db, vault, auth/OIDC, LLM client, middleware, shared UI | `elchinoo/agentpg-kit` | 45 | library (v0.2.2) | `412791e` |
| **identity** | Our own OIDC provider for the fleet | `elchinoo/identity` | 38 | yes | `4f71159` |
| **llm-gateway** | One OpenAI-compatible endpoint, provider keys in one place, per-app budgets and audit | `elchinoo/llm-gateway` | 73 | yes | `c4be8fd` |
| **TrustDesk** | Compliance workspace: AI pre-validates employee requests against company policies | `elchinoo/trustdesk` | 129 | yes | `4b46811` |
| **PagerLens** | Incident-investigation agent: ranks causes with evidence, remembers confirmed causes | `elchinoo/PagerLens` | 158 | yes | `66410b4` |
| **CityPulse** | Smart-city sensor platform with a status engine and a burst button | `elchinoo/CityPulse` | 105 | yes | `30c2faf` |
| **CityPulse Simulator** (`cpsim`) | Field side: cities of simulated sensors, plus load generators ("workloads") for the other apps | `elchinoo/CityPulseSimulator` | 242 | yes | `f7ba488` |
| **PayBroker** | Payment broker with smart routing and a 3-tier fraud engine | `elchinoo/paybroker` | 169 | yes | `fcef686` |
| **ecoctl** | Provisions and operates the whole fleet from one manifest | `ecosystem/` | 92 | tool (local) | `7383c45` |

\*Test functions counted statically (`def test_*`); parametrised cases make the executed number higher. All suites passed at their last run in this project (ecosystem: 127 passed, 1 skipped).

**Fleet health (last check, after the final recreate):** identity, TrustDesk, PagerLens, CityPulse, CityPulse Simulator, PayBroker and the gateway all answered `health 200` and `ready 200` through `ecoctl status`. Auto-pause is **off** for the whole fleet.

**Current public addresses** (they change whenever an app is recreated; `ecoctl status` always prints the live ones):

| App | URL |
|---|---|
| PagerLens | https://app--a1p-prod-sbx-d0f1c2b24abf--brzxtjgdxjm5.code.run |
| TrustDesk | https://app--a1p-prod-sbx-2135bce9a532--brzxtjgdxjm5.code.run |
| CityPulse | https://app--a1p-prod-sbx-d8aec408449e--brzxtjgdxjm5.code.run |
| CityPulse Simulator | https://app--a1p-prod-sbx-e8efa5be135e--brzxtjgdxjm5.code.run |
| PayBroker, LLM gateway, identity | unchanged since the last report; read them with `ecoctl status` |

## 2. What the apps have in common

- **Stack:** FastAPI, psycopg 3, pgvector, plain-SQL migrations (no ORM), MCP over streamable HTTP, a strict CSP (no inline script or style), a CSRF header on browser writes.
- **Sign-in:** every app works **standalone** (one-time setup code and an admin password) or **federated** with `identity` (OIDC code flow with PKCE, roles per app). Services call each other with service tokens scoped to one audience.
- **Secrets:** API tokens stored as hashes and shown once; provider keys encrypted by a vault (data key wrapped under the admin password and/or a platform secret so a restart can unlock itself).
- **Observability:** the same Prometheus families on every app (`/metrics`, token-gated) plus app-specific ones.
- **Every app is an MCP server** at `/mcp` (read tools) so an assistant can use it.
- **State lives in Postgres**, so a sandbox can be recreated without data loss. The URL contains the sandbox id, so a recreate means a new URL; `ecoctl` and the simulator's fleet references follow it.

## 3. Project by project

### 3.1 agentpg-kit (`apgkit`)
**Objective:** stop copying the same vault, auth, middleware and UI into every app.
**What it provides:** `config` (secrets from env or `<ENV>_FILE`, the DSN variants AgentPG sets), `db` (retries while a scaled-to-zero database wakes, pgvector adapter), `migrate` (per-app migration table), `settings_store`, `passwords` (scrypt), `vault` (envelope encryption), `ratelimit`, `netguard` (SSRF guard), `llm` (OpenAI-compatible client: guarded, retried, bounded, key-redacting; explains *why* a chat answer was empty, for example a thinking model that spent its budget on reasoning), `guard` (security middleware), `metrics` (shared metric names), `auth` (`AppAuth`, `LocalAuth`, `TokenVerifier`, `RelyingParty`, `TokenSource`, `Authenticator`), and a shared UI served at `/kit/`.
**How to use:** `pip install` a tagged release (apps pin `>=0.2.x`); `uv pip install -e ../agentpg-kit` for local work. **Known issue:** the vault-unlock banner can redraw while someone types (lives in the kit; not fixed).

### 3.2 identity
**Objective:** one sign-in and one place that says who may do what, for every app.
**Capabilities:** OIDC and OAuth 2.0 (Authlib grant logic): authorization code with mandatory PKCE (S256), rotating refresh tokens with reuse detection, `client_credentials` for services, discovery, JWKS, userinfo, introspection, revocation, RP-initiated logout. Users, groups and roles **per app**; federation with Google and GitHub (invite-only by default); RS256 signing keys generated and encrypted here, rotated from the console (old key published 24 h); audit trail; launcher page listing the apps.
**Operate:** `/admin` with the setup code; register an app (client id, kind `web`/`public`/`service`, exact redirect URIs, roles); give people roles directly or via groups. `ecoctl` does the registration for the fleet.
**Not built:** consent screens, MFA, SAML, SCIM, `prompt=login`, immediate access-token revocation (tokens live 10 minutes).

### 3.3 llm-gateway
**Objective:** apps never hold provider keys; each gets its own revocable, budgeted, audited key.
**Capabilities:** OpenAI-compatible `/v1/chat/completions`, `/v1/embeddings`, `/v1/models`; **aliases with failover** (an alias maps to ordered targets; next target on 5xx, 429, timeout or connection error); **clients** with allowed aliases, requests per minute, monthly token budget (`hard` refuses, `soft` flags), several keys, rotation with grace; metering per request (tokens incl. reasoning tokens, cost estimate, latency, error class, failovers; **no prompts or completions stored**); providers with encrypted keys, SSRF-guarded URLs, a Test button; a built-in `echo` provider for demos; per-target `extra_body` (used to switch Qwen's thinking off); dashboard, audit, Connect page, Prometheus metrics.
**Live configuration:** `chat-default` → Qwen (thinking off), `embed-default` → OpenAI embeddings at 1536 dimensions. Limits per app are set in `ecosystem.yaml` and compared/applied with `ecoctl llm sync`.
**Open item:** TrustDesk's hand-made gateway client differs from the manifest (1M soft budget and extra aliases versus 5M hard). Awaiting a decision (apply the manifest or change the manifest).

### 3.4 TrustDesk
**Objective:** show Postgres + pgvector + AI as a compliance workflow: load company rules, let applications send employee requests, have each request pre-validated against the documents with quoted evidence, and let a human decide.
**Capabilities:**
- **Requests** (19 types: leave, trips, expenses, remote work, overtime, equipment, other) with an AI recommendation (approve / reject / needs information), reasons, and policy passages whose quotes are checked word for word against the source. A person always decides; overriding the advice requires a written justification.
- **Client API** (`/api/v1`) for applications to create employees and send requests, with a token scope that cannot read advice or decide.
- **Knowledge base:** Markdown/text/PDF ingest with heading-aware chunking, versioned and idempotent; **hybrid retrieval** (vector + full-text, reciprocal rank fusion) with provenance; **Ask** (cited answers; an answer citing nothing is flagged); explainable search.
- **Reports:** overview, all requests, compliance by type, out of compliance, decisions against AI advice, expenses, leave, ageing, upcoming absences; tables and CSV.
- **MCP:** `search_knowledge`, `get_policy_passage`, `list_documents`; copy-paste setup for ten clients.
- Multiple provider accounts, embedding switch with background re-embedding, demo embedder that needs no credentials, a fictional "Acme Cloudworks" policy pack.
**Operate:** sign in (SSO or setup code), Documents → Load demo policies (the fleet sets `AUTO_SEED`), Settings → AI accounts (the gateway account is wired by `ecoctl`), create a client API token, send requests.
**Recent hardening:** a vector-size mismatch no longer crash-loops; it warns, falls back to text search, and re-embeds automatically up to 5,000 chunks (then shows a "needs re-embedding" banner).
**Not verified:** the "needs re-embedding" banner in a real browser; behavior with a large corpus.

### 3.5 PagerLens
**Objective:** show an AI agent that investigates incidents with evidence it can be held to, and learns from confirmed causes. It never changes your systems.
**Capabilities:**
- **Lab:** nine scripted failures (bad release, pool exhaustion, memory leak, dependency outage, traffic spike, expired certificate, three confusing mixes); the simulator writes telemetry through the real ingest path and hides the true cause.
- **Investigation:** bounded and read-only; each hypothesis cites observations that support or refute it; red herrings are called out. **Three methods:** Rules, AI (a model drives the same read-only tools), Rules + AI (reconciled; ungrounded model claims are discounted hard; cited observation ids are verified).
- **Collection:** push API, OpenTelemetry OTLP/HTTP (logs and metrics; traces acknowledged, not stored), and `pagerlens-agent` (HTTP probes, Prometheus scrape, sandbox-status).
- **Memory:** confirmed causes are embedded in pgvector and recalled for similar future incidents; `pagerlens eval` compares cold and warm runs. **Runbooks:** hybrid retrieval attached as manual steps.
- **Safety:** PII scrubbing of every log line before storage, tool results fenced as untrusted data, token scopes (`read`, `ingest`, `metrics`, `full`), optional SSO with roles.
**Recent fix:** the fleet's move from 768 to 1536 embedding dimensions made it crash on start (`expected 768 dimensions, not 1536`). It now resizes and re-embeds at startup (verified in the live log: runbook 12 and incident 6 re-embedded).
**Not verified:** AI/hybrid accuracy against a live model (`pagerlens eval --mode hybrid` has never been run; there are no AI accuracy numbers); the memory re-embed produces vectors from stored text and was not compared with the originals.

### 3.6 CityPulse
**Objective:** a monitored system worth investigating: thousands of sensors report through a real collection path and a status engine turns readings into a city-wide verdict.
**Capabilities:** push API, OTLP and `/metrics`; **27 sensor types**; **multiple cities** (a city id is required on every reading; no default city; delete a city with typed confirmation and a purge); districts come from the data; status engine every 10 s; **seven bursts** (`flood`, `noise`, `dropout`, `stuck`, `late`, `corrupt`, `slow_db`) that end by themselves; optional per-protocol breakdown; reports its health to PagerLens. Roles `api` (gateway + dashboard + engine), `sim`, `all`; the fleet runs `api`.
**Not verified:** the whole UI in a real browser; behavior with very large cities.

### 3.7 CityPulse Simulator (`cpsim`)
**Objective:** generate believable field data and load for the whole fleet.
**Capabilities:**
- **Cities:** industrial protocols (Modbus TCP, EtherNet/IP, EtherCAT, PROFINET, PROFIBUS) through an edge gateway over HTTPS; up to 10,000 sensors per city; random bursts per group; a **generator** that makes cities differ (3 to 12 districts with kinds and unique made-up names, sensor mixes, sizes, profiles, seeds).
- **Workloads** for **PayBroker, LLM gateway, PagerLens, TrustDesk, identity and CityPulse**, with schedulers, saved credentials in an encrypted store, and **fleet references** (`${CITYPULSE_URL}` and five more) so targets follow the fleet when an app is recreated.
- Metrics, optional self-report to PagerLens, headless CLI (`cpsim`).
**Not verified:** a full end-to-end PayBroker workload run against the live fleet with a saved merchant key; existing cities and workloads that hold literal URLs still need switching to fleet references.

### 3.8 PayBroker
**Objective:** show a payment API in front of several (simulated) processors with smart routing and a layered fraud engine, on Postgres only.
**Capabilities:**
- **API** (`/v1`): tokenize test cards, authorize, capture, void, refund, idempotency keys; **vault** for test cards only (reserved `999999xx` prefixes give simulators per-customer cards with chosen behavior); signed webhooks with retries; simulated 3-D Secure.
- **Routing:** score `W_auth × auth rate − W_cost × fee − W_latency × latency`, hard constraints first, cascade within 800 ms on timeout or 5xx.
- **Fraud, three tiers:** Tier 1 JSON rules (data, never code); Tier 2 XGBoost trees exported to JSON and walked in pure Python (17 features, reason codes; < 300 approve, 300 to 699 3-D Secure, ≥ 700 decline and open a case); Tier 3 LLM triage for flagged payments with pgvector nearest cases and a daily **rule suggestion** that does nothing until a person approves.
- **Feature store and outbox in Postgres** (`FOR UPDATE SKIP LOCKED`), no Redis or Kafka. Measured locally: fraud engine about 1.5 ms (p95 3 ms), full authorization about 5 ms (p95 9 ms).
- **Training pipeline:** optional capture of the 17 numbers per payment (no card, e-mail, IP, device or name), labels only from real outcomes (chargebacks, analyst decisions, optional aging), retention, CSV/NDJSON export, trainer and model upload; admin **data reset** (phrase `RESET PAYBROKER DATA`).
- Dashboard, MCP server with read-only tools, Prometheus, SSO with five roles.
**Honest limits:** the bundled model is trained on **synthetic** data (holdout PR-AUC 0.776, ROC-AUC 0.879) and only measures how well it fits that generator. The real-data fitting step (profile fitted to TabFormer, Sparkov or PaySim) is **not built**; it needs your dataset choice and a download location. The dataset adapters were never run on real files. The admin reset was never run on the live data (you must press it, simulator paused).

### 3.9 ecoctl (`ecosystem/`)
**Objective:** one file (`ecosystem.yaml`) describes the fleet; one command deploys and operates it.
**Capabilities:** `validate`, `plan` (shows every command and API call), `apply` (dry run) / `apply --execute`, `recreate <app>` (cascades to dependents), `status` (health, LLM client, one real LLM call, drift), `env`, `db`, `llm test`, `llm sync [--apply] [--prune-aliases]`. It creates database and sandbox per app, registers apps, roles, web and service clients in identity, creates groups and grants, generates secrets, wires the gateway (`llm:`), injects fleet addresses (`calls`, `uses`), and keeps state in `.ecosystem/state.json` (mode 0600, git-ignored; **the only copy of client secrets, database API keys, gateway keys and generated secrets: back it up**).
**Deliberately absent:** a `destroy` command.

## 4. Verification status

| Area | Verified | Not verified |
|---|---|---|
| Automated tests | All suites passed at last run | Executed numbers are higher than the static counts |
| Live fleet | All 7 apps health/ready 200; gateway wiring and thinking-off checked live; PagerLens migration seen in the container log | `ecoctl llm test pagerlens` after its last recreate; TrustDesk/PayBroker LLM rows in the last `status` (truncated output; an earlier test passed) |
| User interfaces | Fake-DOM regression tests for the typed-input-wiped bug class | **No UI has been viewed in a real browser** (the built-in browser refuses localhost and live hosts); Google/GitHub sign-in never tried |
| Data paths | Small-data paths | Large corpora, TabFormer-scale training, large generated cities |
| AI quality | Gateway empty-answer handling, Qwen thinking-off | PagerLens/TrustDesk/PayBroker answer quality against real models; `pagerlens eval --mode hybrid` never run |

## 5. Open items, in order of importance

1. **Verify in a real browser** every app's main flows (needs the live URLs opened by someone with a browser; I cannot do it from here).
2. **PayBroker:** press Settings → Maintenance → reset (simulator paused), then decide the real dataset to fit the generator to.
3. **TrustDesk gateway client drift:** `ecoctl llm sync trustdesk --apply` or change the manifest.
4. **Simulator:** convert saved literal URLs to fleet references; do the first PayBroker run with the saved merchant key.
5. **Security hygiene:** CityPulse client secrets were printed in this session once (rotate with `ecoctl apply --rotate`); the `keys/` folder in the project root holds plaintext key files (it is not inside any app repository; keep it that way).
6. **Kit bug:** vault-unlock banner redraw.
7. **Release hygiene:** apps pin different minimum kit versions (`>=0.2.0` to `>=0.2.2`), and CI in the app repos runs ruff and pytest but has no database service, type check, audit or browser tests (see document 4).
