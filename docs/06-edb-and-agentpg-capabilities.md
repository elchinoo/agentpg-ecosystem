# EDB and AgentPG capabilities: what we use, what we can use, what is blocked

> Probed in two rounds on 2026-10-28 (the first incomplete, see section 2) against the throwaway instance `eco-aidb-spike` (Postgres 18, instance login role without superuser, AgentPG CLI as logged in with the account owner). Every statement below was observed, not assumed. The spike instance was emptied afterwards.

## 1. Where we stood (before this work)

The apps used `vector` (HNSW cosine indexes, `<=>` search), `pg_trgm` (GIN trigram, `word_similarity`) and plain Postgres full-text search. On the platform they used instances, sandboxes, image deploys, environment injection and the CLI's extension install. `pg_stat_statements` was installed and unused; `aidb` was never used. The apps therefore showed Postgres and pgvector more than EDB or AgentPG.

## 2. Extension probe (instance role, no superuser)

The probe ran in **two rounds on 2026-10-28**. The first round was **incomplete**: it tried only 24 extensions and wrongly concluded that the rest were unavailable (several were removed from the manifest allow-list on that basis). The second round created **every extension the platform lists** with `agentpg extensions create <name>` on a throwaway instance (raw results: `videos/live-captures/extension-probe-2026-10-28.tsv`). The table below is the consolidated result, grouped by outcome. `ecoctl` validates `extensions:` against exactly these groups (`src/ecoctl/manifest.py`).

**Installs and works** (allowed in the manifest)

| Extension | Notes |
|---|---|
| pg_stat_statements 1.12 | installed by default (not created by `ecoctl`) |
| edb_stat_monitor 2.1 | bucketed metrics, plans (`query_plan`), `client_ip`, errors (`elevel`, `sqlcode`) |
| edb_pg_tuner 1.3.1 | `edb_pg_tuner_recommendations()` returned shared_buffers, effective_cache_size, maintenance_work_mem |
| query_advisor 1.2 | feeds `agentpg recommend` (returned a real index suggestion with size and cost reduction) |
| postgis 3.6.3 | `st_distance(geography)` Paris to Lyon = 391 km |
| postgis_raster, postgis_sfcgal, postgis_tiger_geocoder, postgis_topology | install |
| vector, pg_trgm, unaccent, citext, fuzzystrmatch | work: `unaccent('café crème')` = `cafe creme`, `levenshtein`, `soundex`, `dmetaphone` |
| tcn | `triggered_change_notification()` present (row-change NOTIFY) |
| btree_gin, btree_gist, cube, tsm_system_rows, tsm_system_time | work |
| pgcrypto, hstore, ltree, tablefunc, intarray, isn, lo, seg, dict_int | install |
| anon (PostgreSQL Anonymizer 3.2.2) | `anon.partial()` and `anon.hash()` work; `anon.fake_*()` fail with permission denied on its data tables; there is **no** `anon.start_dynamic_masking()` in this version. See section 4 |
| sql_profiler 4.2 | installs; only `sp_profiler_version()` is visible |
| pgfs | installed as AIDB's dependency |
| aidb 7.8.0 | installs, but the role has no USAGE on schema `aidb` (grants missing: section 6, request 1). Allowed so the install step is covered; no usable grants today |
| refdata, bluefin, autocluster | EDB-specific; install; **usefulness untested** |

**Installs but unusable** (the validator rejects them with a reason)

| Extension | Result |
|---|---|
| address_standardizer, address_standardizer_data_us | `permission denied for table us_lex` (lookup tables) |
| system_stats 3.0 | `permission denied for function pg_sys_cpu_info` |
| pgaa (EDB Analytics Accelerator) | installs with 29 functions and an embedded Seafowl engine, but `convert_to_analytics` and `enable_analytics_replication` fail with `bdr extension not found` (needs EDB Postgres Distributed), and its lakehouse and catalog features need an object store. We **reject** it at validate ("needs bdr (EDB Postgres Distributed) or an object store"): accepting it would let a manifest suggest acceleration that cannot run, which is the more surprising outcome. Move it to the allow-list when the platform offers either prerequisite |

**Refused: need superuser** (the validator says "needs superuser on this platform")

amcheck, autoinc, bloom, data_redaction, dblink, dict_xsyn, earthdistance, file_fdw, insert_username, intagg, moddatetime, pageinspect, password_profile, pg_buffercache, pg_freespacemap, pg_logicalinspect, pg_prewarm, pg_surgery, pg_visibility, pg_walinspect, pgrowlocks, pgstattuple, postgres_fdw, refint, sslinfo, xml2.

Exact error for `data_redaction` (the others read alike): `permission denied to create extension "data_redaction" ... Must be superuser to create this extension`.

**Refused by the CLI:** `uuid-ossp` (the name with a hyphen is rejected as invalid; use the built-in `gen_random_uuid()`).

**Streaming analytics.** `pgaa` is the EDB-native answer to a "streaming analytics" or analytics-acceleration question, but it is **not demonstrable on AgentPG today** (it needs bdr or object storage). The CityPulse recommendation therefore stays: native Postgres partitioning, BRIN indexes and rollup tables, no ClickHouse.

## 3. AgentPG platform features

| Feature | Status | Use |
|---|---|---|
| `agentpg recommend all|index|stats` | works with query_advisor | `ecoctl insights` |
| `agentpg db-users create --permission read|read_write` | works | read-only users for AI access (member of `a1p_readonly`) |
| `agentpg keys` | works | the instance's MCP keys (`default` is read_write); a key is **not** accepted as bearer on the hosted MCP endpoint |
| `agentpg token` | works | 30-minute JWT for the hosted MCP endpoint; **the session runs as `k_mcp_rw_*`, a read-write role (it could CREATE TABLE)** |
| Hosted MCP endpoint (per instance) | works | tools: `execute_sql`, `list_schemas`, `list_objects`, `get_object_details`, `explain_query`. Not read-only when reached with the JWT. |
| `agentpg mcp serve --config` (local or container) | works | `access_mode: restricted` needs `url` and `read_only_url`; runs as the read-only user; writes refused; HTTP mode has **no authentication** unless `A1P_JWT_SECRET` is set |
| `agentpg query "..."` | exists | natural language to SQL; not used yet |
| `agentpg memory --enable` | **not implemented** (`memory configurations are not yet implemented`) | |

## 4. Database-enforced masking without `data_redaction`

`data_redaction` is refused (superuser), but two kinds of building blocks exist. **Views and revokes** (below) enforce masking for read-only users. **`anon`** (PostgreSQL Anonymizer 3.2.2) is installable and gives usable functions: `anon.partial()` (keep a prefix and suffix, mask the middle) and `anon.hash()` can be used inside the masked view's select list instead of hand-written `left()` or `regexp_replace()`. Limits: `anon.fake_*()` fail (permission denied on its data tables), and **dynamic masking through `anon` is not available in this version** (no `anon.start_dynamic_masking()`), so the view and revoke recipe remains the enforcement mechanism; `anon` supplies only the masking expressions.

Verified through the restricted MCP server:

1. `REVOKE SELECT ON public.t FROM a1p_readonly;`
2. `CREATE VIEW public.t_masked WITH (security_barrier) AS SELECT id, left(name,1)||'***' name, regexp_replace(email,'^.*@','***@') email, '**** '||card_last4 card FROM public.t;`
3. `GRANT SELECT ON public.t_masked TO a1p_readonly;`

Result for a read-only user (and for an AI agent using AgentPG's restricted MCP): base table `permission denied`, masked view readable, writes refused. Gotchas: only the owner can revoke or grant; new tables get SELECT for `a1p_readonly` again by default privileges, so the masking must be reapplied after each migration; a multi-statement call runs as one transaction and rolls back entirely on any error. `agentpg-kit` provides this as `apgkit.masking`.

## 5. Decisions

- **MCP:** use AgentPG's MCP server for generic database access (schema, SQL, explain) in restricted mode with a read-only user and masked views; keep an application MCP only for domain actions (writes with our permission scopes, semantic search with our embedder, investigations). `ecoctl mcp ...` configures and checks it. The hosted JWT endpoint is not advertised as read-only.
- **Insights:** every app gets a Database insights page from the kit (edb_stat_monitor, edb_pg_tuner, query_advisor), and `ecoctl insights` runs `agentpg recommend` per instance.
- **Features by app:** CityPulse uses PostGIS; ShopFront and TrustDesk use unaccent and fuzzystrmatch where they help search; PayBroker uses fuzzystrmatch for a name-mismatch signal; TrustDesk and PagerLens use tcn for live updates; PayBroker, ShopFront and TrustDesk mask personal data for read-only access.

## 6. Requests to the platform team (draft, not sent)

1. **AIDB access (the blocker for the TrustDesk showcase).** `agentpg extensions create aidb <instance>` installs AIDB 7.8.0 and creates the roles `aidb_users`, `aidb_governance` and `aidb_admins`, but nobody can use it: our instance login role has no `USAGE` on schema `aidb`, and the MCP role (`k_mcp_rw_*`) is not a member either (checked 2026-10-28; neither is a superuser). AIDB's own install page says access is granted by membership, so the fix is one statement run by a platform superuser, ideally automatically when the extension is created:
   `GRANT aidb_users TO "<instance login role>";` and `GRANT aidb_governance TO "<instance login role>";`
   and, so that read-write database users and the hosted MCP session can use AIDB functions too, `GRANT aidb_users TO a1p_readwrite;` (and whichever group should read the governance traces). Whether `aidb_admins` is needed to create pipelines and knowledge bases is not documented on the pages we read: please confirm.
2. Allow, or pre-install for our instances, the superuser-only extensions we want (full list in section 2; the useful ones: `data_redaction`, `pg_prewarm`, `pgstattuple`, `pg_buffercache`, `pageinspect`, `pg_walinspect`, `postgres_fdw`, `dblink`, `password_profile`, `sslinfo`, `earthdistance`); grant execute on `system_stats` functions, read on the `address_standardizer` lookup tables and on the `anon` data tables (for `anon.fake_*()`); and make `pgaa` usable (bdr or an object store) so an EDB analytics acceleration demo is possible.
3. Security report: the hosted MCP session started with `agentpg token` runs as `k_mcp_rw_*` (read-write) although the tool description says restricted mode uses a read-only role; please confirm whether this is intended and offer a read-only hosted mode (and accept API keys or a scoped token for server-to-server use).
4. `agentpg extensions create uuid-ossp` rejects the hyphen.
5. `agentpg memory` is documented but not implemented.


## 7. Northflank primitives and what AgentPG exposes of them

AgentPG's URLs (`*.code.run`, `*.addon.code.run`) show it runs on Northflank. Northflank facts below come from its public documentation (2026-10-28, search results, not hands-on); what AgentPG exposes comes from the CLI (`agentpg sandbox create --help`). Whether AgentPG can expose more is the AgentPG team's call: this is a list of asks.

**What `agentpg sandbox create` exposes today:** image, compute plan (small to large, plus GPU plans), a linked instance, one public HTTPS port, auto-pause, plain `--env` variables, injected files (`--file`), command and entrypoint overrides, a console, logs, pause and resume. Nothing else: no secret groups, no jobs or cron, no volumes, no templates, no network policies.

| Northflank primitive | What it is | Which of our pieces it would help | Ask for AgentPG |
|---|---|---|---|
| [Secret groups](https://northflank.com/docs/v1/application/secure/manage-secret-groups) | Named collections of runtime variables or build arguments that services and jobs inherit, scoped to services, jobs, tags or environments; values can be overridden per service; secret files; enterprise accounts can bring their own KMS | The vault's platform key (`<PREFIX>_ENCRYPTION_KEY`) and the OIDC client secrets. Today they travel as plain `--env` values visible in the sandbox definition | Let a sandbox reference a secret group by name instead of carrying values; rotation then touches one place |
| [Templates (infrastructure as code)](https://northflank.com/docs/v1/application/infrastructure-as-code/template-nodes) | Declarative project definitions with services, jobs, addons and volumes, run in ordered workflows, stored in Git (GitOps), shareable | This is what `ecoctl` re-implements for AgentPG (the manifest, ordering, idempotent re-apply, extensions, wiring). A customer would not build `ecoctl`; they would want this from the platform | An `agentpg apply -f` for a multi-sandbox, multi-instance definition, with outputs (URLs) usable as inputs; `ecoctl` would shrink to the identity and gateway registration |
| [Jobs (cron and manual)](https://northflank.com/docs/v1/application/infrastructure-as-code/template-nodes) | One-off and scheduled workloads | The Simulator's runs, webhook retries and re-embedding. Sandboxes auto-pause when idle and do not wake on a request, which is awkward for workers | Scheduled and manual jobs next to sandboxes |
| [Ingress security policies](https://northflank.com/docs/v1/application/network/create-path-based-security-policies) | IP allow and deny lists, basic auth, path-based rules per port. **No ingress rate limiting** (load balancers do not provide it) | Keeping admin consoles (identity, gateway) off the public internet, basic auth for demos | Expose policies per sandbox port; keep rate limiting in the gateway and apps (it is not a platform feature there) |
| Volumes, addons | Persistent disks and managed databases | Not needed here: the instance is the database | none |

**What does not map to a primitive:** the identity provider. Northflank offers [SSO for logging in to Northflank itself](https://northflank.com/docs/v1/application/collaborate/manage-an-organisation) (SAML and OpenID Connect through WorkOS, an enterprise feature) and mentions workload identities in passing; I found nothing for authenticating the end users of an app you deploy, so identity stays reference scaffolding. The same for the LLM gateway: no equivalent primitive. A related opening: AgentPG's compute plans include GPUs (`gpu-l4`, `gpu-a100-*`), so a sandbox could host an OpenAI-compatible model server as the gateway's backend: untested.

Sources: Northflank docs pages linked above, and [northflank.com/features/templates](https://northflank.com/features/templates).


## 8. What we can use out of the box on AgentPG today

Checked on the real CLI and account (2026-10-28). "Used" means an app or `ecoctl` uses it now.

| AgentPG feature | State | Use |
|---|---|---|
| Instances (Postgres 18), `databases`, several databases per instance | works | Used: one instance per app. An instance can hold several databases, so a smaller footprint is possible if instance count becomes a limit |
| Sandboxes: image, plan, linked instance, one HTTPS port, auto-pause, `--env`, `--file`, command override, console, logs, pause, resume | works | Used by `ecoctl`. GPU plans (`gpu-l4`, `gpu-a100-*`) exist and are untried |
| `sandbox status` live metrics (CPU, memory, network in and out) | works | **Used: `ecoctl status`** (columns cpu, mem, net in/out, from `sandbox status --json`). Still open: feed them to PagerLens as real platform telemetry |
| Extensions through `agentpg extensions create` | works for 35 allowed, 26 refused for superuser, 4 installed but unusable (see section 2) | Used by `ecoctl apply` |
| `agentpg recommend` (index and statistics advice from `query_advisor`) | works | Used: `ecoctl insights` |
| `db-users` with `read` or `read_write` | works | Used: `ecoctl mcp users` creates the read-only user |
| `agentpg mcp serve` (restricted mode with a read-only user) | works | Used: `ecoctl mcp ...`. The hosted endpoint is not read-only (section 3) |
| `agentpg import` / `upload` (CSV only) | works | **Not used yet.** The way to load a large demo dataset (for example the PayBroker transactions) |
| `agentpg query "..."` (natural language to SQL) | exists | **Not tried.** It sends the question and schema to a model service we have not checked; try on demo data only |
| `keys` (instance MCP keys) | works | Not used by the apps |
| Platform keys (`platform-keys create`, account-wide) | exist, **the CLI has no documented way to use one** (no flag or environment variable; `agentpg login` is browser-only) | Blocks non-interactive use: `ecoctl` in CI, unattended re-runs |
| `agentpg memory` | **not implemented** | |
| AIDB | installs, **no access** (section 3 and request 1) | The TrustDesk centerpiece, blocked |

## 9. Suggestions for the AgentPG developers

Ordered by how much each one hurt while building and operating this fleet. Each item names the incident behind it.

1. **Stable addresses.** A sandbox URL contains the sandbox id, so every recreate changes the URL. For us that meant: identity's issuer changed and every app was recreated, OIDC redirect URIs, webhooks and CORS origins had to be re-registered (`ecoctl connect` exists only to repair this), and recorded demos and screenshots went stale. Ask: a stable per-sandbox hostname or custom domains (Northflank supports both), and private service-to-service addresses so apps need not call each other over the public internet.
2. **Non-interactive authentication.** Platform keys exist but the CLI cannot use one; `agentpg login` is browser-only and the session expired twice mid-work. Ask: `AGENTPG_API_KEY` (or `--api-key`), so `ecoctl` and CI can deploy unattended.
3. **AIDB access and the superuser-only extensions.** One grant (`GRANT aidb_users ...`) blocks the best EDB story; `data_redaction`, `pg_prewarm`, `pgstattuple` and 23 others are refused (section 2), and `pgaa` installs but needs bdr or object storage. Ask: grant AIDB roles automatically when the extension is created, and offer an allow-list of extra extensions to enable per instance (request 1 and 2 in section 6).
4. **Hosted MCP read-only mode.** The hosted MCP session started with `agentpg token` is read-write (it created a table), and keys are not accepted as bearer tokens. Ask: a read-only hosted mode and a scoped token for server-to-server use.
5. **Declarative multi-sandbox definitions** (Northflank templates). `ecoctl` is a small version of this: ordering, idempotent re-apply, wiring outputs (URLs) into other sandboxes' environment, extension lists. Ask: `agentpg apply -f`.
6. **Secret groups** instead of plain `--env`: rotate the platform key and OIDC secrets in one place and reference them by name; today a rotation means recreating every sandbox.
7. **Wake on request.** A paused sandbox answers an empty 503 and does not wake; we keep `auto-pause=false` everywhere. Ask: wake on the first request (scale to zero).
8. **Build from a repository.** We need a CI pipeline and a public container registry image for every app. Ask: build and deploy from a Git repository (Northflank build services).
9. **Jobs and cron** next to sandboxes (workers, the simulator, re-embedding).
10. **Ingress security policies** per port (IP allow list, basic auth) so admin consoles need not be public. Rate limiting is not a Northflank ingress feature, so the gateway and app limits stay ours.
11. **Smaller papercuts.** `extensions create uuid-ossp` rejects the hyphen; `a1p_readonly` receives SELECT on every new table by default privileges (an opt-out for masking would help); `connect --json` and `db-users create --json` field names are undocumented; `agentpg memory` is documented but not implemented; expose sandbox metrics in the API as a Prometheus endpoint.
