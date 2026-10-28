"""Compare each LLM consumer's gateway client with what the manifest says (limits and aliases), and optionally bring it in line.

Pure functions over the client records the gateway returns; the CLI does the I/O. Keys are never involved.
"""
from dataclasses import dataclass, field

from .manifest import Manifest

MISSING = "missing, run apply"


@dataclass
class Row:
    app: str
    field: str
    gateway: str
    manifest: str
    action: str                               # what sync does with it: set, add, remove, "extra, kept", or MISSING


@dataclass
class AppDiff:
    app: str
    rows: list[Row] = field(default_factory=list)
    patch: dict = field(default_factory=dict)  # the fields `--apply` would PATCH (empty when only extras are kept or the client is missing)
    client_id: int | None = None
    missing: bool = False

    @property
    def drift(self) -> bool:
        """Extra aliases that `sync` keeps are informational (an operator's harmless addition); anything else is a real difference."""
        return any(r.action != "extra, kept" for r in self.rows)


def _budget(v) -> str:
    return "none" if v is None else f"{v:,}"


def diff_app(m: Manifest, app: str, client: dict | None, prune: bool = False) -> AppDiff:
    lim = m.limits(app)
    d = AppDiff(app)
    if client is None:
        d.missing = True
        d.rows.append(Row(app, "client", "-", "expected", MISSING))
        return d
    d.client_id = client["id"]
    for fld, api, have, want, show in (("rpm", "rpm_limit", client.get("rpm_limit"), lim.rpm, str),
                                       ("budget (tokens/month)", "monthly_token_budget", client.get("monthly_token_budget"), lim.budget, _budget),
                                       ("budget_mode", "budget_mode", client.get("budget_mode"), lim.budget_mode, str)):
        if have != want:
            d.rows.append(Row(app, fld, show(have), show(want), "set"))
            d.patch[api] = want
    have_aliases = list(client.get("allowed_aliases") or [])
    missing = [a for a in lim.aliases if a not in have_aliases]
    extra = [a for a in have_aliases if a not in lim.aliases]
    if missing:
        d.rows.append(Row(app, "aliases", ", ".join(have_aliases) or "none", ", ".join(lim.aliases), f"add {', '.join(missing)}"))
    if extra:
        d.rows.append(Row(app, "aliases", ", ".join(extra), "(not listed)", "remove (--prune-aliases)" if prune else "extra, kept"))
    if missing or (extra and prune):
        d.patch["allowed_aliases"] = [a for a in have_aliases if a not in extra or not prune] + missing
    return d


def table(rows: list[Row]) -> str:
    head = ("app", "field", "gateway", "manifest", "action")
    cells = [head] + [(r.app, r.field, r.gateway, r.manifest, r.action) for r in rows]
    w = [max(len(c[i]) for c in cells) for i in range(5)]
    return "\n".join("  ".join(c[i].ljust(w[i]) for i in range(5)).rstrip() for c in cells)
