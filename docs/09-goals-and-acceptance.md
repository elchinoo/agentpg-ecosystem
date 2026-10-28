# Goals and acceptance criteria

This page defines what the showcase must achieve and how we know each goal is met. Status is written from what was observed on the live fleet (dates in the evidence column), not from what was built. Anything not observed is marked **not verified**.

## Purpose and audience

**Purpose.** Show, with working software, what EDB Postgres AI and the AgentPG platform give a developer: one Postgres that does vectors, search, geospatial, JSON, time series, security, governance and AI access, and a platform that runs the apps next to it.

**Audience.** Customers (five stories, below) and EDB internal teams (platform, kit, ecoctl, Ansible).

**What we are not claiming.** HA and self-healing failover (not demonstrable on AgentPG today), AIDB (waiting on platform grants), ClickHouse or any non-Postgres engine (out of scope by decision), autonomous database changes (PagerLens proposes, a person approves).

## What is platform, scaffolding and sample

| Label | What | Swappable? |
|---|---|---|
| Platform (AgentPG / EDB) | Postgres 18 instances, sandboxes, extensions (`edb_stat_monitor`, `edb_pg_tuner`, `query_advisor`, PostGIS and others), database users, MCP server | No: this is the thing being shown |
| Reference scaffolding | identity (OIDC), llm-gateway, kit vault, ecoctl, Ansible recipe | Yes: any OIDC provider, any OpenAI-compatible gateway, your own vault, your own automation |
| Sample apps | TrustDesk, PagerLens, CityPulse (+ Simulator), PayBroker, ShopFront | They exist to show the platform |

## Goals, acceptance and status

| # | Goal | Acceptance check | Status and evidence |
|---|---|---|---|
| G1 | EDB tech is visible, not implied | Each app's Insights/Database view shows `edb_stat_monitor` queries, `edb_pg_tuner` settings and `query_advisor` suggestions from the live database | **Met** 2026-10-28: TrustDesk, PagerLens, CityPulse, ShopFront and the gateway show all four sources. Identity: needs its admin login, **not verified** |
| G2 | PostGIS is real | Sensors carry coordinates, Nearby returns metres, the map shows district outlines with health | **Met** 2026-10-28 (CityPulse, Gotham and Metropolis). Cities may be invented: only coordinates matter |
| G3 | One Postgres, five models | One SQL answers a district question across relational, time series, JSON, geo and vector | **Met** 2026-10-28 (`/api/districts/{id}/explain`, 5 models, 1 round trip) |
| G4 | DB-layer security and audit | PayBroker audit chain verifies; a tampered row is named; row-level security is effective | Chain intact and RLS effective **met** 2026-10-28. Tamper-and-verify demo **not yet shown live** |
| G5 | AI that cites and can be checked | TrustDesk quotes are verified against source rows; PagerLens Ask marks each citation verified | **Met** 2026-10-28 (REQ-000015; Ask answered with two verified incident citations) |
| G6 | AI access to the database is safe | Read-only MCP user cannot write, cannot read masked columns from base tables, and the masking survives grants after startup | **Met** 2026-10-28 after kit 0.3.1: 7 of 7 apps deny the base tables; writes refused; durability proven live (a new read-only database user re-opened TrustDesk `employee` at 16:52:39, the app closed it again by 16:55:55 without a restart); `masking_drift_tables` 0 on all apps. Before the fix the tables stayed open until the next restart |
| G7 | Propose, never auto-apply | PagerLens tuning shows proposals, applies nothing by default | **Met** 2026-10-28 (server settings marked not applicable; apply off) |
| G8 | Swappable pieces are explicit | Docs and videos say in the first 30 s that identity, gateway and vault are swappable | Docs **met**. Videos **not yet re-recorded** |
| G9 | Deployable by customers their own way | `ecoctl` (fast) and an Ansible recipe that deploys on AgentPG | `ecoctl` **met**. Ansible on the real platform 2026-10-28 (throwaway fleet `ecoans`): dry run (ok=126, nothing created); partial apply (identity, gateway, PagerLens created, registered, keyed and healthy 200/200; CityPulse's sandbox created; PayBroker's instance stayed `unknown` for 420 s and the run stopped); **real teardown verified** (`destroy.yml`: 4 sandboxes and 5 instances deleted by exact name, `eco-*` untouched, failed=0). A complete apply (all 8 apps) is **not yet verified** |
| G10 | Each customer video lands one of three moments | "Look how much easier this is" / "look at this cool thing only EDB can do" / "look at the level of work you should be doing" | **Not yet re-recorded**; storyboard per app is in the video scripts |
| G11 | Platform gaps become requests | Every limitation we hit is written down with evidence for the AgentPG team | Maintained in `06-edb-and-agentpg-capabilities.md` and `08-two-mcp-routes.md` |

## Customer stories (five videos)

| App | Story | Moment | Signature scene |
|---|---|---|---|
| TrustDesk | "I need answers from our policies I can trust" | Only-EDB (pgvector, AIDB later) | A request is checked against policy; each reason shows a verified quote |
| PagerLens | "I want to ask my systems what is wrong" | Level of work | Ask with verified citations; tuning proposals a person approves |
| CityPulse | "My data is time series, JSON, geo and vectors" | Easier | The map, then one SQL across five models |
| PayBroker | "I must prove nobody altered the record" | Only-EDB | Tamper with a row, Verify names it; row-level security by merchant |
| ShopFront | "I just want an app on a database that works" | Easier | Search with typo tolerance and live order status |

## Known platform findings (for the AgentPG team)

1. `agentpg db-users create` re-grants SELECT on base tables to `a1p_readonly`, undoing REVOKE-based masking. Wanted: respect existing revokes, or support column-level and row-level policy.
2. Server-sent events do not reach the browser through the sandbox ingress (no response headers in 8 s while the app sends its first frame at once). Wanted: streaming responses that flush.
3. `agentpg connect --json` returns only the hosted MCP URL; the database host is in `agentpg describe --json` (`connection_string`).
4. The hosted MCP JWT session is read-write; no read-only hosted endpoint.
5. `pg_read_all_stats` is not available to the app role, so other users' statement texts are hidden in Insights.
6. Superuser-only extensions (data_redaction, pg_prewarm, pgstattuple and others) cannot be installed; AIDB installs but has no grants.
7. Sandbox URLs contain the sandbox id and change when a sandbox is recreated.
8. Creating a fourth and fifth instance in quick succession (Ansible, throwaway fleet `ecoans`, 2026-10-28) left PayBroker's new instance in status `unknown` for over 420 s. The sandboxes and instances created before it kept running.
9. `agentpg account` shows only an instance count; no quota or limits are visible anywhere in the CLI.
10. Different shells on the same machine showed different views of the same account's instances and sandboxes (one saw the `ecoans-*` objects, the other did not). Cause not known; the user's own terminal is the reference.

## Definition of done for this phase

- Every row above is **Met**, or has a written reason and an owner.
- ~~All eight apps run kit 0.3.1 with the masking re-assert, and `masking_drift_tables` is 0 on every app.~~ Done 2026-10-28.
- ~~The Ansible `--check` dry run has been run once on a real account.~~ Done 2026-10-28. A full apply on a throwaway fleet name is still to do.
- Customer videos re-recorded against the live behaviour above.
