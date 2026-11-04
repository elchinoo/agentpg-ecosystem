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

Identity 6a74ab1, CityPulse 6821ee9, TrustDesk 112a5cc, PagerLens 5a1a98b, PayBroker ec14e16, kit e7dd124 (0.5.1) in the images.

* All eight images build from the sibling repositories (cached rebuild: 20 s, nothing private needed).
* `ecoctl dev up` from an empty volume (images built): 35 s. Postgres, identity, gateway, PagerLens, CityPulse, PayBroker, ShopFront, TrustDesk and the Simulator start **sealed** and are unsealed with the passphrase; identity's issuer is pinned; web and service clients, per-app gateway clients, groups, ecoctl's own service client (its tokens for `paybroker.admin` and `shopfront.admin`) and the `dev-admin` user are created.
* `ecoctl dev test`: all eight apps unsealed and healthy; a chat call through the gateway for every app with its own key (echo provider, a stand-in: no model ran); `dev-admin` signs in through identity to llmgw, pagerlens, citypulse, paybroker, trustdesk and cpsim with the roles identity grants; a `client_credentials` token of `cpsim-service` is accepted by TrustDesk (`/api/v1/request-types`).
* AIDB: `ecoctl dev aidb-setup trustdesk pagerlens` created the pipelines, tools and purposes. TrustDesk answers a policy search from the AIDB knowledge base (`/readyz` retrieval true, the app logs the backend, 78 of 78 chunks embedded by the Live pipeline with the local model, top passage correct); PagerLens reports `ready`, seals verified, runbook and incident retrieval on AIDB.
* A container restart leaves the app sealed; `ecoctl dev unseal` opens it (identity, trustdesk and citypulse restarted together, then everything tested again).
* The repeat run (`up` again): 5 s, no container created or restarted, registrations and gateway clients "kept", no secret rotated.
* `docker inspect` of an app shows only `APGKIT_SEALED_FILE=/run/secrets/apgkit_sealed` and public values. Roles: not superuser, not createdb; `aidb_users` only for the apps that use AIDB.

## What did not work, and who owns it

| What | Why | Owner |
|---|---|---|
| ShopFront sign-in (401 at token exchange) | the kit's `auth.from_env` reads the OIDC client secret at import, before unseal | ShopFront (or the kit) |
| `connect` ShopFront to PayBroker (HTTP 422) | PayBroker's `origins.py` takes http only for `localhost`/`127.0.0.1`, not `*.localhost` | PayBroker |
| PayBroker `aidb-setup` | its CLI cannot open a passphrase-wrapped vault (no prompt for the seal passphrase) | PayBroker |
| Agents (TrustDesk, PagerLens, CityPulse; CityPulse's setup refuses without one) | model calls to the http gateway need the database option `aidb.allow_insecure_egress=on`; not set without a decision (the https alternative needs a TLS proxy) | decision |

## Not verified

* Any AIDB **agent** and any ask or investigation through it; the scripted model server was not used (it also needs the egress option). Agent memory is not used.
* CityPulse's AIDB mode (setup stops at the missing chat model); PayBroker's.
* A real model provider (no key was used; the code path is unit-tested against a fake gateway only).
* The first build of `aidb-postgres:17` through compose (the image existed), Linux and Docker Engine without Docker Desktop.

## Drift and maintenance

Edit `ecosystem.yaml`, run `make dev-render`, commit both. `tests/test_devstack.py` fails if they differ; `ecoctl dev up` refuses to run with a stale file.
