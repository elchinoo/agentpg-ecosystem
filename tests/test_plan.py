import json
from pathlib import Path

import pytest

from ecoctl.agentpg import AgentPG, AgentPGError, Command
from ecoctl.manifest import load
from ecoctl.plan import Context, app_env, build, service_spec, web_spec
from ecoctl.state import State
from tests.fakes import FakeAgentPG, FakeGateway, FakeIdentity

MANIFEST = Path(__file__).resolve().parents[1] / "ecosystem.yaml"


@pytest.fixture
def world(tmp_path):
    cli, idp, gw = FakeAgentPG(), FakeIdentity(), FakeGateway()
    state = State(tmp_path / "eco")
    ctx = Context(load(MANIFEST), state, AgentPG(run=cli))
    ctx.http = idp.client(state.admin_key())
    ctx.llm_http = gw.client(state.secret("llmgw", "LLMGW_API_KEY"))
    idp.gateway = gw
    ctx.connect_http = idp.connect.client()
    return ctx, cli, idp


def again(ctx, cli, idp, *, manifest=None, state=None, **kw):
    """A new run of ecoctl over the same fakes (a new process: the state is read from the file again)."""
    c = Context(manifest or load(MANIFEST), state or State(ctx.state.dir), AgentPG(run=cli), **kw)
    c.http = idp.client(c.state.admin_key())
    c.llm_http = idp.gateway.client(c.state.secret("llmgw", "LLMGW_API_KEY"))
    if hasattr(idp, "connect"):
        c.connect_http = idp.connect.client()
    return c


def apply(ctx):
    out = []
    for step in build(ctx):
        out.append((step.title, step.run(ctx)))
    ctx.state.save()
    return out


def test_apply_creates_everything_in_dependency_order(world):
    ctx, cli, idp = world
    apply(ctx)
    created = [c[c.index("create") + 1] for c in cli.calls if c[1:3] == ["sandbox", "create"]]
    assert created == ["eco-identity", "eco-llmgw", "eco-pagerlens", "eco-citypulse", "eco-paybroker", "eco-trustdesk", "eco-cpsim", "eco-shopfront"]
    assert set(cli.sandboxes) == set(created) and "eco-pagerlens-agent" not in cli.sandboxes        # registered only, never deployed
    assert ("inst-3", "vector") in cli.extensions and len(cli.instances) == 8
    # an app's environment names the identity provider and the apps it calls, with real URLs
    env = cli.sandboxes["eco-cpsim"]["env"]
    assert env["CPSIM_OIDC_ISSUER"] == "https://eco-identity.sandbox.example"
    assert env["CITYPULSE_URL"] == "https://eco-citypulse.sandbox.example" and env["TRUSTDESK_URL"] == "https://eco-trustdesk.sandbox.example"
    assert env["CPSIM_OIDC_CLIENT_ID"] == "cpsim" and env["CPSIM_SERVICE_CLIENT_ID"] == "cpsim-service"
    assert env["CPSIM_OIDC_CLIENT_SECRET"] == "cs_cpsim_1" and env["CPSIM_SERVICE_CLIENT_SECRET"] == "cs_cpsim-service_1"
    # the identity provider got the static admin key and a generated encryption key
    assert cli.sandboxes["eco-identity"]["env"]["IDENTITY_API_KEY"] == ctx.state.admin_key()
    assert cli.sandboxes["eco-identity"]["env"]["IDENTITY_ENCRYPTION_KEY"] == ctx.state.secret("identity", "IDENTITY_ENCRYPTION_KEY")


def test_the_registry_ends_up_pointing_at_the_real_urls(world):
    ctx, cli, idp = world
    apply(ctx)
    td = idp.apps["trustdesk"]
    assert td["redirect_uris"] == ["https://eco-trustdesk.sandbox.example/auth/callback"] and td["url"] == "https://eco-trustdesk.sandbox.example"
    assert td["post_logout_uris"] == ["https://eco-trustdesk.sandbox.example/"]
    assert [r["name"] for r in td["roles"]] == ["admin", "approver", "viewer"]
    svc = idp.apps["cpsim-service"]
    assert svc["kind"] == "service" and svc["allowed_audiences"] == ["citypulse", "llmgw", "pagerlens", "trustdesk"]
    assert set(svc["allowed_scopes"]) == {"citypulse.ingest", "trustdesk.requests.write", "pagerlens.ingest", "llmgw.use"}
    assert "pagerlens-agent-service" in idp.apps and "pagerlens-agent" not in idp.apps          # service only
    assert idp.issuer == "https://eco-identity.sandbox.example"


def test_groups_get_grants_only_for_roles_an_app_defines(world):
    ctx, cli, idp = world
    apply(ctx)
    granted = {(g["subject_name"], g["client_id"], g["role"]) for g in idp.grants}
    assert ("ecosystem-admins", "trustdesk", "admin") in granted and ("ecosystem-admins", "llmgw", "admin") in granted
    assert ("risk-team", "paybroker", "risk_manager") in granted and ("support", "trustdesk", "approver") in granted
    assert not any(c == "pagerlens-agent" for _, c, _ in granted)


def test_a_second_run_converges_and_changes_nothing(world):
    ctx, cli, idp = world
    apply(ctx)
    calls, sandboxes, secrets_before = len(cli.calls), dict(cli.sandboxes), {k: dict(v) for k, v in ctx.state.data["apps"].items()}
    ctx2 = again(ctx, cli, idp)
    out = apply(ctx2)
    new = [c for c in cli.calls[calls:] if c[1] in {"create", "extensions"} or c[1:3] == ["sandbox", "create"]]
    assert new == [] and cli.sandboxes == sandboxes
    assert {k: v.get("client_secret") for k, v in ctx2.state.data["apps"].items()} == {k: v.get("client_secret") for k, v in secrets_before.items()}
    assert all(a.get("rotations", 0) == 0 for a in idp.apps.values())
    assert any("unchanged" in o for _, o in out)


def test_a_lost_secret_is_reissued_but_a_known_one_is_kept(world, tmp_path):
    ctx, cli, idp = world
    apply(ctx)
    state = State(ctx.state.dir)
    del state.data["apps"]["paybroker"]["client_secret"]
    ctx2 = again(ctx, cli, idp, state=state)
    apply(ctx2)
    assert idp.apps["paybroker"]["rotations"] == 1 and state.data["apps"]["paybroker"]["client_secret"] == "cs_paybroker_2"
    assert idp.apps["trustdesk"].get("rotations", 0) == 0


def test_rotate_flag_reissues_every_secret(world):
    ctx, cli, idp = world
    apply(ctx)
    ctx2 = again(ctx, cli, idp, rotate=True)
    apply(ctx2)
    assert all(a.get("rotations") == 1 for k, a in idp.apps.items())


def test_a_changed_environment_is_reported_not_silently_applied(world):
    ctx, cli, idp = world
    apply(ctx)
    m = load(MANIFEST)
    app = m.apps["trustdesk"]
    object.__setattr__(app, "env", {**app.env, "EXTRA": "1"})
    ctx2 = again(ctx, cli, idp, manifest=m)
    out = dict(apply(ctx2))
    assert "different environment" in out["TrustDesk: database and sandbox"]
    assert "EXTRA" not in cli.sandboxes["eco-trustdesk"]["env"]


def test_a_failure_keeps_progress_and_the_rerun_resumes(world):
    ctx, cli, idp = world
    cli.fail_on = "sandbox create"
    steps = build(ctx)
    with pytest.raises(RuntimeError):
        for s in steps:
            s.run(ctx)
    cli.fail_on = None
    assert ctx.state.app("identity").get("instance_id") == "inst-1"                           # saved before the failing step
    apply(ctx)
    assert len(cli.instances) == 8                                                           # the identity instance was reused


def test_apply_waits_while_a_sandbox_is_creating(world, monkeypatch):
    ctx, cli, idp = world
    cli.creating_polls = 2
    import ecoctl.agentpg as ag
    monkeypatch.setattr(ag.time, "sleep", lambda s: None)
    apply(ctx)
    assert ctx.state.app("trustdesk")["url"] == "https://eco-trustdesk.sandbox.example"
    assert max(sb["polls"] for sb in cli.sandboxes.values()) >= 3


def test_a_failed_or_never_ready_sandbox_is_an_error_with_the_reason():
    cli = FakeAgentPG()
    cli(["agentpg", "create", "--name", "d", "--size", "small"])
    cli(["agentpg", "sandbox", "create", "s", "--image", "i", "--instance", "inst-1", "--port", "8000"])
    sid = cli.sandboxes["s"]["id"]
    api = AgentPG(run=lambda args: "ID: x\nStatus: failed\n" if args[1:3] == ["sandbox", "status"] else f"ID NAME ERROR\n{sid} s provisioning failed (HTTP 400)\n")
    with pytest.raises(AgentPGError, match="is failed.*provisioning failed"):
        api.wait_url(sid, sleep=lambda s: None)
    slow = AgentPG(run=lambda args: "Status: creating\n")
    ticks = iter(range(0, 10000, 100))
    with pytest.raises(AgentPGError, match="still 'creating'"):
        slow.wait_url("x", timeout=300, sleep=lambda s: None, clock=lambda: next(ticks))


def test_secrets_are_masked_in_the_printed_plan_and_never_in_the_manifest(world):
    ctx, cli, idp = world
    text = "\n".join(d for s in build(ctx) for d in s.detail)
    assert "***" in text and "cs_" not in text and ctx.state.admin_key() not in text
    assert "<generated>" not in text
    cmd = AgentPG.create_sandbox("n", "img", "i-1", 8000, {"A_KEY": "s3cret", "B": "plain"}, {"A_KEY"})
    assert "s3cret" not in cmd.display() and "plain" in cmd.display() and "s3cret" in " ".join(cmd.args)
    assert isinstance(cmd, Command)


def test_state_file_is_private_and_atomic(world, tmp_path):
    ctx, *_ = world
    ctx.state.secret("trustdesk", "TRUSTDESK_ENCRYPTION_KEY")
    ctx.state.save()
    assert oct(ctx.state.path.stat().st_mode & 0o777) == "0o600" and oct(ctx.state.dir.stat().st_mode & 0o777) == "0o700"
    assert State(ctx.state.dir).secret("trustdesk", "TRUSTDESK_ENCRYPTION_KEY") == ctx.state.secret("trustdesk", "TRUSTDESK_ENCRYPTION_KEY")


def test_specs_match_what_identity_accepts():
    m = load(MANIFEST)
    spec = web_spec(m.apps["paybroker"])
    assert spec["kind"] == "web" and spec["redirect_uris"] == ["https://pending.invalid/auth/callback"] and len(spec["roles"]) == 5
    svc = service_spec(m.apps["citypulse"])
    assert svc["client_id"] == "citypulse-service" and svc["allowed_audiences"] == ["pagerlens"] and svc["redirect_uris"] == []


def test_env_for_an_app_before_anything_exists(tmp_path):
    ctx = Context(load(MANIFEST), State(tmp_path / "e"), AgentPG(run=FakeAgentPG()))
    env, secret_names = app_env(ctx, ctx.manifest.apps["trustdesk"])
    assert "TRUSTDESK_OIDC_ISSUER" not in env and "TRUSTDESK_ENCRYPTION_KEY" in secret_names and env["AUTO_SEED"] == "true"


def test_only_deploys_a_subset_with_what_it_calls(tmp_path):
    cli, idp, idp.gateway = FakeAgentPG(), FakeIdentity(), FakeGateway()
    state = State(tmp_path / "eco")
    ctx = again(None, cli, idp, state=state, only={"cpsim"})
    apply(ctx)
    assert set(cli.sandboxes) == {"eco-identity", "eco-llmgw", "eco-pagerlens", "eco-citypulse", "eco-trustdesk", "eco-paybroker", "eco-cpsim"}      # + citypulse -> pagerlens, + paybroker (used)
    assert "paybroker" in idp.apps and "pagerlens-agent" not in idp.apps          # a used app is deployed and registered
    granted = {(g["client_id"]) for g in idp.grants}
    assert "paybroker" in granted and "trustdesk" in granted


def test_unknown_only_names_fail_and_pause_and_plan_reach_the_command(world):
    ctx, cli, idp = world
    with pytest.raises(Exception, match="not an app"):
        ctx.manifest.closure({"ghost"})
    apply(ctx)
    assert all("--auto-pause=false" in c for c in cli.calls if c[1:3] == ["sandbox", "create"])      # the fleet default is to stay up
    cmd = AgentPG.create_sandbox("n", "i", "x", 8000, {}, set(), auto_pause=False, plan="medium")
    assert "--plan" in cmd.args and "medium" in cmd.args


def test_wait_instance_polls_until_running_and_reports_failure():
    states = iter(["creating", "creating", "running"])
    api = AgentPG(run=lambda a: f"ID NAME SIZE VERSION STATUS\n-- ---- ---- ------- ------\npg-1 eco-x-db small 18 {next(states)}\n")
    api.wait_instance("pg-1", sleep=lambda s: None)
    bad = AgentPG(run=lambda a: "ID NAME STATUS\npg-1 x failed\n")
    with pytest.raises(AgentPGError, match="is failed"):
        bad.wait_instance("pg-1", sleep=lambda s: None)


def test_a_sandbox_deleted_behind_our_back_is_recreated_by_the_next_apply(world):
    ctx, cli, idp = world
    apply(ctx)
    old = ctx.state.app("trustdesk")["sandbox_id"]
    del cli.sandboxes["eco-trustdesk"]
    ctx2 = again(ctx, cli, idp)
    apply(ctx2)
    assert "eco-trustdesk" in cli.sandboxes and ctx2.state.app("trustdesk")["sandbox_id"] != old


def test_dependents_follow_calls_transitively():
    m = load(MANIFEST)
    assert m.dependents({"pagerlens"}) == {"citypulse", "cpsim", "pagerlens-agent"} and m.dependents({"trustdesk"}) == {"cpsim"}
    assert m.dependents({"paybroker"}) == {"cpsim", "shopfront"}      # `uses` cascades


def test_recreate_cascades_to_callers_and_resets_the_url(world, monkeypatch):
    import argparse

    from ecoctl import cli as cli_mod
    ctx, cli, idp = world
    apply(ctx)
    before = {k: ctx.state.app(k)["sandbox_id"] for k in ("pagerlens", "citypulse", "cpsim", "trustdesk", "paybroker")}
    monkeypatch.setattr(cli_mod, "_ctx", lambda a: again(ctx, cli, idp))
    assert cli_mod.cmd_recreate(argparse.Namespace(apps="pagerlens", yes=True, file=str(MANIFEST), state=str(ctx.state.dir), execute=False, only=None, rotate=False)) == 0
    after = State(ctx.state.dir)
    changed = {k for k, v in before.items() if after.app(k)["sandbox_id"] != v}
    assert changed == {"pagerlens", "citypulse", "cpsim"}                     # trustdesk and paybroker were left alone
    assert cli.sandboxes["eco-cpsim"]["env"]["PAGERLENS_URL"] == after.app("pagerlens")["url"]


def test_the_database_key_is_kept_when_the_instance_is_created(world):
    ctx, cli, idp = world
    apply(ctx)
    for key in ctx.manifest.apps:
        entry = ctx.state.app(key)
        if entry.get("instance_id"):
            assert entry["db_key"].startswith("a1p_sk_")
    assert json.loads(Path(ctx.state.path).read_text())["apps"]["paybroker"]["db_key"]
    assert oct(ctx.state.path.stat().st_mode & 0o777) == "0o600"


def test_db_command_prints_the_kept_key_and_reset_replaces_it(world, capsys, monkeypatch):
    from ecoctl import cli as cli_mod
    ctx, cli, idp = world
    apply(ctx)
    capsys.readouterr()                                                            # the connect steps print their progress
    monkeypatch.setattr(cli_mod, "_ctx", lambda args: ctx)
    kept = ctx.state.app("paybroker")["db_key"]
    assert cli_mod.cmd_db(type("A", (), {"app": "paybroker", "reset": False})()) == 0
    assert capsys.readouterr().out.strip() == kept
    assert cli_mod.cmd_db(type("A", (), {"app": "paybroker", "reset": True})()) == 0
    new = capsys.readouterr().out.strip()
    assert new != kept and State(ctx.state.dir).app("paybroker")["db_key"] == new
    del ctx.state.app("trustdesk")["db_key"]
    assert cli_mod.cmd_db(type("A", (), {"app": "trustdesk", "reset": False})()) == 0      # lost key: fetched afresh
    assert ctx.state.app("trustdesk")["db_key"]
