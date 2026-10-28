"""`uses:` gives an app another app's URL (and cascades a recreate to it) without a scope: how the simulator reaches PayBroker (a merchant
key, not an identity scope) and holds all seven fleet addresses. Runs against the fakes in tests/fakes.py (no network, no agentpg)."""
import argparse
import copy

import pytest
import yaml

from ecoctl import cli as cli_mod
from ecoctl.manifest import ManifestError, load, parse
from ecoctl.plan import app_env, build, service_spec
from ecoctl.state import State
from tests.test_plan import MANIFEST, again, apply, world  # noqa: F401  (the fixture)

SEVEN = {"CITYPULSE_URL": "citypulse", "PAYBROKER_URL": "paybroker", "TRUSTDESK_URL": "trustdesk", "PAGERLENS_URL": "pagerlens",
         "LLMGW_URL": "llmgw", "IDENTITY_URL": "identity", "SHOPFRONT_URL": "shopfront"}


def doc():
    return copy.deepcopy(yaml.safe_load(MANIFEST.read_text()))


# ---- the manifest ------------------------------------------------------------------------------------
def test_the_shipped_manifest_lets_the_simulator_use_paybroker_and_identity_without_scopes():
    m = load(MANIFEST)
    cpsim = m.apps["cpsim"]
    assert cpsim.uses == ("paybroker", "identity") and "paybroker" not in cpsim.calls
    assert m.url_vars(cpsim) == {"citypulse": "CITYPULSE_URL", "trustdesk": "TRUSTDESK_URL", "pagerlens": "PAGERLENS_URL", "llmgw": "LLMGW_URL",
                                 "shopfront": "SHOPFRONT_URL", "paybroker": "PAYBROKER_URL", "identity": "IDENTITY_URL"}
    spec = service_spec(cpsim)
    assert all(not s.startswith("paybroker.") for s in spec["allowed_scopes"]) and "paybroker" not in spec["allowed_audiences"]   # no identity scope for it


def test_uses_orders_the_used_app_first_and_pulls_it_into_a_selection():
    m = load(MANIFEST)
    order = [a.key for a in m.order()]
    assert order.index("paybroker") < order.index("cpsim")
    assert m.closure({"cpsim"}) >= {"paybroker", "llmgw"} and m.needs("cpsim") >= {"paybroker"} and "identity" not in m.needs("cpsim")
    assert m.dependents({"paybroker"}) == {"cpsim", "shopfront"} and m.dependents({"llmgw"}) >= {"cpsim", "paybroker"}


@pytest.mark.parametrize("mutate, message", [
    (lambda d: d["apps"]["cpsim"].update({"uses": ["ghost"]}), "not an app in this manifest"),
    (lambda d: d["apps"]["cpsim"].update({"uses": ["cpsim"]}), "cannot use itself"),
    (lambda d: d["apps"]["cpsim"].update({"uses": ["paybroker", "paybroker"]}), "twice"),
    (lambda d: d["apps"]["cpsim"].update({"uses": ["trustdesk"]}), "already under calls"),
    (lambda d: d["apps"]["cpsim"].update({"uses": ["pagerlens-agent"]}), "not deployed"),
    (lambda d: d["apps"]["cpsim"].update({"uses": "paybroker"}), "must be a list"),
    (lambda d: d["apps"]["cpsim"].update({"uses": [{"paybroker": ["x"]}]}), "not an app key"),
    (lambda d: d["apps"]["cpsim"].update({"uses": ["Paybroker"]}), "not an app key"),
    (lambda d: d["apps"]["paybroker"].update({"uses": ["cpsim"]}), "cycle"),
    (lambda d: d["apps"]["cpsim"].update({"url_env": {"pagerlens-agent": "AGENT_URL"}}), "only apps it calls or uses"),
])
def test_uses_is_validated(mutate, message):
    d = doc()
    mutate(d)
    with pytest.raises(ManifestError, match=message):
        parse(d)


def test_uses_may_name_identity_and_a_url_variable_of_its_own():
    d = doc()
    d["apps"]["cpsim"]["url_env"] = {"paybroker": "SHOP_BROKER_URL"}
    m = parse(d)
    assert m.url_vars(m.apps["cpsim"])["paybroker"] == "SHOP_BROKER_URL"
    d = doc()
    d["apps"]["trustdesk"]["uses"] = ["identity"]
    assert parse(d).apps["trustdesk"].uses == ("identity",)
    d = doc()
    del d["apps"]["cpsim"]["uses"]
    assert parse(d).apps["cpsim"].uses == ()                         # optional: nothing changes for a manifest without it


# ---- the environment ---------------------------------------------------------------------------------
def test_the_simulator_sandbox_gets_all_seven_fleet_variables(world):  # noqa: F811
    ctx, cli, idp = world
    apply(ctx)
    env = cli.sandboxes["eco-cpsim"]["env"]
    for var, key in SEVEN.items():
        assert env[var] == ctx.state.app(key)["url"] == f"https://eco-{key}.sandbox.example", var
    assert env["CPSIM_OIDC_ISSUER"] == env["IDENTITY_URL"]
    assert "PAYBROKER_URL" not in cli.sandboxes["eco-trustdesk"]["env"] and "IDENTITY_URL" not in cli.sandboxes["eco-trustdesk"]["env"]   # only who asked
    # no URL is a secret, and nothing about uses touches the service client's scopes
    _, secret_names = app_env(ctx, ctx.manifest.apps["cpsim"])
    assert not set(SEVEN) & secret_names


def test_the_plan_shows_the_variables_before_anything_exists(world):  # noqa: F811
    ctx, cli, idp = world
    text = "\n".join(d for s in build(ctx) if s.title.startswith("CityPulse Simulator: database") for d in s.detail)
    for var, key in SEVEN.items():
        assert f"{var}=<{key} url>" in text or (key == "identity" and f"{var}=<identity url>" in text), var


def test_a_second_apply_changes_nothing(world):  # noqa: F811
    ctx, cli, idp = world
    apply(ctx)
    n = len(cli.calls)
    out = dict(apply(again(ctx, cli, idp)))
    assert "unchanged" in out["CityPulse Simulator: database and sandbox"] and not [c for c in cli.calls[n:] if c[1:3] == ["sandbox", "create"]]


def test_an_app_that_only_uses_another_gets_its_url_but_no_service_client(tmp_path):
    d = doc()
    d["apps"]["trustdesk"]["uses"] = ["paybroker"]                    # trustdesk calls nobody
    m = parse(d)
    assert not m.apps["trustdesk"].service and m.url_vars(m.apps["trustdesk"]) == {"paybroker": "PAYBROKER_URL"}
    from tests.fakes import FakeAgentPG, FakeGateway, FakeIdentity
    cli, idp = FakeAgentPG(), FakeIdentity()
    idp.gateway = FakeGateway()
    ctx = again(None, cli, idp, manifest=m, state=State(tmp_path / "s"))
    apply(ctx)
    env = cli.sandboxes["eco-trustdesk"]["env"]
    assert env["PAYBROKER_URL"] == "https://eco-paybroker.sandbox.example" and "TRUSTDESK_SERVICE_CLIENT_ID" not in env
    assert "trustdesk-service" not in idp.apps


# ---- the cascade -------------------------------------------------------------------------------------
def _recreate(ctx, cli, idp, monkeypatch, apps, manifest=None):
    monkeypatch.setattr(cli_mod, "_ctx", lambda a: again(ctx, cli, idp, manifest=manifest))
    ns = argparse.Namespace(apps=apps, yes=True, file=str(MANIFEST), state=str(ctx.state.dir), execute=False, only=None, rotate=False)
    assert cli_mod.cmd_recreate(ns) == 0
    return State(ctx.state.dir)


KEYS = ("llmgw", "pagerlens", "citypulse", "cpsim", "trustdesk", "paybroker", "shopfront", "identity")


def test_recreating_paybroker_recreates_the_simulator_with_the_fresh_url(world, monkeypatch, capsys):  # noqa: F811
    ctx, cli, idp = world
    cli.url_with_id = True                                             # as on AgentPG: a recreated sandbox has a new URL
    apply(ctx)
    before = {k: ctx.state.app(k)["sandbox_id"] for k in KEYS}
    old_url = ctx.state.app("paybroker")["url"]
    after = _recreate(ctx, cli, idp, monkeypatch, "paybroker")
    changed = {k for k, v in before.items() if after.app(k)["sandbox_id"] != v}
    assert changed == {"paybroker", "shopfront", "cpsim"}              # the simulator follows (and the shop, which uses paybroker too); trustdesk, citypulse, the gateway... do not
    assert "Also recreated because they call or use paybroker: cpsim" in capsys.readouterr().out
    assert after.app("paybroker")["url"] != old_url
    assert cli.sandboxes["eco-cpsim"]["env"]["PAYBROKER_URL"] == after.app("paybroker")["url"]      # the new environment, not the old one
    for var, key in SEVEN.items():
        assert cli.sandboxes["eco-cpsim"]["env"][var] == after.app(key)["url"]


def test_without_uses_the_simulator_would_keep_the_old_address(world, monkeypatch):  # noqa: F811
    d = doc()
    del d["apps"]["cpsim"]["uses"]
    del d["apps"]["cpsim"]["calls"]["shopfront"]                      # shopfront is recreated with paybroker and cpsim calls it: cut that path too
    ctx, cli, idp = world
    ctx.manifest = parse(d)
    apply(ctx)
    before = ctx.state.app("cpsim")["sandbox_id"]
    after = _recreate(ctx, cli, idp, monkeypatch, "paybroker", manifest=parse(d))
    assert after.app("cpsim")["sandbox_id"] == before and "PAYBROKER_URL" not in cli.sandboxes["eco-cpsim"]["env"]


def test_recreating_identity_recreates_everything_and_the_simulator_gets_the_new_identity_url(world, monkeypatch):  # noqa: F811
    ctx, cli, idp = world
    cli.url_with_id = True
    apply(ctx)
    before = {k: ctx.state.app(k)["sandbox_id"] for k in KEYS}
    old_identity = ctx.state.app("identity")["url"]
    idp.issuer = None                                                   # the recreated provider is a new process: it pins its new URL
    after = _recreate(ctx, cli, idp, monkeypatch, "identity")
    assert after.app("identity")["url"] != old_identity
    assert all(after.app(k)["sandbox_id"] != v for k, v in before.items())
    assert cli.sandboxes["eco-cpsim"]["env"]["IDENTITY_URL"] == after.app("identity")["url"]


def test_recreating_the_simulator_alone_touches_nothing_else(world, monkeypatch):  # noqa: F811
    ctx, cli, idp = world
    apply(ctx)
    before = {k: ctx.state.app(k)["sandbox_id"] for k in KEYS}
    after = _recreate(ctx, cli, idp, monkeypatch, "cpsim")
    assert {k for k, v in before.items() if after.app(k)["sandbox_id"] != v} == {"cpsim"}
