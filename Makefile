.PHONY: lint test validate plan
lint:     ; .venv/bin/ruff check src tests
test:     ; .venv/bin/pytest -q
validate: ; .venv/bin/ecoctl validate
plan:     ; .venv/bin/ecoctl plan

# The Docker development stack (docker/README.md). Needs docker compose and ECOCTL_SEAL_PASSPHRASE (or a prompt); the first build needs EDB_SUBSCRIPTION_TOKEN.
.PHONY: dev-render dev-up dev-unseal dev-status dev-test dev-down dev-clean
dev-render: ; .venv/bin/ecoctl dev render
dev-up:     ; docker/seal.sh
dev-unseal: ; docker/unseal.sh
dev-status: ; .venv/bin/ecoctl dev status
dev-test:   ; .venv/bin/ecoctl dev test
dev-down:   ; .venv/bin/ecoctl dev down
dev-clean:  ; .venv/bin/ecoctl dev down -v
