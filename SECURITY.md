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
