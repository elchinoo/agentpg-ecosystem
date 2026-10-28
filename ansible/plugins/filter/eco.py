"""Filters that read ecosystem.yaml for the playbooks. Pure Python, no dependency on ecoctl: a customer installs Ansible, not ecoctl.

They reproduce, and only reproduce, the parts of ecoctl that are logic rather than configuration: defaults (env prefix, port, image,
health path), creation order (callees before callers), the URL variable of each callee, the identity specs of an app and the gateway
limits. tests/test_ansible_recipe.py compares every result with ecoctl.manifest and ecoctl.plan, so the two cannot drift apart.
"""
from __future__ import annotations

import hashlib

PENDING_REDIRECT = "https://pending.invalid/auth/callback"       # same placeholder as ecoctl: replaced once the real URL is known


def _app(key: str, raw: dict, identity: bool = False) -> dict:
    raw = raw or {}
    prefix = raw.get("env_prefix", key.upper().replace("-", "_"))
    llm = raw.get("llm", False)
    calls = {c: list(dict.fromkeys(s)) for c, s in (raw.get("calls") or {}).items()}
    return {
        "key": key, "name": raw.get("name", key), "description": str(raw.get("description", ""))[:300],
        "image": raw.get("image", key), "port": raw.get("port", 8000), "extensions": list(raw.get("extensions") or []),
        "env": {k: str(v) for k, v in (raw.get("env") or {}).items()}, "generate": list(raw.get("generate") or []),
        "health": raw.get("health", "/healthz"),
        "roles": [{"name": r["name"], "description": str(r.get("description", ""))[:200]} for r in raw.get("roles") or []],
        "default_roles": list(raw.get("default_roles") or []), "env_prefix": prefix, "exposes": list(raw.get("exposes") or []),
        "calls": calls, "uses": list(raw.get("uses") or []), "url_env": dict(raw.get("url_env") or {}),
        "web": raw.get("web", not identity), "deploy": raw.get("deploy", True), "auto_pause": raw.get("auto_pause"), "plan": raw.get("plan"),
        "llm": bool(llm), "llm_override": llm if isinstance(llm, dict) else {},
        "connect": {p: {"merchant": str(s["merchant"]).strip()} for p, s in (raw.get("connect") or {}).items()},
        "service": bool(calls), "service_client_id": f"{key}-service",
    }


def _needs(fleet: dict, key: str) -> list[str]:
    a = fleet["apps"][key]
    out = set(a["calls"]) | (set(a["uses"]) - {"identity"})
    if a["llm"]:
        out.add(fleet["llm"]["gateway"])
    return sorted(out)


def _order(fleet: dict) -> list[str]:
    done: list[str] = []
    visiting: set[str] = set()

    def visit(key: str) -> None:
        if key in done:
            return
        if key in visiting:
            raise ValueError(f"apps call each other in a cycle through {key!r}")
        visiting.add(key)
        for callee in _needs(fleet, key):
            visit(callee)
        visiting.discard(key)
        done.append(key)

    for key in sorted(fleet["apps"]):
        visit(key)
    return done


def eco_fleet(doc: dict, only=None) -> dict:
    """ecosystem.yaml (as loaded) -> the fleet with every default filled in. `only`: deploy just these apps and what they need."""
    g = doc.get("llm") or {}
    llm = {"gateway": g.get("gateway", "llmgw"), "chat_alias": g.get("chat_alias", "chat-default"), "embed_alias": g.get("embed_alias", "embed-default"),
           "embed_dimensions": g.get("embed_dimensions", 768), "rpm": g.get("rpm", 60), "budget": g.get("budget", 5_000_000),
           "budget_mode": g.get("budget_mode", "hard")}
    reg = doc.get("agentpg") or {}
    fleet = {"agentpg": {"size": str(reg.get("size", "small")), "auto_pause": reg.get("auto_pause", True), "plan": reg.get("plan")}, "fleet": doc["fleet"], "registry": str(reg.get("registry", "ghcr.io/elchinoo")).rstrip("/"), "tag": reg.get("tag", "latest"), "llm": llm,
             "identity": _app("identity", doc.get("identity"), identity=True), "apps": {k: _app(k, v) for k, v in doc["apps"].items()}}
    fleet["order"] = _order(fleet)
    chosen: set[str] = set()

    def add(k: str) -> None:
        if k in chosen:
            return
        if k not in fleet["apps"]:
            raise ValueError(f"{k!r} is not an app in the manifest")
        chosen.add(k)
        for c in _needs(fleet, k):
            add(c)

    for k in (only or fleet["apps"]):
        add(k)
    fleet["selected"] = [k for k in fleet["order"] if k in chosen]
    fleet["deployed"] = [k for k in fleet["selected"] if fleet["apps"][k]["deploy"]]
    fleet["consumers"] = sorted(k for k in fleet["deployed"] if fleet["apps"][k]["llm"])
    for a in [fleet["identity"], *fleet["apps"].values()]:
        a["image_ref"] = f"{fleet['registry']}/{a['image']}:{fleet['tag']}"
        a["sandbox_name"], a["instance_name"] = f"{fleet['fleet']}-{a['key']}", f"{fleet['fleet']}-{a['key']}-db"        # as ecoctl: eco-<app> and eco-<app>-db
        a["pause"] = fleet["agentpg"]["auto_pause"] if a["auto_pause"] is None else a["auto_pause"]
        a["plan_eff"] = a["plan"] or fleet["agentpg"]["plan"]
        a["url_vars"] = {c: a["url_env"].get(c, f"{({'identity': fleet['identity'], **fleet['apps']})[c]['env_prefix']}_URL") for c in (*a["calls"], *a["uses"])}
        if a["llm"]:
            o = a["llm_override"]
            extras = [x for x in o.get("aliases", []) if x not in (llm["chat_alias"], llm["embed_alias"])]
            a["limits"] = {"rpm": o.get("rpm", llm["rpm"]), "budget": o["budget"] if "budget" in o else llm["budget"],
                           "budget_mode": o.get("budget_mode", llm["budget_mode"]), "aliases": [llm["chat_alias"], llm["embed_alias"], *extras]}
    scopes: set[str] = set()
    fleet["connections"] = []
    for k in fleet["order"]:
        a = fleet["apps"][k]
        for peer, spec in a["connect"].items():
            if k in fleet["selected"] and a["deploy"]:
                fleet["connections"].append({"app": k, "peer": peer, "merchant": spec["merchant"]})
                scopes |= {f"{peer}.admin", f"{k}.admin"}
    fleet["connect_scopes"] = sorted(scopes)
    fleet["connect_audiences"] = sorted(s.split(".", 1)[0] for s in scopes)
    fleet["groups"] = [{"name": n, "description": str(g.get("description", ""))[:200], "grants": {str(t): str(r) for t, r in (g.get("grants") or {}).items()}}
                       for n, g in ((doc.get("access") or {}).get("groups") or {}).items()]
    return fleet


def eco_web_spec(app: dict) -> dict:
    return {"client_id": app["key"], "name": app["name"], "kind": "web", "description": app["description"], "redirect_uris": [PENDING_REDIRECT],
            "post_logout_uris": [], "allowed_scopes": [], "allowed_audiences": [], "roles": app["roles"], "default_roles": app["default_roles"], "url": ""}


def eco_service_spec(app: dict) -> dict:
    return {"client_id": app["service_client_id"], "name": f"{app['name']} (service)", "kind": "service", "description": f"{app['name']} calling other apps",
            "allowed_scopes": [s for scopes in app["calls"].values() for s in scopes], "allowed_audiences": sorted(app["calls"]), "roles": [],
            "default_roles": [], "redirect_uris": [], "post_logout_uris": [], "url": ""}


def eco_connect_spec(fleet: dict, client_id: str = "eco-ansible") -> dict:
    """The service client of the connect step (ecoctl's own is named `ecoctl`): the admin scope of both ends of every connection."""
    return {"client_id": client_id, "name": "Ansible (service)", "kind": "service", "description": "the Ansible recipe connecting apps to each other",
            "allowed_scopes": fleet["connect_scopes"], "allowed_audiences": fleet["connect_audiences"], "roles": [], "default_roles": [],
            "redirect_uris": [], "post_logout_uris": [], "url": ""}


def eco_app_patch(existing: dict, spec: dict) -> dict:
    """The fields of `spec` that differ from the registered app (never client_id or kind). A redirect already pointed at the real URL is not reset."""
    patch = {k: v for k, v in spec.items() if k not in ("client_id", "kind") and existing.get(k) != v}
    if spec.get("redirect_uris") == [PENDING_REDIRECT]:
        for k in ("redirect_uris", "post_logout_uris", "url"):
            patch.pop(k, None)
    return patch


def eco_group_grants(fleet: dict) -> list[dict]:
    """Every (group, app, role) to ensure, as ecoctl's `_group`: `*` means every selected web app that defines the role."""
    out = []
    sel = set(fleet["selected"])
    for g in fleet["groups"]:
        for target, role in g["grants"].items():
            keys = [k for k in fleet["selected"] if fleet["apps"][k]["web"] and role in {r["name"] for r in fleet["apps"][k]["roles"]}] if target == "*" else [target]
            for k in keys:
                if k in sel:
                    out.append({"group": g["name"], "description": g["description"], "client_id": k, "role": role})
    return out


def eco_urls(fleet: dict, scheme: str, domain: str, port="", hosts=None) -> dict:
    """key -> public URL. `hosts` overrides the host name of single apps (key -> host)."""
    suffix = f":{port}" if port not in ("", None, 443, 80) else ""
    hosts = hosts or {}
    return {k: f"{scheme}://{hosts.get(k, f'{k}.{domain}')}{suffix}" for k in ("identity", *fleet["apps"])}


def eco_fingerprint(value: str) -> str:
    """First 8 hex of the SHA-256 ShopFront reports instead of a secret (same as ecoctl.connect_client.fingerprint)."""
    return hashlib.sha256(str(value).encode()).hexdigest()[:8]


def eco_secret_names(fleet: dict, target: str = "agentpg") -> list[str]:
    """Every generate-once secret, as `<scope>/<NAME>`: the manifest's `generate:` names, the identity admin key, the gateway operator key and,
    for `eco_target: hosts` only (AgentPG gives each instance its own key), one database password per deployed app."""
    names = [f"identity/{n}" for n in fleet["identity"]["generate"]] + ["identity/IDENTITY_API_KEY"]
    for k in fleet["deployed"]:
        a = fleet["apps"][k]
        names += [f"{k}/{n}" for n in a["generate"]]
    if fleet["consumers"]:
        gw = fleet["apps"][fleet["llm"]["gateway"]]
        names.append(f"{gw['key']}/{gw['env_prefix']}_API_KEY")
    if target == "hosts":
        names += [f"{k}/DB_PASSWORD" for k in ("identity", *fleet["deployed"])]
    return list(dict.fromkeys(names))


def eco_dependents(fleet: dict, keys) -> list[str]:
    """The apps that call or use any of `keys` (or the LLM gateway, when it is one of them), transitively: as Manifest.dependents."""
    out: set[str] = set()
    changed = True
    while changed:
        changed = False
        for k in fleet["apps"]:
            if k not in out and set(_needs(fleet, k)) & (set(keys) | out):
                out.add(k)
                changed = True
    return sorted(out)


def eco_recreate_set(fleet: dict, keys) -> list[str]:
    """What `ecoctl recreate <keys>` deletes: a sandbox's URL contains its id, so the apps that depend on a recreated one are recreated too, and
    recreating `identity` recreates everything that is deployed."""
    keys = list(keys or [])
    if "identity" in keys:
        return ["identity", *fleet["deployed"]]
    return [k for k in fleet["deployed"] if k in set(keys) | set(eco_dependents(fleet, keys))]


def eco_destroy_plan(fleet: dict, run_list: list, only=None) -> list[dict]:
    """What a teardown may delete, in deletion order: the reverse of creation (callers before callees, identity last). Names are exactly the ones the recipe
    creates, `<fleet>-<app>` and `<fleet>-<app>-db`, for apps of the manifest only; `only` narrows the list (it never widens it)."""
    keys = [k for k in reversed(run_list) if not only or k in only]
    return [{"key": k, "sandbox": eco_get(fleet, k)["sandbox_name"], "instance": eco_get(fleet, k)["instance_name"]} for k in keys]


def eco_db_name(fleet: dict, key: str) -> str:
    """Database and login role of an app: `<fleet>_<key>` with dashes turned into underscores (one of each per app, like ecoctl's instance per app)."""
    return f"{fleet['fleet']}_{key}".replace("-", "_")


def eco_get(fleet: dict, key: str) -> dict:
    """An app of the fleet by key; `identity` is the identity provider."""
    return fleet["identity"] if key == "identity" else fleet["apps"][key]


class FilterModule:
    def filters(self):
        return {name: fn for name, fn in globals().items() if name.startswith("eco_") and callable(fn)}
