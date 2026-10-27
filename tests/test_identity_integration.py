"""ecoctl against the REAL identity provider (its admin API, validation and secret handling), not a fake.

Needs a Postgres (TEST_DATABASE_URL) and the identity package importable (`pip install -e ../identity ../agentpg-kit`).
"""
import os
from pathlib import Path

import httpx
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
from tests.fakes_connect import FakeConnect  # noqa: E402

MANIFEST = Path(__file__).resolve().parents[1] / "ecosystem.yaml"


def _claims(auth: str) -> dict:
    """The (unverified) payload of the bearer JWT the real provider issued; the fakes only check who it is for."""
    import base64
    import json
    payload = auth.removeprefix("Bearer ").split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


def _audiences(claims: dict) -> set[str]:
    aud = claims.get("aud")
    return {aud} if isinstance(aud, str) else set(aud or [])


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
        # PayBroker's and the shop's admin APIs are fakes, but ecoctl's tokens come from the REAL provider (client_credentials)
        def real_identity(request: httpx.Request) -> httpx.Response:
            r = http.request(request.method, str(request.url), content=request.content, headers={k: v for k, v in request.headers.items() if k != "host"})
            return httpx.Response(r.status_code, content=r.content, headers={"content-type": r.headers.get("content-type", "application/json")})

        fc = FakeConnect(None, real_identity)
        accepted: set[tuple[str, str]] = set()                                # (audience, scope) of every token the fakes let in

        def accepts(audience: str):
            def ok(auth: str) -> bool:
                c = _claims(auth)
                good = _audiences(c) == {audience} and f"{audience}.admin" in c.get("scope", "").split()
                if good:
                    accepted.add((audience, f"{audience}.admin"))
                return good
            return ok

        fc.pb.token_ok, fc.shop.token_ok = accepts("paybroker"), accepts("shopfront")
        connect_http = httpx.Client(transport=httpx.MockTransport(fc.handler))
        ctx = Context(load(MANIFEST), state, AgentPG(run=cli), http=http, llm_http=llm_http, connect_http=connect_http)
        for step in build(ctx):
            step.run(ctx)
        again = Context(load(MANIFEST), State(state.dir), AgentPG(run=cli), http=http, llm_http=llm_http, connect_http=connect_http)
        fc.pb.writes.clear()
        fc.shop.writes.clear()
        for step in build(again):
            step.run(again)                                                   # converges: no duplicates, no rotation, and no write to PayBroker or the shop
        registry = {a["client_id"]: a for a in http.get("/api/apps", headers={"Authorization": f"Bearer {state.admin_key()}"}).json()}
    assert {"trustdesk", "paybroker", "cpsim", "cpsim-service", "citypulse-service", "pagerlens-agent-service", "llmgw"} <= set(registry)
    assert registry["trustdesk"]["url"] == "https://eco-trustdesk.sandbox.example" and registry["cpsim-service"]["kind"] == "service"
    assert state.data["apps"]["trustdesk"]["client_secret"].startswith("cs_")
    # the shop was connected with tokens the real provider issued for ecoctl's service client, and the second run wrote nothing
    assert registry["ecoctl"]["kind"] == "service" and accepted == {("paybroker", "paybroker.admin"), ("shopfront", "shopfront.admin")}
    assert fc.all_writes() == [] and len(fc.pb.merchants) == 1 and fc.shop.values["base_url"] == "https://eco-paybroker.sandbox.example"
    assert fc.shop.values["secret_key"] == state.data["apps"]["shopfront"]["connect"]["paybroker"]["keys"]["secret"]["value"]
