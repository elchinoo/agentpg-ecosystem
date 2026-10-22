# Deploying the ecosystem on AgentPG

This guide takes you from nothing to a running fleet you can test live: an **identity provider**, six apps that sign in through it, and the
shared **LLM gateway**, every one a sandbox with its own Postgres database on [AgentPG](https://www.agentpg.dev/). It uses `ecoctl`
(this repository) for the repetitive parts and tells you exactly what is manual.

> **Status of this procedure.** Run end to end on a live AgentPG account on 2026-10-22: all seven sandboxes came up healthy, single sign-on
> worked on every app (scripted browser flow against the real identity provider, role `<app>:admin` and `*` permissions), the Simulator fed
> CityPulse with a minted service token (93 readings sent, 0 failures), CityPulse reported to PagerLens (3 services healthy), the Simulator's
> companies created 14 requests in TrustDesk, PayBroker processed 60 demo payments (62% approved, p95 137 ms with simulated connector latency), and
> TrustDesk called the model through the gateway as an ordinary account. **Not seen live:** the UIs in a browser (the author's browser pane could not
> open these URLs), Google/GitHub login, and any real model provider. The first live run found six problems, all fixed (see the end of section 12).

## Contents
1. [What you will create](#1-what-you-will-create)
2. [Prerequisites](#2-prerequisites)
3. [Make the images public](#3-make-the-images-public)
4. [Install ecoctl and check the manifest](#4-install-ecoctl-and-check-the-manifest)
5. [Deploy in stages](#5-deploy-in-stages)
6. [First sign-in: the identity provider](#6-first-sign-in-the-identity-provider)
7. [Sign in to each app](#7-sign-in-to-each-app)
8. [Smoke tests, app by app](#8-smoke-tests-app-by-app)
9. [Wire the LLM gateway into the apps](#9-wire-the-llm-gateway-into-the-apps)
10. [Run the simulators and the PagerLens agent](#10-run-the-simulators-and-the-pagerlens-agent)
11. [Operating it](#11-operating-it)
12. [Troubleshooting](#12-troubleshooting)
13. [Security checklist before you share a URL](#13-security-checklist-before-you-share-a-url)
14. [Tear it all down](#14-tear-it-all-down)

---

## 1. What you will create

| Sandbox | Image | What it is | Depends on |
|---|---|---|---|
| `eco-identity` | `ghcr.io/elchinoo/identity` | OIDC provider: users, groups, per-app roles, Google/GitHub login, signing keys | nothing |
| `eco-trustdesk` | `.../trustdesk` | Compliance workspace (policies, employee requests with AI pre-validation) | identity |
| `eco-pagerlens` | `.../pagerlens` | Observability and incident investigation | identity |
| `eco-citypulse` | `.../citypulse` | Smart-city IoT sample that reports to PagerLens | identity, pagerlens |
| `eco-cpsim` | `.../citypulsesimulator` | Simulated sensors and companies that generate load | identity, citypulse, trustdesk, pagerlens |
| `eco-paybroker` | `.../paybroker` | Payment broker with a three-tier fraud engine | identity |
| `eco-llmgw` | `.../llm-gateway` | One place for model provider keys, budgets and usage | identity |

Each sandbox gets its **own database instance** (`eco-<app>-db`): that is **seven instances and seven sandboxes**. If your AgentPG plan
allows fewer, deploy a subset (section 5): `ecoctl apply --only trustdesk` creates the identity provider, TrustDesk and nothing else.

What `ecoctl` does for you, in order: creates the databases and sandboxes; registers every app in the identity provider (its roles, a
browser-sign-in client, and for apps that call others a **service client** limited to exactly the scopes in the manifest); passes each
app its sign-in settings and generated secrets as environment variables; points each app's redirect URI at its real URL; creates the groups
and role grants. What it does **not** do: create your first user, configure Google/GitHub login, add provider keys to the gateway, or
configure the simulators' targets. Those are in sections 6, 9 and 10.

How the pieces trust each other: you sign in once at the identity provider; each app verifies the provider's signed tokens locally and
maps your **roles in that app** to what you may do. Apps that call other apps (CityPulse reports to PagerLens, the simulator sends load to
CityPulse and TrustDesk) fetch short-lived service tokens from the provider instead of holding hand-copied secrets.

## 2. Prerequisites

- **AgentPG account and CLI.** `agentpg login` (opens a browser flow). Check with `agentpg account`; if it says "Invalid or expired token",
  log in again. The CLI creates instances (`agentpg create`) and sandboxes (`agentpg sandbox create`).
- **Capacity.** Seven instances and seven sandboxes for the full fleet (see above). Free tiers have allowed one sandbox in the past; check
  your plan before you start, and deploy in stages if needed.
- **A machine to run `ecoctl` from**: Python 3.11+ and [uv](https://docs.astral.sh/uv/). It keeps secrets in `.ecosystem/` there
  (git-ignored, mode 0600): treat that directory like a password file and back it up somewhere private. Secrets reach the sandboxes as
  `--env` values, so they pass through that machine's process list for a moment; use a machine you trust.
- **Images.** The images are built by each repository's GitHub Actions workflow on every push to `main` and published to
  `ghcr.io/elchinoo/<image>:latest`. They are **private by default**: section 3.
- **For the optional upstream login:** a Google Cloud OAuth client and/or a GitHub OAuth app (section 6). Local users need nothing.

## 3. Make the images public

AgentPG pulls the image anonymously, so each package must be public. **GitHub offers no API for this; it is a click per package.**

1. Wait for the builds: `gh run list -R elchinoo/<repo> -w "Build and publish image" -L 1` should say `completed success` for `identity`,
   `TrustDesk`, `PagerLens`, `CityPulse`, `CityPulseSimulator`, `PayBroker` and `llm-gateway`.
2. Open <https://github.com/elchinoo?tab=packages>. For **each** package: open it → **Package settings** → **Danger Zone** →
   **Change visibility** → **Public**. (If a package is missing, its first build has not finished.)
3. Verify from your machine, with no login:

```bash
for i in identity trustdesk pagerlens citypulse citypulsesimulator paybroker llm-gateway; do
  tok=$(curl -s "https://ghcr.io/token?scope=repository:elchinoo/$i:pull" | python3 -c 'import sys,json; print(json.load(sys.stdin)["token"])')
  code=$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $tok" -H 'Accept: application/vnd.oci.image.index.v1+json' "https://ghcr.io/v2/elchinoo/$i/manifests/latest")
  echo "$i: $code"        # 200 = public and present; 401/404 = still private or not built
done
```

A sandbox that fails with `provisioning failed (HTTP 400)` almost always means the image is private or missing.

## 4. Install ecoctl and check the manifest

```bash
git clone git@github.com:elchinoo/agentpg-ecosystem.git && cd agentpg-ecosystem
uv venv && uv pip install -e ".[dev]"
.venv/bin/ecoctl validate
```

Open [`ecosystem.yaml`](../ecosystem.yaml) and decide:

- **`fleet`** is the prefix of every name (`eco-trustdesk`, `eco-trustdesk-db`). Change it if `eco-*` collides with something of yours.
- **`agentpg.registry` / `tag`**: where images come from (default `ghcr.io/elchinoo`, `latest`).
- **`agentpg.size`** (database size) and optionally **`plan`** (compute plan: `small`, `medium`, `large`) for every sandbox.
- **`auto_pause`**: idle sandboxes pause themselves and **do not wake on a request** (they answer an empty 503). The manifest keeps the
  identity provider awake (`auto_pause: false`) because every sign-in depends on it; the apps may sleep and are resumed with
  `agentpg sandbox resume <id>`.
- **`apps`**: delete the ones you do not want (and any `calls` or `access.groups.*.grants` that mention them; `ecoctl validate` tells you).

Nothing in this file is a secret, and the validator refuses an environment name that looks like one.

## 5. Deploy in stages

Always look before you leap:

```bash
.venv/bin/ecoctl plan                    # every agentpg command and identity call, secrets masked; changes nothing
```

Then deploy a small slice first. `--only` takes a comma-separated list and automatically adds what those apps **call** (the simulator pulls
in CityPulse, TrustDesk and PagerLens), and the identity provider is always included.

```bash
# Stage 1: identity + TrustDesk (2 instances, 2 sandboxes). Prove sign-in works before adding more.
.venv/bin/ecoctl apply --only trustdesk --execute

# Stage 2: the observability trio plus the load generator
.venv/bin/ecoctl apply --only cpsim --execute        # adds pagerlens, citypulse, trustdesk (already there)

# Stage 3: the rest
.venv/bin/ecoctl apply --execute                     # paybroker and llmgw
```

**A sandbox's URL contains its id** (`https://app--a1p-prod-sbx-<id>--....code.run`), so a sandbox you delete and recreate gets a **new URL**. Anything that
knows the old URL must change: that is why `ecoctl recreate` exists (section 11). Decide your first deployment is the real one before you share URLs.

`apply --execute` prints the plan and asks you to confirm (`--yes` skips the question). It creates databases and sandboxes on your account,
which may use credits. It is **safe to run again**: it finds existing instances and sandboxes by name, never rotates a secret unless you ask
(`--rotate`), and tells you if an app's environment in the manifest no longer matches its sandbox (it cannot change a running sandbox's
environment; delete the sandbox and re-run, the database keeps its data). If a step fails, progress is saved and the message says what to fix.

Check on things at any time:

```bash
.venv/bin/ecoctl status        # health and readiness of every app, and drift from the identity registry
```

Apps print a one-time **setup code** in their log on a fresh database; with single sign-on enabled you do not need it (an app that has
SSO configured does not ask for setup). Read logs with `agentpg sandbox logs <sandbox-id>`. Sandbox ids and URLs
are in `.ecosystem/state.json` and in `agentpg sandbox list` / `agentpg sandbox status <id>`.

## 6. First sign-in: the identity provider

The provider was created with a static admin key (generated, kept in `.ecosystem/`). Use it once to bootstrap yourself.

1. Find the URL: `.venv/bin/ecoctl status` (the `eco-identity` row) or `agentpg sandbox status <identity-sandbox-id>`.
2. Print the key (do not paste it anywhere public): `.venv/bin/ecoctl env identity | grep IDENTITY_API_KEY`.
3. Open `https://<identity>/admin`. Expand **Use the server's API key instead**, paste the key, sign in.
4. **Settings → Admin password**: set one (12+ characters). From now on you can sign in with it, and the provider's vault is protected by it as
   well as by the platform secret. Keep the API key for automation (`ecoctl` uses it).
5. **Users → New user**: create yourself (username, e-mail, a password). **Groups → `ecosystem-admins` → add yourself.** That group was
   created by `ecoctl` with the `admin` role in every app, so one membership gives you access everywhere. Other groups from the manifest
   (`risk-team`, `support`) grant narrower roles; add people to them as needed.
6. Open `https://<identity>/apps` signed in as that user: the **launcher** lists every app you may open.

### Optional: sign in with Google or GitHub

Local users work out of the box. To add upstream login, open **Federation** in the console and enter the client id and secret of an OAuth
app you create at the provider. The **redirect (callback) URL** to register at Google or GitHub is:

```
https://<identity>/login/google/callback
https://<identity>/login/github/callback
```

Mode `invite_only` (the default) never creates users: it only lets in someone whose **verified** e-mail matches an existing user. Mode `open`
creates users for e-mail domains you list; use it only for a domain you control. Add people first, or switch to `open` deliberately.

## 7. Sign in to each app

Open each app's URL (from `ecoctl status`), click **Sign in with single sign-on**, and sign in at the provider. You return to the app signed in
with your **roles in that app**. What to expect:

| App | Roles you can grant (Groups or Users → Roles) | A person with no role sees |
|---|---|---|
| TrustDesk | `admin`, `approver`, `viewer` | "your account has no role in TrustDesk" |
| PagerLens | `admin`, `responder`, `viewer` | the same, for PagerLens |
| CityPulse | `admin`, `operator`, `viewer` | the same |
| CityPulse Simulator | `admin`, `operator`, `viewer` | the same |
| PayBroker | `admin`, `risk_manager`, `analyst`, `operator`, `viewer` | the same |
| LLM gateway | `admin`, `operator`, `viewer` | the same |

Check that **a role really limits what you can do**: add a test user to `viewer` only, sign in as them, and confirm the app hides or refuses
the controls that change things. Role changes reach an app at the person's next sign-in.

Each app also keeps its **local admin login as a break-glass** (set `<PREFIX>_LOCAL_LOGIN=false` in the manifest `env` to remove it once SSO is
proven). Until you set an admin password in an app there is none, which is fine.

## 8. Smoke tests, app by app

Do these in order; each takes a few minutes. Use `agentpg sandbox logs <id>` whenever something looks wrong.

**TrustDesk** (`https://<trustdesk>`)
1. **Documents → Load demo policies** (the fictional Acme pack). It was started with `AUTO_SEED=true`, so it may already be there.
2. **Search**: ask "How many vacation days do new employees get?". You should see passages with document, heading and version.
3. **Settings → Provider accounts**: add an AI account (a real provider, or the gateway: section 9), then **Request validation** → choose it.
4. **Requests → New request** for a demo employee. Within seconds it moves to *Needs decision* with an AI recommendation, the reasons and
   quoted policy passages. Approve or reject it; the history names *you*.
5. As a user with only `viewer`: you can read but not decide.

**PagerLens** (`https://<pagerlens>`)
1. Sign in; the dashboard shows the systems it monitors (CityPulse and the simulator appear once they report, section 10).
2. **Lab**: trigger a demo incident (admin only), press **Investigate**, read the diagnosis and the matched runbook.
3. **Settings → AI accounts** if you want the LLM or hybrid investigation modes (optional; the rules mode needs nothing).

**CityPulse** (`https://<citypulse>`)
1. Overview loads; it holds no sensors until the simulator sends readings (section 10).
2. In PagerLens, `citypulse-*` services should appear within a minute or two of CityPulse starting (it reports with a service token minted by
   the provider: `CITYPULSE_SERVICE_CLIENT_ID/_SECRET` were set for you).

**PayBroker** (`https://<paybroker>`)
1. **Settings → Demo traffic → Send 120**. Overview fills in: approvals, 3-D Secure, fraud rejections, a few open cases.
2. **Payments → click a rejected one**: score, reasons, the rules that fired, the routing attempts.
3. Take a real API payment: **Merchants → New merchant → Keys** (a secret and a publishable key), then the curl commands on the **Connect** tab.
   Only test cards are accepted (`4242 4242 4242 4242` approves).
4. Open a **Case**, resolve it as an analyst. As a viewer you cannot.

**LLM gateway** (`https://<llmgw>`)
1. **Providers** shows the built-in `echo` provider; **Models** shows `echo-chat` and `echo-embed`.
2. **Clients → New client** (for example `trustdesk`), allow `echo-chat`, create a **key** (shown once).
3. Try it: `curl https://<llmgw>/v1/chat/completions -H "Authorization: Bearer lg_..." -H "Content-Type: application/json" -d '{"model":"echo-chat","messages":[{"role":"user","content":"hello"}]}'`
4. **Overview** shows the request and its token count. No prompt or answer text is stored anywhere.

## 9. Wire the LLM gateway into the apps

The gateway holds the real provider keys so the apps do not. Do this once:

1. **Add a real provider** in the gateway (admin): **Providers → Add**: name, base URL (for example `https://api.openai.com/v1`), the API key
   (stored encrypted), and **Test**. Provider URLs are checked like every other outbound URL (https only, no private addresses).
2. **Map model aliases** (**Models**): for example alias `chat` → provider `openai` model `gpt-4o-mini`, with optional second target for failover,
   and `embed` → an embedding model. Add prices if you want cost estimates.
3. **One client per app** (**Clients → New client**): `trustdesk`, `pagerlens`, `paybroker`, each limited to the aliases it needs, with a
   requests-per-minute limit and a monthly token budget (hard or soft). Copy each key when it is shown.
4. **In each app**, add an *OpenAI-compatible account* with: **base URL** `https://<llmgw>/v1`, **API key** the client's key, **chat model** the alias
   (`chat`). (TrustDesk: Settings → Provider accounts; PagerLens: Settings → AI accounts; PayBroker: Settings → AI accounts.) Use **Test chat**.

TrustDesk also needs an **embedding** model for semantic search: give the same account an embedding alias and switch embeddings to it under
Settings. Until then it uses its built-in word-matching embedder, which works for the demo.

Limits worth knowing: the gateway does not stream (the apps never ask it to); its per-client rate limit is per process; token counts the
provider does not report are estimated and marked so; `echo` needs no key and is good for demos.

## 10. Run the simulators and the PagerLens agent

**CityPulse Simulator** (`https://<cpsim>`): sign in (admin or operator).
1. **Cities**: create a city (start small: a few hundred sensors), set its target URL to the CityPulse URL and, if you use the field,
   a token variable name (it must contain `TOKEN`). Leave that variable **unset** and the simulator mints a token from the identity provider
   for you (scope `citypulse.ingest`), because `CPSIM_SERVICE_CLIENT_ID/_SECRET` were set. Save and start the city.
2. In CityPulse, readings arrive; in PagerLens, `citypulse-*` appears as monitored.
3. **Companies**: create a company targeting TrustDesk (`https://<trustdesk>`), leave `TRUSTDESK_CLIENT_TOKEN` unset (same mechanism, scope
   `trustdesk.requests.write`), choose the count of each request type, **Preview**, then **Save and run**. Requests appear in TrustDesk with AI advice.
4. In PagerLens, try the failure stories: stop the CityPulse sandbox (`agentpg sandbox pause <id>`), or start a *burst* in the simulator,
   and watch the incident and its likely cause.

**PagerLens agent** runs **next to** what it watches, not on AgentPG (`deploy: false` in the manifest; it is registered with the provider
and nothing else). Get its settings and run it anywhere that can reach the apps (a laptop, a VM, a container):

```bash
.venv/bin/ecoctl env pagerlens-agent > pagerlens-agent.env      # contains a secret: keep it private
# then, per the PagerLens README: set the scrape targets in agent.yaml and
set -a; . ./pagerlens-agent.env; set +a; pagerlens-agent run agent.yaml      # or: pagerlens-agent once agent.yaml --dry-run
```

The agent mints its own `pagerlens.ingest` token with `PAGERLENS_AGENT_OIDC_ISSUER`, `..._CLIENT_ID` and `..._CLIENT_SECRET`. Each scrape
target's own `/metrics` credential (for example a CityPulse metrics token) is still configured per target in `agent.yaml`.

## 11. Operating it

**Database keys.** AgentPG shows an instance's API key once, when the instance is created, and that key is the database password. `apply`
saves it in `.ecosystem/state.json` as `db_key`. Print one with `ecoctl db <app>`. For an instance created before this was saved (or if you
lose the file), `ecoctl db <app> --reset` rotates the instance's `default` key (nothing in the fleet uses it; sandboxes get their own key
from AgentPG) and saves the new one.

| Task | How |
|---|---|
| See everything | `ecoctl status`, `agentpg sandbox list`, `agentpg instances list` |
| Logs | `agentpg sandbox logs <sandbox-id>` |
| Resume a paused sandbox | `agentpg sandbox resume <sandbox-id>`; apps hold stored keys locked until a sign-in unlocks them (the platform secret, `*_ENCRYPTION_KEY`, makes that automatic: `ecoctl` sets it) |
| Deploy a new image | CI pushes `:latest`. `ecoctl recreate <app>` deletes the sandbox and creates it afresh so it pulls the image (the database and secrets are kept). Because the URL changes, it also recreates every app that **calls** this one (their environment names its URL); recreating `identity` recreates the whole fleet. Allow a couple of minutes per app |
| Change an app's environment | Edit the manifest, then `ecoctl recreate <app>` |
| Rotate a client secret | `ecoctl apply --execute --rotate`, then recreate the sandboxes whose environment changed |
| Add a person or change roles | Identity console → Users or Groups. Takes effect at their next sign-in |
| Forgot an app's local admin password | `agentpg sandbox console <id>`, then `trustdesk reset-admin` (or `pagerlens`, `citypulse`, `cpsim`, `paybroker`, `llmgw`, `identity`); for apps that store provider keys this erases them |
| Rotate the identity signing key | Identity console → Keys → Rotate (the old key stays published for 24 h) |
| Back up | Each app's data is in its instance; use AgentPG's instance tools. Also keep `.ecosystem/state.json`: it is the only copy of the client secrets and of each instance's database key |

## 12. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `agentpg ...` says "Invalid or expired token" | Not logged in | `agentpg login` |
| `ecoctl` cannot find an instance or sandbox id after creating it | The CLI's listing format differs from what `agentpg.py` expects (it matches the format seen on 2026-10-22: a header row, a dashed row, then `ID NAME ...`) | Compare `agentpg instances list` / `agentpg sandbox list` with `_find` in `src/ecoctl/agentpg.py` (the CLI also has `--json`) and re-run: it is safe to repeat |
| `ecoctl` waits a long time at a sandbox | A new sandbox is `creating` for a minute or two; the URL appears when it is `running` | It waits up to 7 minutes and reports a failed sandbox with the platform's reason. `agentpg sandbox logs <id>` |
| Sandbox fails with `provisioning failed (HTTP 400)` | Image private, missing or the CI build failed | Section 3; `gh run list -R elchinoo/<repo>` |
| Every URL answers an empty `503` | Sandbox auto-paused | `agentpg sandbox resume <id>` |
| App page says it "can't be reached", `/readyz` says `waking-database` | The instance is waking | Wait a few seconds |
| Sign-in bounces back with `sso_error=the identity provider is unavailable` | The identity sandbox is paused or crashed, or the app's `*_OIDC_ISSUER` is wrong | `ecoctl status`; `agentpg sandbox logs <identity-id>`; the issuer must equal the identity URL exactly (https, no trailing path) |
| Provider shows an `invalid_request` / redirect error | The registered redirect URI does not match the URL the app sends | `ecoctl status` shows drift; re-run `ecoctl apply --execute`. If the sandbox URL is not what the app derives from the request, set `<PREFIX>_PUBLIC_URL` in the manifest `env` and recreate the sandbox |
| `sso_error=your account has no role in <App>` | You hold no role there | Add yourself to `ecosystem-admins` (or grant a role) and sign in again |
| `sso_error=...` mentioning the issuer or audience | Identity was reached under a different URL than it first answered on | The first request fixes the issuer. Set `IDENTITY_PUBLIC_URL` in the identity `env` and recreate it |
| Identity `/readyz` says `locked` | The vault is locked: `IDENTITY_ENCRYPTION_KEY` missing or the database was replaced | Recreate the sandbox with the same encryption key from `.ecosystem/`, or sign in as admin to unlock |
| A service call gets `401`/`invalid_scope`/`access_denied` | The calling app's service client is not allowed that scope or audience | `ecoctl validate`; compare `calls`/`exposes` in the manifest with Apps in the identity console; re-run `apply` |
| Simulator: "the environment variable ... is empty" | The token variable is empty and the simulator has no service credentials | Check `CPSIM_SERVICE_CLIENT_ID/_SECRET` in `ecoctl env cpsim`, or set the variable |
| PagerLens shows no `citypulse-*` | CityPulse cannot reach PagerLens or lacks the service token | `agentpg sandbox logs <citypulse-id>` (look for `reporter:`); `PAGERLENS_URL` and `CITYPULSE_SERVICE_CLIENT_*` set? |
| Gateway: `403 wrong_credential` | An operator credential was used on `/v1` | Use a client key (`lg_...`) |
| Gateway: `429 budget_exceeded` / `rate_limit_exceeded` | The client's budget or rate limit | Clients → raise the limit, or switch to soft mode |
| Gateway: `503 vault_locked` | A real provider's key is locked after a restart | `LLMGW_ENCRYPTION_KEY` unlocks it automatically; otherwise sign in as admin |
| `ecoctl apply` says an app "exists with a different environment" | You edited the manifest after creating the sandbox | Delete that sandbox and re-run |
| Lost `.ecosystem/state.json` | Client secrets were only shown once; the database keys too (a key is the database password, and the web UI cannot show it again) | Re-run `ecoctl apply --execute --rotate`, then recreate the sandboxes |

## 13. Security checklist before you share a URL

- [ ] Every app is reachable only over https (AgentPG terminates TLS). No app is waiting on a setup code on a public URL.
- [ ] You set an **admin password** at the identity provider and created your own user; the generated **API key** is stored only in `.ecosystem/`.
- [ ] `ecosystem-admins` contains only you (and people you trust). Other groups carry the narrow roles they need.
- [ ] Upstream login (if enabled) is `invite_only`, or `open` for a domain you control.
- [ ] Optional: `<PREFIX>_LOCAL_LOGIN=false` on the apps once SSO works, so the only way in is through the provider.
- [ ] Gateway clients have budgets and rate limits; provider keys exist **only** in the gateway.
- [ ] `.ecosystem/` is not in git (the repository's `.gitignore` covers it) and is backed up privately.
- [ ] PayBroker is a **sample**: test cards only, simulated processors. Do not point real payment traffic at it.

## 14. Tear it all down

`ecoctl` has no destroy command on purpose. Delete in this order, then the state file:

```bash
for id in $(agentpg sandbox list | awk 'NR>1 && $2 ~ /^eco-/ {print $1}'); do agentpg sandbox delete "$id"; done
for id in $(agentpg instances list | awk 'NR>1 && $2 ~ /^eco-.*-db$/ {print $1}'); do agentpg instances delete "$id"; done
rm -rf .ecosystem
```

Check the lists first (`agentpg sandbox list`, `agentpg instances list`): the `awk` filters assume the column layout shown there, and
deleting an instance deletes its data.

### What the first live run found (all fixed; useful if you see a variation)

1. `agentpg extensions create` takes the **extension name first**, then the instance id.
2. Instances and sandboxes are not usable the moment `create` returns: `ecoctl` now waits for the instance to be `running` before extensions
   or a sandbox, and for the sandbox to be `running` before reading its URL (the `Status:` and `URL:` lines of `sandbox status`).
3. **Identity refused sign-in** because the authorization URL it receives (AgentPG hostnames plus PKCE) is longer than the 300 characters it
   allowed for the return address. Fixed in identity (limit 2048, tested). Symptom with an old image: HTTP 422 from `POST /login`.
4. A sandbox's URL changes when it is recreated, so `ecoctl recreate` cascades (section 11).
5. A sandbox deleted outside `ecoctl` is noticed and recreated by the next `apply`.
6. `ecoctl status` reported undeployed apps as "NOT REGISTERED"; it now only checks apps in this deployment.
