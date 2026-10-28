# Deploying the fleet on AgentPG with Ansible

> **Status (2026-10-28).** The recipe in [`ansible/`](../ansible/) deploys the same fleet as `ecoctl`, **on AgentPG**, for people who drive their infrastructure
> with Ansible: it creates the instances, extensions and sandboxes through the `agentpg` CLI and does the identity, gateway, access and connect steps
> through the apps' own HTTPS APIs. `ecoctl` is unchanged and stays the faster, fuller tool. A second target, `eco_target: hosts`, puts the same fleet on
> your own Postgres and Docker hosts (section 11).
>
> **It has not yet touched a real AgentPG account.** Everything below was run against stand-ins (section 10). Run the dry run in section 4 first.

## 1. Who this is for

Customers and clients who deploy with Ansible. The recipe reads the same [`ecosystem.yaml`](../ecosystem.yaml) as `ecoctl` (apps, roles, scopes, extensions,
`generate:` secrets, LLM settings, sizes, images), so nothing is written twice, and turns each step of `ecoctl apply` into an idempotent task you can
tag, check (`--check`), vault, and put in your own pipeline.

## 2. `ecoctl` or Ansible on AgentPG

| | `ecoctl` | Ansible recipe (`eco_target: agentpg`) |
|---|---|---|
| Creates | instances, extensions, sandboxes (`agentpg` CLI), registers apps, groups, gateway clients, connects ShopFront to PayBroker | the same, with the same `agentpg` commands, as three small modules (`agentpg_instance`, `agentpg_extension`, `agentpg_sandbox`) plus HTTP tasks |
| Order | identity, then apps callees-first | identical |
| Use it when | you want the fleet and its operations (status, llm sync, insights, MCP, db keys) from one tool | your team already runs everything through Ansible: inventories, tags, `--check`, Vault, CI, AWX |
| Dry run | `ecoctl plan` prints every command | `--check` reads from AgentPG and reports what it would create or change; `agentpg` commands are not printed |
| State and secrets | `.ecosystem/state.json` | `ansible/.secrets/<fleet>/` (generated once, mode 0600) plus `ansible-vault` for values you supply |
| Changed environment | reported by `apply`; `ecoctl recreate` applies it | reported; `eco_env_change: recreate` or `-e '{"eco_recreate": [...]}'` applies it, with dependents |
| Operations after deploy | `status`, `llm test|sync`, `insights`, `mcp ...`, `db`, `env`, `connect` | `verify.yml` (health, one LLM call per client, registry). The rest: use `ecoctl`, or the AgentPG CLI |
| Speed | the same: minutes, mostly AgentPG creating instances and sandboxes | the same, plus Ansible's own overhead |

**Do not run both tools against the same fleet.** Each keeps its own state; client secrets and database keys are shown once, so the tool that did not create
them cannot recover them (Ansible would **rotate** every client secret it does not hold, and the running sandboxes would keep the old ones until recreated).
Pick a different `fleet:` name (it prefixes every instance and sandbox) when you try the recipe next to a fleet `ecoctl` built.

## 3. Prerequisites

- **AgentPG account and CLI**, logged in as the user who runs Ansible: `agentpg login`, then `agentpg account` (if it says "Invalid or expired token", log in again).
  Capacity for eight instances and eight sandboxes ([DEPLOY section 2](DEPLOY.md#2-prerequisites)); deploy a slice with `eco_only` if your plan allows fewer.
- **Public images.** AgentPG pulls them anonymously, so the packages must be public ([DEPLOY section 3](DEPLOY.md#3-make-the-images-public)).
- **Control node:** `ansible-core` (written and checked with 2.21; older versions are untested) and Python 3.9+. It runs everything, needs no SSH hosts, and must
  reach the sandboxes' https URLs. Nothing else to install for this target.
- A machine you trust: secrets reach sandboxes as `--env` values on the `agentpg` command line, so they pass through that machine's process list for a moment
  (the same as `ecoctl`), and `ansible/.secrets/` holds every secret (section 7).

## 4. A first dry run (reads only)

Use a **separate fleet name** for a first look, so nothing is matched against a fleet `ecoctl` runs (its instances and sandboxes would be *found by name*, and a real run would
then rotate their client secrets, section 2):

```bash
cd ecosystem/ansible
python3 -m venv .venv && .venv/bin/pip install ansible-core
agentpg account                                                       # logged in? (agentpg login otherwise)
sed 's/^fleet: eco /fleet: ecoans /' ../ecosystem.yaml > /tmp/ecosystem-ansible-test.yaml
.venv/bin/ansible-playbook site.yml --check -e eco_manifest_path=/tmp/ecosystem-ansible-test.yaml
```

`--check` runs only `agentpg instances list` and `agentpg sandbox list` and prints, per app, lines such as
`instance ecoans-trustdesk-db would be created; sandbox: would create sandbox ecoans-trustdesk from ghcr.io/elchinoo/trustdesk:latest`. It creates nothing on AgentPG and
does not call any app. It does write `ansible/.secrets/` (the generated keys) because the secret lookups run on the control node regardless; the files are reused by
the real run.

## 5. Run it

```bash
cd ecosystem/ansible
.venv/bin/ansible-playbook site.yml                                  # everything (inventories/agentpg is the default inventory)
.venv/bin/ansible-playbook site.yml -e '{"eco_only": ["shopfront"]}' # one app and what it needs (the gateway, PayBroker, identity)
.venv/bin/ansible-playbook site.yml --tags identity,gateway          # one stage
.venv/bin/ansible-playbook verify.yml                                # a report like `ecoctl status`; fails when an app is unhealthy
```

Stages, their tags, and what each does on AgentPG:

| Stage (tag) | What it does | `ecoctl` equivalent |
|---|---|---|
| prepare (always) | reads `ecosystem.yaml`, checks the variables, generates the secrets once | `State`, `validate` |
| `identity` | instance `eco-identity-db`, its extensions, sandbox `eco-identity`; waits for `/readyz`; checks the issuer equals the sandbox URL | `_deploy`, `_ready` |
| `gateway` | registers the gateway with identity, then its instance, extensions, sandbox; waits for `/readyz` | `_register`, `_deploy` of `llmgw` |
| `apps` | per app, **callees before callers**: register its web and service clients, create its gateway client and key, create instance, extensions and sandbox with the app's environment, point its redirect URI at the sandbox URL | `_register`, `_llm_client`, `_deploy`, `_finish_urls` |
| `access` | groups and role grants of `access.groups` (`*` means every web app that has the role) | `_group` |
| `connect` | ShopFront to PayBroker: service tokens, merchant, one allowed origin, two keys, the webhook (the signing secret rotates only when needed), the shop's settings by fingerprint, the shop's own test | `ecoctl connect` |
| `verify` | `/healthz`, `/readyz`, one chat call per gateway client, the registry | `ecoctl status` |

The commands are those of `ecoctl` (`agentpg create --name N --size S --json`, `extensions create <ext> <instance>`, `sandbox create N --image I --instance ID --port P
[--auto-pause=false] [--plan X] --env K=V ...`, `sandbox delete ID --force`); `tests/test_ansible_recipe.py` compares their arguments with `ecoctl.agentpg.AgentPG`, and the
environment of every app with `ecoctl.plan.app_env`, so the recipe cannot drift. A sandbox gets exactly the environment `ecoctl` gives it
(`DATABASE_URL` is injected by AgentPG when the sandbox is linked to its instance).

**Re-runs converge.** Instances and sandboxes are found by name (`eco-<app>-db`, `eco-<app>`), so a lost state file is not fatal for them; extensions are recorded and
created once; an existing sandbox is left alone.

### Changing an environment, recreating

A sandbox's URL contains its id, so recreating a sandbox gives it a new URL and everything that calls it needs a new environment. As in `ecoctl`:

- A sandbox whose environment differs from the one it was created with is **reported**, not changed (`... exists with a different environment`).
- `-e eco_env_change=recreate` deletes and creates again every such sandbox (its database, and so its data, is kept; the instance and extensions are not touched).
- `-e '{"eco_recreate": ["llmgw"]}'` is `ecoctl recreate llmgw`: that sandbox and every app that depends on it (callers, or for the gateway its users); `identity` recreates all.
  The new URLs flow into the dependents' environments in the same run.
- A new image is picked up the same way (AgentPG pulls on creation): recreate.

## 6. How it maps to platform, scaffolding and samples

The three labels of [GUIDE section 1.1](GUIDE.md#11-what-is-the-platform-what-is-scaffolding-what-is-a-sample), and the variables that swap the swappable ones:

| Label | In the recipe | Swap it with |
|---|---|---|
| **Platform** (AgentPG, EDB Postgres) | the three `agentpg_*` modules; instance size, auto-pause and plan come from the manifest | not swappable here; for your own Postgres use `eco_target: hosts` |
| **Scaffolding**: identity | `eco_identity_mode: managed` creates the `identity` sandbox | `existing` (a provider that already runs: `eco_identity_url`) or `oidc` (any OIDC provider: `eco_oidc_clients`) |
| **Scaffolding**: LLM gateway | `eco_llm_mode: managed` creates the `llm-gateway` sandbox, one client and key per app | `existing` (`eco_llm_gateway_url`) or `openai` (`eco_llm_base_url`, `eco_llm_api_key`) |
| **Scaffolding**: vault, kit | inside every image | not a setting |
| **Scaffolding**: `ecoctl` | replaced by `site.yml` | either tool (not both on one fleet) |
| **Sample apps** | one sandbox per app from `ghcr.io/elchinoo/<image>:<tag>` | `eco_only`, `eco_image_tag`, `eco_image_registry` |

### Pointing at a customer's existing identity provider or gateway

The apps only read `<PREFIX>_OIDC_ISSUER`, `_CLIENT_ID`, `_CLIENT_SECRET` and `LLM_BASE_URL`, `LLM_API_KEY`:

```yaml
# inventories/agentpg/group_vars/all/main.yml
eco_identity_mode: oidc                        # not created, nothing registered by Ansible
eco_identity_url: https://login.corp.example.com/realms/eco     # the issuer
eco_llm_mode: openai                           # the gateway is not created either
eco_llm_base_url: https://llm.corp.example.com/v1
```

```yaml
# a file you encrypt with ansible-vault (section 7)
eco_llm_api_key: "..."
eco_oidc_clients:
  trustdesk: {client_id: trustdesk, client_secret: "..."}
  paybroker: {client_id: paybroker, client_secret: "...", service_client_id: paybroker-service, service_client_secret: "..."}
```

The conditions are those of [GUIDE section 1.1](GUIDE.md#11-what-is-the-platform-what-is-scaffolding-what-is-a-sample): standard OpenID Connect **and** a `roles` claim with
app-namespaced role names (`trustdesk:admin`) and, for service clients, a `scope` claim; only our own identity has been tested. You create the clients, roles and groups in
your provider; in `oidc` mode this recipe registers nothing, creates no groups and does not run `connect`. `existing` means **our** `identity` already running: the recipe
still registers every app in it, with its admin key in `eco_secret_overrides["identity/IDENTITY_API_KEY"]`. Embedding size must match `embed_dimensions`.

## 7. State and secrets

Everything generated lives in `ansible/.secrets/<fleet>/` (git-ignored; directories `0700`, files `0600`):

- `generated/<app>/<NAME>`: the `generate:` encryption keys, the identity admin key, the gateway operator key. Created on first use by Ansible's `password` lookup, never changed afterwards.
- `issued/<app>/<kind>`: values a service shows **once**: client secrets, gateway keys, merchant keys, the webhook signing secret. Kept the moment they are issued; a lost one is replaced on
  the next run (a client secret or gateway key is rotated) and the sandbox that holds it needs recreating (`eco_recreate`).
- `agentpg/<app>.json`: what `ecoctl` keeps in `state.json` for an app: `instance_id`, `sandbox_id`, `url`, the extensions created, the environment hash and the **database key**
  (AgentPG shows it once, at creation; it is also the database password). The modules never print it.

Treat the directory like a password file and back it up privately: it is the only copy. To keep it encrypted between runs:

```bash
tar czf eco-secrets.tgz .secrets && ansible-vault encrypt eco-secrets.tgz && rm -r .secrets     # later: ansible-vault decrypt eco-secrets.tgz && tar xzf eco-secrets.tgz
```

Values **you** supply go into a file you encrypt:

```bash
cp inventories/agentpg/group_vars/all/vault.yml.example inventories/agentpg/group_vars/all/vault.yml
$EDITOR inventories/agentpg/group_vars/all/vault.yml
ansible-vault encrypt inventories/agentpg/group_vars/all/vault.yml
ansible-playbook site.yml --ask-vault-pass                           # or --vault-password-file ~/.eco-vault-pass
ansible-vault edit inventories/agentpg/group_vars/all/vault.yml
ansible-vault encrypt_string --stdin-name eco_llm_api_key            # one value, to paste into a plain vars file
```

`eco_secret_overrides` (`"<app>/<NAME>": value`) replaces any generated secret. The repository ships only `vault.yml.example`, and a test fails if a plain `vault.yml` or a
secret-looking literal appears in `ansible/`. Tasks that handle secrets use `no_log`; the modules take secrets in `no_log` parameters and never return them; do not run with `-vvv`
on a shared terminal. Check mode still creates missing generated secrets (section 4).

## 8. What Ansible does not do that `ecoctl` does

- `status` beyond `verify.yml`: no live CPU/memory/network, no drift table of gateway limits (`ecoctl llm sync`), no connection table.
- `ecoctl llm test|sync`, `insights`, `mcp users|config|serve|claude|check`, `db <app>` (print or reset a database key), `env <app> --reveal`, `plan` (the printed command list).
- Rotation on request: delete a file under `.secrets/` and re-run (the lost secret is re-issued); there is no `--rotate`.
- Confirmation prompt and parallel metric reads. Ansible runs what you tell it to.
- Recovering a database key that was not saved: if the instance exists but `agentpg/<app>.json` has no key, the recipe cannot reset it (`ecoctl db --reset` can).
- Teardown (neither tool has a destroy command; [DEPLOY section 14](DEPLOY.md#14-tear-it-all-down)).
- `--check` is not `plan`: it shows what would be created or changed on AgentPG, not the identity, gateway and connect calls (they need running apps).
- A new image tag alone does not recreate a sandbox (the environment hash does not include it, as in `ecoctl`): use `eco_recreate`.

Not different: the apps, their environment, the order, the secrets that reach them, the groups and the connection are the same, and the platform's extensions, auto-pause and MCP are
AgentPG's whichever tool created the fleet.

## 9. Tags, files

`ansible/`: `site.yml` (the stages), `infra_hosts.yml` (hosts target only), `verify.yml`, `library/agentpg_{instance,extension,sandbox}.py` with `module_utils/eco_agentpg.py`
(the CLI wrapper; `ansible-doc -M library agentpg_sandbox` shows each module), `plugins/filter/eco.py` (reads the manifest), `roles/` (`common`, `identity`, `llm_gateway`, `app`,
`connect`, `verify`; for hosts also `container_host`, `postgres_db`, `proxy`), `inventories/agentpg` (default), `inventories/own-infra`, `inventories/own-infra-local`.
All variables and their defaults: `roles/common/defaults/main.yml`.

## 10. What was verified (and what was not)

| Check | Result |
|---|---|
| `ansible-playbook --syntax-check`, `ansible-inventory --graph`, `ansible-lint`, `yamllint`, `ansible-doc` of the modules | pass |
| `tests/test_ansible_recipe.py` (manifest sync, env of every app equal to `ecoctl.plan.app_env` for both targets, order, specs, command arguments equal to `ecoctl.agentpg.AgentPG`, the modules' behaviour against ecoctl's in-memory fake CLI: create, converge, changed environment, recreate, deleted sandbox, existing sandbox, check mode, wait loops, state mode 0600) | pass |
| The **whole AgentPG-target playbook** against a **stand-in `agentpg` executable** (ecoctl's fake CLI, with state kept between calls) and ecoctl's in-memory identity, gateway, PayBroker and ShopFront fakes served over HTTP: create, no-op re-run (0 mutating calls), `--check` (only `instances list` and `sandbox list`), `eco_recreate` of the gateway (sandbox deleted and created again for it and its dependents, instances and extensions untouched), changed environment reported then applied, interrupted run resumed | ran |
| The real `agentpg` CLI, a real AgentPG account, the real identity, gateway, PayBroker and ShopFront servers | **not run** |
| The `hosts` target: its `db` stage against a real scratch Postgres 17, the rest against a stand-in `docker` | ran; real Docker, Caddy and TLS **not run** |

What this means: the command shapes and the parsing of the CLI's listings are ecoctl's, tested against ecoctl's fake. If the real CLI's output differs from what `ecoctl` expects, both
break the same way (`agentpg.py` in `ecoctl` documents the format seen on 2026-10-22). The PayBroker and ShopFront field names are the ones `ecoctl` assumes from the service contracts.

## 11. Secondary: your own infrastructure (`eco_target: hosts`)

The same fleet on a Postgres server and container hosts you run, for customers who cannot or will not use AgentPG. Use `inventories/own-infra` (db host plus app hosts) or
`inventories/own-infra-local` (one machine, Docker, a throwaway pgvector database), and set `eco_target: hosts` (the inventories do). The shared stages are unchanged; instead of
`agentpg_*` modules the recipe adds:

- `hosts`: engine answers (Docker 24+, or Podman with `eco_container_engine: podman`), directories, the `eco-net` network.
- `db`: one login role and database per app, and every extension the server lists in `pg_available_extensions`; the others (`query_advisor`, `edb_*`, PostGIS on plain Postgres) are
  **reported, not an error**. `pgvector` is required. Needs Postgres 17+, `python3-psycopg2` on the db host, and `pg_hba.conf`/`listen_addresses` set by you (the recipe edits neither).
  Requires `ansible-galaxy collection install -r requirements.yml` (`community.postgresql`).
- `proxy`: Caddy with `https://<app>.<eco_domain>` (`eco_tls_mode`: `acme`, `internal` for demos, or `none` behind your own TLS) and network aliases so containers resolve the public names.
- containers instead of sandboxes: `docker run` with a `0600` env file; the environment is the same plus `DATABASE_URL`, `IDENTITY_PUBLIC_URL`, `PORT` (if not 8000) and `SSL_CERT_FILE`.

```bash
ansible-playbook -i inventories/own-infra site.yml --check
ansible-playbook -i inventories/own-infra-local site.yml            # one machine; see the comments in the inventory
```

You do **not** get AgentPG's extensions beyond what your server has, auto-pause, the restricted MCP server, `agentpg db-users`/`recommend`, instance backups and lifecycle, or any hardening
beyond the proxy; and a run with `eco_only` rewrites the proxy for that slice only. This path was exercised against stand-ins only (section 10). Assumptions to confirm: the images listen on
`$PORT` and read `DATABASE_URL` and `SSL_CERT_FILE` (the kit and httpx do); apps accept `https://identity.<domain>[:port]` as the issuer; one database per app on a shared server is acceptable.

## 12. Assumptions to confirm for the AgentPG target

1. `agentpg` prints the listings and `sandbox status` the way `ecoctl/agentpg.py` expects (a header row, a dashed row, `Status:` and `URL:` lines).
2. The CLI is non-interactive once logged in, and a second `agentpg` process may run while the first waits (Ansible forks one task at a time here; nothing runs in parallel).
3. The control node reaches the sandboxes' https URLs; `eco_validate_certs` defaults to true for this target.
