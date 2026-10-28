# EDB and AgentPG capabilities: what we use, what we can use, what is blocked

> Probed on 2026-10-28 against the throwaway instance `eco-aidb-spike` (Postgres 18, instance login role without superuser, AgentPG CLI as logged in with the account owner). Every statement below was observed, not assumed. The spike instance was emptied afterwards.

## 1. Where we stood (before this work)

The apps used `vector` (HNSW cosine indexes, `<=>` search), `pg_trgm` (GIN trigram, `word_similarity`) and plain Postgres full-text search. On the platform they used instances, sandboxes, image deploys, environment injection and the CLI's extension install. `pg_stat_statements` was installed and unused; `aidb` was never used. The apps therefore showed Postgres and pgvector more than EDB or AgentPG.

## 2. Extension probe (instance role, no superuser)

| Extension | Result | Notes |
|---|---|---|
| pg_stat_statements 1.12 | works | installed by default |
| edb_stat_monitor 2.1 | works | bucketed metrics, plans (`query_plan`), `client_ip`, errors (`elevel`, `sqlcode`) |
| edb_pg_tuner 1.3.1 | works | `edb_pg_tuner_recommendations()` returned shared_buffers, effective_cache_size, maintenance_work_mem |
| query_advisor 1.2 | works | feeds `agentpg recommend` (returned a real index suggestion with size and cost reduction) |
| sql_profiler 4.2 | installs | only `sp_profiler_version()` visible: not usable yet |
| postgis 3.6.3 | works | `st_distance(geography)` Paris to Lyon = 391 km |
| unaccent, citext, fuzzystrmatch | work | `unaccent('café crème')` = `cafe creme`, `levenshtein`, `soundex`, `dmetaphone` |
| tcn | works | `triggered_change_notification()` present (row-change NOTIFY) |
| btree_gin, btree_gist, cube, tsm_system_rows, tsm_system_time, pg_trgm, vector | work | |
| aidb 7.8.0 | installs, unusable | the role has no USAGE on schema `aidb` (grant still missing) |
| system_stats 3.0 | installs, unusable | `permission denied for function pg_sys_cpu_info` |
| address_standardizer (+_data_us) | installs, unusable | `permission denied for table us_lex` |
| data_redaction, pg_prewarm, pgstattuple, pgrowlocks, password_profile, sslinfo, earthdistance, pg_surgery | refused | `Must be superuser to create this extension` |
| uuid-ossp | refused by the CLI | the name with a hyphen is rejected as invalid |

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
2. Allow, or pre-install for our instances, the superuser-only extensions we want: `data_redaction`, `pg_prewarm`, `pgstattuple`, `pgrowlocks`, `password_profile`, `sslinfo`, `earthdistance`, `pg_surgery`; and grant execute on `system_stats` functions and read on the `address_standardizer` lookup tables.
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
