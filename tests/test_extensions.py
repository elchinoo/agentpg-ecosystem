"""The shipped manifest's extensions, and `apply` adding missing ones to an existing instance idempotently. Fakes only."""
import pytest

from ecoctl.agentpg import AgentPGError
from ecoctl.manifest import load
from ecoctl.plan import build, installed_extensions, missing_extensions
from ecoctl.state import State
from tests.test_plan import MANIFEST, again, apply, world  # noqa: F401  (the fixture)

BASE = {"query_advisor", "edb_stat_monitor"}
EXPECTED = {
    "identity": BASE, "llmgw": BASE, "cpsim": BASE,
    "trustdesk": BASE | {"vector", "pg_trgm", "unaccent", "tcn"},
    "pagerlens": BASE | {"vector", "pg_trgm", "tcn"},
    "citypulse": BASE | {"postgis"},
    "paybroker": BASE | {"vector", "fuzzystrmatch"},
    "shopfront": BASE | {"vector", "pg_trgm", "unaccent", "fuzzystrmatch"},
}


def test_the_shipped_manifest_lists_the_edb_extensions_per_app():
    m = load(MANIFEST)
    got = {k: set(a.extensions) for k, a in m.everything().items() if a.deploy}
    assert got == EXPECTED


def test_apply_creates_every_listed_extension_on_a_new_instance(world):  # noqa: F811
    ctx, cli, idp = world
    apply(ctx)
    ids = {n.removeprefix("eco-").removesuffix("-db"): i for n, i in cli.instances.items()}
    for key, want in EXPECTED.items():
        assert {e for i, e in cli.extensions if i == ids[key]} == want, key
    assert sorted(ctx.state.app("citypulse")["extensions"]) == sorted(EXPECTED["citypulse"])


def test_an_existing_instance_gets_only_the_missing_extensions(world):  # noqa: F811
    ctx, cli, idp = world
    apply(ctx)
    state = State(ctx.state.dir)
    state.app("shopfront")["extensions"] = ["vector", "pg_trgm"]      # as if created by an older manifest
    state.save()
    cli.extensions.clear()
    ctx2 = again(ctx, cli, idp, state=State(ctx.state.dir))
    plan_text = "\n".join(d for s in build(ctx2) for d in s.detail if "extensions create" in d)
    assert "extensions create unaccent" in plan_text
    apply(ctx2)
    sf = cli.instances["eco-shopfront-db"]
    assert {e for i, e in cli.extensions} == {"query_advisor", "edb_stat_monitor", "unaccent", "fuzzystrmatch"} and {i for i, _ in cli.extensions} == {sf}
    calls = len(cli.calls)
    apply(again(ctx, cli, idp))
    assert [c for c in cli.calls[calls:] if c[1] == "extensions"] == []                           # a third run creates nothing


def test_the_plan_of_an_existing_instance_shows_only_what_is_missing(world):  # noqa: F811
    ctx, cli, idp = world
    apply(ctx)
    state = State(ctx.state.dir)
    state.app("citypulse")["extensions"] = ["query_advisor"]
    state.save()
    ctx2 = again(ctx, cli, idp, state=State(ctx.state.dir))
    step = next(s for s in build(ctx2) if s.title == "CityPulse: database and sandbox")
    lines = [d for d in step.detail if "extensions create" in d]
    assert sorted(ln.split()[3] for ln in lines) == ["edb_stat_monitor", "postgis"]
    assert any("wait until it is running" in d for d in step.detail)
    paybroker = next(s for s in build(ctx2) if s.title == "PayBroker: database and sandbox")
    assert any("already created" in d for d in paybroker.detail)


def test_waits_for_running_and_keeps_progress_when_one_extension_fails(world):  # noqa: F811
    ctx, cli, idp = world
    apply(ctx)
    state = State(ctx.state.dir)
    state.app("citypulse")["extensions"] = []
    state.save()
    cli.calls.clear()
    cli.fail_on = "extensions create"
    ctx2 = again(ctx, cli, idp, state=State(ctx.state.dir))
    step = next(s for s in build(ctx2) if s.title == "CityPulse: database and sandbox")
    with pytest.raises(RuntimeError):
        step.run(ctx2)
    assert cli.calls[0][1:3] == ["instances", "list"]                                      # waited for running before the first create
    assert ctx2.state.app("citypulse").get("extensions", []) == []


def test_an_already_installed_extension_counts_as_done(world):  # noqa: F811
    ctx, cli, idp = world
    apply(ctx)
    state = State(ctx.state.dir)
    state.app("cpsim")["extensions"] = []
    state.save()
    orig = cli.__call__

    def refusing(args):
        if args[1:3] == ["extensions", "create"] and args[3] == "query_advisor":
            raise AgentPGError("extension \"query_advisor\" already exists")
        return orig(args)
    ctx2 = again(ctx, cli, idp, state=State(ctx.state.dir))
    ctx2.agentpg._run = refusing
    next(s for s in build(ctx2) if s.title == "CityPulse Simulator: database and sandbox").run(ctx2)
    assert set(ctx2.state.app("cpsim")["extensions"]) == BASE


def test_a_state_from_before_the_list_is_read_as_vector_and_pg_trgm(world):  # noqa: F811
    ctx, cli, idp = world
    m = load(MANIFEST)
    st = State(ctx.state.dir / "legacy")
    st.app("trustdesk")["extensions_done"] = True
    assert installed_extensions(st, m.apps["trustdesk"]) == ["vector", "pg_trgm"]
    assert missing_extensions(st, m.apps["trustdesk"]) == ["query_advisor", "edb_stat_monitor", "unaccent", "tcn"]
