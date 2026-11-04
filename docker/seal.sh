#!/usr/bin/env bash
# Build the sealed blobs and start the stack: `ecoctl dev up` (see docker/README.md). The passphrase comes from ECOCTL_SEAL_PASSPHRASE or a prompt.
# Blobs are made in memory and handed to `docker compose` as secrets sourced from its environment: no plaintext secret and no blob file is written to disk.
set -Eeuo pipefail
cd "$(dirname "$0")/.."
exec "${ECOCTL:-.venv/bin/ecoctl}" dev up "$@"
