from pathlib import Path

import pytest

from ecoctl.agentpg import AgentPG, AgentPGError, Command
from ecoctl.manifest import load
from ecoctl.plan import Context, app_env, build, service_spec, web_spec
from ecoctl.state import State
from tests.fakes import FakeAgentPG, FakeIdentity

MANIFEST = Path(__file__).resolve().parents[1] / "ecosystem.yaml"


@pytest.fixture
def world(tmp_path):
    cli, idp = FakeAgentPG(), FakeIdentity()
    state = State(tmp_path / "eco")
    ctx = Context(load(MANIFEST), state, AgentPG(run=cli))
    ctx.http = idp.client(state.admin_key())
    return ctx, cli, idp


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
    assert created == ["eco-identity", "eco-pagerlens", "eco-citypulse", "eco-trustdesk", "eco-cpsim", "eco-llmgw", "eco-paybroker"]
    assert set(cli.sandboxes) == set(created) and "eco-pagerlens-agent" not in cli.sandboxes        # registered only, never deployed
    assert ("inst-2", "vector") in cli.extensions and len(cli.instances) == 7
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
    assert svc["kind"] == "service" and svc["allowed_audiences"] == ["citypulse", "pagerlens", "trustdesk"]
    assert set(svc["allowed_scopes"]) == {"citypulse.ingest", "trustdesk.requests.write", "pagerlens.ingest"}
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
    ctx2 = Context(load(MANIFEST), State(ctx.state.dir), AgentPG(run=cli))
    ctx2.http = idp.client(ctx2.state.admin_key())
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
    ctx2 = Context(load(MANIFEST), state, AgentPG(run=cli))
    ctx2.http = idp.client(state.admin_key())
    apply(ctx2)
    assert idp.apps["paybroker"]["rotations"] == 1 and state.data["apps"]["paybroker"]["client_secret"] == "cs_paybroker_2"
    assert idp.apps["trustdesk"].get("rotations", 0) == 0


def test_rotate_flag_reissues_every_secret(world):
    ctx, cli, idp = world
    apply(ctx)
    ctx2 = Context(load(MANIFEST), State(ctx.state.dir), AgentPG(run=cli), rotate=True)
    ctx2.http = idp.client(ctx2.state.admin_key())
    apply(ctx2)
    assert all(a.get("rotations") == 1 for k, a in idp.apps.items())


def test_a_changed_environment_is_reported_not_silently_applied(world):
    ctx, cli, idp = world
    apply(ctx)
    m = load(MANIFEST)
    app = m.apps["trustdesk"]
    object.__setattr__(app, "env", {**app.env, "EXTRA": "1"})
    ctx2 = Context(m, State(ctx.state.dir), AgentPG(run=cli))
    ctx2.http = idp.client(ctx2.state.admin_key())
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
    assert len(cli.instances) == 7                                                           # the identity instance was reused


def test_a_sandbox_without_a_url_is_an_error(world):
    ctx, cli, idp = world
    orig = cli.__call__

    def no_url(args):
        return "Name: x\nState: starting\n" if args[1:3] == ["sandbox", "status"] else orig(args)

    ctx.agentpg = AgentPG(run=no_url)
    with pytest.raises(AgentPGError, match="no URL yet"):
        apply(ctx)


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
    cli, idp = FakeAgentPG(), FakeIdentity()
    state = State(tmp_path / "eco")
    ctx = Context(load(MANIFEST), state, AgentPG(run=cli), only={"cpsim"})
    ctx.http = idp.client(state.admin_key())
    apply(ctx)
    assert set(cli.sandboxes) == {"eco-identity", "eco-pagerlens", "eco-citypulse", "eco-trustdesk", "eco-cpsim"}      # cpsim + its callees (citypulse -> pagerlens)
    assert "paybroker" not in idp.apps and "llmgw" not in idp.apps
    granted = {(g["client_id"]) for g in idp.grants}
    assert "paybroker" not in granted and "trustdesk" in granted


def test_unknown_only_names_fail_and_pause_and_plan_reach_the_command(world):
    ctx, cli, idp = world
    with pytest.raises(Exception, match="not an app"):
        ctx.manifest.closure({"ghost"})
    apply(ctx)
    ident = next(c for c in cli.calls if c[1:3] == ["sandbox", "create"] and c[3] == "eco-identity")
    other = next(c for c in cli.calls if c[1:3] == ["sandbox", "create"] and c[3] == "eco-trustdesk")
    assert "--auto-pause=false" in ident and "--auto-pause=false" not in other
    cmd = AgentPG.create_sandbox("n", "i", "x", 8000, {}, set(), auto_pause=False, plan="medium")
    assert "--plan" in cmd.args and "medium" in cmd.args
