# Deploying the fleet on AgentPG with Ansible

> **Status (2026-10-28).** The recipe in [`ansible/`](../ansible/) deploys the same fleet as `ecoctl`, **on AgentPG**, for people who drive their infrastructure
> with Ansible: it creates the instances, extensions and sandboxes through the `agentpg` CLI and does the identity, gateway, access and connect steps
> through the apps' own HTTPS APIs. `ecoctl` is unchanged and stays the faster, fuller tool. A second target, `eco_target: hosts`, puts the same fleet on
> your own Postgres and Docker hosts (section 11).
>
> **Verified on a real AgentPG account (2026-10-29), throwaway fleet `ecoans`:** a complete apply of all eight apps from an empty fleet in one run (14 minutes,
> `failed=0`); a second `site.yml` with `changed=0`; `verify.yml` passing (all eight sandboxes 200, five LLM clients answering, 11 identity clients, masking drift 0,
> ShopFront's connection test to PayBroker passing); and a guarded `destroy.yml` that left the account at its starting 9 instances with the real `eco` fleet untouched.
> Not verified: the `eco_target: hosts` path on real hosts, and `eco_env_change: recreate` on a real fleet. Run the dry run in section 4 first.
>
> To try it without touching a fleet you run, use `ansible/throwaway.sh apply|verify|preview|destroy`: it builds the fleet `ecoans` from a copy of the manifest.

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
- **Control node, AgentPG target: `ansible-core` only** (written and checked with 2.21; older versions are untested) and Python 3.9+. No collections, no extra Python packages, no
  SSH hosts. It runs everything and must reach the sandboxes' https URLs. (A test fails if a collection module, filter or lookup creeps into this path.) `requirements.yml` (the
  `community.postgresql` collection) is for the hosts target only.
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
.venv/bin/ansible-playbook verify.yml                                # a report like `ecoctl status`; fails when an app is unhealthy or a password vault is locked
.venv/bin/ansible-playbook unlock.yml                                # after a platform restart: open the vaults of identity and the gateway (section 7a)
```

Stages, their tags, and what each does on AgentPG:

| Stage (tag) | What it does | `ecoctl` equivalent |
|---|---|---|
| prepare (always) | reads `ecosystem.yaml`, checks the variables, generates the secrets once | `State`, `validate` |
| `identity` | instance `eco-identity-db`, its extensions, sandbox `eco-identity`; waits for `/healthz`; **creates or opens its vault with the admin password**; waits for `/readyz` (it answers only once it can sign); checks the issuer equals the sandbox URL | `_deploy`, vault step, `_ready` |
| `gateway` | registers the gateway with identity, then its instance, extensions, sandbox; **creates or opens its vault**; waits for `/readyz` | `_register`, `_deploy`, vault step of `llmgw` |
| `apps` | per app, **callees before callers**: register its web and service clients, create its gateway client and key, create instance, extensions and sandbox with the app's environment, point its redirect URI at the sandbox URL | `_register`, `_llm_client`, `_deploy`, `_finish_urls` |
| `access` | groups and role grants of `access.groups` (`*` means every web app that has the role) | `_group` |
| `connect` | ShopFront to PayBroker: service tokens, merchant, one allowed origin, two keys, the webhook (the signing secret rotates only when needed), the shop's settings by fingerprint, the shop's own test | `ecoctl connect` |
| `verify` | `/healthz`, `/readyz`, the vault state of every app, one chat call per gateway client, the registry | `ecoctl status` |
| `unlock` (playbook `unlock.yml`) | opens the vault of every app with `vault: password`, identity first; never creates one | `ecoctl unlock` |

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

## 6a. Tear down, and the throwaway-fleet workflow

`destroy.yml` removes a fleet: its sandboxes, then its instances (**a database and its data are gone for good**). Nothing else.

```bash
cd ecosystem/ansible
.venv/bin/ansible-playbook destroy.yml --check -e eco_manifest_path=/tmp/ecosystem-ansible-test.yaml -e eco_destroy_confirm=ecoans   # prints the list, deletes nothing
.venv/bin/ansible-playbook destroy.yml         -e eco_manifest_path=/tmp/ecosystem-ansible-test.yaml -e eco_destroy_confirm=ecoans   # deletes
```

Safety rails (each is tested):

- **Confirmation.** It refuses unless `eco_destroy_confirm` equals the manifest's `fleet:` exactly, and only for `eco_target: agentpg`. It checks this before it reads the secrets directory or calls `agentpg`.
- **Exact names only.** It deletes the sandboxes `<fleet>-<app>` and the instances `<fleet>-<app>-db` of the apps this recipe creates, nothing matched by prefix: with fleet `ecoans`, an instance
  called `eco-trustdesk-db` or `ecoans-trustdesk-db-old` is never touched. Every deletion carries the list of allowed names and the module refuses any other.
- **Ids come from AgentPG, not the state file.** Each object is resolved by name from `agentpg sandbox list` / `agentpg instances list` at run time; a different id in the state file is only mentioned.
- **Order.** Sandboxes first, reverse of creation (the apps that call others, then the callees, identity last), then the instances.
- **Prints first.** The full list, then what exists now; in `--check` that is all it does.
- **Idempotent.** An object already gone is reported (`already gone`) and is not a change; a second run changes nothing.
- `-e '{"eco_destroy_only": ["trustdesk"]}'` deletes just those apps (identity only if you list `identity`).

What it does **not** delete: anything outside the fleet name; the local `ansible/.secrets/<fleet>/` (generated keys, client secrets, the database keys), unless you add `-e eco_destroy_secrets=true`
(it then removes that directory, and only when it is named after the fleet; the default prints a reminder); the apps' `ghcr.io` images; anything created by hand in the sandboxes. The state
of an app (`agentpg/<app>.json`) is removed together with its instance, since it describes an instance that no longer exists. `eco_target: hosts` has no teardown (remove the containers and databases yourself).

**Recommended throwaway-fleet workflow** (separate fleet name `ecoans`, so a fleet `ecoctl` runs is never involved):

```bash
cd ecosystem/ansible
sed 's/^fleet: eco /fleet: ecoans /' ../ecosystem.yaml > /tmp/ecosystem-ansible-test.yaml
M="-e eco_manifest_path=/tmp/ecosystem-ansible-test.yaml"
.venv/bin/ansible-playbook site.yml --check $M                                  # 1. dry run (reads only)
.venv/bin/ansible-playbook site.yml $M                                          # 2. apply
.venv/bin/ansible-playbook verify.yml $M                                        # 3. verify
.venv/bin/ansible-playbook destroy.yml --check $M -e eco_destroy_confirm=ecoans # 4. what a teardown would delete
.venv/bin/ansible-playbook destroy.yml $M -e eco_destroy_confirm=ecoans -e eco_destroy_secrets=true   # 5. drop everything, local secrets included
```

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

## 7a. The vault password (identity and the gateway)

The manifest sets `vault: password` for identity and the gateway: their sandboxes get **no** `IDENTITY_ENCRYPTION_KEY` / `LLMGW_ENCRYPTION_KEY`, and their vaults are created and opened with an admin password that
the platform never stores (policy, threat model and limits: [GUIDE section 9a](GUIDE.md#9a-the-vault-policy-identity-and-the-gateway-are-opened-by-the-operator), [SECURITY.md](../SECURITY.md)). The recipe does what
`ecoctl` does, over the apps' own API (`GET /api/session`, `POST /api/admin-password` the first time, `POST /api/vault/unlock` after a restart). Where the password comes from:

| Source | How |
|---|---|
| Your own, in Ansible Vault | `eco_admin_password: "..."` in `inventories/agentpg/group_vars/all/vault.yml` (encrypted, see above) |
| Your own, from the environment | `ECO_ADMIN_PASSWORD` on the control node (the default of `eco_admin_password`); use your password manager's CLI, not a typed value in shell history |
| Prompt | `-e eco_admin_password_prompt=true` (hidden, never written down) |
| Generated once | none of the above: a strong one is written to `.secrets/<fleet>/vault/admin_password` (mode 0600, git-ignored) on the first run; the run says so, and you copy it to your password manager. The password is not printed |

A vault that already exists is never given a made-up password: if no password was given and the generated file did not exist before the run, the play stops. Every task that touches the password or the operator keys has
`no_log: true` (a test checks all of them, and a real run at `-vvv` against local services checks nothing leaks).

`site.yml` creates the vaults in order (identity: container, `/healthz`, **vault**, `/readyz`, issuer; the gateway: container, vault; only then clients and dependents), and re-runs change nothing (`changed=0`: an open vault is
left alone). **After a platform restart** run `ansible-playbook unlock.yml`: it opens what is locked (`changed` counts the vaults it opened) and never creates one (`-e eco_vault_create=true` lifts that). Until it has run,
identity cannot sign (new sign-ins and service tokens fail; issued tokens keep working until they expire) and the gateway cannot use a stored provider key. `verify.yml` reports each app's vault (`unlocked`, `LOCKED`, `auto`) and
fails while a `vault: password` app is locked. With `eco_identity_mode` or `eco_llm_mode` other than `managed` the recipe does not run that app and leaves its vault to its owner.

## 8. What Ansible does not do that `ecoctl` does

- `status` beyond `verify.yml`: no live CPU/memory/network, no drift table of gateway limits (`ecoctl llm sync`), no connection table.
- `ecoctl llm test|sync`, `insights`, `mcp users|config|serve|claude|check`, `db <app>` (print or reset a database key), `env <app> --reveal`, `plan` (the printed command list).
- Rotation on request: delete a file under `.secrets/` and re-run (the lost secret is re-issued); there is no `--rotate`.
- Confirmation prompt and parallel metric reads. Ansible runs what you tell it to.
- Recovering a database key that was not saved: if the instance exists but `agentpg/<app>.json` has no key, the recipe cannot reset it (`ecoctl db --reset` can).
- Teardown: the recipe has one (`destroy.yml`, section 6a); `ecoctl` deliberately has none ([DEPLOY section 14](DEPLOY.md#14-tear-it-all-down)).
- `--check` is not `plan`: it shows what would be created or changed on AgentPG, not the identity, gateway and connect calls (they need running apps).
- A new image tag alone does not recreate a sandbox (the environment hash does not include it, as in `ecoctl`): use `eco_recreate`.

Not different: the apps, their environment, the order, the secrets that reach them, the groups and the connection are the same, and the platform's extensions, auto-pause and MCP are
AgentPG's whichever tool created the fleet.

## 9. Tags, files

`ansible/`: `site.yml` (the stages), `unlock.yml` (open the password vaults after a restart), `destroy.yml` (teardown), `infra_hosts.yml` (hosts target only), `verify.yml`, `library/agentpg_{instance,extension,sandbox}.py` with `module_utils/eco_agentpg.py`
(the CLI wrapper; `ansible-doc -M library agentpg_sandbox` shows each module), `plugins/filter/eco.py` (reads the manifest), `roles/` (`common`, `identity`, `llm_gateway`, `app`,
`connect`, `verify`, `vault`; for hosts also `container_host`, `postgres_db`, `proxy`), `inventories/agentpg` (default), `inventories/own-infra`, `inventories/own-infra-local`.
All variables and their defaults: `roles/common/defaults/main.yml`.

## 10. What was verified (and what was not)

| Check | Result |
|---|---|
| `ansible-playbook --syntax-check`, `ansible-inventory --graph`, `ansible-lint`, `yamllint`, `ansible-doc` of the modules | pass |
| `tests/test_ansible_recipe.py` (manifest sync, env of every app equal to `ecoctl.plan.app_env` for both targets, order, specs, command arguments equal to `ecoctl.agentpg.AgentPG`, the modules' behaviour against ecoctl's in-memory fake CLI (plus an `instances delete`): teardown leaves the fake account empty, a second one changes nothing, wrong confirmation refused, a similarly named foreign instance untouched, check mode deletes nothing; create, converge, changed environment, recreate, deleted sandbox, existing sandbox, check mode, wait loops, state mode 0600) | pass |
| The **whole AgentPG-target playbook** against a **stand-in `agentpg` executable** (ecoctl's fake CLI, with state kept between calls) and ecoctl's in-memory identity, gateway, PayBroker and ShopFront fakes served over HTTP: create, no-op re-run (0 mutating calls), `--check` (only `instances list` and `sandbox list`), `eco_recreate` of the gateway (sandbox deleted and created again for it and its dependents, instances and extensions untouched), changed environment reported then applied, interrupted run resumed | ran |
| The AgentPG path in a **clean venv with only `ansible-core`** and no collections visible (`ANSIBLE_COLLECTIONS_PATH=/nonexistent`): syntax check, inventory, `--check` and apply | ran |
| The vault tasks (`unlock.yml`, the role `vault`) with real Ansible against **local stand-ins** of the two APIs (`tests/test_ansible_recipe.py`), and against the **real identity and llm-gateway** started locally on a throwaway Postgres: create, `changed=0` re-run, restart, unlock (`changed=2`), wrong password refused, password never in the output at `-vvv` | ran |
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
