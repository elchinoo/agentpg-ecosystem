#!/usr/bin/env bash
# Open every running app with the operator's passphrase (needed after any container restart): `ecoctl dev unseal` (the same HTTP exchange as `ecoctl unseal`).
set -Eeuo pipefail
cd "$(dirname "$0")/.."
exec "${ECOCTL:-.venv/bin/ecoctl}" dev unseal "$@"
