# Two MCP routes: the apps' own MCP servers and AgentPG's database MCP

We keep both on purpose. The apps expose MCP tools that know their domain. AgentPG exposes a generic MCP server over the database. Side by side they show what the platform already gives you and what is still missing. The "Gaps to take to the AgentPG team" section at the end is the list we want fixed.

Status of each claim is marked: **verified** (observed on the live platform), **by design** (follows from how the route works), **unverified** (not yet run; `ecoctl mcp users|check` has never run live).

## What each route is

| | App MCP (TrustDesk, PagerLens, CityPulse, PayBroker, ShopFront) | AgentPG database MCP (`ecoctl mcp serve`) |
|---|---|---|
| Runs as | Part of each app, streamable HTTP | `agentpg mcp serve`, restricted mode, one config per app database |
| Sees | Domain objects through the app's code | Tables and views the read-only database user may read |
| Identity | Our OIDC identity: roles and scopes such as `trustdesk.kb.read` | A database user (`a1p_readonly`) and a JWT secret (`A1P_JWT_SECRET`); no notion of the human or the tenant |
| Writes | Yes, behind scopes | No in restricted mode with the read-only URL |
| Masking | Application code | Revoked base tables plus `security_barrier` masked views (kit `mask_view`) |

## Pros and cons

| | App MCP | AgentPG MCP |
|---|---|---|
| Effort per app | Tools, schemas, tests and docs written by hand (by design) | Nothing to write: new tables are queryable at once (by design) |
| Answer quality | Shaped results; verified citations (PagerLens), quote check (TrustDesk), explain (CityPulse) (by design) | The model writes SQL and rediscovers the schema every time; nothing verifies the answer (by design) |
| Authorization | Per-user roles and scopes, and per-merchant row-level security in PayBroker (by design) | Database-level only; no per-user or per-tenant context unless the database user is built for it (by design) |
| Availability | Needs the app sandbox running | Needs only the instance (by design); behaviour against an auto-paused instance is unverified |
| Safety of the endpoint | Behind our identity | The hosted endpoint's JWT session is read-write: it created a table (verified). Only `mcp serve` with `access_mode: restricted` and a read-only URL is safe; HTTP mode without `A1P_JWT_SECRET` has no authentication and binds 0.0.0.0 (verified) |
| Limits | Per-tool result caps we chose | Only what the database enforces; no row or time cap of its own (unverified) |
| Audit trail | App logs, identity audit | Database logs; no per-call record of who asked what (by design) |
| Public surface | Stable contract we version | Changes with the platform |

## When to use which

- Exploring data, one-off questions, "what is in this table", slow queries, index advice: the AgentPG route.
- Anything that changes data, needs the caller's identity or tenant, or must be verifiable (citations, quote checks, explain): the app route.

## Gaps to take to the AgentPG team

1. **A read-only hosted MCP endpoint.** The hosted session is read-write. Offer a read-only token or URL per instance, so nobody has to run `mcp serve` themselves.
2. **Bearer authentication with platform keys.** API keys are not accepted as bearer tokens, and the CLI can't use platform keys (no flag or environment variable).
3. **Per-caller identity.** Pass the end user's identity (OIDC subject, roles) to the database as session settings, so row-level security can apply per user and per tenant, as PayBroker does with `app.merchant_id`.
4. **Built-in masking.** `data_redaction` needs superuser here. Provide masking policies for read-only users without it.
5. **Result and cost limits.** Row caps, statement timeouts and byte limits per MCP session.
6. **Per-call audit log.** Who asked what, which SQL ran, how many rows came back.
7. **Wake-on-request.** A paused instance should wake on the first MCP call. Today this is unverified.
8. **Schema hints.** Let the database owner attach descriptions to tables and columns that the MCP tool exposes, so the model stops guessing.
9. **Stable URLs.** Sandbox URLs contain the sandbox id and change on recreate; MCP clients configured against them break.

## How we demonstrate it

`ecoctl mcp config` writes the restricted configuration, `ecoctl mcp serve` runs it, `ecoctl mcp claude` prints the client stanza, and `ecoctl mcp check` tests it. Ask the same question through both routes, for example "which merchants had a NAME_MISMATCH this week?", and compare: the app tool returns the shaped result with its checks, the database route returns whatever SQL the model wrote.

**Not yet done:** run `ecoctl mcp users` and `ecoctl mcp check` against the live fleet, then replace each "unverified" above with what was observed.
