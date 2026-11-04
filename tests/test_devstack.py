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


def test_the_tty_helper_types_the_secret_at_a_hidden_prompt_and_never_echoes_it():
    code, out = devstack.run_on_tty(["bash", "-c", "read -s -p 'Seal key passphrase: ' x; echo; [ \"$x\" = s3cret-value ] && echo matched"], {}, "s3cret-value")
    assert code == 0 and "matched" in out and "s3cret-value" not in out


def test_the_identity_dev_http_pair_is_set_for_identity_only():
    env = yaml.safe_load(COMPOSE.read_text())["services"]
    assert env["identity"]["environment"]["IDENTITY_DEV_HTTP"] == "1"
    assert env["identity"]["environment"]["IDENTITY_ISSUER"] == devstack.url_of("identity")
    assert not [k for k, v in env.items() if k != "identity" and "IDENTITY_DEV_HTTP" in v.get("environment", {})]


def _apps():
    return load(MANIFEST).apps


def test_an_aidb_check_passes_only_when_the_state_is_ready_for_apps_with_the_switch():
    A = _apps()
    ok, why = devstack.aidb_check(A["paybroker"], {"status": "ready", "aidb": {"enabled": True, "state": "ready"}})
    assert ok and "ready" in why
    for state in ("error", "tampered", "unavailable", "vault_locked", "not-set-up", "off"):
        ok, why = devstack.aidb_check(A["paybroker"], {"aidb": {"enabled": True, "state": state, "reason": "permission denied"}})
        assert not ok and "paybroker" in why and f"state={state}" in why, (state, why)
    assert "permission denied" in why
    assert not devstack.aidb_check(A["citypulse"], {"aidb": {"enabled": False}})[0]            # switch on, but readyz says nothing is active
    assert not devstack.aidb_check(A["pagerlens"], {"status": "ready"})[0]                       # no AIDB part at all
    assert devstack.aidb_check(A["pagerlens"], {"aidb": {"requested_backend": "aidb", "state": "ready"}})[0]


def test_trustdesk_is_ready_when_retrieval_is_in_use_and_no_problem_is_reported():
    assert devstack.aidb_check(_apps()["trustdesk"], {"aidb": {"requested": "aidb", "retrieval": True, "agent": True}})[0]
    ok, why = devstack.aidb_check(_apps()["trustdesk"], {"aidb": {"requested": "aidb", "retrieval": False, "agent": False, "problem": "not_set_up"}})
    assert not ok and "trustdesk" in why and "not_set_up" in why
    assert not devstack.aidb_check(_apps()["trustdesk"], {"aidb": {"requested": "aidb", "retrieval": True, "problem": "tampered"}})[0]


def test_the_smoke_test_fails_an_app_whose_aidb_state_is_not_ready(monkeypatch):
    from types import SimpleNamespace as NS
    bodies = {"paybroker": {"aidb": {"enabled": True, "state": "error", "reason": "permission denied"}},
              "citypulse": {"aidb": {"enabled": True, "state": "ready"}}}

    class Http:
        def get(self, url, **k):
            key = url.split("//")[1].split(".")[0]
            return NS(status_code=200, json=lambda: bodies.get(key, {}))
    monkeypatch.setattr(devstack.seal, "state_of", lambda url, http: "unsealed")
    mm = load(MANIFEST)
    apps = [NS(key=k, web=False, llm=False) for k in ("paybroker", "citypulse")]
    stack = NS(ctx=NS(http=Http(), gateway=lambda: None, state=None), m=mm, active=lambda: apps)
    lines = {name: (ok, detail) for name, ok, detail in devstack.smoke(stack)}
    ok, detail = lines["paybroker reports its AIDB state and it is ready"]
    assert not ok and "paybroker" in detail and "state=error" in detail
    assert lines["citypulse reports its AIDB state and it is ready"][0]


def test_paybroker_dev_http_and_the_egress_option_are_dev_stack_only():
    svc = yaml.safe_load(COMPOSE.read_text())["services"]
    assert svc["paybroker"]["environment"]["PAYBROKER_DEV_HTTP"] == "1"
    assert not [k for k, v in svc.items() if k != "paybroker" and "PAYBROKER_DEV_HTTP" in v.get("environment", {})]
    assert svc["paybroker"]["environment"]["PAYBROKER_DEV_WEBHOOK_HOSTS"] == "shopfront,shopfront.localhost"
    assert not [k for k, v in svc.items() if k != "paybroker" and any(n.startswith("PAYBROKER_DEV_") for n in v.get("environment", {}))]
    assert not [k for k, v in svc.items() if "PAYBROKER_ALLOW_PRIVATE_URLS" in v.get("environment", {})], "the SSRF guard is not switched off"
    assert svc["postgres"]["command"] == ["postgres", "-c", "shared_preload_libraries=aidb", "-c", "aidb.allow_insecure_egress=on"]
    assert "egress" not in (ROOT / "docker" / "aidb-postgres" / "Dockerfile").read_text(), "the option is not in the published image"


def test_a_real_provider_key_goes_to_the_gateway_only_after_the_limits_and_is_in_no_output(tmp_path, capsys):
    from ecoctl.llm_client import Gateway
    secret = "sk-proj-SENTINEL-must-never-appear-0123456789"
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path, json.loads(request.content) if request.content else None))
        p, mth = request.url.path, request.method
        if p == "/api/clients" and mth == "GET":
            return httpx.Response(200, json={"items": [{"id": 1, "name": "trustdesk"}, {"id": 2, "name": "pagerlens"}]})
        if p == "/api/providers" and mth == "GET":
            return httpx.Response(200, json={"items": []})
        if p == "/api/providers":
            return httpx.Response(201, json={"id": 9})
        if p == "/api/models" and mth == "GET":
            return httpx.Response(200, json={"items": [{"alias": "chat-default", "description": "d"}]})
        return httpx.Response(200, json={})

    gw = Gateway("http://gw.test", "k" * 32, httpx.Client(transport=httpx.MockTransport(handler)))
    lines = devstack.use_real_provider(gw, {"OPENAI_API_TOKEN": secret}, "openai")
    out = "\n".join(lines)
    assert secret not in out and secret not in capsys.readouterr().out
    first_post = next(i for i, c in enumerate(calls) if c[0] == "POST")
    assert [c[2] for c in calls[:first_post] if c[0] == "PATCH"] == [{"rpm_limit": 10, "monthly_token_budget": 300000, "budget_mode": "hard"}] * 2, "limits before the key"
    assert [c for c in calls if c[1] == "/api/providers" and c[0] == "POST"][0][2]["api_key"] == secret            # the one place the key goes: the gateway's admin API
    assert [c[1] for c in calls if c[0] == "PUT"] == ["/api/models/chat-default"], "embeddings are not repointed"
    assert secret not in COMPOSE.read_text()
    st = State(tmp_path / "s")
    st.save()
    assert not (tmp_path / "s").exists() or secret not in (tmp_path / "s" / "state.json").read_text()


def test_aidb_status_reads_each_opted_in_apps_readyz(tmp_path):
    from types import SimpleNamespace as NS

    from ecoctl import aidb
    m = load(MANIFEST)
    st = State(tmp_path / "s")
    for k in ("trustdesk", "pagerlens", "paybroker"):
        st.app(k)["url"] = f"http://{k}.localhost:1"
    bodies = {"trustdesk": {"aidb": {"requested": "aidb", "retrieval": True, "agent": True}},
              "pagerlens": {"aidb": {"state": "tampered", "tampered": ["pl_fence"]}}}

    class Http:
        def get(self, url, **k):
            key = url.split("//")[1].split(".")[0]
            if key == "paybroker":
                raise httpx.ConnectError("down")
            return NS(json=lambda: bodies[key])
    rows = {a: (s, d) for a, s, d in aidb.status(NS(manifest=m, state=st), Http())}
    assert rows["trustdesk"][0] == "ready" and rows["pagerlens"][0] == "tampered" and "pl_fence" in rows["pagerlens"][1]
    assert rows["paybroker"][0] == "unreachable" and rows["citypulse"][0] == "no url"
