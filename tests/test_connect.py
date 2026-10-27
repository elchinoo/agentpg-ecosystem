"""`ecoctl connect`: ShopFront is connected to PayBroker by itself (merchant, one allowed origin, two keys, webhook, settings, test), a re-run
changes nothing, and a moved shop is followed. Everything runs against the fakes in tests/fakes_connect.py (no network, no agentpg)."""
import argparse
import copy
import json
import logging

import httpx
import pytest
import yaml

from ecoctl import cli as cli_mod
from ecoctl.connect import connection_status, reconcile
from ecoctl.connect_client import ConnectError, ServiceTokens, check_url, fingerprint
from ecoctl.manifest import ManifestError, load, parse
from ecoctl.plan import build, ecoctl_spec
from ecoctl.state import State
from tests.fakes_connect import fp
from tests.test_plan import MANIFEST, again, apply, world  # noqa: F401  (the fixture)

SHOP = "https://eco-shopfront.sandbox.example"
BROKER = "https://eco-paybroker.sandbox.example"


def doc():
    return copy.deepcopy(yaml.safe_load(MANIFEST.read_text()))


def rec(ctx) -> dict:
    return State(ctx.state.dir).app("shopfront")["connect"]["paybroker"]


def merchant(idp):
    (m,) = idp.connect.pb.merchants.values()
    return m


class Clock:
    """A clock that only moves when something sleeps, so waiting for a locked vault takes no time."""

    def __init__(self):
        self.now = 0.0
        self.slept: list[float] = []

    def sleep(self, s):
        self.slept.append(s)
        self.now += s

    def __call__(self):
        return self.now


def run(ctx, cli, idp, monkeypatch, *args, **kw):
    """`ecoctl connect ...` as a new process over the same fakes; returns the exit code."""
    clock = kw.pop("clock", None) or Clock()
    monkeypatch.setattr(cli_mod, "_ctx", lambda a: again(ctx, cli, idp, connect_sleep=clock.sleep, connect_clock=clock, **kw))
    return cli_mod.main(["--state", str(ctx.state.dir), "-f", str(MANIFEST), "connect", *args])


def secret_values(ctx, idp) -> list[str]:
    st = State(ctx.state.dir)
    r = st.app("shopfront")["connect"]["paybroker"]
    values = [st.app("ecoctl")["service_secret"], r["webhook_secret"], *(k["value"] for k in r["keys"].values()), *idp.connect.shop.values.values()]
    values += [f"tok.{aud}.{i}" for i in range(1, len(idp.token_requests) + 1) for aud in ("paybroker", "shopfront")]
    return [v for v in values if v and not v.startswith("https://")]


# ---- the manifest ---------------------------------------------------------------------------------------
def test_the_shipped_manifest_connects_the_shop_to_paybroker():
    m = load(MANIFEST)
    assert m.apps["shopfront"].connect["paybroker"].merchant == "ShopFront" and not m.apps["trustdesk"].connect
    assert "paybroker.admin" in m.apps["paybroker"].exposes and "shopfront.admin" in m.apps["shopfront"].exposes
    assert m.connect_scopes() == (["paybroker.admin", "shopfront.admin"], ["paybroker", "shopfront"])
    spec = ecoctl_spec(m)
    assert spec["client_id"] == "ecoctl" and spec["kind"] == "service" and spec["allowed_scopes"] == ["paybroker.admin", "shopfront.admin"]
    assert spec["allowed_audiences"] == ["paybroker", "shopfront"]


@pytest.mark.parametrize("mutate, message", [
    (lambda d: d["apps"]["shopfront"]["connect"]["paybroker"].update({"merchant": ""}), "merchant must be a text of 1-60"),
    (lambda d: d["apps"]["shopfront"]["connect"]["paybroker"].update({"merchant": "x" * 61}), "merchant must be a text of 1-60"),
    (lambda d: d["apps"]["shopfront"]["connect"]["paybroker"].update({"merchant": 5}), "merchant must be a text of 1-60"),
    (lambda d: d["apps"]["shopfront"]["connect"]["paybroker"].pop("merchant"), "merchant must be a text"),
    (lambda d: d["apps"]["shopfront"]["connect"]["paybroker"].update({"key": "x"}), "unknown key"),
    (lambda d: d["apps"]["shopfront"]["connect"].update({"trustdesk": {"merchant": "x"}}), "only paybroker can be connected"),
    (lambda d: d["apps"]["shopfront"].update({"connect": ["paybroker"]}), "must be a mapping"),
    (lambda d: d["apps"]["shopfront"].update({"uses": []}), "under `uses` or `calls`"),
    (lambda d: d["apps"]["shopfront"]["exposes"].remove("shopfront.admin"), "list it under apps.shopfront.exposes"),
    (lambda d: d["apps"]["paybroker"]["exposes"].remove("paybroker.admin"), "list it under apps.paybroker.exposes"),
    (lambda d: d["apps"]["paybroker"].update({"connect": {"paybroker": {"merchant": "x"}}}), "cannot be connected to itself"),
    (lambda d: d["apps"].update({"ecoctl": {"name": "x"}}), "reserved"),
])
def test_connect_is_validated(mutate, message):
    d = doc()
    mutate(d)
    with pytest.raises(ManifestError, match=message):
        parse(d)


def test_connect_is_optional_and_calls_also_count():
    d = doc()
    del d["apps"]["shopfront"]["connect"]
    assert parse(d).connect_scopes() == ([], [])
    d = doc()
    d["apps"]["shopfront"]["uses"] = []
    d["apps"]["shopfront"]["calls"] = {"paybroker": ["paybroker.admin"]}
    assert parse(d).apps["shopfront"].connect["paybroker"].merchant == "ShopFront"


def test_the_plan_registers_ecoctl_and_connects_after_the_groups(world):  # noqa: F811
    ctx, cli, idp = world
    titles = [s.title for s in build(ctx)]
    assert titles[-3:] == ["Access: group 'support'", "ecoctl: register its service client", "ShopFront: connect to PayBroker"]
    text = "\n".join(d for s in build(ctx) for d in s.detail)
    assert "<shopfront url>/webhooks/paybroker" in text


# ---- the first run: everything is created ---------------------------------------------------------------------
def test_apply_connects_the_shop_by_itself(world, capsys):  # noqa: F811
    ctx, cli, idp = world
    apply(ctx)
    out = capsys.readouterr().out
    for line in ("merchant 'ShopFront': created", f"origins: update to {SHOP}", "key ecoctl:shopfront:secret: create", "key ecoctl:shopfront:publishable: create",
                 f"webhook: set {SHOP}/webhooks/paybroker and rotate", "ShopFront settings: update base_url, publishable_key, secret_key, webhook_secret", "test: ok"):
        assert line in out, line
    pb, shop = idp.connect.pb, idp.connect.shop
    m = merchant(idp)
    assert m["name"] == "ShopFront" and m["allowed_origins"] == [SHOP] and m["webhook_url"] == f"{SHOP}/webhooks/paybroker"
    assert sorted((k["kind"], k["name"]) for k in m["keys"]) == [("publishable", "ecoctl:shopfront:publishable"), ("secret", "ecoctl:shopfront:secret")]
    r = rec(ctx)
    assert shop.values == {"base_url": BROKER, "publishable_key": r["keys"]["publishable"]["value"], "secret_key": r["keys"]["secret"]["value"],
                           "webhook_secret": r["webhook_secret"]} and r["webhook_url"] == f"{SHOP}/webhooks/paybroker" and r["merchant_id"] == m["id"]
    assert shop.puts == [["base_url", "publishable_key", "secret_key", "webhook_secret"]] and pb.rotations == 1
    # ecoctl is a service client of its own; each token asks for one admin scope of one audience
    assert idp.apps["ecoctl"]["kind"] == "service" and set(idp.apps["ecoctl"]["allowed_scopes"]) == {"paybroker.admin", "shopfront.admin"}
    assert {(t["client"], t["scope"], t["audience"]) for t in idp.token_requests} == {("ecoctl", "paybroker.admin", "paybroker"), ("ecoctl", "shopfront.admin", "shopfront")}
    assert State(ctx.state.dir).app("ecoctl")["service_secret"] == "cs_ecoctl_1" and oct(ctx.state.path.stat().st_mode & 0o777) == "0o600"


def test_a_rerun_on_a_connected_fleet_writes_nothing(world, monkeypatch, capsys):  # noqa: F811
    ctx, cli, idp = world
    apply(ctx)
    before_state = json.loads(ctx.state.path.read_text())
    before = (dict(idp.connect.shop.values), json.dumps(idp.connect.pb.merchants, sort_keys=True))
    idp.connect.pb.writes.clear()
    idp.connect.shop.writes.clear()
    calls = len(cli.calls)
    capsys.readouterr()
    assert run(ctx, cli, idp, monkeypatch) == 0
    out = capsys.readouterr().out
    assert idp.connect.all_writes() == [] and idp.connect.pb.rotations == 1                         # not one write call, no rotation
    assert (dict(idp.connect.shop.values), json.dumps(idp.connect.pb.merchants, sort_keys=True)) == before
    assert json.loads(ctx.state.path.read_text()) == before_state and len(cli.calls) == calls
    assert "nothing to change" in out and "created" not in out and "update" not in out
    for line in ("merchant 'ShopFront': kept", f"origins: kept ({SHOP})", "key ecoctl:shopfront:secret: kept", "webhook: kept", "ShopFront settings: kept", "test: ok"):
        assert line in out, line
    # the same through apply: its connect step finds nothing to do
    idp.connect.pb.writes.clear()
    out2 = dict(apply(again(ctx, cli, idp)))
    assert out2["ShopFront: connect to PayBroker"] == "connected (nothing to change)" and idp.connect.all_writes() == []


# ---- the shop moves ---------------------------------------------------------------------------------------
def test_a_recreated_shop_gets_its_origin_webhook_and_a_rotated_secret(world, monkeypatch, capsys):  # noqa: F811
    ctx, cli, idp = world
    cli.url_with_id = True                                              # as on AgentPG: a recreated sandbox has a new address
    apply(ctx)
    old_url, old_secret, old_keys = ctx.state.app("shopfront")["url"], rec(ctx)["webhook_secret"], {k: v["value"] for k, v in rec(ctx)["keys"].items()}
    capsys.readouterr()
    monkeypatch.setattr(cli_mod, "_ctx", lambda a: again(ctx, cli, idp))
    assert cli_mod.cmd_recreate(argparse.Namespace(apps="shopfront", yes=True, file=str(MANIFEST), state=str(ctx.state.dir), execute=False, only=None, rotate=False)) == 0
    out = capsys.readouterr().out
    new_url = State(ctx.state.dir).app("shopfront")["url"]
    assert new_url != old_url
    assert merchant(idp)["allowed_origins"] == [new_url] and merchant(idp)["webhook_url"] == f"{new_url}/webhooks/paybroker"     # the list is managed: the old origin is gone
    r = rec(ctx)
    assert r["webhook_secret"] != old_secret and idp.connect.pb.rotations == 2
    assert idp.connect.shop.values["webhook_secret"] == r["webhook_secret"] and idp.connect.shop.puts[-1] == ["webhook_secret"]       # pushed; keys and base URL were kept
    assert {k: v["value"] for k, v in r["keys"].items()} == old_keys                                                                 # no new key
    assert "origins: update to" in out and "(was: " + old_url in out and "webhook: set" in out and "key ecoctl:shopfront:secret: kept" in out and "test: ok" in out


def test_recreating_paybroker_reconnects_the_shop_to_the_new_address(world, monkeypatch, capsys):  # noqa: F811
    ctx, cli, idp = world
    cli.url_with_id = True
    apply(ctx)
    capsys.readouterr()
    monkeypatch.setattr(cli_mod, "_ctx", lambda a: again(ctx, cli, idp))
    assert cli_mod.cmd_recreate(argparse.Namespace(apps="paybroker", yes=True, file=str(MANIFEST), state=str(ctx.state.dir), execute=False, only=None, rotate=False)) == 0
    out = capsys.readouterr().out
    st = State(ctx.state.dir)
    assert "Also recreated because they call or use paybroker: cpsim, shopfront" in out                   # the cascade reconnects the shop
    assert idp.connect.shop.values["base_url"] == st.app("paybroker")["url"] != BROKER
    assert merchant(idp)["allowed_origins"] == [st.app("shopfront")["url"]] and "test: ok" in out


def test_only_paybroker_moving_pushes_the_base_url_and_nothing_else(world, monkeypatch):  # noqa: F811
    ctx, cli, idp = world
    apply(ctx)
    state = State(ctx.state.dir)
    state.app("paybroker")["url"] = "https://eco-paybroker-new.sandbox.example"
    state.save()
    idp.connect.hosts.clear()
    # the fake routes by host name: "paybroker" in the new host still reaches the fake PayBroker
    idp.connect.pb.writes.clear()
    idp.connect.shop.writes.clear()
    assert run(ctx, cli, idp, monkeypatch) == 0
    assert idp.connect.shop.puts[-1] == ["base_url"] and idp.connect.pb.writes == [] and idp.connect.pb.rotations == 1


# ---- keys -----------------------------------------------------------------------------------------------
def test_a_key_paybroker_no_longer_lists_is_created_again_and_pushed(world, monkeypatch, capsys):  # noqa: F811
    ctx, cli, idp = world
    apply(ctx)
    old = rec(ctx)["keys"]["secret"]
    pb = idp.connect.pb
    del pb.secrets[merchant(idp)["id"]][old["id"]]                      # revoked in PayBroker's own console
    merchant(idp)["keys"] = [k for k in merchant(idp)["keys"] if k["id"] != old["id"]]
    capsys.readouterr()
    assert run(ctx, cli, idp, monkeypatch) == 0
    out = capsys.readouterr().out
    new = rec(ctx)["keys"]["secret"]
    assert new["id"] != old["id"] and new["value"] != old["value"] and "PayBroker no longer lists it" in out
    assert idp.connect.shop.values["secret_key"] == new["value"] and idp.connect.shop.puts[-1] == ["secret_key"]
    assert len(merchant(idp)["keys"]) == 2 and "test: ok" in out and pb.rotations == 1


def test_a_lost_state_value_replaces_the_key_and_deletes_the_stale_one(world, monkeypatch, capsys):  # noqa: F811
    ctx, cli, idp = world
    apply(ctx)
    old = rec(ctx)["keys"]["publishable"]
    state = State(ctx.state.dir)
    del state.data["apps"]["shopfront"]["connect"]["paybroker"]["keys"]["publishable"]       # the state file lost the value
    state.save()
    capsys.readouterr()
    assert run(ctx, cli, idp, monkeypatch) == 0
    out = capsys.readouterr().out
    new = rec(ctx)["keys"]["publishable"]
    assert new["id"] != old["id"] and "delete 1 stale" in out
    assert [k["id"] for k in merchant(idp)["keys"] if k["kind"] == "publishable" and not k.get("revoked_at")] == [new["id"]]
    assert ("DELETE", f"/api/merchants/{merchant(idp)['id']}/keys/{old['id']}") in idp.connect.pb.writes


def test_keys_ecoctl_did_not_create_are_never_touched(world, monkeypatch):  # noqa: F811
    ctx, cli, idp = world
    apply(ctx)
    pb = idp.connect.pb
    mid = merchant(idp)["id"]
    merchant(idp)["keys"].append({"id": "key_hand", "kind": "secret", "name": "hand made", "last4": "abcd"})
    pb.secrets[mid]["key_hand"] = "sk_hand"
    merchant(idp)["keys"].append({"id": "key_other", "kind": "secret", "name": "ecoctl:otherapp:secret", "last4": "abcd"})
    old = rec(ctx)["keys"]["secret"]["id"]
    del pb.secrets[mid][old]
    merchant(idp)["keys"] = [k for k in merchant(idp)["keys"] if k["id"] != old]
    assert run(ctx, cli, idp, monkeypatch) == 0
    assert {"key_hand", "key_other"} <= {k["id"] for k in merchant(idp)["keys"]} and not [w for w in pb.writes if w[0] == "DELETE"]


def test_rotate_creates_new_keys_and_a_new_webhook_secret_and_pushes_them(world, monkeypatch, capsys):  # noqa: F811
    ctx, cli, idp = world
    apply(ctx)
    old = rec(ctx)
    capsys.readouterr()
    assert run(ctx, cli, idp, monkeypatch, "--rotate") == 0
    out = capsys.readouterr().out
    new = rec(ctx)
    assert new["webhook_secret"] != old["webhook_secret"] and all(new["keys"][k]["id"] != old["keys"][k]["id"] for k in new["keys"])
    assert len([k for k in merchant(idp)["keys"] if not k.get("revoked_at")]) == 2 and "rotating" in out and "delete 1 stale" in out                     # the old keys are gone, not piled up
    assert idp.connect.shop.puts[-1] == ["publishable_key", "secret_key", "webhook_secret"] and idp.connect.pb.rotations == 2 and "test: ok" in out
    n = len(idp.connect.all_writes())
    assert run(ctx, cli, idp, monkeypatch) == 0 and len(idp.connect.all_writes()) == n                          # and the next run is quiet again


def test_apply_never_rotates_the_merchants_credentials(world):  # noqa: F811
    ctx, cli, idp = world
    apply(ctx)
    old = rec(ctx)
    apply(again(ctx, cli, idp, rotate=True))                                                                      # `apply --rotate`: the identity client secrets
    assert rec(ctx) == old and idp.connect.pb.rotations == 1
    assert State(ctx.state.dir).app("ecoctl")["service_secret"] == "cs_ecoctl_2"                                  # ecoctl's own secret did rotate, and is used at once


# ---- a locked vault, a race, a failing push --------------------------------------------------------------
def test_a_locked_vault_is_retried_with_backoff_then_succeeds(world, monkeypatch, capsys):  # noqa: F811
    ctx, cli, idp = world
    apply(ctx)
    state = State(ctx.state.dir)
    del state.data["apps"]["shopfront"]["connect"]["paybroker"]["keys"]["secret"]
    state.save()
    idp.connect.shop.locked = 3
    clock = Clock()
    assert run(ctx, cli, idp, monkeypatch, clock=clock) == 0
    assert clock.slept == [1.0, 2.0, 4.0] and "test: ok" in capsys.readouterr().out and idp.connect.shop.locked == 0


def test_a_vault_that_stays_locked_stops_with_a_clear_message_and_saved_progress(world, monkeypatch, capsys):  # noqa: F811
    ctx, cli, idp = world
    apply(ctx)
    state = State(ctx.state.dir)
    del state.data["apps"]["shopfront"]["connect"]["paybroker"]["keys"]["secret"]
    state.save()
    idp.connect.shop.locked = 10**6
    clock = Clock()
    capsys.readouterr()
    assert run(ctx, cli, idp, monkeypatch, clock=clock) == 2
    err = capsys.readouterr().err
    assert "vault is still locked after 60 s" in err and "Progress is saved" in err and 60 <= clock.now < 90
    idp.connect.shop.locked = 0
    assert run(ctx, cli, idp, monkeypatch) == 0 and idp.connect.shop.values["secret_key"] == rec(ctx)["keys"]["secret"]["value"]


def test_a_merchant_created_behind_our_back_is_found_not_an_error(world, monkeypatch, capsys):  # noqa: F811
    ctx, cli, idp = world
    idp.connect.pb.race = True                                              # the name appears between our find and our create: 409
    apply(ctx)
    out = capsys.readouterr().out
    assert "merchant 'ShopFront': kept (created concurrently)" in out and len(idp.connect.pb.merchants) == 1 and "test: ok" in out


def test_an_existing_merchant_with_other_origins_gets_exactly_the_shop(world, monkeypatch):  # noqa: F811
    ctx, cli, idp = world
    m = idp.connect.pb.add_merchant("ShopFront")                            # made by hand, with an origin ecoctl did not add
    m["allowed_origins"] = ["https://old.example", SHOP + "/", "http://localhost:3000"]
    apply(ctx)
    assert merchant(idp)["allowed_origins"] == [SHOP] and len(idp.connect.pb.merchants) == 1


def test_a_failed_push_keeps_the_new_keys_and_the_rerun_finishes_without_duplicates(world, monkeypatch, capsys):  # noqa: F811
    ctx, cli, idp = world
    idp.connect.shop.fail_puts = 1
    with pytest.raises(ConnectError, match="PUT /api/settings/paybroker: HTTP 500 the shop broke"):
        apply(ctx)
    r = rec(ctx)                                                            # the values PayBroker showed once are already in the state file
    assert r["keys"]["secret"]["value"] and r["webhook_secret"] and len(merchant(idp)["keys"]) == 2
    capsys.readouterr()
    assert run(ctx, cli, idp, monkeypatch) == 0
    assert len(merchant(idp)["keys"]) == 2 and len(idp.connect.pb.merchants) == 1
    assert idp.connect.shop.values["secret_key"] == rec(ctx)["keys"]["secret"]["value"] == r["keys"]["secret"]["value"] and "test: ok" in capsys.readouterr().out


def test_a_failing_connection_test_is_an_error_with_the_reason(world, monkeypatch, capsys):  # noqa: F811
    ctx, cli, idp = world
    apply(ctx)
    idp.connect.pb.webhook_secrets[merchant(idp)["id"]] = "something-else"           # PayBroker signs with another secret than the shop holds
    capsys.readouterr()
    assert run(ctx, cli, idp, monkeypatch) == 2
    err = capsys.readouterr().err
    assert "connection test to paybroker failed: base_url: ok; secret_key: ok; publishable_key: ok; webhook_secret: FAILED" in err


def test_errors_stop_with_a_message_and_never_a_traceback(world, monkeypatch, capsys):  # noqa: F811
    ctx, cli, idp = world
    apply(ctx)
    state = State(ctx.state.dir)
    state.data["apps"]["ecoctl"]["service_secret"] = "wrong"
    state.save()
    capsys.readouterr()
    assert run(ctx, cli, idp, monkeypatch) == 2
    assert "refused the ecoctl service client for paybroker.admin (HTTP 401)" in capsys.readouterr().err
    del state.data["apps"]["ecoctl"]["service_secret"]
    state.save()
    assert run(ctx, cli, idp, monkeypatch) == 2
    assert "ecoctl has no service client yet" in capsys.readouterr().err


def test_connect_names_the_app_and_refuses_unknown_ones(world, monkeypatch, capsys):  # noqa: F811
    ctx, cli, idp = world
    apply(ctx)
    capsys.readouterr()
    assert run(ctx, cli, idp, monkeypatch, "shopfront") == 0 and "shopfront -> paybroker" in capsys.readouterr().out
    assert run(ctx, cli, idp, monkeypatch, "trustdesk") == 1 and "no `connect:` block" in capsys.readouterr().err
    assert run(ctx, cli, idp, monkeypatch, "ghost") == 1 and "no such app" in capsys.readouterr().err


# ---- dry run ---------------------------------------------------------------------------------------------
def test_dry_run_prints_the_plan_and_writes_nothing(world, monkeypatch, capsys):  # noqa: F811
    ctx, cli, idp = world
    apply(ctx)
    pb, shop = idp.connect.pb, idp.connect.shop
    pb.merchants.clear()                                                    # a fresh PayBroker database and a fresh shop
    pb.secrets.clear()
    pb.webhook_secrets.clear()
    shop.values.update({k: "" for k in shop.values})
    state_file = ctx.state.path.read_text()
    pb.writes.clear()
    shop.writes.clear()
    capsys.readouterr()
    assert run(ctx, cli, idp, monkeypatch, "--dry-run") == 0
    out = capsys.readouterr().out
    assert idp.connect.all_writes() == [] and ctx.state.path.read_text() == state_file and not pb.merchants
    for line in ("(dry run: nothing is written)", "merchant 'ShopFront': would create", f"origins: would update to {SHOP}", "key ecoctl:shopfront:secret: would create",
                 f"webhook: would set {SHOP}/webhooks/paybroker", "ShopFront settings: would update base_url, publishable_key, secret_key, webhook_secret",
                 "test: skipped (dry run)", "would change something: run without --dry-run"):
        assert line in out, line
    assert run(ctx, cli, idp, monkeypatch) == 0 and "test: ok" in capsys.readouterr().out                   # the real run then does it
    assert run(ctx, cli, idp, monkeypatch, "--dry-run") == 0 and "nothing to change" in capsys.readouterr().out


# ---- status ------------------------------------------------------------------------------------------------
def test_status_shows_a_connections_table_from_read_only_calls(world, monkeypatch, capsys):  # noqa: F811
    ctx, cli, idp = world
    apply(ctx)
    monkeypatch.setattr(cli_mod, "_ctx", lambda a: again(ctx, cli, idp))
    monkeypatch.setattr(cli_mod.httpx, "get", lambda url, **kw: httpx.Response(200, request=httpx.Request("GET", url)))
    monkeypatch.setattr(cli_mod, "Identity", lambda url, key: type("I", (), {"apps": lambda self: {k: {**a, "client_id": k} for k, a in idp.apps.items()}})())
    idp.connect.pb.writes.clear()
    idp.connect.shop.writes.clear()
    capsys.readouterr()
    assert cli_mod.cmd_status(argparse.Namespace(file=str(MANIFEST), state=str(ctx.state.dir))) == 0
    out = capsys.readouterr().out
    section = out.split("CONNECTIONS\n")[1].splitlines()
    assert section[0].split() == ["app", "->", "peer", "merchant", "origin", "keys", "webhook", "test"]
    assert section[1].split() == ["shopfront", "->", "paybroker", "ShopFront", "ok", "ok", "ok", "ok"]
    assert idp.connect.all_writes() == []
    # an origin that no longer matches (the shop moved) and a webhook secret the shop lost are shown, and make the exit code 1
    merchant(idp)["allowed_origins"] = ["https://elsewhere.example"]
    idp.connect.shop.values["webhook_secret"] = ""
    assert cli_mod.cmd_status(argparse.Namespace(file=str(MANIFEST), state=str(ctx.state.dir))) == 1
    row = capsys.readouterr().out.split("CONNECTIONS\n")[1].splitlines()[1].split()
    assert row[:4] == ["shopfront", "->", "paybroker", "ShopFront"] and row[4:7] == ["DIFFERS", "ok", "DIFFERS"] and row[7] == "FAILED"
    assert idp.connect.all_writes() == []


def test_status_before_ecoctl_registered_says_so_and_unreachable_services_are_a_row(world):  # noqa: F811
    ctx, cli, idp = world
    apply(ctx)
    state = State(ctx.state.dir)
    del state.data["apps"]["ecoctl"]["service_secret"]
    c2 = again(ctx, cli, idp, state=state)
    row = connection_status(c2, "shopfront")
    assert row.problems == 1 and "no service client yet" in row.test
    c3 = again(ctx, cli, idp)
    c3.connect_http = httpx.Client(transport=httpx.MockTransport(lambda r: (_ for _ in ()).throw(httpx.ConnectError("down"))))
    row = connection_status(c3, "shopfront")
    assert row.problems == 1 and "ConnectError" in row.test


# ---- security --------------------------------------------------------------------------------------------
def test_no_secret_reaches_the_output_the_logs_a_url_or_an_error(world, monkeypatch, capsys, caplog):  # noqa: F811
    caplog.set_level(logging.DEBUG)
    ctx, cli, idp = world
    cli.url_with_id = True
    seen: list[str] = []

    def take():
        out = capsys.readouterr()
        seen.extend([out.out, out.err])

    idp.connect.shop.fail_puts = 1
    with pytest.raises(ConnectError):
        apply(ctx)                                                         # a failure after secrets were created
    take()
    run(ctx, cli, idp, monkeypatch)
    take()
    run(ctx, cli, idp, monkeypatch, "--rotate")
    take()
    run(ctx, cli, idp, monkeypatch, "--dry-run")
    take()
    idp.connect.pb.webhook_secrets[merchant(idp)["id"]] = "x"
    run(ctx, cli, idp, monkeypatch)                                        # a failing test
    take()
    monkeypatch.setattr(cli_mod, "_ctx", lambda a: again(ctx, cli, idp))
    monkeypatch.setattr(cli_mod.httpx, "get", lambda url, **kw: httpx.Response(200, request=httpx.Request("GET", url)))
    monkeypatch.setattr(cli_mod, "Identity", lambda url, key: type("I", (), {"apps": lambda self: {k: {**a, "client_id": k} for k, a in idp.apps.items()}})())
    cli_mod.cmd_status(argparse.Namespace(file=str(MANIFEST), state=str(ctx.state.dir)))
    take()
    cli_mod.cmd_env(argparse.Namespace(app="shopfront", reveal=False))
    take()
    everything = "\n".join(seen) + caplog.text + "\n".join(idp.connect.urls)
    secrets = secret_values(ctx, idp)
    assert len(secrets) >= 6 and all(s not in everything for s in secrets), [s for s in secrets if s in everything]
    assert all("?" not in u or u.split("?")[1].startswith("name=") for u in idp.connect.urls)             # the only query is the merchant's name
    assert all(u.startswith("https://") for u in idp.connect.urls)


def test_plain_http_is_refused_except_for_localhost(world):  # noqa: F811
    ctx, cli, idp = world
    apply(ctx)
    state = State(ctx.state.dir)
    state.app("shopfront")["url"] = "http://eco-shopfront.sandbox.example"
    with pytest.raises(ConnectError, match="must be https"):
        reconcile(again(ctx, cli, idp, state=state), "shopfront", dry_run=True)
    assert check_url("http://localhost:8000/", "x") == "http://localhost:8000" and check_url("http://127.0.0.1:1", "x") == "http://127.0.0.1:1"
    assert check_url("https://a.example/", "x") == "https://a.example"
    for bad in ("http://a.example", "ftp://localhost", "localhost:8000", "", "https://"):
        with pytest.raises(ConnectError):
            check_url(bad, "x")


def test_tokens_are_cached_until_shortly_before_they_expire(world):  # noqa: F811
    ctx, cli, idp = world
    apply(ctx)
    clock = Clock()
    tokens = ServiceTokens(State(ctx.state.dir).app("identity")["url"], "ecoctl", "cs_ecoctl_1", idp.connect.client(), clock)
    a = tokens.token("paybroker")
    assert tokens.token("paybroker") == a and tokens.fetched == 1
    clock.now = 280                                                        # inside the 30 s margin before the 300 s expiry
    assert tokens.token("paybroker") != a and tokens.fetched == 2
    assert tokens.token("shopfront").startswith("tok.shopfront.") and tokens.fetched == 3


def test_fingerprints_are_the_first_8_hex_of_sha256():
    assert fingerprint("abc") == "ba7816bf" == fp("abc")
