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
PayBroker and the LLM gateway all read the same `<PREFIX>_OIDC_*` variables.

**Full walkthrough for a first live deployment: [docs/DEPLOY.md](docs/DEPLOY.md).**

## Use

```bash
uv venv && uv pip install -e ".[dev]"
.venv/bin/ecoctl validate                # check the manifest
.venv/bin/ecoctl plan                    # show every command and API call; changes nothing
.venv/bin/ecoctl apply                   # the same, as a dry run
.venv/bin/ecoctl apply --execute         # asks first, then creates and registers; safe to run again
.venv/bin/ecoctl status                  # health of each app, and drift from the identity registry
.venv/bin/ecoctl env trustdesk           # the environment an app is created with (contains secrets)
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
that runs elsewhere, such as the PagerLens agent), `url_env` (which variable carries a callee's URL; default `<CALLEE>_URL`).

## Wire an app to the LLM gateway

The gateway holds the model provider keys. In the gateway's console create a client for the app (it shows a key once), then in the
app add an OpenAI-compatible account with base URL `https://<gateway>/v1`, that key, and a model alias as the chat model. See the
gateway's README.

## Limits

- Tested against a fake `agentpg` CLI and, for the identity side, against the real provider's admin API. **It has not been run
  against a live AgentPG account.** The CLI output formats follow the deployment tutorial (id = first column of the listing line that
  names the object, URL after `URL:`); if your CLI prints something else, `agentpg.py` is the one place to adjust.
- A sandbox's environment cannot be changed in place by this tool (see above).
- One database per app. Secrets reach the sandbox as `--env` values, so they pass through the process list of the machine running
  `ecoctl` for a moment; run it from a machine you trust.
- No `destroy` command on purpose: delete sandboxes and instances with `agentpg` yourself.

## Development

```bash
make lint && make test        # the identity integration test needs TEST_DATABASE_URL and ../identity, ../agentpg-kit installed
```
