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

Identity 6a74ab1, CityPulse 6821ee9, TrustDesk 112a5cc, PagerLens 5a1a98b, PayBroker 9eff9f7, ShopFront d850b8c (kit 0.5.2) and the kit's earlier pins as built.

* All eight images build from the sibling repositories (cached rebuild: 15-20 s).
* `ecoctl dev up` from an empty volume (images built): 35 s. All eight apps start **sealed**, are unsealed with the passphrase, register with identity, get gateway clients (CityPulse now has one: `llm: true`), groups and grants, ecoctl's service client with `paybroker.admin` and `shopfront.admin` tokens, and the `dev-admin` user.
* `ecoctl dev test`: 29 checks, all passing after setup: every app unsealed and healthy; a chat call through the gateway per consuming app (echo provider: stand-in); `dev-admin` signs in through identity to **all seven** web apps including ShopFront (kit 0.5.2); a `cpsim-service` token is accepted by TrustDesk; TrustDesk answers a policy search from the AIDB knowledge base; PagerLens seals verify; `/readyz` AIDB state is `ready` for TrustDesk, PagerLens, PayBroker and CityPulse.
* `ecoctl dev aidb-setup --agent` for trustdesk, pagerlens, paybroker and citypulse: pipelines, tools, purposes, seals, the chat model, the agents (`td_prevalidator`, `pagerlens_investigator`, `pb_triage_agent`, `pb_ask_agent`, `cp_ask_agent`) created. PayBroker's and CityPulse's setups open their vaults with the seal passphrase typed at a hidden prompt.
* `connect` ShopFront to PayBroker: service tokens, merchant, allowed origin (http `*.localhost`, with `PAYBROKER_DEV_HTTP=1`) and keys now work (they returned 422 before).
* One call per app through the agent path: PayBroker `/api/ask` and CityPulse `/api/ask` each ran the AIDB agent and returned its answer (the echo stand-in's text); TrustDesk pre-validation invoked `td_prevalidator`, the app rejected the non-verdict ("final answer is not valid JSON") and fell back to the gateway path, logging `AIDB MODE FALLBACK`, as designed.
* `ecoctl dev aidb-setup --agent` leaves every app `ready` by itself (fresh stack: 36 s for the four apps): it waits for the apps' own once-a-minute re-examination (TrustDesk: 15 s) and restarts and unseals CityPulse, whose vault is created by its setup but only opened at unseal time (cause: the running process does not look again, so this is a stack ordering step, not a cache delay).
* The repeat run: 5 s, no container created, restarted or recreated, registrations, gateway clients, merchant, origin and keys "kept", no secret rotated.
* Restarting every container leaves each app sealed; `ecoctl dev unseal` opens them all and `ecoctl dev test` passes again.
* `docker inspect` of an app shows only `APGKIT_SEALED_FILE` and public values.

## What did not work, or was not done

* **`connect` stops at the PayBroker webhook** (HTTP 422: private or local address). Needs `PAYBROKER_ALLOW_PRIVATE_URLS=true` on PayBroker or a different webhook policy: a decision.
* **The stand-in model proves plumbing only.** No agent produced a verdict or called a SQL tool, so no tool call was allowed or denied: **no denial was seen in any governance view**, TrustDesk's agent verdict path (citations checked, fallback avoided), PagerLens' AIDB-agent investigation (no incident was created to investigate) and PayBroker's case triage by the agent were **not exercised**. The kit's scripted server (`tests/aidbfake.py`) could drive them; per-app scripts (TrustDesk's must cite a retrieved passage) were not written.

## Not verified

* A real model provider (`ECOCTL_DEV_LLM_API_KEY` path is unit-tested against a fake gateway only), AIDB agent memory, AgentPG-hosted AIDB, the first compose-driven build of `aidb-postgres:17`, Linux and Docker Engine without Docker Desktop.

## Drift and maintenance

Edit `ecosystem.yaml`, run `make dev-render`, commit both. `tests/test_devstack.py` fails if they differ; `ecoctl dev up` refuses to run with a stale file.
