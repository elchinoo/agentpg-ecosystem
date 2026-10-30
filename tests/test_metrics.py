import argparse
import json

from ecoctl import cli as cli_mod
from ecoctl.agentpg import AgentPG, AgentPGError
from tests.test_llm import MANIFEST, deployed, world  # noqa: F401


def series(unit, *points):
    return {"resolution": 0, "unit": unit, "series": [{"container": "container-0", "points": [{"ts": t, "value": v} for t, v in points]}]}


FULL = {
    "cpu": series("pct", ("2026-10-28T10:00:00Z", 0.2), ("2026-10-28T10:01:00Z", 0.51)),
    "memory": series("pct", ("2026-10-28T10:01:00Z", 12.6)),
    "networkIngress": series("kbps", ("2026-10-28T10:01:00Z", 1)),
    "networkEgress": series("Bps", ("2026-10-28T10:01:00Z", 2048.0)),
}


def api(text=None, exc=None):
    def run(args):
        if exc:
            raise exc
        return text
    return AgentPG(run=run)


def test_latest_point_of_each_metric_with_the_units_the_platform_reports():
    out = api(json.dumps({"metrics": FULL})).sandbox_metrics("sbx-1")
    assert out == {"cpu": "0.5%", "memory": "12.6%", "network_in": "1 kbps", "network_out": "2048 Bps"}


def test_missing_metrics_empty_series_paused_and_odd_output_are_tolerated():
    assert api(json.dumps({"metrics": {"cpu": series("pct", ("t", 3))}})).sandbox_metrics("s") == {"cpu": "3%"}
    assert api(json.dumps({"metrics": {"cpu": {"unit": "pct", "series": []}, "memory": {"series": [{"points": []}]}}})).sandbox_metrics("s") == {}
    assert api(json.dumps({"status": "paused"})).sandbox_metrics("s") == {}
    assert api(json.dumps({"metrics": None})).sandbox_metrics("s") == {}
    assert api("not json").sandbox_metrics("s") == {}
    assert api(json.dumps({"metrics": {"cpu": {"series": [{"points": [{"value": "x"}, None]}]}}})).sandbox_metrics("s") == {}


def test_an_old_cli_or_a_failing_call_gives_empty():
    assert api(exc=RuntimeError("boom")).sandbox_metrics("s") == {}
    assert api(exc=AgentPGError("unknown flag: --json")).sandbox_metrics("s") == {}


def _status(ctx, capsys):
    code = cli_mod.cmd_status(argparse.Namespace(file=str(MANIFEST), state=str(ctx.state.dir)))
    return code, capsys.readouterr().out


def test_status_shows_metric_columns_after_ready_and_dashes_when_unavailable(deployed, capsys):  # noqa: F811
    ctx, cli, idp = deployed
    cli.metrics["eco-llmgw"] = FULL
    code, out = _status(ctx, capsys)
    assert code == 0
    lines = out.splitlines()
    head = lines[0].split()
    assert head[:7] == ["app", "health", "ready", "vault", "cpu", "mem", "net"]
    row = next(ln for ln in lines if ln.startswith("llmgw"))
    assert row.split()[1:9] == ["200", "200", "unlocked", "0.5%", "12.6%", "1", "kbps", "/"]
    other = next(ln for ln in lines if ln.startswith("identity"))
    assert other.split()[1:7] == ["200", "200", "unlocked", "-", "-", "-"]


def test_a_failing_metrics_call_does_not_break_status(deployed, capsys):  # noqa: F811
    ctx, cli, idp = deployed
    cli.metrics_fail = True
    code, out = _status(ctx, capsys)
    assert code == 0
    assert next(ln for ln in out.splitlines() if ln.startswith("identity")).split()[4:7] == ["-", "-", "-"]
