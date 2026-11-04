"""The Docker development stack: the compose file follows the manifest, holds no secret, and the blobs and roles are made the way the docs say."""
import json
import re
import subprocess
from pathlib import Path

import httpx
import yaml
from apgkit import sealed as kit

from ecoctl import devstack
from ecoctl.manifest import SECRETISH, URL_WITH_PASSWORD, load
from ecoctl.plan import Context
from ecoctl.state import State

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "ecosystem.yaml"
COMPOSE = ROOT / "docker" / "compose.yml"
PASS = "test-passphrase-for-the-state-file-0"


def test_the_committed_compose_file_is_what_the_manifest_renders():
    assert COMPOSE.read_text() == devstack.render(load(MANIFEST)), "run `ecoctl dev render`"


def test_no_secret_is_in_the_compose_file():
    text = COMPOSE.read_text()
    assert not URL_WITH_PASSWORD.search(text)
    doc = yaml.safe_load(text)
    for name, svc in doc["services"].items():
        for var, value in (svc.get("environment") or {}).items():
            # a name that looks secret may only point at a file under /run/secrets or at a public id: never carry a value
            if SECRETISH.search(var) and var not in {"APP_DATABASES"}:
                assert str(value).startswith("/run/secrets/") or var.endswith("_CLIENT_ID"), f"{name}: {var} is a literal"
        assert "APGKIT_SEALED" not in (svc.get("environment") or {}), "the blob goes in a file, not in the container environment"
    for sec in doc["secrets"].values():
        assert set(sec) == {"environment"}, "compose secrets come from the environment, never from a file on disk"


def test_every_app_is_sealed_and_has_its_own_database_and_role():
    m = load(MANIFEST)
    doc = yaml.safe_load(COMPOSE.read_text())
    apps = [a.key for a in devstack.deployed(m)]
    for key in apps:
        svc = doc["services"][key]
        assert svc["environment"]["APGKIT_SEALED_FILE"] == "/run/secrets/apgkit_sealed"
        assert svc["secrets"] == [{"source": f"sealed_{key}", "target": "apgkit_sealed"}]
        assert svc["ports"][0].startswith("127.0.0.1:")
        assert svc["build"]["context"] == f"../../{devstack.REPOS[key]}"
    specs = {s.split(":")[0]: s for s in doc["services"]["postgres"]["environment"]["APP_DATABASES"].split()}
    assert set(specs) == set(apps)
    assert {k for k, s in specs.items() if s.split(":")[1] == "1"} == set(devstack.AIDB_APPS)
    assert "query_advisor" not in doc["services"]["postgres"]["environment"]["APP_DATABASES"]


def test_the_init_script_makes_unprivileged_roles_without_a_password():
    sh = (ROOT / "docker" / "initdb" / "20-app-databases.sh").read_text()
    assert "NOSUPERUSER" in sh and "NOCREATEDB" in sh and "NOCREATEROLE" in sh
    code = "\n".join(line for line in sh.splitlines() if not line.lstrip().startswith("#"))
    assert "PASSWORD" not in code.upper().replace("POSTGRES_PASSWORD", "")
    assert "REVOKE ALL ON DATABASE" in sh and "GRANT aidb_users" in sh


def test_the_blob_holds_the_secrets_and_the_database_url(tmp_path):
    m = load(MANIFEST)
    ctx = Context(m, State(tmp_path / "dev"), None)                                            # type: ignore[arg-type]
    for a in devstack.deployed(m):
        e = ctx.state.app(a.key)
        e["url"] = devstack.url_of(a.key)
        e.update(client_secret="oidc-secret-value", service_secret="svc-secret-value", llm_key="lg_key_value")
    calls = []
    stack = devstack.Stack(ctx, COMPOSE, say=lambda t: None, run=lambda *a, **k: calls.append((a, k)))
    td = m.apps["trustdesk"]
    blob = stack.blob(td)
    opened = kit.open_blob(blob, PASS)
    assert opened["DATABASE_URL"].startswith("postgresql://trustdesk:") and opened["DATABASE_URL"].endswith("@postgres:5432/trustdesk?sslmode=disable")
    assert opened["TRUSTDESK_OIDC_CLIENT_SECRET"] == "oidc-secret-value" and opened["LLM_API_KEY"] == "lg_key_value"
    assert td.encryption_key_name not in opened, "the passphrase is the only unlock secret"
    assert "oidc-secret-value" not in blob and "postgresql://" not in blob


def test_only_a_missing_container_is_created_with_a_blob(tmp_path):
    m = load(MANIFEST)
    ctx = Context(m, State(tmp_path / "dev"), None)                                            # type: ignore[arg-type]
    ctx.state.app("identity")["url"] = devstack.url_of("identity")
    seen: list[dict] = []

    def run(cmd, env=None, input=None, **kw):
        out = {"ps": "running\n"}.get(cmd[4], "")
        seen.append({"cmd": cmd, "env": env})
        return subprocess.CompletedProcess(cmd, 0, out, "")

    stack = devstack.Stack(ctx, COMPOSE, say=lambda t: None, run=run)
    assert stack.start_app(m.identity, True, False) == "running"
    assert not any(c["cmd"][4] == "up" for c in seen), "a running container is left alone: the repeat run changes nothing"


def test_a_provider_is_added_only_when_a_key_is_exported():
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path, json.loads(request.content) if request.content else None))
        if request.url.path == "/api/providers" and request.method == "GET":
            return httpx.Response(200, json={"items": [{"name": "echo"}]})
        if request.url.path == "/api/providers":
            return httpx.Response(201, json={"id": 7})
        if request.url.path == "/api/models" and request.method == "GET":
            return httpx.Response(200, json={"items": [{"alias": "chat-default"}, {"alias": "embed-default", "embedding_dim": 1536}]})
        return httpx.Response(200, json={})

    from ecoctl.llm_client import Gateway
    gw = Gateway("http://gw.test", "k" * 32, httpx.Client(transport=httpx.MockTransport(handler)))
    assert "stay on the built-in echo" in devstack.ensure_provider(gw, {})
    assert calls == []
    out = devstack.ensure_provider(gw, {devstack.PROVIDER_ENV["key"]: "sk-test-not-real"})
    assert "now use it" in out
    puts = {p: b for m_, p, b in calls if m_ == "PUT"}
    assert puts["/api/models/chat-default"]["targets"] == [{"provider_id": 7, "upstream_model": "gpt-4o-mini"}]
    assert puts["/api/models/embed-default"]["embedding_dim"] == 1536
    assert re.search("sk-test", json.dumps(out)) is None, "the key is never in the outcome line"
