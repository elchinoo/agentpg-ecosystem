# agentpg-ecosystem

`ecoctl` describes a **fleet of apps** in one file, [`ecosystem.yaml`](ecosystem.yaml), and does the repetitive part of putting it on
[AgentPG](https://www.agentpg.dev/) with single sign-on:

1. creates a database and a sandbox for the identity provider and each app (`agentpg` CLI),
2. registers every app with the [identity provider](https://github.com/elchinoo/identity): its roles, its web client for browser
   sign-in, and a **service client** for the apps it calls (scoped to exactly the scopes you list),
3. creates the identity provider's launcher entries, points each app's redirect URI at its real URL, and
4. creates groups (`ecosystem-admins`, ...) with role grants, so adding a person to a group gives them access everywhere.

It generates random secrets (encryption keys, client secrets), keeps them in `.ecosystem/` (git-ignored, mode 0600) and passes them
to the sandboxes as environment variables. **No secret is ever written in the manifest**: the validator refuses an env name that
looks like one and tells you to list it under `generate:`.

Apps are built on [agentpg-kit](https://github.com/elchinoo/agentpg-kit): TrustDesk, PagerLens, CityPulse, CityPulse Simulator,
PayBroker, ShopFront and the LLM gateway all read the same `<PREFIX>_OIDC_*` variables.

**Full walkthrough for a first live deployment (eight sandboxes): [docs/DEPLOY.md](docs/DEPLOY.md).**

**With Ansible instead of `ecoctl`:** [`ansible/`](ansible/) deploys the same fleet on AgentPG from the same manifest (the `agentpg` CLI for instances, extensions and sandboxes, HTTPS for identity, gateway, groups and the ShopFront-PayBroker connection), for teams that drive their infrastructure with Ansible; a secondary target puts it on your own Postgres and Docker hosts. Comparison, a read-only first dry run (`--check`) and what Ansible does not do: [docs/07-ansible-deployment.md](docs/07-ansible-deployment.md).

**Everything in one place: [docs/GUIDE.md](docs/GUIDE.md)** (every project, how they interact, the manifest and command reference, secrets and
rotation, the release loop, troubleshooting, the e2e suite, AIDB status, how to add an app). **Tutorial video script:
[docs/VIDEO-SCRIPT.md](docs/VIDEO-SCRIPT.md)** ("from zero to a running ecosystem with ecoctl"). Documents 01 to 04 under `docs/` are dated reports; GUIDE.md is the current reference.

**EDB and AgentPG capabilities** (which extensions each app lists, what is blocked and why; probed in two rounds, the first incomplete): [GUIDE section 18](docs/GUIDE.md#18-edb-and-agentpg-capabilities-used) and the probe report [docs/06-edb-and-agentpg-capabilities.md](docs/06-edb-and-agentpg-capabilities.md). New commands: `ecoctl insights` (query-advisor recommendations per app database) and `ecoctl mcp users|config|serve|claude|check` (read-only AI access through AgentPG's database MCP server): [GUIDE sections 7.9 and 7.10](docs/GUIDE.md#79-insights-app---all---json).

## Use

```bash
uv venv && uv pip install -e ".[dev]"
.venv/bin/ecoctl validate                # check the manifest
.venv/bin/ecoctl plan                    # show every command and API call; changes nothing
.venv/bin/ecoctl apply                   # the same, as a dry run
.venv/bin/ecoctl apply --execute         # asks first, then creates and registers; safe to run again
.venv/bin/ecoctl status                  # health and live CPU/memory/network of each app, each LLM client + one real call, and drift from the identity registry
.venv/bin/ecoctl llm test trustdesk      # one tiny chat call through the LLM gateway with that app's own key
.venv/bin/ecoctl connect --dry-run       # what connecting ShopFront to PayBroker would change (apply does it for you)
.venv/bin/ecoctl unlock                  # after a platform restart: open the vaults of identity and the gateway with the admin password (`vault: password`; docs/GUIDE.md section 9a)
.venv/bin/ecoctl env trustdesk           # the environment an app is created with (secrets shown as ***; --reveal prints them)
```

Requires the `agentpg` CLI, logged in (`agentpg login`). `apply --execute` creates databases and sandboxes on your account, which may
use credits; it prints the plan and asks unless you pass `--yes`.

### What happens, in order

The identity provider first (the first request after it starts fixes its issuer, and `ecoctl` makes that request over the public
URL). Then each app, **callees before callers** so a caller's environment can name the callee's URL: register in identity (secret
issued once, kept in `.ecosystem/state.json`), create the database and sandbox with its OIDC settings, then replace the placeholder
redirect URI with the real `https://<sandbox>/auth/callback`. Finally the groups and grants. An error stops the run with progress saved;
running the same command again resumes. A second run on a finished fleet changes nothing, and never rotates a secret unless you pass
`--rotate` (or the state file lost one: client secrets are shown by the provider only once).

If you change an app's environment in the manifest after its sandbox exists, `apply` says so instead of silently ignoring it: delete
the sandbox (`agentpg sandbox delete <id>`) and run `apply` again; the database keeps its data.

## The manifest

```yaml
apps:
  cpsim:
    name: CityPulse Simulator
    env_prefix: CPSIM                       # the app's own variable prefix (default: the key upper-cased)
    roles: [{name: admin}, {name: operator}, {name: viewer}]
    exposes: [cpsim.read, cpsim.metrics]    # scopes other services may request FROM this app
    calls:                                  # scopes this app's service client may request FROM others
      citypulse: [citypulse.ingest]
      trustdesk: [trustdesk.requests.write]
```

A scope names its app by its prefix (`trustdesk.requests.write` is for `trustdesk`), the identity provider issues a service token for
**one app at a time**, and a call is only valid if the callee lists the scope under `exposes`: typos fail at `validate`, not in
production. An app with `calls` gets a second client, `<key>-service`, and the variables `<PREFIX>_SERVICE_CLIENT_ID/_SECRET`
beside the web client's `<PREFIX>_OIDC_CLIENT_ID/_SECRET`. Other keys: `image`, `port`, `extensions`, `env` (non-secret),
`generate` (names that get a random secret), `health`, `web: false` (service only), `deploy: false` (register only: a job or agent
that runs elsewhere, such as the PagerLens agent), `url_env` (which variable carries a callee's URL; default `<CALLEE>_URL`), `uses` (below), `llm` (true: wired to the LLM gateway, below).

### `uses`: an app's address without a scope

`calls` is for apps reached with an identity token: it registers a service client with that scope and injects the callee's URL.
An app can also reach another one **some other way** (CityPulse Simulator drives PayBroker with a merchant secret key, which no identity
scope can carry) and still need its address, and its address changes whenever that app is recreated. `uses` is that, with no scope:

```yaml
  cpsim:
    llm: true
    calls: {citypulse: [citypulse.ingest], trustdesk: [trustdesk.requests.write], pagerlens: [pagerlens.ingest], llmgw: [llmgw.use], shopfront: [shopfront.api]}
    uses: [paybroker, identity]     # PAYBROKER_URL and IDENTITY_URL; no service client, no scope
```

Each listed app (or `identity`, the provider) gets its URL injected as `<PREFIX>_URL` (override per app with `url_env`), is deployed **before**
this app, is pulled in by `--only`, and **`recreate` cascades**: `ecoctl recreate paybroker` also recreates cpsim so its environment
names PayBroker's new URL (recreating `identity` already recreates everything). Validated: an existing deployed app other than this one, not
listed twice, not also under `calls`, no cycles. With `calls` and `uses` together the simulator holds all seven fleet addresses
(`CITYPULSE_URL`, `PAYBROKER_URL`, `TRUSTDESK_URL`, `PAGERLENS_URL`, `LLMGW_URL`, `SHOPFRONT_URL`, `IDENTITY_URL`), which is what lets its cities, companies
and workloads follow the fleet with `${CITYPULSE_URL}`-style targets (see the simulator's README). A deployed sandbox's environment cannot change in place:
after adding `uses` to a running fleet, `ecoctl recreate cpsim` once.

## Wire an app to the LLM gateway (automatic)

Add `llm: true` to an app and `ecoctl apply --execute` does the rest, with no manual step:

```yaml
llm:                          # optional; these are the defaults
  gateway: llmgw              # the app that runs the gateway; deployed before the apps that use it
  chat_alias: chat-default    # aliases the gateway seeds on its built-in echo provider
  embed_alias: embed-default
  embed_dimensions: 1536      # EMBEDDING_DIM of the apps = size of the real embedding model behind embed-default (text-embedding-3-small); the code default is 768, the built-in echo model's size
  rpm: 60                     # requests/minute of each app's gateway client
  budget: 5000000             # tokens/month of each app's client (null: none)
  budget_mode: hard           # or soft
apps:
  trustdesk: {llm: true, ...}
```

`apply` generates a static operator key for the gateway (`LLMGW_API_KEY`), uses it to create **one client per app** (name = the app
key, allowed only the two aliases, with that limit and budget) and keeps each `lg_` key in `.ecosystem/state.json` (a lost key is rotated
and reported). Each app is created with `LLM_BASE_URL=<gateway>/v1`, `LLM_API_KEY` (secret, masked in plans), `LLM_CHAT_MODEL`,
`EMBEDDING_MODEL` and `EMBEDDING_DIM`. Echo models work at once; to use a real model add one provider key in the gateway and repoint
`chat-default` / `embed-default` there: every app follows, nothing else changes. A second `apply` changes nothing, and `recreate llmgw`
recreates the apps that use it (its URL changes). `ecoctl status` checks each client and makes one tiny chat call with its key;
`ecoctl llm test <app>` does just that call.

**Limits per app.** The `llm:` block above is the default. An app can override it with a mapping instead of `true` (every key optional;
`budget: null` = no budget; `aliases` = extra aliases besides the two defaults):

```yaml
apps:
  pagerlens: {llm: {rpm: 120, budget: 20000000, budget_mode: soft, aliases: [my-extra-alias]}, ...}
  trustdesk: {llm: true, ...}   # the global defaults
```

Limits are applied when the client is **first created**; `apply` never overwrites an existing client's limits (it only adds missing
default aliases, and says so). To change a running client use the gateway console/API, or make the gateway follow the manifest:

```
ecoctl llm sync                         # table of differences (app, field, gateway, manifest); changes nothing; exit 1 if any (CI)
ecoctl llm sync --apply trustdesk       # set only the differing fields of the named app(s); or --all
ecoctl llm sync --all --apply --prune-aliases   # also remove aliases the manifest does not list (default: kept, reported "extra, kept")
```

Missing aliases are always added; clients that do not exist yet are reported `missing, run apply`; keys are never touched.
`ecoctl status` shows a `limits` column (`DRIFT`) and a warning pointing to `ecoctl llm sync`. Details, rotation and upgrade notes: [docs/DEPLOY.md](docs/DEPLOY.md) section 9.

## Connect apps to each other (ShopFront and PayBroker)

An app can declare that ecoctl connects it to a peer once both are up, so nothing is copied by hand and a moved sandbox is followed:

```yaml
  shopfront:
    uses: [paybroker]
    exposes: [shopfront.api, shopfront.read, shopfront.metrics, shopfront.admin]
    connect:
      paybroker: {merchant: "ShopFront"}       # exact merchant name, 1-60 characters; unknown keys are refused
  paybroker:
    exposes: [paybroker.read, paybroker.metrics, paybroker.admin]
```

The app must `uses` or `calls` the peer and both `<app>.admin` scopes must be listed under `exposes` (the validator says which one is missing).
`apply --execute` then registers a service client `ecoctl` for those scopes and, after both apps are up (also at the end of `recreate`), reconciles:
find or create the merchant, set its allowed origins to exactly the shop's current URL (**managed**: other origins are replaced), keep two keys
`ecoctl` created (`ecoctl:<app>:secret|publishable`, values only in `.ecosystem/state.json`), set the webhook `<shop>/webhooks/paybroker` (its signing
secret rotates on every set, so only when the URL changed or the secret is lost), push PayBroker's URL, the keys and the secret to the shop (only fields whose
fingerprint differs) and run the shop's connection test. A re-run on a connected fleet makes no write call.

```bash
.venv/bin/ecoctl connect [app] [--dry-run] [--rotate]    # --rotate: new keys and a new signing secret; --dry-run: reads only
.venv/bin/ecoctl status                                    # ends with a CONNECTIONS table (origin, keys, webhook, test)
```

Details, rotation and what the state file holds: [docs/DEPLOY.md](docs/DEPLOY.md) section 9b.

## Limits

- Tested against a fake `agentpg` CLI and, for the identity side, against the real provider's admin API. **It was run against a live
  AgentPG account on 2026-10-22 (see docs/DEPLOY.md for what was verified).** The CLI output formats follow the deployment tutorial (id = first column of the listing line that
  names the object, URL after `URL:`); if your CLI prints something else, `agentpg.py` is the one place to adjust.
- A sandbox's environment cannot be changed in place by this tool (see above).
- One database per app. Secrets reach the sandbox as `--env` values, so they pass through the process list of the machine running
  `ecoctl` for a moment; run it from a machine you trust.
- No `destroy` command on purpose: delete sandboxes and instances with `agentpg` yourself.

## Development

```bash
make lint && make test        # the identity integration test needs TEST_DATABASE_URL and ../identity, ../agentpg-kit installed
```
