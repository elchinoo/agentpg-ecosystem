# Security policy

Report vulnerabilities privately through GitHub's "Report a vulnerability" (Security tab); please do not open a public issue.

## Model
- The manifest holds no secrets: environment names that look like secrets (`*SECRET*`, `*PASSWORD*`, `*TOKEN*`, `*KEY*`, `*DSN*`) are
  refused, as are URLs with embedded credentials. Secrets are generated with `secrets.token_urlsafe`.
- `.ecosystem/state.json` (mode 0600 in a 0700 directory, written atomically, git-ignored) is the only place secrets are kept. Treat it
  like a password file. Printed plans and errors mask secret values.
- The tool never destroys anything and changes nothing without `--execute` and a confirmation.
- It talks to the identity provider with the static admin key it generated for it; client secrets are issued by the provider once.
- It trusts the `agentpg` CLI on your PATH and the output formats described in the README.

## Vault policy: the signing key and the provider keys are opened by the operator, not by the platform
Every app encrypts what it stores (webhook secrets, credentials, provider keys, the identity provider's signing key) under a *data key* kept in its
database only in wrapped form. `vault: platform` (the default, set per app in `ecosystem.yaml`) adds a wrap under `<PREFIX>_ENCRYPTION_KEY`, a
value in the sandbox environment, so the app unlocks itself after a restart. For the two apps that hold the highest-value secrets the manifest sets
`vault: password`:

| App | What its vault protects |
|---|---|
| `identity` | the RS256 signing key: whoever holds it mints a token for any app, any role |
| `llmgw` | the real provider API keys: money |

For these two `ecoctl` sets **no** `IDENTITY_ENCRYPTION_KEY` / `LLMGW_ENCRYPTION_KEY` (the manifest refuses to list one under `generate:`). The vault is
created on the first `apply` and opened after every restart with an **admin password that the operator holds and the platform never stores**
(`ECOCTL_ADMIN_PASSWORD`, `--admin-password-prompt`, or `.ecosystem/state.json` on the operator's machine; a strong one is generated and shown once when none
exists). `ecoctl unlock` opens them after a platform restart; the Ansible recipe has `unlock.yml`. Other apps keep their platform wrap and auto-unlock.

**Threat addressed.** A disclosure of the platform environment (the sandbox environment, injected files, the database credentials, a database copy)
no longer opens the identity signing key or the provider keys: the value needed to unwrap the data key (the password) is not on the platform, so the
wrapped key in the database cannot be opened with what the platform holds.

**Not addressed** (do not read more into this control than it gives):
- **Write access to the database** is still full control of the app (rows of sessions and tokens, and, for identity, the keys it publishes); the vault does not stop that.
- **The data key lives in process memory** while the app runs unlocked: anyone who can execute code inside the running process, or read its memory, gets it. A running, unlocked identity provider still signs for whoever can call its admin API; the static `IDENTITY_API_KEY` and `LLMGW_API_KEY` in the sandbox environment remain wildcard operator credentials (a separate control).
- **The admin password is a high-value secret**: it opens the vault and the admin console. Offline guessing against a copy of the database is slowed only by the key derivation (scrypt), so use the generated one or a long passphrase, keep it in a password manager, and keep `.ecosystem/` private. Losing it loses the vault (`reset-admin` recreates it and discards what it protected).
- **Restarts need a human.** After any platform restart, recreate or crash, identity cannot sign and the gateway cannot use a stored provider key until `ecoctl unlock` (or `unlock.yml`) runs. Tokens already issued keep working until they expire, because the apps verify them offline against the published keys; new sign-ins and service tokens fail meanwhile.
- A vault that was ever opened by a platform key keeps that wrap in its database until it is dropped and the data key rotated (docs/DEPLOY.md, moving an existing fleet).

## LLM gateway keys
- `ecoctl` generates a static operator key for the gateway (`LLMGW_API_KEY`) and keeps it in the state file like every other generated
  secret (the gateway's vault is opened by the admin password, see above, not by this key). It is passed to the gateway sandbox as an environment variable, shown as `***` in plans and by `ecoctl env`, and is never
  accepted by the gateway's `/v1` data plane. Anyone holding it administers the gateway: protect `.ecosystem/state.json` accordingly.
- Each app that uses an LLM gets **its own gateway client and key** (`lg_...`, kept in the state file, injected as `LLM_API_KEY`, masked in
  plans). A client key reaches only `/v1`, only the two default aliases, and is limited by requests per minute and a monthly token budget
  (defaults in the manifest `llm:` block, `hard` by default). **One compromised app can therefore spend at most its own budget** and can be cut
  off alone (revoke or rotate its key); provider keys exist only in the gateway and never in an app.
- Rotation: `ecoctl apply --execute --rotate` rotates every app's gateway key (the old ones stop at once), then `ecoctl recreate <app>` gives the
  sandboxes the new ones. To rotate one app, rotate its key in the gateway console (or revoke it) and run `apply`: a client with no key in
  the state file has its active key rotated and the new one stored. After a suspected leak of the state file, also rotate the operator key
  (remove `LLMGW_API_KEY` under `apps.llmgw.generated` in the state file, then `ecoctl recreate llmgw`).
- `apply` never overwrites the limits of a client an operator tuned; it creates missing clients, replaces lost keys and only *adds* a missing
  default alias (so a hand-made client cannot lock an app out). Budgets and rate limits are the **blast-radius limit** of a leaked app key, so
  they can be set per app in the manifest (`llm: {rpm, budget, budget_mode, aliases}`; give a risky or exposed app a small budget and `hard`).
  Changing a running client is **explicit**: `ecoctl llm sync` only shows the difference (exit 1 on drift); `--apply <app>|--all` sets the
  differing fields, adds missing aliases, and removes extras only with `--prune-aliases`. It never touches keys; the gateway's own audit log
  records the change.
- `ecoctl status` and `ecoctl llm test` make one tiny chat call with an app's key from the state file; keys are never printed or logged.
