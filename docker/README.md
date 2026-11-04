# Docker development stack

The whole ecosystem on one machine, with the real AIDB image, **sealed like the fleet**. It is a development environment: see
[SECURITY.md](../SECURITY.md) for what it does and does not protect. Longer write-up: [docs/10-docker-dev-stack.md](../docs/10-docker-dev-stack.md).

```
postgres (aidb-postgres:17, one database + one login role per app)
identity  llmgw  trustdesk  pagerlens  paybroker  citypulse  cpsim  shopfront      (each image built from ../../<repo>/Dockerfile)
```

## Run it

Needs Docker with Compose v2 and BuildKit, the sibling repositories next to this one (`../identity`, `../llm-gateway`, `../trustdesk`, ...) and the ecoctl venv
(`python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'`).

```bash
export ECOCTL_SEAL_PASSPHRASE="$(.venv/bin/ecoctl seal-passphrase)"   # or leave it unset and type one when asked. 20+ characters. Keep it: it opens every app and the state file
export EDB_SUBSCRIPTION_TOKEN=...                                       # ONLY for the first build of aidb-postgres:17 (see below); not needed once the image exists
make dev-up        # build, start, seal, unseal, register, connect (safe to run again: it changes nothing that is already right)
make dev-test      # smoke checks: every app unsealed and healthy, the issuer, a gateway call per app
make dev-down      # stop (the databases and .ecosystem-dev/ stay)
make dev-clean     # stop and delete the databases and the generated secrets (docker compose down -v + the state file)
```

`make dev-unseal` after any container restart (a restarted app is sealed again until the operator opens it). `make dev-status` shows container and seal state.
`.venv/bin/ecoctl dev password` prints the `dev-admin` password (a person in the group that is admin everywhere) for the browser.

Addresses (loopback only): identity <http://identity.localhost:8101>, gateway :8102, trustdesk :8103, pagerlens :8104, paybroker :8105, citypulse :8106,
cpsim :8107, shopfront :8108. `*.localhost` resolves to loopback in browsers and on macOS; the compose network gives every service the same name as an alias, so the
issuer URL in a token is identical from the browser, from ecoctl and from the other containers. Postgres is on `127.0.0.1:55460`; `docker compose -f docker/compose.yml exec postgres psql -U postgres`
works over the socket without a password.

## Where the secrets are

| Secret | How it reaches the container | On disk | In `docker inspect` |
|---|---|---|---|
| each app's database URL, generated secrets, identity client secret, gateway key | one sealed blob (`apgkit-seal1$...`, AES-256-GCM under your passphrase) made in memory by `ecoctl dev up` and given to `docker compose` as a **secret whose source is an environment variable** of that process; the container sees it as `/run/secrets/apgkit_sealed` (`APGKIT_SEALED_FILE`) | no | only the path |
| your passphrase | never stored; `ecoctl dev unseal` sends it **encrypted to a key that exists only in the app's process** (the same exchange as `ecoctl unseal`) | no | no |
| role passwords | generated, kept in `.ecosystem-dev/state.json` (encrypted with your passphrase, mode 0600), set with `ALTER ROLE` over `docker compose exec` stdin; they are inside each app's blob | ciphertext only | no |
| Postgres superuser password | compose secret from the environment (`PG_SUPERUSER_PASSWORD`, set by ecoctl), `POSTGRES_PASSWORD_FILE` | no | no |
| EDB repository token | BuildKit secret from `EDB_SUBSCRIPTION_TOKEN`, only while building the image; the Dockerfile fails the build if it finds the token anywhere in the image | no | not in the image |
| provider key for the gateway | see below | no | no |

`docker/compose.yml` therefore holds only public values (ports, URLs, model aliases, OIDC client ids); a test fails if a literal secret appears in it. The compose
secret mechanism was chosen over a tmpfs directory because it needs no file on the host at all: the content goes from the process environment to the container through
the Docker API. A host tmpfs directory would have to be shared into Docker Desktop's VM and removed on exit, and is a file a crash leaves behind; the environment route has no such file, so it is the one used (a tmpfs directory was not tried). Its residue: the blob (ciphertext) and the superuser password sit in the environment of the short-lived `docker compose` child process,
readable by the same user and root while it runs, and the blob is in Docker's copy of the container's files like a platform's stored sandbox config.

## Databases and roles

`docker/initdb/20-app-databases.sh` reads `APP_DATABASES` (rendered from the manifest: name, uses AIDB, extensions) and creates, for each app, a database and a login role of
the same name: `NOSUPERUSER NOCREATEDB NOCREATEROLE`, owner of its own database only, `PUBLIC` revoked from the database. It creates the extensions as superuser (the apps'
own `CREATE EXTENSION IF NOT EXISTS` then finds them) and skips, with a notice, the ones the image does not have (`query_advisor`, `edb_stat_monitor`, `edb_pg_tuner`,
PostGIS). `aidb_users` is granted only to the apps that use AIDB (`devstack.AIDB_APPS`: trustdesk, pagerlens, citypulse, cpsim); that role gives the AIDB functions, not access to
the app's tables. The script is idempotent: it runs when the volume is created and `ecoctl dev up` runs it again, so an app added to the manifest later gets its database.

## The gateway's provider

Out of the box the gateway answers with its built-in echo provider (deterministic, no key, no network): every app works and `make dev-test` makes real gateway calls.
To use a real model, export before `make dev-up` (the key goes from your environment to the gateway's vault; nothing is read from a file or printed):

```
ECOCTL_DEV_LLM_API_KEY      the provider key (required to switch)
ECOCTL_DEV_LLM_BASE_URL     default https://api.openai.com/v1  (any OpenAI-compatible endpoint)
ECOCTL_DEV_LLM_CHAT_MODEL   default gpt-4o-mini
ECOCTL_DEV_LLM_EMBED_MODEL  default text-embedding-3-small  (1536 dimensions, matching `llm.embed_dimensions`)
```

It adds a provider `dev-provider` and repoints `chat-default` and `embed-default` at it, once; running again keeps it. Change it later in the gateway's UI.
That path is covered by a unit test against a fake gateway, not by a live provider.

## The compose file follows the manifest

`docker/compose.yml` is generated (`ecoctl dev render`) from `ecosystem.yaml`, so ports, environment, dependencies and databases cannot drift from what ecoctl does for the fleet; the
test `tests/test_devstack.py` and `ecoctl dev up` both fail if the committed file differs from the render. After editing `ecosystem.yaml` run `make dev-render`. Ports,
repository names, the AIDB list and the dev-only settings live in `src/ecoctl/devstack.py`.

## AIDB per app

The stack runs trustdesk, pagerlens, paybroker and citypulse with `<APP>_RETRIEVAL_BACKEND=aidb` (each falls back to its own search, loudly, until set up) and grants their roles
`aidb_users`. The apps' operator step is `ecoctl dev aidb-setup [APP...]`: it runs `<app> aidb-setup` in a one-off container of the app's image on the stack's network. The privileged
connection string (the stack's own superuser, built from the encrypted state) and the app's database URL are given in that container's environment only (`-e NAME`: in no argument,
file or log; the container is removed when the command ends); the passphrase that opens the app's vault is typed at the command's hidden prompt by ecoctl. `--agent` also registers the gateway chat model and
the agent (it needs the gateway reachable from the database, see below). Check with `make dev-test`, which reads each app's `/readyz` AIDB state, runs a policy search through TrustDesk
and checks PagerLens' seals.

## Sign-in

`ecoctl dev password` prints the `dev-admin` password. `make dev-test` signs in to every web app through identity with it (the browser flow, scripted) and calls TrustDesk with the
`cpsim-service` token from `client_credentials`. Identity is run with `IDENTITY_DEV_HTTP=1` and `IDENTITY_ISSUER=http://identity.localhost:8101`, which together let it serve OAuth over plain http
(its `/readyz` says `insecure_transport_relaxed`). They are set by the stack only (`devstack.DEV_ENV`), never in a published image's example env.

## Agents: stand-in first, a real model on request

`ecoctl dev aidb-setup --agent` registers the gateway's `chat-default` alias as the agents' chat model. Out of the box that alias is the gateway's built-in **echo provider, a stand-in**: it answers with fixed
text, never a verdict, a tool call or an answer, so the agents are only *invoked* (the model call really goes AIDB -> gateway).

**A real model** (spends API credit): put the provider's key in an environment variable of one command, never in a file of this repository:

```bash
( set -a; . /path/to/your/env-file; set +a; unset EDB_SUBSCRIPTION_TOKEN; .venv/bin/ecoctl dev llm-provider openai )      # or: openrouter
```

`llm-provider` reads `OPENAI_API_TOKEN` (or `OPENROUTER_API_TOKEN`) from that process's environment, **first** sets hard limits on every gateway client (default 10 requests/min and 300,000 tokens/month,
`--rpm`, `--budget`), then registers the key with the gateway's admin API (the gateway stores it encrypted in its vault; it is in no output, state file, compose file or image) and points **only the chat alias**
at a cheap model (`gpt-4.1-mini` on OpenAI, `openai/gpt-4.1-mini` on OpenRouter; `--chat-model` changes it): small, current, and good enough at the structured output and tool-call format AIDB agents need.
Embeddings stay on the echo provider for the apps' own use, and AIDB's knowledge bases embed locally (`bge-small-en-v1.5-f16`). `ecoctl dev down -v` deletes the gateway database that holds the encrypted key.
Already-registered agents need no re-setup: the alias is what changed. The limits are the money guard: a hard budget stops a runaway agent loop, and the rate limit makes extra calls fail fast (see the table in docs/10).

Two dev-only settings make that path possible, both in the stack and not in the published image: the Postgres service runs `postgres -c shared_preload_libraries=aidb -c aidb.allow_insecure_egress=on`
(AIDB refuses plain http for model calls otherwise), and because that option allows an **IP literal only, never a host name**, the gateway has a fixed address (`10.213.77.10`, network `10.213.77.0/24`,
`devstack.GATEWAY_IP`) which `--agent` uses as the model URL. If that subnet clashes with a network of yours, change `NETWORK_SUBNET` and `GATEWAY_IP` and run `make dev-render`.
`PAYBROKER_DEV_HTTP=1` (PayBroker only) lets it accept http `*.localhost` origins.

After `aidb-setup`, `ecoctl dev aidb-setup` does what an operator would: it waits for each app's `/readyz` AIDB state to become `ready` (the apps re-examine a fallback about every 60 s; TrustDesk took 15 s on a fresh stack) and, for an app whose state says `vault_locked`
(CityPulse: its setup creates the vault the seals need, but the running process only opens its vault at unseal time and never looks again), restarts the container and unseals it. No manual step.

## Known gaps (2026-11-04)

* **`connect` ShopFront to PayBroker stops at the webhook**: PayBroker refuses a webhook URL on a private or local address (`PAYBROKER_ALLOW_PRIVATE_URLS=true` would allow it; not set without a decision).
  The merchant, the allowed origin and the keys are created; `ecoctl dev up` finishes everything else and exits 1 with the message.
* A real model was run once per scenario (docs/10); with the stand-in alone no agent does real work.
* PostGIS and EDB's platform extensions are not in the image; CityPulse's geography and the query advisor degrade as they do on a platform without them.
