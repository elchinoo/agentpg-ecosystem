"""AIDB mode as a manifest setting: `apps.<app>.aidb: true | {retrieval: true, agent: true}` (default off).

What it does: the app's environment gets the app's own switches (`<PREFIX>_RETRIEVAL_BACKEND=aidb`, and for the agent features the app's triage and analyst switches), and `aidb-setup`
(the app's operator command, which needs a privileged database connection) is an explicit step. Only where the platform can run it: on AgentPG the instance's role has no AIDB grants, so
the fleet path never sets the switches and the step reports "not available" without failing the plan (`agentpg.aidb` is not a setting: there is nothing to switch on yet). The Docker
stack (`ecoctl dev`) is the place that runs it. `ecoctl aidb status` reads each opted-in app's /readyz.
"""
import httpx

SUPPORTED = ("trustdesk", "pagerlens", "paybroker", "citypulse")          # the apps whose repository has an AIDB mode and an `aidb-setup` command
AGENT_SWITCHES = {"paybroker": {"PAYBROKER_TRIAGE_METHOD": "aidb_agent", "PAYBROKER_ANALYST_QA": "aidb"}}      # features of the agent that have their own switch
NOT_AVAILABLE_ON_AGENTPG = ("not available on AgentPG: an instance's login role has no AIDB grants (`aidb_users`, `aidb_governance`) and `agentpg db-users` offers no role that does, "
                            "so the app stays on its own search; the switches are not set. The Docker stack (`ecoctl dev`) runs this step.")


def switches(app_key: str, env_prefix: str, retrieval: bool, agent: bool) -> dict[str, str]:
    out: dict[str, str] = {}
    if retrieval:
        out[f"{env_prefix}_RETRIEVAL_BACKEND"] = "aidb"
    if agent:
        out.update(AGENT_SWITCHES.get(app_key, {}))
    return out


def state_of(body: dict) -> str:
    """The AIDB state word of an app's /readyz body: `state` (PayBroker, PagerLens, CityPulse), or for TrustDesk (booleans and a one-word `problem`) `ready` when retrieval is
    in use and nothing is wrong. `missing` when /readyz has no AIDB part at all."""
    a = body.get("aidb")
    if isinstance(a, str):
        return a
    if not isinstance(a, dict):
        return "missing"
    if a.get("state"):
        return str(a["state"])
    if "retrieval" in a:
        return a.get("problem") or ("ready" if a["retrieval"] else "not-ready")
    return "enabled" if a.get("enabled") else "off"


def summarize(body: dict) -> tuple[str, str]:
    """(state, one line of detail: reason, tampered definitions, features) from an app's /readyz body."""
    a, state = body.get("aidb"), state_of(body)
    if not isinstance(a, dict):
        return state, ""
    bits = []
    if a.get("reason"):
        bits.append(str(a["reason"]))
    if a.get("tampered"):
        bits.append(f"TAMPERED: {', '.join(map(str, a['tampered']))}")
    if isinstance(a.get("features"), dict) and a["features"]:
        bits.append("features " + ", ".join(f"{k}={'on' if (v.get('ready') if isinstance(v, dict) else v) else 'off'}" for k, v in a["features"].items()))
    for k in ("retrieval", "agent", "verified"):
        if k in a and not isinstance(a[k], dict):
            bits.append(f"{k}={a[k]}")
    return state, "; ".join(bits)


def status(ctx, http: httpx.Client | None = None) -> list[tuple[str, str, str]]:
    """(app, state, detail) for each app that opted in. `unreachable` when /readyz does not answer; `no url` when the app was never deployed."""
    out = []
    for key, app in ctx.manifest.apps.items():
        if not app.aidb:
            continue
        url = ctx.state.app(key).get("url")
        if not url:
            out.append((key, "no url", "not deployed yet"))
            continue
        try:
            body = (http or httpx).get(url.rstrip("/") + "/readyz", timeout=15).json()
        except (httpx.HTTPError, ValueError):
            out.append((key, "unreachable", url))
            continue
        out.append((key, *summarize(body)))
    return out
