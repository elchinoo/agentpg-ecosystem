"""`vault: password` (identity, the LLM gateway): no encryption key in their sandbox environment, the vault created and opened with the operator's
admin password, `ecoctl unlock` after a platform restart, the vault state in `ecoctl status`; apps with `vault: platform` are unchanged.
Everything runs against the fakes in tests/fakes.py (their vault follows the kit's rules for admin-password-only mode)."""
import copy
import json
import os
import stat

import httpx
import pytest
import yaml

from ecoctl import cli as cli_mod
from ecoctl.manifest import ManifestError, load, parse
from ecoctl.plan import Context, build
from ecoctl.state import State
from ecoctl.vault import VaultAdmin, VaultError, VaultState, describe, ensure_unlocked, operator_password
from tests.fakes import fake_get
from tests.test_llm import gateway_of
from tests.test_plan import MANIFEST, again, apply, world  # noqa: F401  (the fixture)


def doc():
    return copy.deepcopy(yaml.safe_load(MANIFEST.read_text()))


class Said:
    """What ecoctl showed once (`announce`): the one place a generated password may appear."""

    def __init__(self):
        self.lines: list[str] = []

    def __call__(self, text):
        self.lines.append(text)


def sandbox_env(cli) -> dict[str, dict]:
    return {n: s["env"] for n, s in cli.sandboxes.items()}


# ---- the manifest ------------------------------------------------------------------------------------------------------------
def test_the_shipped_manifest_opens_identity_and_the_gateway_with_the_password_and_keeps_the_platform_wrap_for_the_rest():
    m = load(MANIFEST)
    assert m.identity.vault == "password" and m.apps["llmgw"].vault == "password"
    assert [a.key for a in m.password_vault_apps()] == ["identity", "llmgw"]
    assert all(a.vault == "platform" for k, a in m.apps.items() if k != "llmgw")
    assert "IDENTITY_ENCRYPTION_KEY" not in m.identity.generate and "LLMGW_ENCRYPTION_KEY" not in m.apps["llmgw"].generate
    assert "TRUSTDESK_ENCRYPTION_KEY" in m.apps["trustdesk"].generate                  # auto-unlock stays for the others


def test_a_manifest_without_the_option_is_all_platform_and_keeps_its_generated_keys():
    d = doc()
    del d["identity"]["vault"], d["apps"]["llmgw"]["vault"]
    d["identity"]["generate"] = ["IDENTITY_ENCRYPTION_KEY"]
    d["apps"]["llmgw"]["generate"] = ["LLMGW_ENCRYPTION_KEY"]
    m = parse(d)
    assert m.identity.vault == "platform" and m.apps["llmgw"].vault == "platform" and m.password_vault_apps() == []
    assert m.identity.generate == ("IDENTITY_ENCRYPTION_KEY",)


def test_password_vault_is_refused_for_an_app_whose_code_has_no_support_and_says_so():
    d = doc()
    d["apps"]["trustdesk"]["vault"] = "password"
    with pytest.raises(ManifestError, match=r"apps\.trustdesk\.vault: password is not supported for trustdesk.*identity provider and the LLM gateway.*vault: platform"):
        parse(d)


def test_the_gateway_is_whatever_llm_gateway_names():
    d = doc()
    d["apps"]["llmgw"].pop("vault")
    d["apps"]["paybroker"]["vault"] = "password"
    with pytest.raises(ManifestError, match="not supported for paybroker"):
        parse(d)


def test_an_unknown_mode_a_listed_encryption_key_and_an_undeployed_app_are_refused():
    d = doc()
    d["identity"]["vault"] = "kms"
    with pytest.raises(ManifestError, match=r"identity\.vault must be one of platform, password"):
        parse(d)
    d = doc()
    d["identity"]["generate"] = ["IDENTITY_ENCRYPTION_KEY"]
    with pytest.raises(ManifestError, match="must not be listed under `generate:`"):
        parse(d)
    d = doc()
    d["apps"]["llmgw"]["generate"] = ["LLMGW_ENCRYPTION_KEY"]
    with pytest.raises(ManifestError, match="LLMGW_ENCRYPTION_KEY must not be listed"):
        parse(d)
    d = doc()
    d["apps"]["pagerlens-agent"]["vault"] = "password"
    with pytest.raises(ManifestError, match="not supported for pagerlens-agent"):
        parse(d)


def test_an_env_value_cannot_smuggle_the_key_back_in():
    d = doc()
    d["identity"]["env"] = {"IDENTITY_ENCRYPTION_KEY": "x"}
    with pytest.raises(ManifestError, match="looks like a secret"):
        parse(d)


# ---- apply: environment, order, the password ---------------------------------------------------------------------------------
def test_no_sandbox_of_identity_or_the_gateway_gets_an_encryption_key_and_the_others_keep_theirs(world):  # noqa: F811
    ctx, cli, idp = world
    apply(ctx)
    env = sandbox_env(cli)
    assert not [n for n in env["eco-identity"] if n.endswith("ENCRYPTION_KEY")]
    assert not [n for n in env["eco-llmgw"] if n.endswith("ENCRYPTION_KEY")]
    assert env["eco-trustdesk"]["TRUSTDESK_ENCRYPTION_KEY"] and env["eco-cpsim"]["CPSIM_ENCRYPTION_KEY"] and env["eco-shopfront"]["SHOPFRONT_ENCRYPTION_KEY"]
    assert "LLMGW_API_KEY" in env["eco-llmgw"]                                         # the operator key opens nothing by itself: it cannot unlock the vault
    assert not any(n.endswith("ENCRYPTION_KEY") for n in ctx.state.data["apps"]["identity"].get("generated", {}))


def test_the_vault_is_created_before_identity_is_waited_for_and_before_any_client_is_registered(world):  # noqa: F811
    ctx, cli, idp = world
    out = apply(ctx)
    titles = [t for t, _ in out]
    i = titles.index("Identity provider: create or open its vault with the operator's admin password")
    assert titles[i - 1] == "Identity provider: database and sandbox" and titles[i + 1].startswith("Identity provider: wait until it is ready")
    # the fake identity answers 503 `locked` on /readyz until the vault is open, so reaching the registrations proves the order; check it on the wire too
    wire = [p for _, p in idp.requests]
    assert wire.index("/api/admin-password") < wire.index("/readyz") < wire.index("/api/apps")
    gw = titles.index("LLM gateway: create or open its vault with the operator's admin password")
    assert titles[gw - 1] == "LLM gateway: database and sandbox" and gw < titles.index("PagerLens: LLM gateway client and key")
    assert idp.vault.unlocked and gateway_of(idp).vault.unlocked


def test_a_generated_password_is_kept_only_in_the_state_file_and_shown_once(world):  # noqa: F811
    ctx, cli, idp = world
    said = Said()
    ctx.announce = said
    out = apply(ctx)
    pw = State(ctx.state.dir).vault_admin_password()
    assert pw and len(pw) >= 32 and idp.vault.password == pw and gateway_of(idp).vault.password == pw
    assert len(said.lines) == 1 and pw in said.lines[0]                                 # shown once, for both apps, when it was made
    assert (ctx.state.dir / "state.json").stat().st_mode & 0o777 == 0o600
    assert not any(pw in json.dumps(e) for e in sandbox_env(cli).values())              # never in a sandbox environment
    assert not any(pw in line for _, line in out)                                       # nor in any step outcome
    said2 = Said()
    c2 = again(ctx, cli, idp, announce=said2)
    apply(c2)
    assert said2.lines == [] and idp.vault.initializations == 1 and gateway_of(idp).vault.initializations == 1       # a second run neither shows nor creates anything


def test_plan_and_env_never_contain_the_password(world, capsys):  # noqa: F811
    ctx, cli, idp = world
    apply(ctx)
    pw = ctx.state.vault_admin_password()
    capsys.readouterr()
    cli_mod.main(["--state", str(ctx.state.dir), "-f", str(MANIFEST), "plan"])
    for app in ("identity", "llmgw", "trustdesk"):
        cli_mod.main(["--state", str(ctx.state.dir), "-f", str(MANIFEST), "env", app, "--reveal"])
    shown = capsys.readouterr()
    assert pw not in shown.out and pw not in shown.err
    assert "admin password" in shown.out and "IDENTITY_ENCRYPTION_KEY is not set" in shown.out


def test_the_password_from_the_environment_is_used_and_not_kept(world, monkeypatch):  # noqa: F811
    ctx, cli, idp = world
    monkeypatch.setenv("ECOCTL_ADMIN_PASSWORD", "operator-held-passphrase-one")
    said = Said()
    ctx.announce = said
    apply(ctx)
    assert idp.vault.password == "operator-held-passphrase-one" == gateway_of(idp).vault.password
    assert said.lines == [] and State(ctx.state.dir).vault_admin_password() is None
    assert "operator-held-passphrase-one" not in (ctx.state.dir / "state.json").read_text()


def test_a_prompted_password_is_confirmed_when_a_vault_is_created_and_never_kept(world):  # noqa: F811
    ctx, cli, idp = world
    answers = iter(["typed-passphrase-123", "typed-passphrase-123"] * 2)
    ctx.prompt_password, ctx.ask_password = True, lambda _q: next(answers)
    apply(ctx)
    assert idp.vault.password == "typed-passphrase-123" and State(ctx.state.dir).vault_admin_password() is None
    c2 = again(ctx, cli, idp, prompt_password=True, ask_password=lambda _q: pytest.fail("an unlocked vault needs no password"))
    apply(c2)


def test_a_mistyped_confirmation_creates_nothing(world):  # noqa: F811
    ctx, cli, idp = world
    answers = iter(["typed-passphrase-123", "typed-passphrase-124"])
    ctx.prompt_password, ctx.ask_password = True, lambda _q: next(answers)
    with pytest.raises(VaultError, match="differ"):
        apply(ctx)
    assert not idp.vault.initialized


def test_a_weak_password_is_refused_by_the_app_and_the_error_does_not_echo_it(world, monkeypatch):  # noqa: F811
    ctx, cli, idp = world
    monkeypatch.setenv("ECOCTL_ADMIN_PASSWORD", "short")
    with pytest.raises(VaultError) as e:
        apply(ctx)
    assert "too short" in str(e.value) and "short" not in str(e.value).replace("too short", "")
    assert not idp.vault.initialized


def test_an_existing_vault_never_gets_a_made_up_password(world):  # noqa: F811
    ctx, cli, idp = world
    apply(ctx)
    ctx.state.data.pop("vault")                                                         # the state file was lost
    ctx.state.save()
    idp.vault.restart()
    c2 = again(ctx, cli, idp)
    with pytest.raises(VaultError, match="ECOCTL_ADMIN_PASSWORD.*--admin-password-prompt"):
        apply(c2)
    assert State(ctx.state.dir).vault_admin_password() is None and idp.vault.initializations == 1


def test_a_wrong_password_says_which_one_was_used_and_where_it_came_from_without_showing_it(world, monkeypatch):  # noqa: F811
    ctx, cli, idp = world
    apply(ctx)
    idp.vault.restart()
    monkeypatch.setenv("ECOCTL_ADMIN_PASSWORD", "not-the-right-passphrase")
    with pytest.raises(VaultError) as e:
        ensure_unlocked(again(ctx, cli, idp), load(MANIFEST).identity)
    assert "ECOCTL_ADMIN_PASSWORD" in str(e.value) and "not-the-right-passphrase" not in str(e.value) and "platform mode" in str(e.value)


# ---- restart, recreate, unlock -----------------------------------------------------------------------------------------------
def restart_platform(idp):
    idp.vault.restart()
    gateway_of(idp).vault.restart()


def run(ctx, cli, idp, monkeypatch, *args, **kw):
    monkeypatch.setattr(cli_mod, "_ctx", lambda a: again(ctx, cli, idp, vault_sleep=lambda s: None, **{"prompt_password": getattr(a, "admin_password_prompt", False), **kw}))
    return cli_mod.main(["--state", str(ctx.state.dir), "-f", str(MANIFEST), *args])


def test_after_a_platform_restart_both_stay_locked_until_unlock_opens_them_identity_first(world, monkeypatch, capsys):  # noqa: F811
    ctx, cli, idp = world
    apply(ctx)
    restart_platform(idp)
    assert not idp.vault.unlocked and not gateway_of(idp).vault.unlocked
    assert run(ctx, cli, idp, monkeypatch, "unlock") == 0
    out = capsys.readouterr().out
    assert out.index("identity:") < out.index("llmgw:") and idp.vault.unlocked and gateway_of(idp).vault.unlocked
    assert idp.vault.unlocks == 1 and idp.vault.initializations == 1                    # opened, not created again
    assert ctx.state.vault_admin_password() not in out


def test_unlock_of_one_app_and_of_an_already_open_one(world, monkeypatch, capsys):  # noqa: F811
    ctx, cli, idp = world
    apply(ctx)
    restart_platform(idp)
    assert run(ctx, cli, idp, monkeypatch, "unlock", "llmgw") == 0
    assert gateway_of(idp).vault.unlocked and not idp.vault.unlocked
    assert run(ctx, cli, idp, monkeypatch, "unlock") == 0
    assert "llmgw: vault already unlocked" in capsys.readouterr().out and idp.vault.unlocks == 1 and gateway_of(idp).vault.unlocks == 1


def test_unlock_takes_the_password_from_the_environment_or_a_prompt(world, monkeypatch):  # noqa: F811
    ctx, cli, idp = world
    monkeypatch.setenv("ECOCTL_ADMIN_PASSWORD", "operator-held-passphrase-one")
    apply(ctx)
    restart_platform(idp)
    assert run(ctx, cli, idp, monkeypatch, "unlock") == 0                               # from the environment: nothing in the state file
    restart_platform(idp)
    monkeypatch.delenv("ECOCTL_ADMIN_PASSWORD")
    assert run(ctx, cli, idp, monkeypatch, "unlock") == 2 and not idp.vault.unlocked   # no password anywhere: refused, nothing invented
    assert run(ctx, cli, idp, monkeypatch, "unlock", "--admin-password-prompt", ask_password=lambda q: "operator-held-passphrase-one") == 0
    assert idp.vault.unlocked and gateway_of(idp).vault.unlocked


def test_unlock_never_creates_a_vault_and_reports_a_wrong_password(world, monkeypatch, capsys):  # noqa: F811
    ctx, cli, idp = world
    ctx.state.app("identity")["url"] = "https://eco-identity.sandbox.example"          # deployed, but nothing was ever created
    ctx.state.save()
    assert run(ctx, cli, idp, monkeypatch, "unlock", "identity") == 2
    assert "does not exist yet" in capsys.readouterr().err and not idp.vault.initialized
    apply(ctx)
    restart_platform(idp)
    monkeypatch.setenv("ECOCTL_ADMIN_PASSWORD", "a-different-passphrase-1")
    assert run(ctx, cli, idp, monkeypatch, "unlock") == 2
    err = capsys.readouterr().err
    assert "did not open the vault" in err and "a-different-passphrase-1" not in err and not idp.vault.unlocked


def test_unlock_names_apps_with_a_platform_vault_and_unknown_ones(world, monkeypatch, capsys):  # noqa: F811
    ctx, cli, idp = world
    apply(ctx)
    assert run(ctx, cli, idp, monkeypatch, "unlock", "trustdesk") == 0
    assert "trustdesk: `vault: platform`" in capsys.readouterr().out
    assert run(ctx, cli, idp, monkeypatch, "unlock", "ghost") == 1


def test_a_recreate_opens_the_vault_before_anything_waits_for_the_new_sandbox(world, monkeypatch, capsys):  # noqa: F811
    ctx, cli, idp = world
    apply(ctx)
    idp.requests.clear()
    # recreating identity recreates everything: the new identity process starts locked, and the apply that follows must open it before its /readyz wait
    real_delete = cli.__call__

    def delete_and_lock(args):
        if args[1:3] == ["sandbox", "delete"]:
            restart_platform(idp)
        return real_delete(args)
    monkeypatch.setattr(type(cli), "__call__", lambda self, args: delete_and_lock(args))
    assert run(ctx, cli, idp, monkeypatch, "recreate", "identity", "--yes") == 0
    wire = [p for _, p in idp.requests]
    assert wire.index("/api/vault/unlock") < wire.index("/readyz") < wire.index("/api/apps")
    assert idp.vault.unlocked and gateway_of(idp).vault.unlocked and idp.vault.initializations == 1


def test_the_end_of_apply_says_what_a_restart_means(world, monkeypatch, capsys):  # noqa: F811
    ctx, cli, idp = world
    monkeypatch.setattr(cli_mod, "_ctx", lambda a: again(ctx, cli, idp, vault_sleep=lambda s: None))
    assert cli_mod.main(["--state", str(ctx.state.dir), "-f", str(MANIFEST), "apply", "--execute", "--yes"]) == 0
    out = capsys.readouterr().out
    assert "The vault of identity, llmgw is opened with the admin password, which the platform does not store" in out
    assert "ecoctl unlock" in out and "existing tokens keep working" in out


def test_a_failing_vault_step_is_an_error_with_saved_progress_not_a_traceback(world, monkeypatch, capsys):  # noqa: F811
    ctx, cli, idp = world
    monkeypatch.setenv("ECOCTL_ADMIN_PASSWORD", "short")
    monkeypatch.setattr(cli_mod, "_ctx", lambda a: again(ctx, cli, idp, vault_sleep=lambda s: None))
    assert cli_mod.main(["--state", str(ctx.state.dir), "-f", str(MANIFEST), "apply", "--execute", "--yes"]) == 2
    assert "FAILED" in capsys.readouterr().err


# ---- status -----------------------------------------------------------------------------------------------------------------
def test_status_reports_each_vault_and_fails_while_a_password_vault_is_locked(world, monkeypatch, capsys):  # noqa: F811
    ctx, cli, idp = world
    apply(ctx)
    monkeypatch.setattr(cli_mod, "_ctx", lambda a: again(ctx, cli, idp))
    monkeypatch.setattr(cli_mod.httpx, "get", fake_get(idp))
    monkeypatch.setattr(cli_mod, "Identity", lambda url, key: type("I", (), {"apps": lambda self: {k: {**a, "client_id": k} for k, a in idp.apps.items()}})())
    assert cli_mod.main(["--state", str(ctx.state.dir), "-f", str(MANIFEST), "status"]) == 0
    rows = {}
    for ln in capsys.readouterr().out.splitlines():
        if ln.split() and ln.split()[0] in {"identity", "llmgw", "trustdesk"}:
            rows.setdefault(ln.split()[0], ln.split())
    assert rows["identity"][3] == "unlocked" and rows["llmgw"][3] == "unlocked" and rows["trustdesk"][3] == "auto"
    restart_platform(idp)
    assert cli_mod.main(["--state", str(ctx.state.dir), "-f", str(MANIFEST), "status"]) == 1
    out = capsys.readouterr().out
    assert next(ln for ln in out.splitlines() if ln.startswith("identity")).split()[3] == "LOCKED" and "ecoctl unlock" in out


def test_describe_says_what_each_state_means():
    open_, shut, fresh = VaultState(True, True), VaultState(True, False, reason="no_platform_key"), VaultState(False, False, reason="not_initialized")
    assert describe("password", open_) == ("unlocked", True) and describe("password", shut) == ("LOCKED", False)
    assert describe("password", fresh) == ("not initialised", False) and describe("password", None) == ("?", False)
    assert describe("platform", VaultState(True, True, True)) == ("auto", True) and describe("platform", None) == ("?", True)
    assert describe("platform", VaultState(True, False, True)) == ("LOCKED", True)     # a platform vault retries by itself: not a failure


def test_connect_refuses_to_start_while_identity_is_locked(world, monkeypatch, capsys):  # noqa: F811
    ctx, cli, idp = world
    apply(ctx)
    restart_platform(idp)
    assert run(ctx, cli, idp, monkeypatch, "connect") == 2
    assert "ecoctl unlock" in capsys.readouterr().err


# ---- apps with `vault: platform` still work the way they did ------------------------------------------------------------------
def test_a_manifest_with_platform_vaults_behaves_as_before(tmp_path):
    from ecoctl.agentpg import AgentPG
    from tests.fakes import FakeAgentPG, FakeGateway, FakeIdentity
    d = doc()
    del d["identity"]["vault"], d["apps"]["llmgw"]["vault"]
    d["identity"]["generate"] = ["IDENTITY_ENCRYPTION_KEY"]
    d["apps"]["llmgw"]["generate"] = ["LLMGW_ENCRYPTION_KEY"]
    cli, idp, gw = FakeAgentPG(), FakeIdentity(), FakeGateway()
    idp.vault.platform = gw.vault.platform = True                                       # the platform key opens them by itself
    state = State(tmp_path / "eco")
    ctx = Context(parse(d), state, AgentPG(run=cli))
    ctx.http, ctx.llm_http, idp.gateway = idp.client(state.admin_key()), gw.client(state.secret("llmgw", "LLMGW_API_KEY")), gw
    ctx.connect_http = idp.connect.client()
    idp.vault.initialized = idp.vault.unlocked = True
    titles = [s.title for s in build(ctx)]
    assert not [t for t in titles if "vault" in t]
    apply(ctx)
    assert cli.sandboxes["eco-identity"]["env"]["IDENTITY_ENCRYPTION_KEY"] == state.secret("identity", "IDENTITY_ENCRYPTION_KEY")
    assert cli.sandboxes["eco-llmgw"]["env"]["LLMGW_ENCRYPTION_KEY"] == state.secret("llmgw", "LLMGW_ENCRYPTION_KEY")
    assert "/api/admin-password" not in [p for _, p in idp.requests] and State(state.dir).vault_admin_password() is None


def test_ecoctl_with_only_platform_apps_has_nothing_to_unlock(tmp_path, monkeypatch, capsys):
    d = doc()
    del d["identity"]["vault"], d["apps"]["llmgw"]["vault"]
    path = tmp_path / "m.yaml"
    path.write_text(yaml.safe_dump(d))
    assert cli_mod.main(["--state", str(tmp_path / "s"), "-f", str(path), "unlock"]) == 0
    assert "no app with `vault: password`" in capsys.readouterr().out


# ---- the client itself --------------------------------------------------------------------------------------------------------
def test_the_client_waits_for_an_app_that_is_still_starting_then_gives_up_with_a_clear_message():
    class Clock:
        now = 0.0

        def sleep(self, s):
            self.now += s

        def __call__(self):
            return self.now

    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(503, json={"detail": "waking"}) if len(calls) < 3 else httpx.Response(200, json={"vault": {"initialized": False, "unlocked": False}})

    c = Clock()
    admin = VaultAdmin("https://x", "k", httpx.Client(transport=httpx.MockTransport(handler)), c.sleep, c)
    assert admin.wait_state() == VaultState(False, False) and len(calls) == 3
    never = VaultAdmin("https://x", "k", httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(503))), c.sleep, c)
    with pytest.raises(VaultError, match="did not answer /api/session within 180 s"):
        never.wait_state()
    old = VaultAdmin("https://x", "k", httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"state": "logged_out"}))), c.sleep, c)
    with pytest.raises(VaultError, match="no admin-password vault"):
        old.wait_state()


def test_the_operator_key_goes_only_in_the_authorization_header_and_the_status_call_sends_none():
    seen = []

    def handler(request):
        seen.append((request.url.path, request.headers.get("authorization"), request.content))
        return httpx.Response(200, json={"vault": {"initialized": True, "unlocked": False}, "ok": True})

    admin = VaultAdmin("https://x", "OPERATOR", httpx.Client(transport=httpx.MockTransport(handler)))
    admin.state()
    admin.unlock("pw-pw-pw-pw-pw", "identity", "test")
    assert seen[0][:2] == ("/api/session", None)
    assert seen[1][1] == "Bearer OPERATOR" and json.loads(seen[1][2]) == {"password": "pw-pw-pw-pw-pw"}


def test_operator_password_order_is_environment_then_prompt_then_state(monkeypatch, tmp_path):
    st = State(tmp_path / "s")
    st.keep_vault_admin_password("from-state")
    ctx = Context(load(MANIFEST), st, None, prompt_password=True, ask_password=lambda q: "from-prompt")
    fresh, made = VaultState(False, False), VaultState(True, False)
    assert operator_password(ctx, "identity", made, allow_generate=False) == ("from-prompt", "prompt")
    ctx.prompt_password = False
    assert operator_password(ctx, "identity", made, allow_generate=False) == ("from-state", "the state file")
    monkeypatch.setenv("ECOCTL_ADMIN_PASSWORD", "from-env")
    assert operator_password(ctx, "identity", made, allow_generate=False) == ("from-env", "ECOCTL_ADMIN_PASSWORD")
    monkeypatch.delenv("ECOCTL_ADMIN_PASSWORD")
    st.data["vault"] = {}
    with pytest.raises(VaultError, match="does not exist yet"):
        operator_password(ctx, "identity", fresh, allow_generate=False)
    with pytest.raises(VaultError, match="does not have"):
        operator_password(ctx, "identity", made, allow_generate=True)


def test_the_state_file_stays_private_when_it_holds_the_password(tmp_path):
    st = State(tmp_path / "s")
    st.keep_vault_admin_password("x" * 32)
    st.save()
    assert stat.S_IMODE(os.stat(st.path).st_mode) == 0o600 and stat.S_IMODE(os.stat(st.dir).st_mode) == 0o700


@pytest.mark.parametrize("command", ["apply", "recreate", "unlock"])
def test_the_commands_offer_the_prompt_flag(command, capsys):
    with pytest.raises(SystemExit) as e:
        cli_mod.main([command, "--help"])
    assert e.value.code == 0 and "--admin-password-prompt" in capsys.readouterr().out
