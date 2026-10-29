# Component catalogue: what you can take from the AgentPG ecosystem without taking an app

The five apps (TrustDesk, PagerLens, CityPulse, PayBroker, ShopFront) are templates under the Apache License 2.0, and their building
blocks can be used alone. This is the honest list of those blocks, checked against the code on 2026-10-29. Every row says where the
code lives, how to use it on its own, what lifting it out costs today, and one of three statuses:

| Status | Meaning |
|---|---|
| **library** | Importable today from the shared kit `agentpg-kit` (import name `apgkit`, Apache-2.0). `pip install`, import, configure by environment. |
| **service** | Runs alone today as its own process with only a Postgres behind it. |
| **copy-the-pattern** | Good code that lives inside an app. It is not a package. Either one self-contained file you copy as is ("file lifts as is"), or a design you re-implement ("entangled"). The row says which, and where the code is. |

Anything that is specific to one app is listed at the bottom as **app-specific**, so it is not mistaken for a component.

**Install the kit** (from a clone, or from a release tag):

```bash
uv pip install ../agentpg-kit                                                    # a sibling checkout
pip install "agentpg-kit @ https://github.com/elchinoo/agentpg-kit/archive/refs/tags/v0.3.1.tar.gz"   # a release tag
```

The kit is 2078 lines of Python plus 762 lines of shared UI (v0.3.1). Its tests are in `agentpg-kit/tests` (six files for the Python
modules, seven browser tests in `tests/ui`).

## Identity and access

| Name | Where | What it does | Use it alone | Effort | Status |
|---|---|---|---|---|---|
| Authentication and authorization | `apgkit.auth`: `AppAuth`, `Principal`, `LocalAuth`, `Authenticator`, `TokenVerifier`, `RelyingParty`, `TokenSource`, `from_env` | An app declares permissions; roles (people) and scopes (services, API tokens) map onto them; `auth.require("x:y")` protects a route and accepts a session cookie, a local API token, the static key or a federated JWT. Verification is strict: RS256/ES256/EdDSA only, issuer, audience and expiry checked, one rate-limited key refresh. | `from apgkit.auth import ...`, a Postgres for sessions and tokens, `<APP>_OIDC_ISSUER` to turn federation on | minutes | library |
| Password hashing, failed-attempt limiter | `apgkit.passwords`, `apgkit.ratelimit` | scrypt hashes with a strength check; an in-memory limiter by client (the authenticator answers 429 after ten failures in a minute) | import | minutes | library |
| Identity (OIDC provider) | `identity` repository | OpenID Connect and OAuth 2.0: authorization code with mandatory PKCE, rotating refresh tokens, client credentials for services, discovery, JWKS, introspection, revocation, per-app roles in the token, groups, Google and GitHub sign-in, RS256 keys stored encrypted and rotated from the console | `identity start` with `DATABASE_URL`, `IDENTITY_PUBLIC_URL`, `IDENTITY_ENCRYPTION_KEY`; any other OIDC provider works if it sends the roles the app expects | minutes | service |

## Data and Postgres

| Name | Where | What it does | Use it alone | Effort | Status |
|---|---|---|---|---|---|
| Database access | `apgkit.db.Database` | Connections that retry with backoff while a scaled-to-zero database wakes, an optional pool (fail-fast option), the pgvector adapter registered once the extension exists | `Database()` reads `DATABASE_URL` or the `PG*` variables | minutes | library |
| Migrations and settings | `apgkit.migrate`, `apgkit.settings_store` | Plain numbered `.sql` files, one migration table per app, `after_migrate` hooks; a key/value settings table | `migrate.run(db, Path("migrations"), table="app_migrations")` | minutes | library |
| Config helpers | `apgkit.config`, `apgkit.ids` | `read_secret` (env, or a file named by `<ENV>_FILE`), `database_url`, `env_bool`, ids and HMAC helpers | import | minutes | library |
| Accent-folded fuzzy text search | `trustdesk/src/trustdesk/textsearch.py` (78 lines, imports only `re`) and `migrations/007_trigram_search.sql` (`td_fold`, trigram indexes) | `fuzzy_hits` over any table with pg_trgm and an immutable unaccent wrapper | copy the file and the migration, call `fuzzy_hits(conn, table, id_col, [cols], text)` | minutes | copy-the-pattern (file lifts as is) |

## AI access

| Name | Where | What it does | Use it alone | Effort | Status |
|---|---|---|---|---|---|
| OpenAI-compatible client and SSRF guard | `apgkit.llm`, `apgkit.netguard` | Chat client that guards the URL, retries, never follows redirects, bounds the response, redacts keys, and explains an empty answer (`finish_reason`, a thinking model out of tokens). `NetGuard` refuses private, loopback and link-local addresses, embedded credentials and plain http to public hosts | `LLMClient(NetGuard("MYAPP_ALLOW_PRIVATE_URLS")).chat(ChatConfig(...), messages)` | minutes | library |
| LLM gateway | `llm-gateway` repository | One OpenAI-compatible door to models: per-app clients with revocable keys, requests-per-minute and monthly token budgets (hard or soft), aliases that map to ordered provider targets, failover on 5xx and 429, usage counts only (no prompts stored), admin console, built-in echo model that needs no network | `llmgw migrate`, `llmgw serve` with `DATABASE_URL`, `LLMGW_ENCRYPTION_KEY`; apps send `Authorization: Bearer lg_...` to `/v1` | minutes | service |
| Ask loop with verified citations | `pagerlens/src/pagerlens/ask.py` (670 lines): `run_turn`, `verify_citations`, `fence`, `RateLimiter`, `Conversations` | A bounded tool-calling loop whose answer must cite things it actually read; unverified citations are named with a reason | read it for the design; a reusable form needs a registry of citation kinds and tools | hours to a day | copy-the-pattern (entangled with PagerLens tables) |
| Verified-quote checking | `trustdesk/src/trustdesk/validator.py` (`_norm`, `quote_in`, `parse_verdict`, about 90 lines of pure functions) | A quote counts only if it appears word for word in the cited passage; a verdict with no verifiable basis is downgraded to "needs info" | copy the functions and feed them your passage dicts | an hour (the module imports the whole app, the functions do not) | copy-the-pattern |
| Hybrid search (RRF) | `trustdesk/search.py`, `pagerlens/retrieval.py`, `shopfront/search.py` | Reciprocal-rank fusion of pgvector, full-text and trigram results in one SQL statement; each part is skipped if its extension is missing | copy the SQL template and change table and columns | hours | copy-the-pattern (written three times: a kit module is the obvious next step) |
| Typo-tolerant "did you mean" | `shopfront/search.py` `suggest()` | Corrects each word against the shop's own vocabulary (levenshtein and dmetaphone, or trigram), restores accents, and suggests only if it matches more products | copy with your table and columns | hours | copy-the-pattern |

## Security and governance

| Name | Where | What it does | Use it alone | Effort | Status |
|---|---|---|---|---|---|
| Vault for stored secrets | `apgkit.vault.Vault` | Envelope encryption: a random data key (Fernet) wrapped under the admin password and, optionally, a platform secret; locks and unlocks; can start from the platform secret alone for SSO-only deployments; a rotated platform key is re-wrapped on the next password unlock | `Vault("MYAPP")`, `initialize`, `encrypt`, `decrypt` | minutes | library |
| Security middleware | `apgkit.guard.SecurityGuard` | Pure ASGI: nosniff, frame denial, no-referrer, no-store, a strict CSP on every response; body cap (also for chunked bodies); a CSRF header for cookie-authenticated browser writes; request metrics | `app.add_middleware(SecurityGuard, csrf_header="x-myapp-csrf", observe=metrics.observe)` | minutes | library |
| Masking | `apgkit.masking`: `mask_view`, `deny_tables`, `drift`, `reassert_forever` | Takes the base table away from the read-only database role and grants a security-barrier view of masked columns instead; re-applies the policy in a loop because new grants reopen tables | `mask_view(conn, "person", "person_masked", {...})` where the role `a1p_readonly` exists | minutes | library |
| Hash-chained audit ledger | `paybroker/src/paybroker/migrations/007_audit_ledger.sql` (core Postgres only) with `audit_append` and `audit_verify_detail()` | Append-only chain written in the same transaction as the change; update, delete and truncate refused; an owner who switches triggers off is detected, and the first broken entry is named | `psql -f 007_audit_ledger.sql`, then `SELECT audit_append(...)` | an hour to make the payload guard generic | copy-the-pattern (SQL file lifts as is) |
| Webhook signing and verification | `shopfront/src/shopfront/webhooks.py` (stdlib only), `paybroker/webhooks.py` `sign` | HMAC-SHA256 over `timestamp.body`, constant-time compare, five-minute tolerance, stable error codes | copy the file | minutes | copy-the-pattern (file lifts as is) |
| Row-level security by tenant | `paybroker/migrations/008_row_level_security.sql`, `paybroker/db.py` | Forced policies keyed by a session setting, fail closed, connection returned to the pool with the setting cleared | adapt the SQL template; `db.py` is nearly generic | hours | copy-the-pattern |
| Idempotency keys | `paybroker/payments.py` `authorize` | `ON CONFLICT DO NOTHING`, compare the request hash, replay or refuse | about fifteen lines to copy | minutes | copy-the-pattern |
| Propose-and-approve tuning | `pagerlens/src/pagerlens/tuning.py` | An allow-list parser (only `CREATE INDEX` and `CREATE STATISTICS` on public), proposals, approval, apply with advisory lock and timeouts, before and after cost; apply is off unless enabled | `parse_statement` is pure; the rest needs the two tables of migration 006 | one to two hours | copy-the-pattern |

## Observability and tuning

| Name | Where | What it does | Use it alone | Effort | Status |
|---|---|---|---|---|---|
| Insights | `apgkit.insights` and `static/insights.js` | Query insights from `edb_stat_monitor` or `pg_stat_statements`, tuner settings from `edb_pg_tuner`, index suggestions from `query_advisor`; each source degrades to `{"available": false, "reason": ...}` | `insights_payload(conn)` behind an endpoint, `initInsights(root, api)` in the page | minutes (the EDB extensions need an EDB Postgres) | library |
| Shared metrics | `apgkit.metrics.Metrics` | The same metric names in every app: `http_requests_total`, `http_request_duration_seconds`, `app_info`, `db_up`, insights and masking gauges | `Metrics("myapp", version)`, a `/metrics` route | minutes | library |

## Web and UI

| Name | Where | What it does | Use it alone | Effort | Status |
|---|---|---|---|---|---|
| Shared UI | `apgkit/static` served at `/kit/`: `base.css`, `ui.js`, `api.js`, `session.js`, `insights.js` | Light and dark theme, tables, badges, toasts, loading and error states, accessible `confirmDialog` and `promptDialog` (no native dialogs), session bootstrap with unlock banner; contrast and accessibility are tested | `mount_kit_static(app)`, then `import ... from "/kit/ui.js"` | minutes | library |

## Deployment and operations

| Name | Where | What it does | Use it alone | Effort | Status |
|---|---|---|---|---|---|
| Per-app conventions | every app and service: `Dockerfile`, `/healthz`, `/readyz`, numbered migrations, `/metrics` (needs a permission) | The same operational surface everywhere, so any component or app deploys on AgentPG the same way. The three-DSN variable fallback and `<ENV>_FILE` secrets come from the kit | copy the pattern from `docs/examples/notes` | minutes | copy-the-pattern |
| New app from the blocks | `agentpg-kit/docs/examples/notes` (203 lines of app, 41 of wiring, 8 of SQL, 69 of JavaScript) | A complete app: first-run setup, login, API tokens, encrypted private notes, CSRF, metrics, health | `uv pip install -e docs/examples/notes`, `notes` | minutes | copy-the-pattern |
| ecoctl | `ecosystem/src/ecoctl` | One manifest, one command to create and wire a fleet on AgentPG; dry run first | internal tool; `ecoctl/agentpg.py` (stdlib only, injectable runner) lifts as a file | minutes for the wrapper | copy-the-pattern |
| Ansible recipe and modules | `ecosystem/ansible`: modules `agentpg_instance`, `agentpg_sandbox`, `agentpg_extension`, `module_utils/eco_agentpg.py` | Idempotent, resumable, check-mode aware steps; `eco_target` is `agentpg` or your own hosts | the three modules are thin; the playbooks are specific to this fleet | hours to package as a collection | copy-the-pattern |

## Patterns inside the apps

| Name | Where | What it does | Effort | Status |
|---|---|---|---|---|
| Live updates with Postgres `tcn` | `pagerlens/live.py` (broadcast hub), `shopfront/live.py` (per-order hub), `trustdesk/live.py` | A LISTEN connection feeding server-sent events, no message broker | one to two hours to merge the three copies into one kit module | copy-the-pattern (three copies, two shapes) |
| PostGIS areas, Nearby and boundaries | `CityPulse/src/citypulse/geo.py`, `boundaries.py` | Built-in, real-boundary and derived areas (convex hull plus buffer), nearest sensors by distance in metres. `boundaries.py` is stdlib only | hours (tied to the `sensor` and `district_area` tables) | copy-the-pattern |
| One SQL statement over five data models | `CityPulse/explain.py` | Relational, time-series, JSONB, PostGIS and pgvector fragments in one statement; a missing extension gives NULL and a reason | hours | copy-the-pattern |
| Pattern memory with pgvector | `CityPulse/memory.py` | A generated vector column from an array, an HNSW index, graceful fallback to a plain array | hours | copy-the-pattern |
| Real-city placement | `CityPulseSimulator/src/cpsim/realcities.py` (stdlib only) | Deterministic sensor positions inside real commune outlines: near main roads, never in water. Data: Brussels from OpenStreetMap (ODbL), Vienna from data.wien.gv.at (CC BY 3.0 AT), attribution required | minutes | copy-the-pattern (file lifts with its data) |
| Incident memory | `pagerlens/retrieval.py` | Similar resolved incidents by pgvector cosine distance | about 30 lines of SQL | copy-the-pattern |

## App-specific, not components

TrustDesk's validation orchestration and prompts, PagerLens's investigator rules and hypothesis list, CityPulseSimulator's workload kinds
and HR generator, `ecoctl plan.py` and the manifest's contents, the Ansible playbooks and roles. They show how to wrap a solution around
the blocks above; they are not meant to be lifted.

## Findings from the inventory

- Reciprocal-rank-fusion search is written three times and the `tcn` hub three times: the next kit modules.
- The Ansible `eco_agentpg.py` repeats `ecoctl/agentpg.py`; a test keeps the two in step.
- CityPulseSimulator's `LICENSE` is MIT while its `pyproject.toml` says Apache-2.0; the other nine repositories are Apache-2.0.
- Map data in `CityPulse/geodata` and `CityPulseSimulator/geodata` is not Apache: ODbL (OpenStreetMap) and CC BY 3.0 AT; keep the attribution files.

## Proof

`ecosystem/docs/video-components` runs the library and service rows above for real in a throw-away environment and records the output:
install from the sibling checkout, protect a route with `Authenticator.require`, accept a token from the real identity service,
vault round trip, wake-up retry, migrations and pgvector, the LLM client against the real gateway, the security middleware, masking,
the audit ledger, webhook verification and real-city placement. See its README.
