"""`ecoctl insights`: what AgentPG's query advisor recommends for each app's database (`agentpg recommend all <instance> --json`).

Read-only. The CLI's answer is parsed tolerantly: JSON of any reasonable shape (a list, or an object holding lists), or the plain-text
lines it prints without --json, e.g. `CREATE INDEX ON t USING btree (c); Size: 8 KB | Cost reduction: 36.3%`.
"""
import re
from dataclasses import asdict, dataclass

from .agentpg import AgentPG, AgentPGError, parse_json

STATEMENT = re.compile(r"\b(CREATE\s+(?:UNIQUE\s+)?INDEX|CREATE\s+STATISTICS|ANALYZE|ALTER\s+TABLE|VACUUM)\b[^;|]*;?", re.I)
SIZE = re.compile(r"Size:\s*([^|]+?)\s*(?:\||$)", re.I)
COST = re.compile(r"Cost\s+reduction:\s*([-\d.]+)\s*%", re.I)
STATEMENT_KEYS = ("statement", "sql", "ddl", "recommendation", "command", "query", "action", "description")
SIZE_KEYS = ("size", "index_size", "estimated_size")
COST_KEYS = ("cost_reduction", "cost_reduction_percent", "cost_reduction_pct", "improvement", "benefit")


@dataclass(frozen=True)
class Recommendation:
    kind: str                     # index | stats | other
    statement: str
    size: str = ""
    cost_reduction: str = ""      # percent, as text


def _kind(statement: str) -> str:
    up = statement.upper()
    if "CREATE INDEX" in up or "CREATE UNIQUE INDEX" in up:
        return "index"
    if "STATISTICS" in up or up.startswith("ANALYZE"):
        return "stats"
    return "other"


def _from_text(text: str) -> list[Recommendation]:
    out = []
    for line in text.splitlines():
        m = STATEMENT.search(line)
        if not m:
            continue
        size, cost = SIZE.search(line), COST.search(line)
        stmt = m.group(0).strip()
        out.append(Recommendation(_kind(stmt), stmt if stmt.endswith(";") else stmt + ";", size.group(1).strip() if size else "", cost.group(1) if cost else ""))
    return out


def _first(d: dict, keys) -> object:
    return next((d[k] for k in keys if d.get(k) not in (None, "")), "")


def _walk(node, found: list[Recommendation]) -> None:
    if isinstance(node, str):
        found.extend(_from_text(node))
    elif isinstance(node, list):
        for item in node:
            _walk(item, found)
    elif isinstance(node, dict):
        stmt = _first(node, STATEMENT_KEYS)
        if isinstance(stmt, str) and STATEMENT.search(stmt):
            line = stmt
            size, cost = _first(node, SIZE_KEYS), _first(node, COST_KEYS)
            rec = _from_text(line)
            base = rec[0] if rec else Recommendation(_kind(stmt), stmt)
            cost_text = str(cost).rstrip("%") if cost != "" else base.cost_reduction
            found.append(Recommendation(base.kind, base.statement, str(size) if size != "" else base.size, cost_text))
        else:
            for v in node.values():
                _walk(v, found)


def parse_recommendations(output: str) -> list[Recommendation]:
    """Recommendations from the CLI's answer, in order, without duplicates. An answer with none yields an empty list."""
    data = parse_json(output)
    found: list[Recommendation] = []
    if data is not None:
        _walk(data, found)
    if not found:
        found = _from_text(output)
    seen, unique = set(), []
    for r in found:
        if r.statement not in seen:
            seen.add(r.statement)
            unique.append(r)
    return unique


ADVISOR_HINT = re.compile(r"query_advisor|advisor|extension", re.I)


def advisor_missing_message(instance_id: str) -> str:
    return f"query_advisor is not installed on this database: add it to the app's `extensions:` and run `ecoctl apply --execute`, or run `agentpg extensions create query_advisor {instance_id}`"


@dataclass
class Result:
    app: str
    instance: str
    recommendations: list[Recommendation]
    error: str = ""
    hint: str = ""

    def as_dict(self) -> dict:
        return {"app": self.app, "instance": self.instance, "recommendations": [asdict(r) for r in self.recommendations], "error": self.error, "hint": self.hint}


def collect(ctx, apps: list[str]) -> list[Result]:
    """One `agentpg recommend all` per app instance. An app without an instance in the state is reported, not an error of the command."""
    results = []
    for key in apps:
        iid = ctx.state.app(key).get("instance_id")
        if not iid:
            results.append(Result(key, "", [], error="no instance yet (run `ecoctl apply --execute`)"))
            continue
        try:
            out = ctx.agentpg.run(AgentPG.recommend(iid))
        except AgentPGError as exc:
            msg = str(exc)
            results.append(Result(key, iid, [], error=msg, hint=advisor_missing_message(iid) if ADVISOR_HINT.search(msg) else ""))
            continue
        results.append(Result(key, iid, parse_recommendations(out)))
    return results


def render(results: list[Result]) -> str:
    lines = []
    for r in results:
        lines.append(f"{r.app} ({r.instance or 'no instance'})")
        if r.error:
            lines.append(f"  cannot read recommendations: {r.error}")
            if r.hint:
                lines.append(f"  {r.hint}")
        elif not r.recommendations:
            lines.append("  no recommendations (the advisor learns from query traffic: use the app, then ask again)")
        else:
            lines.append(f"  {'kind':<7}{'size':<12}{'cost reduction':<16}statement")
            for rec in r.recommendations:
                lines.append(f"  {rec.kind:<7}{rec.size or '-':<12}{(rec.cost_reduction + '%') if rec.cost_reduction else '-':<16}{rec.statement}")
        lines.append("")
    lines.append("Read-only: nothing was changed. Review a statement before running it; `agentpg recommend` only suggests.")
    return "\n".join(lines)
