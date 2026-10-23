"""`ecoctl apply` wires every app that uses an LLM to the gateway: a client per app, its key kept once, the environment injected, and
the same on every later run. Everything runs against the fakes in tests/fakes.py (no network, no agentpg)."""
import argparse
import json

import httpx
import pytest

from ecoctl import cli as cli_mod
from ecoctl.llm_client import Gateway, GatewayError
from ecoctl.plan import app_env, build
from ecoctl.state import State
from tests.fakes import FakeGateway
from tests.test_plan import MANIFEST, again, apply, world  # noqa: F401  (the fixture)

CONSUMERS = ("pagerlens", "paybroker", "trustdesk")


def gateway_of(idp) -> FakeGateway:
    return idp.gateway


def test_apply_gives_every_llm_app_a_client_a_key_and_the_environment(world):  # noqa: F811
    ctx, cli, idp = world
    gw = gateway_of(idp)
    apply(ctx)
    assert sorted(c["name"] for c in gw.clients.values()) == list(CONSUMERS)             # one per consumer; cpsim, citypulse... get none
    for name in CONSUMERS:
        c = next(c for c in gw.clients.values() if c["name"] == name)
        assert c["allowed_aliases"] == ["chat-default", "embed-default"] and c["rpm_limit"] == 60
        assert c["monthly_token_budget"] == 5_000_000 and c["budget_mode"] == "hard"
        key = ctx.state.app(name)["llm_key"]
        assert key.startswith("lg_")
        env = cli.sandboxes[f"eco-{name}"]["env"]
        # exactly the names TrustDesk and PagerLens read (config.py, embedcfg.py)
        assert env["LLM_BASE_URL"] == "https://eco-llmgw.sandbox.example/v1" and env["LLM_API_KEY"] == key
        assert env["LLM_CHAT_MODEL"] == "chat-default" and env["EMBEDDING_MODEL"] == "embed-default" and env["EMBEDDING_DIM"] == "1536"
    assert not any("LLM_BASE_URL" in cli.sandboxes[f"eco-{k}"]["env"] for k in ("citypulse", "cpsim", "llmgw", "identity"))
    # the gateway is created with the operator key and the default alias names it seeds
    gwenv = cli.sandboxes["eco-llmgw"]["env"]
    assert gwenv["LLMGW_API_KEY"] == ctx.state.secret("llmgw", "LLMGW_API_KEY") and gwenv["LLMGW_DEFAULT_CHAT_ALIAS"] == "chat-default"
    assert gwenv["LLMGW_DEFAULT_EMBED_ALIAS"] == "embed-default" and gwenv["LLMGW_DEFAULT_EMBED_DIM"] == "1536"
    # each app's key works for its own chat call, and the operator key never reaches /v1
    for name in CONSUMERS:
        assert Gateway("https://eco-llmgw.sandbox.example", "x", gw.client(gw.operator_key)).chat_test(ctx.state.app(name)["llm_key"], "chat-default") == (True, "ok")


def test_each_app_gets_a_different_key_and_the_state_file_stays_private(world):  # noqa: F811
    ctx, cli, idp = world
    apply(ctx)
    keys = [ctx.state.app(k)["llm_key"] for k in CONSUMERS]
    assert len(set(keys)) == 3
    assert json.loads(ctx.state.path.read_text())["apps"]["trustdesk"]["llm_key"] == keys[2] and oct(ctx.state.path.stat().st_mode & 0o777) == "0o600"


def test_the_manifest_budget_rpm_and_aliases_reach_the_client(tmp_path):
    import yaml

    from tests.test_manifest import doc
    d = doc()
    d["llm"].update({"rpm": 7, "budget": 1000, "budget_mode": "soft", "chat_alias": "smart", "embed_alias": "vectors", "embed_dimensions": 1536})
    path = tmp_path / "m.yaml"
    path.write_text(yaml.safe_dump(d))
    from ecoctl.agentpg import AgentPG
    from ecoctl.manifest import load
    from ecoctl.plan import Context
    from tests.fakes import FakeAgentPG, FakeIdentity
    cli, idp = FakeAgentPG(), FakeIdentity()
    idp.gateway = FakeGateway(aliases=("smart", "vectors"))
    ctx = Context(load(path), State(tmp_path / "s"), AgentPG(run=cli))
    ctx.http = idp.client(ctx.state.admin_key())
    ctx.llm_http = idp.gateway.client(ctx.state.secret("llmgw", "LLMGW_API_KEY"))
    apply(ctx)
    c = next(c for c in idp.gateway.clients.values() if c["name"] == "trustdesk")
    assert (c["rpm_limit"], c["monthly_token_budget"], c["budget_mode"], c["allowed_aliases"]) == (7, 1000, "soft", ["smart", "vectors"])
    env = cli.sandboxes["eco-trustdesk"]["env"]
    assert (env["LLM_CHAT_MODEL"], env["EMBEDDING_MODEL"], env["EMBEDDING_DIM"]) == ("smart", "vectors", "1536")
    assert cli.sandboxes["eco-llmgw"]["env"]["LLMGW_DEFAULT_EMBED_DIM"] == "1536"
    d["llm"]["budget"] = None
    path.write_text(yaml.safe_dump(d))
    assert load(path).llm.budget is None


def test_a_second_apply_changes_nothing_in_the_gateway(world):  # noqa: F811
    ctx, cli, idp = world
    gw = gateway_of(idp)
    apply(ctx)
    keys, calls, sandboxes = {k: ctx.state.app(k)["llm_key"] for k in CONSUMERS}, len(cli.calls), dict(cli.sandboxes)
    gw.requests.clear()
    ctx2 = again(ctx, cli, idp)
    out = dict(apply(ctx2))
    assert {k: ctx2.state.app(k)["llm_key"] for k in CONSUMERS} == keys
    assert [r for r in gw.requests if r[0] != "GET"] == []                                    # no client created, no key issued or rotated
    assert len(gw.clients) == 3 and all(len(v) == 1 for v in gw.keys.values()) and not getattr(gw, "rotations", 0)
    assert out["TrustDesk: LLM gateway client and key"] == "gateway client 'trustdesk': kept"
    assert [c for c in cli.calls[calls:] if c[1:3] == ["sandbox", "create"]] == [] and cli.sandboxes == sandboxes


def test_an_operators_changes_to_a_client_survive_apply(world):  # noqa: F811
    ctx, cli, idp = world
    gw = gateway_of(idp)
    apply(ctx)
    next(c for c in gw.clients.values() if c["name"] == "paybroker").update(rpm_limit=999, monthly_token_budget=42)
    apply(again(ctx, cli, idp))
    c = next(c for c in gw.clients.values() if c["name"] == "paybroker")
    assert c["rpm_limit"] == 999 and c["monthly_token_budget"] == 42


def test_a_lost_key_is_rotated_and_the_operator_is_told_what_to_do(world):  # noqa: F811
    ctx, cli, idp = world
    gw = gateway_of(idp)
    apply(ctx)
    old = ctx.state.app("trustdesk")["llm_key"]
    state = State(ctx.state.dir)
    del state.data["apps"]["trustdesk"]["llm_key"]                                          # the state file lost it
    out = dict(apply(again(ctx, cli, idp, state=state)))
    new = state.app("trustdesk")["llm_key"]
    assert new != old and new.startswith("lg_")
    assert "rotated (the key was lost)" in out["TrustDesk: LLM gateway client and key"] and "ecoctl recreate trustdesk" in out["TrustDesk: LLM gateway client and key"]
    assert gw.rotations == 1 and len(gw.clients) == 3
    g = Gateway("https://x", "k", gw.client(gw.operator_key))
    assert g.chat_test(old, "chat-default")[0] is False and g.chat_test(new, "chat-default")[0] is True       # the old key is dead
    assert State(ctx.state.dir).app("pagerlens")["llm_key"] == ctx.state.app("pagerlens")["llm_key"]            # the others are untouched
    assert "different environment" in out["TrustDesk: database and sandbox"]


def test_a_lost_key_with_no_active_key_left_gets_a_fresh_one(world):  # noqa: F811
    ctx, cli, idp = world
    gw = gateway_of(idp)
    apply(ctx)
    cid = next(i for i, c in gw.clients.items() if c["name"] == "pagerlens")
    for k in gw.keys[cid]:
        k["revoked"] = True
    state = State(ctx.state.dir)
    del state.data["apps"]["pagerlens"]["llm_key"]
    out = dict(apply(again(ctx, cli, idp, state=state)))
    assert "none was active" in out["PagerLens: LLM gateway client and key"] and state.app("pagerlens")["llm_key"] != ""
    assert sum(not k["revoked"] for k in gw.keys[cid]) == 1


def test_rotate_replaces_every_apps_key_and_leaves_one_live_key_each(world):  # noqa: F811
    ctx, cli, idp = world
    gw = gateway_of(idp)
    apply(ctx)
    before = {k: ctx.state.app(k)["llm_key"] for k in CONSUMERS}
    apply(again(ctx, cli, idp, rotate=True))
    after = {k: State(ctx.state.dir).app(k)["llm_key"] for k in CONSUMERS}
    assert all(after[k] != before[k] for k in CONSUMERS) and gw.rotations == 3
    assert all(sum(not k["revoked"] for k in ks) == 1 for ks in gw.keys.values())


def test_a_client_that_vanished_from_the_gateway_is_created_again_and_the_key_replaced(world):  # noqa: F811
    ctx, cli, idp = world
    gw = gateway_of(idp)
    apply(ctx)
    old = ctx.state.app("trustdesk")["llm_key"]
    gw.clients.clear()                                                                   # a replaced gateway database
    gw.keys.clear()
    apply(again(ctx, cli, idp))
    assert State(ctx.state.dir).app("trustdesk")["llm_key"] != old and sorted(c["name"] for c in gw.clients.values()) == list(CONSUMERS)


def test_a_gateway_that_does_not_know_the_operator_key_says_how_to_fix_it(world):  # noqa: F811
    ctx, cli, idp = world
    gateway_of(idp).operator_key = "something-else"                                      # created before ecoctl set LLMGW_API_KEY
    with pytest.raises(GatewayError, match="ecoctl recreate"):
        apply(ctx)
    assert "llm_key" not in ctx.state.app("trustdesk")


def test_an_unknown_alias_is_reported_by_the_gateway(world):  # noqa: F811
    ctx, cli, idp = world
    gateway_of(idp).aliases.discard("embed-default")
    with pytest.raises(GatewayError, match="unknown model alias: embed-default"):
        apply(ctx)


def test_apply_waits_for_a_gateway_that_is_still_starting(world, monkeypatch):  # noqa: F811
    ctx, cli, idp = world
    gateway_of(idp).ready_after = 3
    import ecoctl.llm_client as lc
    monkeypatch.setattr(lc.time, "sleep", lambda s: None)
    apply(ctx)
    assert len(gateway_of(idp).clients) == 3
    slow = Gateway("https://x", "k", httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(503))))
    ticks = iter(range(0, 10000, 100))
    with pytest.raises(GatewayError, match="not ready after"):
        slow.wait_ready(timeout=300, sleep=lambda s: None, clock=lambda: next(ticks))


# ---- the plan, the environment and secrets ---------------------------------------------------------------------------------
def test_the_plan_shows_the_wiring_without_any_key(world):  # noqa: F811
    ctx, cli, idp = world
    text = "\n".join([s.title + "\n" + "\n".join(s.detail) for s in build(ctx)])
    assert "TrustDesk: LLM gateway client and key" in text and "ensure client 'trustdesk'" in text and "5,000,000 tokens/month, hard" in text
    assert "LLM_BASE_URL=" in text and "<llmgw url>/v1" in text and "LLM_API_KEY=***" in text and "LLMGW_API_KEY=***" in text
    assert "LLM_CHAT_MODEL=chat-default" in text and "EMBEDDING_MODEL=embed-default" in text and "EMBEDDING_DIM=1536" in text
    assert ctx.state.secret("llmgw", "LLMGW_API_KEY") not in text
    titles = [s.title for s in build(ctx)]
    assert titles.index("LLM gateway: database and sandbox") < titles.index("TrustDesk: LLM gateway client and key") < titles.index("TrustDesk: database and sandbox")


def test_the_plan_after_apply_masks_the_real_key(world):  # noqa: F811
    ctx, cli, idp = world
    apply(ctx)
    key = ctx.state.app("trustdesk")["llm_key"]
    text = "\n".join(d for s in build(ctx) for d in s.detail)
    assert key not in text and ctx.state.secret("llmgw", "LLMGW_API_KEY") not in text
    assert "LLM_BASE_URL=https://eco-llmgw.sandbox.example/v1" in text           # the URL is shown; only the key is hidden
    env, secret_names = app_env(ctx, ctx.manifest.apps["trustdesk"])
    assert "LLM_API_KEY" in secret_names and env["LLM_API_KEY"] == key


def test_env_command_masks_secrets_unless_asked(world, capsys, monkeypatch):  # noqa: F811
    ctx, cli, idp = world
    apply(ctx)
    monkeypatch.setattr(cli_mod, "_ctx", lambda a: ctx)
    key = ctx.state.app("trustdesk")["llm_key"]
    assert cli_mod.cmd_env(argparse.Namespace(app="trustdesk", reveal=False)) == 0
    out = capsys.readouterr()
    assert key not in out.out and "LLM_API_KEY='***'" in out.out and "LLM_BASE_URL='https://eco-llmgw.sandbox.example/v1'" in out.out
    assert cli_mod.cmd_env(argparse.Namespace(app="trustdesk", reveal=True)) == 0
    assert f"LLM_API_KEY='{key}'" in capsys.readouterr().out


def test_recreating_the_gateway_recreates_every_app_that_uses_it_with_the_new_url(world, monkeypatch):  # noqa: F811
    ctx, cli, idp = world
    apply(ctx)
    before = {k: ctx.state.app(k)["sandbox_id"] for k in ("llmgw", "pagerlens", "citypulse", "cpsim", "trustdesk", "paybroker", "identity")}
    keys = {k: ctx.state.app(k)["llm_key"] for k in CONSUMERS}
    monkeypatch.setattr(cli_mod, "_ctx", lambda a: again(ctx, cli, idp))
    ns = argparse.Namespace(apps="llmgw", yes=True, file=str(MANIFEST), state=str(ctx.state.dir), execute=False, only=None, rotate=False)
    assert cli_mod.cmd_recreate(ns) == 0
    after = State(ctx.state.dir)
    changed = {k for k, v in before.items() if after.app(k)["sandbox_id"] != v}
    assert changed == {"llmgw", "pagerlens", "paybroker", "trustdesk", "citypulse", "cpsim"}            # identity was left alone
    new_url = after.app("llmgw")["url"]
    for k in CONSUMERS:
        assert cli.sandboxes[f"eco-{k}"]["env"]["LLM_BASE_URL"] == new_url + "/v1" and after.app(k)["llm_key"] == keys[k]     # same key, kept
    assert len(gateway_of(idp).clients) == 3                                                           # nothing was created again


def test_only_a_consumer_also_deploys_the_gateway(tmp_path):
    from tests.fakes import FakeAgentPG, FakeIdentity
    cli, idp = FakeAgentPG(), FakeIdentity()
    idp.gateway = FakeGateway()
    ctx = again(None, cli, idp, state=State(tmp_path / "s"), only={"paybroker"})
    apply(ctx)
    assert set(cli.sandboxes) == {"eco-identity", "eco-llmgw", "eco-paybroker"} and [c["name"] for c in idp.gateway.clients.values()] == ["paybroker"]


# ---- status and `ecoctl llm test` ---------------------------------------------------------------------------------------------
@pytest.fixture
def deployed(world, monkeypatch):  # noqa: F811
    ctx, cli, idp = world
    apply(ctx)
    monkeypatch.setattr(cli_mod, "_ctx", lambda a: again(ctx, cli, idp))
    monkeypatch.setattr(cli_mod.httpx, "get", lambda url, **kw: httpx.Response(200, request=httpx.Request("GET", url)))
    monkeypatch.setattr(cli_mod, "Identity", lambda url, key: type("I", (), {"apps": lambda self: idp_apps(idp)})())
    return ctx, cli, idp


def idp_apps(idp):
    return {k: {**a, "client_id": k} for k, a in idp.apps.items()}


def test_status_reports_each_clients_existence_and_a_real_call(deployed, capsys):
    ctx, cli, idp = deployed
    assert cli_mod.cmd_status(argparse.Namespace(file=str(MANIFEST), state=str(ctx.state.dir))) == 0
    out = capsys.readouterr().out
    assert "LLM gateway (llmgw): https://eco-llmgw.sandbox.example" in out
    section = out.split("LLM gateway (")[1].splitlines()
    for k in CONSUMERS:
        assert next(ln for ln in section if ln.strip().startswith(k)).split() == [k, "ok", "ok", "ok"]
    assert [c["client"] for c in gateway_of(idp).chat_calls] == sorted(CONSUMERS) and all(c["max_tokens"] == 1 for c in gateway_of(idp).chat_calls)
    assert all(ctx.state.app(k)["llm_key"] not in out for k in CONSUMERS)             # keys are never printed


def test_status_flags_a_missing_client_a_revoked_key_and_a_broken_alias(deployed, capsys):
    ctx, cli, idp = deployed
    gw = gateway_of(idp)
    cid = next(i for i, c in gw.clients.items() if c["name"] == "paybroker")
    del gw.clients[cid]                                                                # paybroker's client is gone
    cid = next(i for i, c in gw.clients.items() if c["name"] == "trustdesk")
    gw.keys[cid][0]["revoked"] = True                                                  # trustdesk's key was revoked
    assert cli_mod.cmd_status(argparse.Namespace(file=str(MANIFEST), state=str(ctx.state.dir))) == 1
    out = capsys.readouterr().out
    assert "MISSING" in out and "HTTP 401 invalid_api_key" in out
    gw.upstream_status = 502
    assert cli_mod.cmd_status(argparse.Namespace(file=str(MANIFEST), state=str(ctx.state.dir))) == 1
    assert "HTTP 502 upstream_unavailable" in capsys.readouterr().out


def test_status_notes_a_key_missing_from_the_state_file(deployed, capsys):
    ctx, cli, idp = deployed
    state = State(ctx.state.dir)
    del state.data["apps"]["pagerlens"]["llm_key"]
    state.save()
    assert cli_mod.cmd_status(argparse.Namespace(file=str(MANIFEST), state=str(ctx.state.dir))) == 1
    assert "no key in the state file" in capsys.readouterr().out


def test_llm_test_runs_one_call_with_that_apps_key(deployed, capsys):
    ctx, cli, idp = deployed
    gw = gateway_of(idp)
    ns = lambda app: argparse.Namespace(action="test", app=app, file=str(MANIFEST), state=str(ctx.state.dir))  # noqa: E731
    assert cli_mod.cmd_llm(ns("trustdesk")) == 0
    out = capsys.readouterr().out
    assert "trustdesk -> llmgw" in out and "ok" in out and ctx.state.app("trustdesk")["llm_key"] not in out
    assert gw.chat_calls == [{"client": "trustdesk", "model": "chat-default", "max_tokens": 1}]
    gw.upstream_status = 503
    assert cli_mod.cmd_llm(ns("trustdesk")) == 1 and "FAILED (HTTP 503" in capsys.readouterr().out
    assert cli_mod.cmd_llm(ns("cpsim")) == 1 and "not an app with `llm: true`" in capsys.readouterr().err
    assert cli_mod.cmd_llm(ns("ghost")) == 1


def test_llm_test_before_apply_explains_itself(world, monkeypatch, capsys):  # noqa: F811
    ctx, cli, idp = world
    monkeypatch.setattr(cli_mod, "_ctx", lambda a: ctx)
    assert cli_mod.cmd_llm(argparse.Namespace(action="test", app="trustdesk")) == 1
    assert "no gateway client yet" in capsys.readouterr().err


def test_main_parses_the_new_commands(deployed, capsys):
    ctx, *_ = deployed
    assert cli_mod.main(["--state", str(ctx.state.dir), "-f", str(MANIFEST), "llm", "test", "trustdesk"]) == 0
    assert cli_mod.main(["--state", str(ctx.state.dir), "-f", str(MANIFEST), "env", "trustdesk"]) == 0
    assert ctx.state.app("trustdesk")["llm_key"] not in capsys.readouterr().out
