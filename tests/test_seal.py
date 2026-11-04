# ruff: noqa: E402
"""Sealed boot, operator side: the sandbox gets only a blob; the passphrase opens it through the kit's real unseal endpoints."""
from pathlib import Path

import pytest
from apgkit import sealed as kit
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ecoctl import seal
from ecoctl.agentpg import AgentPG
from ecoctl.manifest import load
from ecoctl.plan import Context, app_env
from ecoctl.state import State
from tests.fakes import FakeAgentPG
from tests.unsealed import UNSEALED

MANIFEST = Path(__file__).resolve().parents[1] / "ecosystem.yaml"
PASS = "correct-horse-battery-staple-9"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv(seal.ENV_PASSPHRASE, PASS)
    yield
    kit.set_active(None)


@pytest.fixture
def ctx(tmp_path):
    cli = FakeAgentPG()
    m = load(MANIFEST)
    c = Context(m, State(tmp_path / "eco"), AgentPG(run=cli))
    iid = "inst-1"
    cli.instances[m.instance_name("trustdesk")] = iid
    e = c.state.app("trustdesk")
    e.update(instance_id=iid, db_key="dbpass/with:odd chars")
    return c, m


def test_secrets_leave_the_environment_and_go_into_the_blob(ctx):
    c, m = ctx
    app = m.apps["trustdesk"]
    c.state.app("identity")["url"] = "https://idp.example.test"
    env, secrets_ = app_env(c, app)
    assert secrets_, "the fixture app has secrets"
    sealed_env, sealed_secret_names, notes = seal.seal_env(c, app, env, secrets_)
    assert sealed_secret_names == {seal.BLOB_ENV}
    for name in secrets_:
        assert name not in sealed_env
    assert not [v for v in sealed_env.values() if any(env[n] in v for n in secrets_ if n in env and len(env[n]) > 8)]
    assert app.encryption_key_name not in sealed_env
    opened = kit.open_blob(sealed_env[seal.BLOB_ENV], PASS)
    assert {n: opened[n] for n in secrets_ if n != app.encryption_key_name} == {n: env[n] for n in secrets_ if n != app.encryption_key_name}
    assert opened["DATABASE_URL"] == "postgresql://admin:dbpass%2Fwith%3Aodd%20chars@db.example.test:5432/appdb?sslmode=require"
    assert notes == []


def test_a_weak_passphrase_is_refused(ctx, monkeypatch):
    c, m = ctx
    monkeypatch.setenv(seal.ENV_PASSPHRASE, "short")
    app = m.apps["trustdesk"]
    with pytest.raises(seal.SealError):
        seal.seal_env(c, app, {"X_KEY": "v"}, {"X_KEY"})


def test_missing_instance_key_is_reported_not_guessed(ctx):
    c, m = ctx
    c.state.app("trustdesk").pop("db_key")
    _, _, notes = seal.seal_env(c, m.apps["trustdesk"], {"A_TOKEN": "v" * 12}, {"A_TOKEN"})
    assert notes and "DATABASE_URL not sealed" in notes[0]


def _app_for(blob, ready):
    s = kit.Sealed(blob)
    s.on_unsealed(lambda sealed, pw: ready.append(sealed.get("DATABASE_URL")))
    app = FastAPI()
    kit.install(app, s, trusted_hops=0, harden=False)
    return s, TestClient(app)


def test_unseal_opens_the_app_with_the_passphrase(ctx):
    c, m = ctx
    app = m.apps["trustdesk"]
    blob = kit.seal({"DATABASE_URL": "postgresql://x/y"}, PASS)
    ready: list = []
    s, client = _app_for(blob, ready)
    c.http = client
    c.state.app("trustdesk")["url"] = "http://testserver"
    assert seal.state_of("http://testserver", client) == "sealed"
    assert seal.unseal(c, app).startswith("unsealed (key fingerprint ")
    assert ready == ["postgresql://x/y"] and seal.state_of("http://testserver", client) == "unsealed"
    assert seal.unseal(c, app) == "already open"


def test_wrong_passphrase_is_reported_plainly_and_nothing_opens(ctx, monkeypatch):
    c, m = ctx
    blob = kit.seal({"DATABASE_URL": "postgresql://x/y"}, "another-passphrase-entirely-0")
    ready: list = []
    s, client = _app_for(blob, ready)
    c.http = client
    c.state.app("trustdesk")["url"] = "http://testserver"
    with pytest.raises(seal.SealError, match="did not open the blob") as exc:
        seal.unseal(c, m.apps["trustdesk"])
    assert PASS not in str(exc.value) and not s.unsealed and ready == []


def test_app_without_sealing_says_so(ctx):
    c, m = ctx
    client = TestClient(FastAPI())
    c.http = client
    c.state.app("trustdesk")["url"] = "http://testserver"
    assert seal.unseal(c, m.apps["trustdesk"]) == "sealing is not enabled in this app"


def test_manifest_flag_must_be_boolean(tmp_path):
    from ecoctl.manifest import ManifestError
    text = UNSEALED.read_text()
    p = tmp_path / "e.yaml"
    p.write_text(text.replace("seal: false", "seal: yes please"))
    with pytest.raises(ManifestError, match="seal"):
        load(p)
    p.write_text(text.replace("seal: false", "seal: true"))
    assert load(p).seal is True


def test_seal_is_on_by_default_in_the_loader_and_in_the_shipped_manifest(tmp_path):
    assert load(MANIFEST).seal is True                                     # ecosystem.yaml says so explicitly
    p = tmp_path / "e.yaml"
    p.write_text("\n".join(line for line in UNSEALED.read_text().splitlines() if not line.strip().startswith("seal:")))
    assert not [line for line in p.read_text().splitlines() if line.strip().startswith("seal:")]
    assert load(p).seal is True                                            # and the loader's default is true when the key is absent


def test_opting_out_prints_a_loud_warning_in_the_plan_output_and_sealing_does_not(capsys):
    from ecoctl import cli
    assert cli.main(["--state", "/nonexistent-dir-for-plan-test", "-f", str(UNSEALED), "plan"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("WARNING: agentpg.seal is false") and "in the clear" in out
    assert cli.main(["--state", "/nonexistent-dir-for-plan-test", "-f", str(MANIFEST), "plan"]) == 0
    assert "WARNING: agentpg.seal is false" not in capsys.readouterr().out


def test_sealed_plan_unseals_each_app_and_has_no_vault_password_steps(tmp_path):
    from ecoctl.plan import build
    m = load(MANIFEST)
    c = Context(m, State(tmp_path / "eco"), AgentPG(run=FakeAgentPG()))
    titles = [st.title for st in build(c)]
    assert any("unseal it with the operator's passphrase" in t for t in titles)
    assert not any("create or open its vault with the operator's admin password" in t for t in titles)
    assert sum("unseal it" in t for t in titles) == 1 + len([a for a in m.order() if a.deploy])
