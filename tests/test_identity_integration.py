"""ecoctl against the REAL identity provider (its admin API, validation and secret handling), not a fake.

Needs a Postgres (TEST_DATABASE_URL) and the identity package importable (`pip install -e ../identity ../agentpg-kit`).
"""
import os
from pathlib import Path

import pytest

if not os.environ.get("TEST_DATABASE_URL"):
    pytest.skip("set TEST_DATABASE_URL to run the identity integration test", allow_module_level=True)
identity_api = pytest.importorskip("identity.api")

from fastapi.testclient import TestClient  # noqa: E402

from ecoctl.agentpg import AgentPG  # noqa: E402
from ecoctl.manifest import load  # noqa: E402
from ecoctl.plan import Context, build  # noqa: E402
from ecoctl.state import State  # noqa: E402
from tests.fakes import FakeAgentPG, FakeGateway  # noqa: E402

MANIFEST = Path(__file__).resolve().parents[1] / "ecosystem.yaml"


def test_apply_against_the_real_provider(tmp_path, monkeypatch):
    from identity import cli as identity_cli
    from identity.core import db

    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    with db.connect() as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
        conn.commit()
    identity_cli.run_migrations()
    state = State(tmp_path / "eco")
    monkeypatch.setenv("IDENTITY_API_KEY", state.admin_key())
    monkeypatch.setenv("IDENTITY_ENCRYPTION_KEY", "integration-test-encryption-key-0123456789")
    monkeypatch.setenv("IDENTITY_PUBLIC_URL", "https://eco-identity.sandbox.example")
    identity_api.reset_server()
    with TestClient(identity_api.app, base_url="https://eco-identity.sandbox.example") as http:
        cli, gw = FakeAgentPG(), FakeGateway()
        llm_http = gw.client(state.secret("llmgw", "LLMGW_API_KEY"))
        ctx = Context(load(MANIFEST), state, AgentPG(run=cli), http=http, llm_http=llm_http)
        for step in build(ctx):
            step.run(ctx)
        again = Context(load(MANIFEST), State(state.dir), AgentPG(run=cli), http=http, llm_http=llm_http)
        for step in build(again):
            step.run(again)                                                   # converges: no duplicates, no rotation
        registry = {a["client_id"]: a for a in http.get("/api/apps", headers={"Authorization": f"Bearer {state.admin_key()}"}).json()}
    assert {"trustdesk", "paybroker", "cpsim", "cpsim-service", "citypulse-service", "pagerlens-agent-service", "llmgw"} <= set(registry)
    assert registry["trustdesk"]["url"] == "https://eco-trustdesk.sandbox.example" and registry["cpsim-service"]["kind"] == "service"
    assert state.data["apps"]["trustdesk"]["client_secret"].startswith("cs_")
