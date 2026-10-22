.PHONY: lint test validate plan
lint:     ; .venv/bin/ruff check src tests
test:     ; .venv/bin/pytest -q
validate: ; .venv/bin/ecoctl validate
plan:     ; .venv/bin/ecoctl plan
