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

## LLM gateway keys
- `ecoctl` generates a static operator key for the gateway (`LLMGW_API_KEY`) and keeps it in the state file like every other generated
  secret. It is passed to the gateway sandbox as an environment variable, shown as `***` in plans and by `ecoctl env`, and is never
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
