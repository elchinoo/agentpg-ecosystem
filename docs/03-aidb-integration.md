# Integrating the ecosystem with AIDB

> **Dated report, 2026-10-25.** This is a snapshot of the AIDB design as of that date and is not updated. The current reference is [GUIDE.md](GUIDE.md) (see its section on differences); where the two disagree, GUIDE.md wins.

Status of this document: a **design**, not an implementation. Nothing in the repositories uses AIDB today (a search of all repos finds no reference). The AIDB facts below come from EDB's current documentation (AIDB 7.8.0, released 12 Oct 2026, read on 2026-10-25). Statements about how our apps would use it are proposals to be proven in the spike in section 7. The AgentPG CLI's own help lists `aidb` ("AI embeddings and inference") among the extensions it can enable, but I could not check which AIDB version a sandbox's database offers (the CLI login had expired); that is the first thing to verify.

> **Update 2026-10-25:** `agentpg extensions list <instance> -s aidb` shows AIDB **7.8.0 available**, and `agentpg extensions create aidb <instance>` installs it. On the spike instance the instance's own login role (`pg-<id>`, member of `a1p_readwrite` only) gets `permission denied for schema aidb`, and `agentpg db-users create` only offers `read` and `read_write`. The platform has to grant `aidb_users` (and `aidb_governance`) to that role, or provide an admin user, before any step below can run. pgfs 3.2.1 is available too.

## 1. Why this matters for the showcase

Today every AI feature in the apps is written **in application code**: chunk the text, call an embedding endpoint, store the vector, build the hybrid query, fence the model's tool results, check the model's citations, record what happened. That code is good and tested, but it is the same kind of plumbing in three apps, and it runs *outside* the database. The story we want to tell is that **the database is the AI platform**: the data, the retrieval, the agent, the permissions and the audit trail all sit in Postgres and are driven from SQL. AIDB is what makes that story true rather than a slogan, and AgentPG is where it runs with one command.

## 2. What AIDB is (from the documentation)

An EDB-maintained Postgres extension (Postgres 14 to 18; EPAS, EPE or community) with five building blocks:

| Block | What it is |
|---|---|
| **Pipelines** | Declarative multi-step workflows (parse, chunk, OCR, summarize, embed) from a table or external storage into a vector table; run on demand, in batch, or **automatically when source data changes** (trigger-based live mode or background worker). |
| **Knowledge bases** | The pgvector-indexed output (HNSW or IVFFlat); `aidb.retrieve_text()` / `retrieve_key()`; several pipelines can feed one KB; hybrid search with relational filters and full-text; `aidb.rerank_text()` cross-encoder re-ranking. |
| **Agents** | Named agents (instructions, model, tools) run through a ReAct loop **inside Postgres** with `aidb.create_agent()` and `aidb.agent_converse()`; delegation to other agents; OpenTelemetry traces. |
| **Tools** | One catalog (`aidb.tools`): native tools (SQL query, catalog discovery, semantic KBs, pipelines, health checks), **custom SQL tools** you register, and **MCP tools** imported from external servers. |
| **Agent memory** (preview) | Durable per-actor memory of conversations in Postgres, distilled in the background, searched by meaning, namespaced, with auditable deletion. |

Around them: **governance** (purposes that resolve to one Postgres role, enforced by Postgres privileges; decision records), an **MCP endpoint** (`edb-endpoints`) that serves the tool catalog to external clients where every call runs as the authenticated Postgres role, **semantic knowledge bases** (index the schema for text-to-SQL, join-path discovery), standalone SQL functions (`aidb.chunk_text`, `parse_pdf`, `perform_ocr`, `summarize_text`, `generate_text`), PGFS (S3, GCS, Azure and local files as pipeline sources), local models (Candle, llama.cpp) and remote models (OpenAI-compatible, OpenAI Responses, Anthropic Messages, Gemini, NVIDIA NIM, OpenRouter) registered with `aidb.create_model()`.

## 3. Benefits, stated plainly

1. **Less code we own.** Chunking, embedding, re-embedding on change, vector index management and retrieval move from our Python into declared pipelines. The two incidents of this project that were embedding-size outages (PagerLens and TrustDesk after 768 → 1536) are exactly the class of problem a pipeline that owns its model and vector table removes.
2. **Freshness without jobs.** Live pipelines keep the knowledge base in step with the source table in the same database; no reconciler, no startup re-embed.
3. **Permissions the database enforces.** An agent's tools run under a purpose's Postgres role; "the AI can read tickets but not update them" becomes `GRANT SELECT` and a purpose, not a prompt or an application check. Our current guard rails are application-level and well tested, but they are *our* promises; AIDB's are Postgres's.
4. **An audit trail we did not write.** With telemetry on, each allow/deny decision is a span in `aidb_otel.traces`, and **EDB Agent Governance** shows agents, sessions and denials from that database through a read-only role. That is the compliance story TrustDesk talks about, told by the platform.
5. **One tool catalog for in-database agents and external agents.** The same SQL tools serve an in-database agent and, through the MCP endpoint, Claude Code or any MCP client, each as its own Postgres role.
6. **Text-to-SQL that knows the schema.** A semantic KB indexes tables, columns, comments and join paths so an agent finds the right columns by meaning.
7. **Fewer moving parts to deploy.** Apps would not each need an embedding client, a chunker and a re-embed job; the sandbox image gets smaller and the failure modes fewer.
8. **A product story for EDB.** Each app becomes a customer-shaped proof of "Postgres as the AI platform", runnable by anyone with an AgentPG account.

## 4. What only AIDB gives us (versus plain Postgres + pgvector + our code)

| Capability | Why pgvector plus app code cannot match it |
|---|---|
| **Role-confined in-database agents** (purposes, decision records) | Requires the extension inside the engine; an app can only promise. |
| **Agent loop and tool catalog as SQL objects** (`create_agent`, `agent_converse`, `aidb.tools`) | The agent runs next to the data with no network hop and no connection pool in our app. |
| **Governance telemetry that EDB Agent Governance reads** | The Agent Governance screens read `aidb_otel.traces`; there is no equivalent for app-side agents (those use a separate legacy path). |
| **MCP endpoint where `current_user` is the agent** | Postgres log, `pg_stat_activity` and grants all show the agent as itself. Our `/mcp` servers authenticate tokens, then act as the app. |
| **Auto-processing pipelines in the database** | Trigger-based live mode keeps vectors in sync inside Postgres. |
| **Semantic KB, join-path search, query-history relationship discovery** | Needs schema crawling, `pg_stat_statements` and curation built in. |
| **PGFS + pipelines** (documents from S3/GCS/Azure/local into KBs, including OCR) | One declaration instead of an ingest service. |
| **Agent memory with auditable deletion** | Native tables, tools and background distillation; supports erasure requests. |
| **Local models inside the Postgres host** | Data never leaves the instance (subject to host resources, section 8). |
| **Reranking in SQL** (`aidb.rerank_text`) | A cross-encoder as a function. |

What AIDB does **not** replace: PayBroker's Tier 1 rules and Tier 2 XGBoost model (classical ML scoring is not an AIDB feature), identity, the gateway's budgets and per-app audit (AIDB has no equivalent of per-app token budgets across providers), ecoctl, or the apps' UIs.

## 5. Where AIDB fits in each project

### 5.1 TrustDesk (best first candidate)
| Today (app code) | With AIDB |
|---|---|
| Ingest, heading-aware chunking, embedding, versioning in Python; `kb_chunk` with `vector(EMBEDDING_DIM)`; reconciler on dimension change | `aidb.create_pipeline` over the policy-document table (steps: parse, chunk, `KnowledgeBase`), live mode; `retrieve_text` for retrieval, `rerank_text` for ordering; PGFS volume if policies live in object storage |
| Hybrid retrieval: our RRF of vector and full-text | KB hybrid search with relational filters (document type, effective date, department) in one query |
| AI pre-validation and Ask calling the gateway | An **agent** (`create_agent`) whose tools are a retrieval tool and custom SQL tools (employee, balance, travel policy tables); structured output; quote verification stays in the app as a second check |
| Receipts and scanned documents: not supported | `aidb.perform_ocr` and `parse_pdf` in a pipeline: expense requests with receipts checked against policy |
| Audit of AI advice | Purpose = a read-only role over policy and employee views; decision records in `aidb_otel.traces`; Agent Governance dashboard as the compliance console |
| Per-employee context | Agent memory namespace per application, per-actor |

### 5.2 PagerLens
| Today | With AIDB |
|---|---|
| Investigator in Python: bounded loop over registered read-only tools, fenced results, citation checks | An AIDB **agent** with **custom SQL tools** over evidence views (deploys, metrics, logs, dependencies) and a purpose bound to a read-only role; the "never changes your systems" claim is then enforced by Postgres. Keep the app's evidence-id verification and Rules method; AIDB agent becomes the "AI" method |
| Incident memory: embeddings in pgvector, recalled by the app | Pipeline from a resolved-incident table into a KB, or **agent memory** per responder/team |
| Runbooks: hybrid retrieval | KB over runbooks from a volume (Markdown in object storage) with live updates |
| Own MCP server | Keep ours for the UI-aware tools; add the AIDB **MCP endpoint** so an assistant gets governed SQL tools as its own role |
| "Ask the data" | Semantic KB + text-to-SQL over incidents and metrics |

### 5.3 PayBroker
| Today | With AIDB |
|---|---|
| Tier 3 triage: app calls a model with derived features and nearest cases found by a `vector(17)` feature distance | An agent with a purpose limited to **derived feature views** (no card hashes, no PII columns, no write on cases) and tools for case lookup and rule proposals written to a *suggestions* table only. "The AI never decides" becomes a grant boundary |
| Daily rule suggestions | A scheduled agent run writing to the suggestions table; approval stays human |
| Analyst questions about the data | Semantic KB over payments, cases, merchants for text-to-SQL, with a read-only role |
| Case narrative similarity (new) | A pipeline embedding case summaries for text retrieval alongside the numeric nearest-neighbour |
Unchanged: Tier 1, Tier 2, routing, feature store, outbox.

### 5.4 CityPulse and the Simulator
- **CityPulse:** semantic KB so an operator (or an agent) can ask "which districts lost the most sensors during the last burst?" in natural language; health analysis via the native `analyze_db_health` tool; pipeline over the incident/alert text.
- **Simulator:** becomes the load and workload generator **for AIDB itself**: pipelines under sustained inserts, agents under concurrent conversations, retrieval latency under a 10,000-sensor city. This is the performance showcase.

### 5.5 identity, llm-gateway, agentpg-kit, ecoctl
- **LLM gateway stays.** AIDB registers remote models as OpenAI-compatible endpoints, so **`aidb.create_model(...)` can point at the gateway** (`https://<gateway>/v1`, a dedicated gateway client such as `aidb`), keeping provider keys, budgets, failover and per-consumer usage in one place. Alias choice per consumer stays in the gateway. Test this early: Responses/Anthropic native providers bypass the gateway's OpenAI surface, so agents using native tool calling must use a provider the gateway can proxy, or the gateway needs the Responses/tool-call surface.
- **identity:** AIDB's MCP endpoint authenticates with Postgres usernames and passwords. Map identity groups to Postgres roles through ecoctl provisioning (a role per agent with only the grants it needs); there is no OIDC on that endpoint, so say so in the demo.
- **agentpg-kit:** add a **retrieval backend interface** with two implementations: `app` (today's code) and `aidb`; apps choose with one setting. This keeps every app runnable on plain Postgres and makes the before/after comparison a toggle.
- **ecoctl:** a manifest key (`aidb: true`, models, pipelines) that runs `agentpg extensions create aidb`, registers the models against the gateway, creates the credential, pipelines, purposes and roles; `ecoctl aidb status` shows pipeline lag and agent denials.

## 6. Target architecture

```
 browser / MCP client ──▶ app (UI, API, human decisions, verification of quotes/ids)
                              │  SQL
                              ▼
            ┌──────────────── Postgres (AgentPG instance) ───────────────────┐
            │ app tables ──live pipeline──▶ knowledge base (pgvector, HNSW)  │
            │ agents ◀── tools: retrieve · custom SQL · MCP · native         │
            │ purposes → Postgres roles (what a tool may touch)              │
            │ aidb_otel.traces ──▶ EDB Agent Governance (read-only role)     │
            │ MCP endpoint (edb-endpoints) ◀── external agents (as roles)    │
            └──────────────┬─────────────────────────────────────────────────┘
                           ▼ OpenAI-compatible
                      LLM gateway (keys, budgets, failover, usage)
```

## 7. Plan: prove it, then migrate

**Spike (about two days), on one throwaway instance, before any app change:**
1. `agentpg login`; `agentpg extensions list <instance> -s aidb`: is it offered, which version? Governance needs 7.7 or later; agent memory is preview.
2. `agentpg extensions create aidb <instance>`; confirm `aidb.model_providers` lists `openai_embeddings` and `openai_completions`.
3. `aidb.create_credential` + `aidb.create_model` against the **gateway** (embedding alias at 1536, chat alias). Check the credential host restriction and that the gateway records an `aidb` client's usage.
4. Pipeline over a copy of TrustDesk's policy chunks (live mode); insert a row; confirm the KB updates without our code; time `retrieve_text`.
5. Agent with one custom SQL tool under a purpose with a read-only role; show a denied write in `aidb_otel.traces`.
6. Check the MCP endpoint can be reached through an AgentPG sandbox (the platform exposes HTTP/HTTPS only; the endpoint's default is localhost-only, so find out whether it can be served remotely there).
7. Local-model feasibility: can the sandbox's database host run `bge-small` embeddings at acceptable speed? (The earlier "LLM on a small CPU box" idea showed small boxes are tight.)

**Then, in this order:**
1. **TrustDesk** (largest code removed, clearest before/after, compliance narrative).
2. **PagerLens agent method** (governance showpiece; keep Rules method).
3. **PayBroker Tier 3** (purpose-confined agent).
4. **CityPulse** text-to-SQL, **Simulator** workloads for AIDB.
5. ecoctl `aidb:` provisioning and the kit's retrieval backend interface, extracted from step 1.

Each step ships behind the backend switch so the plain-Postgres path stays green in CI.

## 8. Risks and open questions
- **Availability and version on AgentPG** is unverified; features used must be in the version offered.
- **Preview features** (agent memory) may change; label them in demos.
- **Purpose enforcement does not cover MCP-imported tools** (outbound HTTP has no local Postgres identity), so keep sensitive actions in SQL tools.
- **Model path:** tool calling through the gateway must work for the chosen provider type; verify before promising.
- **Host resources:** local models and heavy pipelines share CPU and memory with the database.
- **Two ways to do the same thing** during migration; the backend switch must be tested both ways.
- **MCP endpoint** uses database credentials and, by default, localhost only.
- **EDB Agent Governance** is a separate product with its own setup (read-only role, instance registration); confirm access for the demo.

Sources: EDB docs, AIDB overview, agents, tools, governance, MCP endpoint, pipelines, knowledge bases, agent memory, semantic knowledge bases, integrating models, compatibility and release notes (https://www.enterprisedb.com/docs/aidb/latest/), and Agent Governance (https://www.enterprisedb.com/docs/agent-governance/latest/).
