# Deployment, operations, integration and AI use

> **Dated report, 2026-10-25.** This is a snapshot of the deployment and operations as of that date and is not updated. The current reference is [GUIDE.md](GUIDE.md) (see its section on differences); where the two disagree, GUIDE.md wins.

Companion to [01-state-of-the-projects.md](01-state-of-the-projects.md). The click-by-click first deployment, with troubleshooting from the first live run, is [DEPLOY.md](DEPLOY.md); this document is the whole picture in one place and points to it for detail.

## 1. Deployment

### 1.1 What you need
- An **AgentPG account**, the `agentpg` CLI installed and logged in (`agentpg login`; tokens expire, and `ecoctl` fails with "Invalid or expired token" until you log in again).
- Permission to create **one instance (database) and one sandbox per app**: identity, llmgw, trustdesk, pagerlens, citypulse, cpsim, paybroker (7 + 7). Extensions are enabled per instance: `vector` and `pg_trgm` for TrustDesk and PagerLens, `vector` for PayBroker.
- **Public container images** at `ghcr.io/elchinoo/<image>` (each repo's GitHub Action "Build and publish image" builds on push to `main`; set the package public once). Image names: `identity`, `llm-gateway`, `trustdesk`, `pagerlens`, `citypulse`, `citypulsesimulator`, `paybroker`.
- A machine you trust to run `ecoctl` (secrets reach sandboxes as `--env` values and pass through that machine's process list for a moment), Python 3.11+ and `uv`.
- **Model access** for real AI: one provider key for chat (the fleet uses Qwen with thinking off) and one for embeddings (OpenAI `text-embedding-3-small`, 1536 dimensions). Without them the gateway's built-in `echo` provider keeps everything running with canned answers.

### 1.2 Deploy
```bash
cd ecosystem
uv venv && uv pip install -e ".[dev]"
.venv/bin/ecoctl validate            # check ecosystem.yaml
.venv/bin/ecoctl plan                # every command and API call; changes nothing
.venv/bin/ecoctl apply --execute     # asks first; safe to run again; resumes after an error
.venv/bin/ecoctl status              # health of each app, each LLM client + one real call, drift
```
Order is handled for you: identity first (the first request fixes its issuer), then callees before callers, then groups and grants. `apply` never rotates a secret unless you pass `--rotate`.

Stage it if you like (`--only identity,trustdesk,llmgw`, then the observability trio and simulator, then the rest; see DEPLOY.md section 5).

### 1.3 First sign-in
1. Read the identity setup code from its log, open `<identity>/admin`, set the admin password.
2. Add yourself to `ecosystem-admins` (grants `admin` in every app).
3. Open any app: it redirects to identity. In a fresh app, standalone mode still works with its own setup code if SSO is off.
4. Optional: add Google or GitHub under Providers (invite-only by default).

### 1.4 Configuration you will actually change
| Where | What |
|---|---|
| `ecosystem.yaml` → `agentpg.auto_pause` | `false` keeps the fleet up (current); `true` saves credit, then you resume with `agentpg sandbox resume <id>` and unlock vaults |
| `ecosystem.yaml` → `llm:` | gateway app, aliases, `embed_dimensions` (must equal the real embedding model's size), default rpm and budget |
| `ecosystem.yaml` → `apps.<x>.llm` | per-app limits (`rpm`, `budget`, `budget_mode`, `aliases`) |
| `ecosystem.yaml` → `apps.<x>.env` | non-secret environment (for example `CITYPULSE_ROLE: api`); the validator refuses names that look like secrets, use `generate:` |
| Gateway console | provider keys, alias → target mapping, `extra_body` per target |

**A sandbox's environment cannot change in place.** Change the manifest, then `ecoctl recreate <app>` (data stays in the database; the URL changes; dependents are recreated so they learn the new URL).

## 2. Operations

### 2.1 Daily commands
```bash
ecoctl status                     # fleet health, LLM clients, limits drift
ecoctl llm test <app>             # one tiny chat call with that app's own gateway key
ecoctl llm sync                   # compare gateway clients with the manifest (exit 1 on drift)
ecoctl llm sync <app> --apply     # make the gateway follow the manifest
ecoctl env <app>                  # the environment an app is created with (secrets masked; --reveal prints)
ecoctl db <app>                   # database connection details for a manual psql session
agentpg sandbox logs <sandbox-id> # container log (startup errors, setup codes)
agentpg sandbox resume <id>       # if auto-pause is on
```

### 2.2 Releasing a change to one app (the loop used throughout this project)
1. Commit in the app repo, push `main`.
2. Wait for the image build: `gh run list -R elchinoo/<repo> -w "Build and publish image"` until `success`.
3. `ecoctl recreate <app> --yes` (dependents are recreated too).
4. `ecoctl status`, read the app's log for the startup lines, open the app.
A sandbox can reuse a stale image after pause/resume; recreate is the reliable way to pull `latest`.

### 2.3 State, secrets and backups
- **`ecosystem/.ecosystem/state.json`** is the only copy of: identity client secrets (shown once), per-app **database API keys** (also the database password; AgentPG does not show them again), gateway client keys, generated encryption keys. Back it up somewhere private. Losing it means rotating (`apply --rotate`) and re-entering credentials.
- Vault behavior: each app's `<PREFIX>_ENCRYPTION_KEY` lets it unlock stored provider keys after a restart. Without it, a restart locks them until an admin signs in.
- Data lives in each app's Postgres. Back up with the platform's snapshot tools or `pg_dump` using `ecoctl db <app>`.

### 2.4 Monitoring the fleet
Every app exposes `/healthz`, `/readyz` and token-gated `/metrics`. CityPulse and the simulator report to PagerLens; the `pagerlens-agent` (registered, deployed elsewhere) probes and scrapes the rest. The gateway adds `llmgw_requests_total`, `llmgw_tokens_total{kind=prompt|completion|reasoning}`, `llmgw_failovers_total`, `llmgw_budget_rejections_total`, `llmgw_empty_content_total`.

### 2.5 Common problems
| Symptom | Cause | Fix |
|---|---|---|
| Empty `503` on an app URL | sandbox auto-paused | `agentpg sandbox resume <id>`; unlock the vault |
| `no healthy upstream` | the container is crash-looping | `agentpg sandbox logs`; the last one was PagerLens failing on a 768 vs 1536 vector size (fixed) |
| `model_not_allowed` (403) from the gateway | the app's client lacks the alias | `ecoctl llm sync <app> --apply` (also adds missing aliases) |
| "the chat endpoint returned an empty answer" | a thinking model spent its token budget on reasoning | the gateway alias target sets `extra_body` to turn thinking off; the message names `finish_reason` and reasoning tokens |
| App cannot reach a callee | the callee was recreated, URL changed | `ecoctl recreate <callee>` already cascades; check `ecoctl env <app>` |
| "Invalid or expired token" from `agentpg` | CLI login expired | `agentpg login` |
| PayBroker mass declines in simulation | card-velocity rule on shared test numbers | simulator workloads use `999999xx` prefixes and retire customers; see PayBroker README |

## 3. How the projects integrate

### 3.1 Identity: people
Every app registers as a **web** client in identity (client id = app key, exact redirect `https://<app>/auth/callback`, roles defined by the app). Browsers sign in with code + PKCE; apps verify the JWT locally against identity's JWKS (RS256/ES256/EdDSA only, issuer, audience, expiry checked; roles of other apps ignored). Roles map to permissions inside the app (`requests:decide`, `cases:resolve`, ...). Groups give roles across apps (`ecosystem-admins` → `admin` everywhere; `risk-team` → PayBroker `risk_manager` + PagerLens `viewer`; `support` → TrustDesk `approver`).

### 3.2 Identity: services
An app that calls another gets a second client `<key>-service` (`client_credentials`), restricted to the scopes listed under `calls`, one audience at a time. The callee must list the scope under `exposes` (typos fail at `validate`).

| Caller | Callee | Scope / credential | Purpose |
|---|---|---|---|
| CityPulse | PagerLens | `pagerlens.ingest` | send its own health and alerts |
| CityPulse Simulator | CityPulse | `citypulse.ingest` | send sensor readings |
| CityPulse Simulator | TrustDesk | `trustdesk.requests.write` | generate employee requests |
| CityPulse Simulator | PagerLens | `pagerlens.ingest` | self-report |
| CityPulse Simulator | LLM gateway | `llmgw.use` | LLM-driven workloads |
| CityPulse Simulator | PayBroker | merchant secret key (saved in its vault), address via `uses` | payment traffic |
| pagerlens-agent | PagerLens | `pagerlens.ingest` | probes and scrapes |
| TrustDesk, PagerLens, PayBroker | LLM gateway | `lg_...` key per app (not an identity token) | chat and embeddings |

### 3.3 Finding each other
Sandbox URLs change on recreate. `ecoctl` injects callee URLs as `<CALLEE>_URL` (or `url_env`) at creation, and recreating an app cascades to everything that names it. The simulator additionally accepts fleet references (`${CITYPULSE_URL}` ...) as targets, resolved at start, so saved cities and workloads follow without edits.

### 3.4 Data flows worth demonstrating
1. **Sensors → CityPulse → PagerLens:** the simulator drives a city; a burst degrades CityPulse; CityPulse's alert reaches PagerLens; PagerLens investigates with the evidence it was sent.
2. **Requests → TrustDesk:** the simulator (or your app) sends employee requests; TrustDesk retrieves policy passages, asks the model, returns advice with checked quotes; an approver decides.
3. **Payments → PayBroker:** the simulator sends payments; tiers 1 and 2 decide in about a millisecond; flagged payments open a case; the LLM triages; an analyst decides; chargebacks feed back.
4. **Everything → gateway:** every model call from the three AI apps passes through one place with budgets and per-app usage.

## 4. Using the integration with AI

### 4.1 The LLM gateway is the single door
Each AI app has an OpenAI-compatible "account": base URL `https://<gateway>/v1`, key `lg_...`, chat model `chat-default`, embedding model `embed-default`, embedding dimension 1536. `ecoctl` sets these as `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_CHAT_MODEL`, `EMBEDDING_MODEL`, `EMBEDDING_DIM`; apps need no code change to switch models. **To change the model for every app, edit the alias in the gateway.** The model name inside an app is an alias, never a provider's model name.

Gateway behaviors that matter: failover only on 5xx, 429, timeouts and connection errors (never on other 4xx); `hard` budgets refuse with 429, `soft` budgets flag; reasoning tokens counted separately; prompts and completions never stored; the real upstream model is recorded per request.

### 4.2 What the AI does in each app
| App | Embeddings (alias `embed-default`) | Chat (alias `chat-default`) | Guard rails |
|---|---|---|---|
| TrustDesk | document chunks and queries (hybrid with full-text) | request pre-validation, Ask | quotes verified word for word against the documents; answers that cite nothing flagged; human decides |
| PagerLens | runbooks and incident memory | AI and Rules + AI investigations (function calling over read-only tools) | read-only tools only; tool results fenced as untrusted; cited observation ids verified; ungrounded claims discounted; budget on rounds, calls, time |
| PayBroker | none: nearest past cases are found by the payment's own 17-number feature vector (`vector(17)`), not by text embeddings | Tier 3 triage and daily rule suggestions | strict output schema; suggested rules need human approval; the AI never decides a case; rule-based triage if no AI |
| Simulator | none | LLM-driven workload text where configured | none needed |

### 4.3 MCP: use the apps from an assistant
Every app serves MCP at `/mcp` (streamable HTTP, bearer token). TrustDesk: `search_knowledge`, `get_policy_passage`, `list_documents`. PagerLens: incident and evidence tools ("why is checkout failing?"). PayBroker: read-only investigation tools. Each app's Settings page has copy-paste setup for the common clients; create a **read** token for assistants (read tokens cannot change anything or spend provider credit).

### 4.4 AI safety properties common to all three
Provider keys never leave the gateway; per-app keys are revocable and budgeted; outbound URLs pass an SSRF guard on save and on every call; model output is validated against a schema and checked against real data before it counts; nothing the model says triggers an action without a person.

### 4.5 Known AI limits today
Quality against real models is **not measured** for PagerLens (AI methods) or TrustDesk pre-validation; PayBroker's model is synthetic-trained; a thinking model needs `extra_body` to keep answers short; `embed_dimensions` must be changed together with a re-embed (the apps now reconcile automatically, up to 5,000 chunks in TrustDesk).
