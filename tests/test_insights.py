"""`ecoctl insights`: parsing what `agentpg recommend all --json` prints, and the command. Fakes only."""
import argparse
import json

import pytest

from ecoctl import cli as cli_mod
from ecoctl.insights import advisor_missing_message, parse_recommendations
from tests.test_plan import apply, world  # noqa: F401  (the fixture)

LINE = "CREATE INDEX ON orders USING btree (customer_id); Size: 8 KB | Cost reduction: 36.3%"


def test_plain_text_lines_are_parsed():
    recs = parse_recommendations(f"Index recommendations\n{LINE}\nCREATE STATISTICS s ON a, b FROM t; | Cost reduction: 4%\nnothing else\n")
    assert [(r.kind, r.size, r.cost_reduction) for r in recs] == [("index", "8 KB", "36.3"), ("stats", "", "4")]
    assert recs[0].statement == "CREATE INDEX ON orders USING btree (customer_id);"


@pytest.mark.parametrize("payload", [
    [{"statement": "CREATE INDEX ON orders USING btree (customer_id);", "size": "8 KB", "cost_reduction": 36.3}],
    {"recommendations": {"index": [{"sql": "CREATE INDEX ON orders USING btree (customer_id);", "size": "8 KB", "cost_reduction_percent": "36.3%"}]}},
    {"index": [LINE]},
    {"output": LINE},
])
def test_json_of_several_shapes_is_parsed(payload):
    recs = parse_recommendations(json.dumps(payload))
    assert len(recs) == 1 and recs[0].kind == "index" and recs[0].size == "8 KB" and recs[0].cost_reduction == "36.3"


def test_duplicates_and_empty_answers():
    assert len(parse_recommendations(f"{LINE}\n{LINE}")) == 1
    assert parse_recommendations("{}") == [] and parse_recommendations("No recommendations found.") == [] and parse_recommendations("") == []


def run(monkeypatch, world, capsys, *apps, **kw):  # noqa: F811
    ctx, cli, idp = world
    monkeypatch.setattr(cli_mod, "_ctx", lambda a: ctx)
    rc = cli_mod.cmd_insights(argparse.Namespace(apps=list(apps), all=kw.get("all", False), json=kw.get("json", False)))
    return rc, capsys.readouterr()


def test_the_command_shows_a_table_per_app_and_the_install_hint(monkeypatch, world, capsys):  # noqa: F811
    ctx, cli, idp = world
    apply(ctx)
    capsys.readouterr()
    cli.recommendations[ctx.state.app("shopfront")["instance_id"]] = LINE + "\n"
    cli.recommendations[ctx.state.app("trustdesk")["instance_id"]] = "No recommendations found.\n"
    cli.calls.clear()
    rc, out = run(monkeypatch, world, capsys, "shopfront", "trustdesk", "paybroker")
    assert rc == 1                                                          # paybroker's instance has no advisor in the fake
    assert "36.3%" in out.out and "CREATE INDEX ON orders" in out.out and "no recommendations" in out.out
    assert advisor_missing_message(ctx.state.app("paybroker")["instance_id"]) in out.out and "agentpg extensions create query_advisor" in out.out
    assert all(c[1] == "recommend" for c in cli.calls)                      # read-only: nothing but `recommend all`


def test_json_output_and_default_to_every_deployed_app(monkeypatch, world, capsys):  # noqa: F811
    ctx, cli, idp = world
    apply(ctx)
    capsys.readouterr()
    for k in ("identity", "llmgw", "pagerlens", "citypulse", "paybroker", "shopfront", "trustdesk", "cpsim"):
        cli.recommendations[ctx.state.app(k)["instance_id"]] = json.dumps({"recommendations": [{"statement": LINE.split(";")[0] + ";", "size": "8 KB"}]})
    rc, out = run(monkeypatch, world, capsys, json=True)
    data = json.loads(out.out)
    assert rc == 0 and len(data) == 8 and data[0]["recommendations"][0]["kind"] == "index" and not any(d["error"] for d in data)


def test_unknown_app_and_no_database(monkeypatch, world, capsys):  # noqa: F811
    rc, out = run(monkeypatch, world, capsys, "ghost")
    assert rc == 1 and "no such app" in out.err
    rc, out = run(monkeypatch, world, capsys)
    assert rc == 1 and "no app with a database" in out.err
