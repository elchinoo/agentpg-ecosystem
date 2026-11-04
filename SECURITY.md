# Security policy

Report vulnerabilities privately through GitHub's "Report a vulnerability" (Security tab); please do not open a public issue.

## Model
- The manifest holds no secrets: environment names that look like secrets (`*SECRET*`, `*PASSWORD*`, `*TOKEN*`, `*KEY*`, `*DSN*`) are
  refused, as are URLs with embedded credentials. Secrets are generated with `secrets.token_urlsafe`.
- `.ecosystem/state.json` (mode 0600 in a 0700 directory, git-ignored) is the operator machine's only store of generated secrets, client secrets and
  database keys, and it is **encrypted at rest**: an `apgkit-seal1$...` blob (AES-256-GCM, key derived with scrypt from the operator's passphrase;
  agentpg-kit `apgkit.sealed`). The file is encrypted in memory and only the blob is written, atomically, through a temporary file that never holds
  plaintext. A state file from an older ecoctl (plain JSON) is read, then rewritten encrypted on the next save, its old bytes overwritten first; ecoctl says so.
  Printed plans and errors mask secret values, and **no command prints the state or a stored secret**: `ecoctl env APP` shows names, non-secret values and `***` for
  every secret, and has no way to show them (`--reveal` was removed). A sealed secret is not readable by design; to change one, rotate it (`apply --execute --rotate`,
  or rotate it at its source: the identity console for a client secret, the gateway console for a key).
- **The passphrase is the single secret the operator holds.** `ECOCTL_SEAL_PASSPHRASE`, the output of the command in `ECOCTL_SEAL_PASSPHRASE_CMD` (a password manager's CLI), or a prompt, once per run; it is never written to the state,
  a sandbox, a log or an error message, and the same passphrase seals the apps (below). Lose it and the state is unreadable: recover with `apply --execute --rotate`.
- **Limits, stated plainly.** While ecoctl runs, the passphrase and the decrypted state are in its memory: a compromised operator machine (malware,
  a debugger, root, a memory dump, a swapped-out page) sees them, and anything in the environment of the shell that exports `ECOCTL_SEAL_PASSPHRASE`.
  A weak passphrase can be guessed offline against a copy of the file at the scrypt cost (use `ecoctl seal-passphrase`). Overwriting the old plaintext on migration
  is best effort: copy-on-write filesystems, SSD wear levelling, snapshots and backups can keep the old bytes, so a plaintext state that existed should be treated
  as exposed and its secrets rotated. Commands that need no secrets (`validate`, and `plan` before any state exists) run without the passphrase; every command that reads an
  existing state file needs it.
- **The read-only AI database user is covered too (`ecoctl mcp`).** Its credentials live only in the encrypted state. `mcp config` writes the AgentPG MCP config as an
  `apgkit-seal1$...` blob (`databases.sealed`), never as YAML. `mcp serve`, `mcp check` and the Claude Code entries (`mcp claude`) decrypt or build the config in memory and
  hand it to `agentpg mcp serve --config /dev/fd/N`, an inherited **pipe**: no file with credentials exists at any moment (`agentpg mcp serve` reads `--config FILE`, which
  accepts a pipe, or the `MCP_CONFIG` variable, which would put the YAML in the process environment, so the pipe is the strongest option it supports; ecoctl removes
  `MCP_CONFIG` from the child's environment). The Claude Code stanza launches `ecoctl mcp serve --transport stdio APP`; it holds no credential and no passphrase.
  Where the OS has no `/dev/fd`, the fallback is a 0600 file in a fresh 0700 directory (RAM-backed `/dev/shm` when present) removed on exit and on SIGTERM/SIGINT/SIGHUP.
  *Limits:* the child process (`agentpg`) holds the credentials in memory and uses them to connect; before it has read the pipe, a process of the same user (or root) could read
  it through `/proc/<pid>/fd` on Linux; a `kill -9` of ecoctl can only leave a fallback file behind, never a pipe; `--transport http` serves the databases to anyone with a valid
  JWT, whose HS256 secret is in the encrypted state (`mcp.jwt_secret`).
- **How a stdio MCP client gets the passphrase.** stdio belongs to the protocol, so no prompt is possible. Set `ECOCTL_SEAL_PASSPHRASE_CMD` to your password manager's command
  (for example `pass show ecoctl`) before `ecoctl mcp claude`: the stanza carries the command, which is not a secret, and the passphrase is fetched when the server starts.
  The alternative, `ECOCTL_SEAL_PASSPHRASE` in the environment of the shell that starts Claude Code, is inherited by every tool Claude runs there (including its shell tool): do not
  use it where the model can run commands you would not run yourself.
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
(`ECOCTL_ADMIN_PASSWORD` or `--admin-password-prompt`; with sealing, the default, the seal passphrase itself). It is **never stored**: not in the state file, not on the platform;
ecoctl does not generate one for you (`ecoctl seal-passphrase` prints a strong random one for a password manager). `ecoctl unlock` opens them after a platform restart (with sealing, `ecoctl unseal`); the Ansible recipe has `unseal.yml`. Other apps keep their platform wrap and auto-unlock.

**Threat addressed.** A disclosure of the platform environment (the sandbox environment, injected files, the database credentials, a database copy)
no longer opens the identity signing key or the provider keys: the value needed to unwrap the data key (the password) is not on the platform, so the
wrapped key in the database cannot be opened with what the platform holds.

**Not addressed** (do not read more into this control than it gives):
- **Write access to the database** no longer plants a session, token, key or route while the vault is unlocked: the security tables carry row MACs under a subkey of the data key (see below). It is not covered by the MACs: replaying an older authentic row from a backup, deleting rows, and columns that are not authority (names, descriptions, timestamps).
- **The data key lives in process memory** while the app runs unlocked: anyone who can execute code inside the running process, or read its memory, gets it. A running, unlocked identity provider still signs for whoever can call its admin API; the static `IDENTITY_API_KEY` and `LLMGW_API_KEY` in the sandbox environment remain wildcard operator credentials (a separate control).
- **The admin password is a high-value secret**: it opens the vault and the admin console. Offline guessing against a copy of the database is slowed only by the key derivation (scrypt), so use a long random passphrase, keep it in a password manager, and keep `.ecosystem/` private. Losing it loses the vault (`reset-admin` recreates it and discards what it protected).
- **Restarts need a human.** After any platform restart, recreate or crash, identity cannot sign and the gateway cannot use a stored provider key until `ecoctl unseal` / `ecoctl unlock` (or `unseal.yml`) runs. Tokens already issued keep working until they expire, because the apps verify them offline against the published keys; new sign-ins and service tokens fail meanwhile.
- A vault that was ever opened by a platform key keeps that wrap in its database until it is dropped and the data key rotated (docs/DEPLOY.md, moving an existing fleet).

## Sealed boot: no secret in the environment, the config or the image
With `agentpg.seal: true` (the default; `seal: false` is an explicit opt-out and every plan then starts with a loud warning) an app is deployed with a single encrypted blob (`APGKIT_SEALED`: AES-256-GCM under a key derived with scrypt
from an operator passphrase). It starts sealed and answers 503 except for `/_seal/*` and the health probes. `ecoctl unseal` sends the passphrase, encrypted
to an ephemeral key that exists only in the app's memory; the app opens the blob (database URL, client secrets, API keys), runs its migrations, unlocks its
vault with the same passphrase and starts serving. The passphrase comes from `ECOCTL_SEAL_PASSPHRASE` or a prompt and is never stored; a restart seals the
app again. The data key and the unsealed secrets are held in masked, locked, dump-excluded memory (`apgkit.sealed.Protected`) and revealed only for the
instant of use.

**Threat addressed.** Reading the sandbox environment, injected files, the image or a database copy yields neither the database credentials nor any key.
With the vault unlocked, writing to the database cannot create a valid session, token, signing key, provider, route, merchant key or rule (row MACs).

**Not addressed.**
- Root or a debugger on the live, unsealed process reads its memory; masking makes a casual dump useless, not a determined one.
- An active man in the middle at the platform proxy can replace the public key the operator encrypts to. Check the fingerprint `ecoctl unseal` prints when it matters.
- The platform knows the database credentials it provisioned; apps still use the instance admin credential (a runtime least-privilege role is not done yet).
- Python can leave transient copies of a secret as a `str`; the long-lived copies are the masked ones.
- After a platform rebuild each app needs `ecoctl unseal`; upgraded databases need one `adopt-rowmacs` per app.

## The Ansible recipe is sealed like ecoctl (`ansible/`)
The recipe never renders a secret. A sandbox is created with `APGKIT_SEALED` (its secrets and `DATABASE_URL`, encrypted under the operator's passphrase by agentpg-kit's `apgkit.sealed`) plus
non-secret settings; on your own hosts the container's env file holds the same and nothing else. `site.yml` refuses a manifest with `agentpg.seal: false`. After creating an app the recipe
unseals it (`unseal.yml` does it after a platform restart; `unlock.yml` is now an alias). Everything secret the recipe holds (the instances' database keys, the generate-once secrets, the
client secrets, gateway keys, merchant keys and webhook secrets that services show once) is in **one encrypted file**, `ansible/.secrets/<fleet>/state.sealed` (same blob format and same
passphrase as ecoctl's state file; `ECOCTL_SEAL_PASSPHRASE` in the environment of `ansible-playbook`, or `-e eco_seal_passphrase_prompt=true` for a hidden prompt; stored nowhere). The
`.secrets/<fleet>/generated|issued|vault` files and the plaintext `db_key` of an older recipe are moved into it, overwritten and removed on the first run (best effort, see below).
The crypto runs in an action plugin on the control node, in memory (`plugins/action/eco_state.py` reusing `apgkit.sealed`); the control node needs `agentpg-kit` in the Python environment
that runs Ansible. Every task that carries a secret is `no_log: true` and `pipelining` is on, so a module's arguments never reach a temporary file. *Limits:* the passphrase and the decrypted
secrets are in the memory of `ansible-playbook` (and, with the prompt, in a fact for the run); Ansible Vault files you supply are your own to protect; the throwaway
demo database container of `eco_target: hosts` still receives its password as a `docker run --env` argument and the `community.postgresql` modules still receive the
database logins as module arguments (hosts target only); `ansible-playbook -vvv` logs requests and responses of tasks that are not `no_log`, none of which carries a secret here.

## LLM gateway keys
- `ecoctl` generates a static operator key for the gateway (`LLMGW_API_KEY`) and keeps it in the state file like every other generated
  secret (the gateway's vault is opened by the admin password, see above, not by this key). It is passed to the gateway sandbox (inside the sealed blob when sealing is on), shown as `***` in plans and by `ecoctl env`, and is never
  accepted by the gateway's `/v1` data plane. Anyone holding it administers the gateway: protect the state passphrase accordingly.
- Each app that uses an LLM gets **its own gateway client and key** (`lg_...`, kept in the state file, injected as `LLM_API_KEY`, masked in
  plans). A client key reaches only `/v1`, only the two default aliases, and is limited by requests per minute and a monthly token budget
  (defaults in the manifest `llm:` block, `hard` by default). **One compromised app can therefore spend at most its own budget** and can be cut
  off alone (revoke or rotate its key). The gateway holds the provider keys of every app that calls it; an app that is configured with its own
  provider account (TrustDesk and PagerLens keep one in `llm_account`) holds that key in its own vault.
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
