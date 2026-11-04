# 10. The Docker development stack

Written 2026-11-04. Until AgentPG grants AIDB roles again, the whole ecosystem can be deployed and tested on one machine: `make dev-up`. Run instructions, the secret table
and the provider variables are in [docker/README.md](../docker/README.md); this page says what it is, how it mirrors the fleet, and what was and was not verified.

## What it is

One Postgres 17 with AIDB 7.9 (the `aidb-postgres:17` image, built from `docker/aidb-postgres/Dockerfile` with the EDB token as a BuildKit secret), plus the identity provider, the LLM
gateway and the apps, each built from its sibling repository's own Dockerfile. `ecoctl dev` drives it with the same code the fleet uses: `plan._register`, `_llm_client`, `_group`,
`connect_step`, `seal.unseal`, the encrypted state file. Only the part that talked to AgentPG (instances, sandboxes) is replaced by `docker compose`.

| Fleet (AgentPG) | Docker stack |
|---|---|
| an instance per app, its key as the database password | a database and a login role per app in one Postgres; role password generated, in the encrypted state |
| a sandbox with `APGKIT_SEALED` in its environment | a container with the blob as a compose secret taken from the environment (file under `/run/secrets`, not in the container's environment) |
| `ecoctl unseal` | `ecoctl dev unseal` (same HTTP exchange) |
| https behind AgentPG's proxy | plain http on `http://<app>.localhost:<port>` (the kit accepts http for localhost only) |
| `ecoctl apply` order: identity, then apps after what they need | the same order |

Per app, as in `app_env`: the OIDC issuer, client id and secret; the service client for the apps that call others; the callee URLs; `LLM_BASE_URL` (the gateway), `LLM_CHAT_MODEL`,
`EMBEDDING_MODEL`, `EMBEDDING_DIM` and one gateway client key per app. The compose file is generated from the manifest and a test keeps it equal, so the drift risk of a hand-kept
file does not exist; what lives only in `src/ecoctl/devstack.py` (ports, repository names, which apps get `aidb_users`, dev-only settings) is small and listed at the top.

## Verified on 2026-11-04 (real images, real AIDB, Docker Desktop on macOS, Apple silicon)

* All eight app images build from the sibling repositories (cached rebuild of all eight: 20 s). Their dependencies are hash-locked public ones; nothing private was needed.
* `ecoctl dev up` from an empty volume: postgres healthy with the databases and roles, identity, gateway, PagerLens, PayBroker, ShopFront, TrustDesk and CityPulse Simulator
  started **sealed**, each unsealed with the passphrase (the app then migrated and served), identity pinned its issuer, every web and service client registered, one gateway client per
  app, groups and grants created.
* `ecoctl dev test`: all seven running apps unsealed and `/healthz` 200; identity's issuer equals its own URL; a chat call through the gateway with each app's own key on the `chat-default`
  alias (echo provider) succeeds.
* A container restart leaves the app sealed (`/_seal/status` says `sealed`), `ecoctl dev unseal trustdesk` opens it again.
* `docker inspect` of an app shows only `APGKIT_SEALED_FILE=/run/secrets/apgkit_sealed` and public values: no URL, key or blob in its environment.
* Roles: `rolsuper` and `rolcreatedb` false for all; `aidb_users` member only for trustdesk, pagerlens, citypulse, cpsim.
* Running `ecoctl dev up` again changes nothing that is already right: running containers are left alone (no new blob, no recreate), the registrations and gateway clients answer "kept", no
  secret is rotated.

## Not verified, and why

* **Sign-in through identity and everything that needs a token from it** (`client_credentials` for ecoctl's service client, `connect` of ShopFront to PayBroker, a TrustDesk call with a
  service token): identity's token endpoint answers HTTP 500, `InsecureTransportError: OAuth 2 MUST utilize https` (authlib checks the scheme of the request URL; identity has no http
  allowance for loopback although its README starts it on `http://localhost:8000`). Options: (a) identity treats a loopback or `*.localhost` request as secure, as the kit already does for
  issuers, (b) the stack sets authlib's `AUTHLIB_INSECURE_TRANSPORT=1` on the identity container only (an off switch for a protection; it was not applied without a decision), (c) a TLS proxy
  with a local CA that every container and browser trusts. `ecoctl dev up` stops at that step until one is chosen.
* **CityPulse**: `citypulse start` migrates before the app is unsealed and `serve` never migrates afterwards, so with only a blob it never gets a schema. It is skipped by default
  (`--include citypulse` to try). The simulator's calls to it will fail meanwhile. This is a CityPulse change (move `migrate.run()` into the deferred lifespan, as TrustDesk does).
* A real model provider (no key was used): the code path is unit-tested against a fake gateway only.
* AIDB itself inside the apps: the stack creates the extension and grants `aidb_users`; whether an app's AIDB backend works is the other agents' work and is not exercised here.
* The first build of `aidb-postgres:17` through compose (the image already existed; it was built by `docker build` with the same Dockerfile and secret).
* Linux and Docker Engine without Docker Desktop.

## Drift and maintenance

Edit `ecosystem.yaml`, run `make dev-render`, commit both. `tests/test_devstack.py` fails if they differ; `ecoctl dev up` refuses to run with a stale file.
