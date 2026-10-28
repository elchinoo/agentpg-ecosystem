# ruff: noqa: F811

"""`ecoctl llm sync`, the default-alias repair in `apply`, and the drift warning in `status`; all against the fakes (no network)."""
import argparse

import yaml

from ecoctl import cli as cli_mod
from tests.fakes import FakeGateway
from tests.test_llm import CONSUMERS, MANIFEST, again, apply, deployed, gateway_of  # noqa: F401  (the fixture)
from tests.test_plan import world  # noqa: F401


def client(gw: FakeGateway, name: str) -> dict:
    return next(c for c in gw.clients.values() if c["name"] == name)


def use(monkeypatch, fx, d, tmp_path):
    """Run the next commands against a modified manifest (the `deployed` fixture pins `_ctx` to the shipped one)."""
    from ecoctl.manifest import parse
    ctx, cli, idp = fx
    monkeypatch.setattr(cli_mod, "_ctx", lambda a: again(ctx, cli, idp, manifest=parse(d)))


def sync(ctx, *apps, apply_=False, all_=False, prune=False, file=MANIFEST):
    return cli_mod.cmd_llm_sync(argparse.Namespace(action="sync", apps=list(apps), apply=apply_, all=all_, prune_aliases=prune,
                                                   file=str(file), state=str(ctx.state.dir)))


def test_in_sync_fleet_exits_0_and_changes_nothing(deployed, capsys):
    ctx, cli, idp = deployed
    gw = gateway_of(idp)
    gw.requests.clear()
    assert sync(ctx) == 0
    assert "in sync" in capsys.readouterr().out
    assert [r for r in gw.requests if r[0] != "GET"] == [] and gw.patches == []


def test_diff_without_apply_prints_a_table_exits_1_and_changes_nothing(deployed, capsys):
    ctx, cli, idp = deployed
    gw = gateway_of(idp)
    client(gw, "paybroker").update(rpm_limit=999, monthly_token_budget=None, budget_mode="soft")
    before = {c["name"]: dict(c) for c in gw.clients.values()}
    assert sync(ctx) == 1
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert lines[0].split() == ["app", "field", "gateway", "manifest", "action"]
    assert next(ln for ln in lines if ln.startswith("paybroker") and " rpm " in ln).split() == ["paybroker", "rpm", "999", "60", "set"]
    assert any(ln.split()[:2] == ["paybroker", "budget"] and "none" in ln and "5,000,000" in ln for ln in lines)
    assert any(ln.split()[:2] == ["paybroker", "budget_mode"] for ln in lines) and "Nothing was changed" in out
    assert "trustdesk" not in "\n".join(lines[:-3])                          # the clients that match are not listed
    assert gw.patches == [] and {c["name"]: dict(c) for c in gw.clients.values()} == before


def test_apply_needs_apps_or_all_and_only_known_consumers(deployed, capsys):
    ctx, cli, idp = deployed
    client(gateway_of(idp), "paybroker").update(rpm_limit=1)
    assert sync(ctx, apply_=True) == 1 and "--apply needs" in capsys.readouterr().err
    assert sync(ctx, "citypulse") == 1 and "not an app with `llm: true`" in capsys.readouterr().err
    assert sync(ctx, "paybroker", all_=True, apply_=True) == 1
    assert gateway_of(idp).patches == [] and client(gateway_of(idp), "paybroker")["rpm_limit"] == 1


def test_apply_patches_only_the_named_app_and_only_the_differing_fields(deployed, capsys):
    ctx, cli, idp = deployed
    gw = gateway_of(idp)
    client(gw, "paybroker").update(rpm_limit=999)
    client(gw, "trustdesk").update(rpm_limit=1, budget_mode="soft")
    keys = {cid: [dict(k) for k in ks] for cid, ks in gw.keys.items()}
    assert sync(ctx, "paybroker", apply_=True) == 0
    assert gw.patches == [{"rpm_limit": 60}]                                   # nothing but the differing field
    assert client(gw, "paybroker")["rpm_limit"] == 60 and client(gw, "trustdesk")["rpm_limit"] == 1
    assert gw.keys == keys and not getattr(gw, "rotations", 0)                 # never touches keys
    assert sync(ctx, "paybroker", apply_=True) == 0 and len(gw.patches) == 1   # idempotent
    assert sync(ctx) == 1                                                       # trustdesk still drifts


def test_apply_all_fixes_every_app_and_a_second_run_is_clean(deployed, capsys):
    ctx, cli, idp = deployed
    gw = gateway_of(idp)
    for n in CONSUMERS:
        client(gw, n).update(rpm_limit=1)
    client(gw, "trustdesk").update(monthly_token_budget=7)
    assert sync(ctx, all_=True, apply_=True) == 0
    assert sorted(p.get("rpm_limit") for p in gw.patches) == [60] * len(CONSUMERS) and {"monthly_token_budget": 5_000_000, "rpm_limit": 60} in gw.patches
    n = len(gw.patches)
    assert sync(ctx) == 0 and sync(ctx, all_=True, apply_=True) == 0 and len(gw.patches) == n


def test_a_budget_can_be_cleared_to_none(deployed, monkeypatch, tmp_path):
    ctx, cli, idp = deployed
    gw = gateway_of(idp)
    d = yaml.safe_load(MANIFEST.read_text())
    d["apps"]["trustdesk"]["llm"] = {"budget": None}
    use(monkeypatch, deployed, d, tmp_path)
    assert sync(ctx, "trustdesk", apply_=True) == 0
    assert gw.patches == [{"monthly_token_budget": None}] and client(gw, "trustdesk")["monthly_token_budget"] is None


def test_per_app_override_beats_the_global_default(deployed, monkeypatch, tmp_path, capsys):
    ctx, cli, idp = deployed
    gw = gateway_of(idp)
    d = yaml.safe_load(MANIFEST.read_text())
    d["apps"]["trustdesk"]["llm"] = {"rpm": 5, "budget_mode": "soft"}
    use(monkeypatch, deployed, d, tmp_path)
    assert sync(ctx) == 1                                            # trustdesk differs, the others (global default) do not
    out = capsys.readouterr().out
    assert "trustdesk" in out and "pagerlens" not in out and "paybroker" not in out
    assert sync(ctx, "trustdesk", apply_=True) == 0
    c = client(gw, "trustdesk")
    assert (c["rpm_limit"], c["budget_mode"], c["monthly_token_budget"]) == (5, "soft", 5_000_000)
    assert client(gw, "pagerlens")["rpm_limit"] == 60


def test_missing_aliases_are_added_always_and_extras_are_kept_unless_pruned(deployed, capsys):
    ctx, cli, idp = deployed
    gw = gateway_of(idp)
    gw.aliases.update({"legacy"})
    client(gw, "trustdesk")["allowed_aliases"] = ["embed-default", "legacy"]      # lacks chat-default, has an extra
    assert sync(ctx) == 1
    out = capsys.readouterr().out
    assert "add chat-default" in out and "extra, kept" in out
    assert sync(ctx, "trustdesk", apply_=True) == 0
    assert client(gw, "trustdesk")["allowed_aliases"] == ["embed-default", "legacy", "chat-default"]      # added, extra kept
    assert gw.patches == [{"allowed_aliases": ["embed-default", "legacy", "chat-default"]}]
    assert sync(ctx, "trustdesk", apply_=True) == 0 and len(gw.patches) == 1
    capsys.readouterr()
    assert sync(ctx, "trustdesk") == 1 and "extra, kept" in capsys.readouterr().out    # still reported
    assert sync(ctx, "trustdesk", apply_=True, prune=True) == 0
    assert client(gw, "trustdesk")["allowed_aliases"] == ["embed-default", "chat-default"]
    assert sync(ctx) == 0


def test_the_manifest_can_list_extra_aliases_for_one_app(deployed, monkeypatch, tmp_path, capsys):
    ctx, cli, idp = deployed
    gw = gateway_of(idp)
    gw.aliases.add("smart")
    d = yaml.safe_load(MANIFEST.read_text())
    d["apps"]["pagerlens"]["llm"] = {"aliases": ["smart"]}
    use(monkeypatch, deployed, d, tmp_path)
    assert sync(ctx) == 1 and "add smart" in capsys.readouterr().out
    assert sync(ctx, "pagerlens", apply_=True) == 0
    assert client(gw, "pagerlens")["allowed_aliases"] == ["chat-default", "embed-default", "smart"]
    assert sync(ctx) == 0


def test_an_unknown_alias_is_a_gateway_error_for_that_app_and_exit_2(deployed, monkeypatch, tmp_path, capsys):
    ctx, cli, idp = deployed
    d = yaml.safe_load(MANIFEST.read_text())
    d["apps"]["pagerlens"]["llm"] = {"aliases": ["nope"]}
    d["apps"]["trustdesk"]["llm"] = {"rpm": 3}
    use(monkeypatch, deployed, d, tmp_path)
    assert sync(ctx, all_=True, apply_=True) == 2
    err = capsys.readouterr().err
    assert "pagerlens: FAILED" in err and "unknown model alias" in err
    assert client(gateway_of(idp), "trustdesk")["rpm_limit"] == 3                # the other app was still done


def test_a_client_that_does_not_exist_yet_is_reported_missing(deployed, capsys):
    ctx, cli, idp = deployed
    gw = gateway_of(idp)
    del gw.clients[next(i for i, c in gw.clients.items() if c["name"] == "paybroker")]
    assert sync(ctx) == 1
    assert "missing, run apply" in capsys.readouterr().out
    assert sync(ctx, "paybroker", apply_=True) == 0 and "run `ecoctl apply --execute`" in capsys.readouterr().out
    assert gw.patches == [] and not any(c["name"] == "paybroker" for c in gw.clients.values())     # sync never creates


def test_sync_before_the_gateway_exists_explains_itself(world, monkeypatch, capsys):  # noqa: F811
    ctx, cli, idp = world
    monkeypatch.setattr(cli_mod, "_ctx", lambda a: ctx)
    assert cli_mod.cmd_llm_sync(argparse.Namespace(apps=[], apply=False, all=False, prune_aliases=False)) == 1
    assert "not deployed yet" in capsys.readouterr().err


def test_main_parses_llm_sync(deployed, capsys):
    ctx, cli, idp = deployed
    base = ["--state", str(ctx.state.dir), "-f", str(MANIFEST), "llm"]
    client(gateway_of(idp), "pagerlens").update(rpm_limit=2)
    assert cli_mod.main([*base, "sync"]) == 1
    assert cli_mod.main([*base, "sync", "--apply", "pagerlens", "paybroker"]) == 0
    assert cli_mod.main([*base, "sync", "--all", "--apply", "--prune-aliases"]) == 0
    assert cli_mod.main([*base, "test", "trustdesk"]) == 0
    assert cli_mod.main([*base, "sync"]) == 0


# ---- apply repairs missing default aliases ---------------------------------------------------------------------------------
def test_apply_adds_missing_default_aliases_to_an_existing_client_and_nothing_else(world, capsys):  # noqa: F811
    ctx, cli, idp = world
    gw = gateway_of(idp)
    apply(ctx)
    c = client(gw, "paybroker")
    c.update(allowed_aliases=["embed-default"], rpm_limit=999, monthly_token_budget=42)       # a hand-made / tuned client
    keys = {cid: [dict(k) for k in ks] for cid, ks in gw.keys.items()}
    out = dict(apply(again(ctx, cli, idp)))
    assert c["allowed_aliases"] == ["embed-default", "chat-default"]
    assert c["rpm_limit"] == 999 and c["monthly_token_budget"] == 42                           # limits never overwritten
    assert gw.patches == [{"allowed_aliases": ["embed-default", "chat-default"]}]
    assert "added missing default alias(es) chat-default" in out["PayBroker: LLM gateway client and key"]
    assert gw.keys == keys and not getattr(gw, "rotations", 0)
    out = dict(apply(again(ctx, cli, idp)))                                                   # idempotent
    assert len(gw.patches) == 1 and out["PayBroker: LLM gateway client and key"] == "gateway client 'paybroker': kept"


def test_apply_does_not_add_per_app_extras_or_remove_anything(world):  # noqa: F811
    ctx, cli, idp = world
    gw = gateway_of(idp)
    apply(ctx)
    gw.aliases.add("legacy")
    client(gw, "trustdesk")["allowed_aliases"] = ["chat-default", "embed-default", "legacy"]
    apply(again(ctx, cli, idp))
    assert gw.patches == [] and "legacy" in client(gw, "trustdesk")["allowed_aliases"]


def test_a_new_client_gets_the_per_app_extra_aliases_and_limits(tmp_path):
    from ecoctl.agentpg import AgentPG
    from ecoctl.manifest import load
    from ecoctl.plan import Context
    from ecoctl.state import State
    from tests.fakes import FakeAgentPG, FakeIdentity
    d = yaml.safe_load(MANIFEST.read_text())
    d["apps"]["trustdesk"]["llm"] = {"rpm": 5, "budget": None, "aliases": ["smart"]}
    path = tmp_path / "m.yaml"
    path.write_text(yaml.safe_dump(d))
    cli, idp = FakeAgentPG(), FakeIdentity()
    idp.gateway = FakeGateway(aliases=("chat-default", "embed-default", "smart"))
    ctx = Context(load(path), State(tmp_path / "s"), AgentPG(run=cli))
    ctx.http = idp.client(ctx.state.admin_key())
    ctx.llm_http = idp.gateway.client(ctx.state.secret("llmgw", "LLMGW_API_KEY"))
    ctx.connect_http = idp.connect.client()
    apply(ctx)
    c = client(idp.gateway, "trustdesk")
    assert c["allowed_aliases"] == ["chat-default", "embed-default", "smart"] and c["rpm_limit"] == 5 and c["monthly_token_budget"] is None
    assert client(idp.gateway, "pagerlens")["rpm_limit"] == 60
    text = "\n".join("\n".join(s.detail) for s in __import__("ecoctl.plan", fromlist=["build"]).build(ctx))
    assert "aliases chat-default, embed-default, smart; 5 requests/min; no token budget" in text


# ---- status drift ----------------------------------------------------------------------------------------------------------
def test_status_warns_about_drift_without_failing_and_points_to_sync(deployed, capsys):  # noqa: F811
    ctx, cli, idp = deployed
    gw = gateway_of(idp)
    ns = argparse.Namespace(file=str(MANIFEST), state=str(ctx.state.dir))
    assert cli_mod.cmd_status(ns) == 0 and "DRIFT" not in capsys.readouterr().out
    client(gw, "paybroker").update(rpm_limit=999)
    client(gw, "trustdesk")["allowed_aliases"] = ["embed-default"]
    cli_mod.cmd_status(ns)
    out = capsys.readouterr().out
    section = out.split("LLM gateway (")[1].splitlines()
    assert next(ln for ln in section if ln.strip().startswith("paybroker")).split()[:3] == ["paybroker", "ok", "DRIFT"]
    assert next(ln for ln in section if ln.strip().startswith("pagerlens")).split()[:3] == ["pagerlens", "ok", "ok"]
    assert "warning:" in out and "paybroker, trustdesk" in out and "ecoctl llm sync" in out
    assert "model_not_allowed" in out or "HTTP 403" in out                                   # the live chat test still runs (and shows the lockout)
    assert [c["client"] for c in gw.chat_calls].count("paybroker") == 2                      # once per status run
