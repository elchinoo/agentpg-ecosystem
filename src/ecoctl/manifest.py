"""ecosystem.yaml: the fleet in one file, validated strictly (unknown keys are errors, secrets are never accepted as plain values)."""
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

NAME = re.compile(r"^[a-z0-9][a-z0-9-]{1,39}$")          # also the identity provider's client id and role namespace
ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
SCOPE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,59}$")
ROLE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,39}$")
EXTENSIONS = {"vector", "pg_trgm", "pgcrypto", "hstore", "citext", "uuid-ossp", "btree_gin", "btree_gist"}
PLANS = {"small", "medium", "large"}
SECRETISH = re.compile(r"(SECRET|PASSWORD|PASSWD|TOKEN|KEY|DSN|CREDENTIAL)", re.I)
URL_WITH_PASSWORD = re.compile(r"://[^/\s:@]+:[^/\s@]+@")


class ManifestError(ValueError):
    pass


@dataclass(frozen=True)
class Role:
    name: str
    description: str = ""


@dataclass(frozen=True)
class App:
    key: str                                  # client id, sandbox name suffix and scope prefix
    name: str
    description: str
    image: str
    port: int
    extensions: tuple[str, ...]
    env: dict[str, str]
    generate: tuple[str, ...]                 # env names that get a generated random secret
    health: str
    roles: tuple[Role, ...]
    default_roles: tuple[str, ...]
    env_prefix: str                           # <PREFIX>_OIDC_ISSUER ... (the app's own convention)
    exposes: tuple[str, ...]                  # scopes other services may request from this app
    calls: dict[str, tuple[str, ...]]         # callee app -> scopes this app's service identity may request
    url_env: dict[str, str]                   # callee app -> env var that holds the callee's URL for this app
    web: bool                                 # browser sign-in (registered as a web client)
    deploy: bool = True                       # False: only registered (a job or CLI that runs elsewhere)
    auto_pause: bool | None = None            # None: the fleet default; False keeps a sandbox running when idle
    plan: str | None = None                   # compute plan for the sandbox (small|medium|large); None: the fleet default

    @property
    def service(self) -> bool:
        return bool(self.calls)

    @property
    def service_client_id(self) -> str:
        return f"{self.key}-service"


@dataclass(frozen=True)
class Group:
    name: str
    description: str
    grants: dict[str, str]                    # app key (or "*") -> role


@dataclass(frozen=True)
class Manifest:
    fleet: str
    registry: str
    tag: str
    size: str
    identity: App
    apps: dict[str, App]
    groups: tuple[Group, ...] = field(default_factory=tuple)
    auto_pause: bool = True
    plan: str | None = None

    def pause_enabled(self, app: App) -> bool:
        return self.auto_pause if app.auto_pause is None else app.auto_pause

    def plan_for(self, app: App) -> str | None:
        return app.plan or self.plan

    def closure(self, keys) -> set[str]:
        """The apps to deploy for a selection: the selection plus every app it calls, transitively (their URLs are needed)."""
        out: set[str] = set()

        def add(k: str) -> None:
            if k in out:
                return
            if k not in self.apps:
                raise ManifestError(f"{k!r} is not an app in this manifest")
            out.add(k)
            for callee in self.apps[k].calls:
                add(callee)

        for k in keys:
            add(k)
        return out

    def sandbox_name(self, key: str) -> str:
        return f"{self.fleet}-{key}"

    def instance_name(self, key: str) -> str:
        return f"{self.fleet}-{key}-db"

    def image(self, app: App) -> str:
        return f"{self.registry}/{app.image}:{self.tag}"

    def everything(self) -> dict[str, App]:
        return {self.identity.key: self.identity, **self.apps}

    def order(self) -> list[App]:
        """Apps in creation order: callees before their callers, so a caller's env can name the callee's URL."""
        done: list[str] = []
        visiting: set[str] = set()

        def visit(key: str) -> None:
            if key in done:
                return
            if key in visiting:
                raise ManifestError(f"apps call each other in a cycle through {key!r}")
            visiting.add(key)
            for callee in sorted(self.apps[key].calls):
                visit(callee)
            visiting.discard(key)
            done.append(key)

        for key in sorted(self.apps):
            visit(key)
        return [self.apps[k] for k in done]


def _only(d: dict, allowed: set[str], where: str) -> None:
    extra = set(d) - allowed
    if extra:
        raise ManifestError(f"{where}: unknown key(s) {', '.join(sorted(extra))}")


def _dict(v, where: str) -> dict:
    if v is None:
        return {}
    if not isinstance(v, dict):
        raise ManifestError(f"{where} must be a mapping")
    return v


def _list(v, where: str) -> list:
    if v is None:
        return []
    if not isinstance(v, list):
        raise ManifestError(f"{where} must be a list")
    return v


def _text(v, where: str, default: str | None = None, limit: int = 300) -> str:
    if v is None and default is not None:
        return default
    if not isinstance(v, str) or not v.strip() or len(v) > limit:
        raise ManifestError(f"{where} must be a non-empty text of at most {limit} characters")
    return v.strip()


def _env(raw, where: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in _dict(raw, where).items():
        if not isinstance(k, str) or not ENV_NAME.match(k):
            raise ManifestError(f"{where}: {k!r} is not an environment variable name")
        if SECRETISH.search(k):
            raise ManifestError(f"{where}: {k} looks like a secret. List it under `generate:` (ecoctl creates a random value and keeps it in .ecosystem/) "
                                "instead of writing a value in this file")
        v = str(v)
        if URL_WITH_PASSWORD.search(v):
            raise ManifestError(f"{where}: the value of {k} embeds a password in a URL; never put credentials in the manifest")
        out[k] = v
    return out


def _app(key: str, raw: dict, where: str, *, identity: bool = False) -> App:
    _only(raw, {"name", "description", "image", "port", "extensions", "env", "generate", "health", "web", "roles", "default_roles",
                "env_prefix", "exposes", "calls", "url_env", "deploy", "auto_pause", "plan"}, where)
    if not NAME.match(key):
        raise ManifestError(f"{where}: {key!r} must be 2-40 lowercase letters, digits or dashes")
    port = raw.get("port", 8000)
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ManifestError(f"{where}.port must be a port number")
    exts = tuple(_list(raw.get("extensions"), f"{where}.extensions"))
    bad = [e for e in exts if e not in EXTENSIONS]
    if bad:
        raise ManifestError(f"{where}.extensions: unsupported {', '.join(map(str, bad))} (allowed: {', '.join(sorted(EXTENSIONS))})")
    generate = tuple(_list(raw.get("generate"), f"{where}.generate"))
    for g in generate:
        if not isinstance(g, str) or not ENV_NAME.match(g):
            raise ManifestError(f"{where}.generate: {g!r} is not an environment variable name")
    roles = []
    for r in _list(raw.get("roles"), f"{where}.roles"):
        r = _dict(r, f"{where}.roles[]")
        _only(r, {"name", "description"}, f"{where}.roles[]")
        if not ROLE.match(str(r.get("name", ""))):
            raise ManifestError(f"{where}.roles: {r.get('name')!r} is not a valid role name")
        roles.append(Role(r["name"], str(r.get("description", ""))[:200]))
    names = {r.name for r in roles}
    default_roles = tuple(_list(raw.get("default_roles"), f"{where}.default_roles"))
    if set(default_roles) - names:
        raise ManifestError(f"{where}.default_roles must be roles this app defines")
    exposes = tuple(_list(raw.get("exposes"), f"{where}.exposes"))
    for s in exposes:
        if not isinstance(s, str) or not SCOPE.match(s) or s.split(".", 1)[0] != key or "." not in s:
            raise ManifestError(f"{where}.exposes: {s!r} must look like {key}.<thing>")
    calls: dict[str, tuple[str, ...]] = {}
    for callee, scopes in _dict(raw.get("calls"), f"{where}.calls").items():
        scopes = _list(scopes, f"{where}.calls.{callee}")
        if not scopes:
            raise ManifestError(f"{where}.calls.{callee} lists no scopes")
        for s in scopes:
            if not isinstance(s, str) or not SCOPE.match(s) or s.split(".", 1)[0] != callee:
                raise ManifestError(f"{where}.calls.{callee}: scope {s!r} must start with '{callee}.' (a scope addresses one app by its prefix)")
        calls[callee] = tuple(dict.fromkeys(scopes))
    url_env = {k: str(v) for k, v in _dict(raw.get("url_env"), f"{where}.url_env").items()}
    for k, v in url_env.items():
        if not ENV_NAME.match(v):
            raise ManifestError(f"{where}.url_env.{k}: {v!r} is not an environment variable name")
    prefix = raw.get("env_prefix", key.upper().replace("-", "_"))
    if not ENV_NAME.match(str(prefix)):
        raise ManifestError(f"{where}.env_prefix must be an upper-case identifier")
    web = raw.get("web", not identity)
    if not isinstance(web, bool):
        raise ManifestError(f"{where}.web must be true or false")
    deploy = raw.get("deploy", True)
    if not isinstance(deploy, bool):
        raise ManifestError(f"{where}.deploy must be true or false")
    auto_pause = raw.get("auto_pause")
    if auto_pause is not None and not isinstance(auto_pause, bool):
        raise ManifestError(f"{where}.auto_pause must be true or false")
    plan = raw.get("plan")
    if plan is not None and plan not in PLANS:
        raise ManifestError(f"{where}.plan must be one of {', '.join(sorted(PLANS))}")
    return App(deploy=deploy, auto_pause=auto_pause, plan=plan, key=key, name=_text(raw.get("name"), f"{where}.name", key), description=str(raw.get("description", ""))[:300],
               image=_text(raw.get("image"), f"{where}.image", key, 100), port=port, extensions=exts, env=_env(raw.get("env"), f"{where}.env"),
               generate=generate, health=_text(raw.get("health"), f"{where}.health", "/healthz", 100), roles=tuple(roles), default_roles=default_roles,
               env_prefix=str(prefix), exposes=exposes, calls=calls, url_env=url_env, web=web)


def parse(doc: dict) -> Manifest:
    doc = _dict(doc, "the manifest")
    _only(doc, {"version", "fleet", "agentpg", "identity", "apps", "access"}, "the manifest")
    if doc.get("version") != 1:
        raise ManifestError("version must be 1")
    fleet = _text(doc.get("fleet"), "fleet", limit=20)
    if not re.match(r"^[a-z0-9][a-z0-9-]{0,19}$", fleet):
        raise ManifestError("fleet must be lowercase letters, digits and dashes (at most 20)")
    ag = _dict(doc.get("agentpg"), "agentpg")
    _only(ag, {"registry", "tag", "size", "auto_pause", "plan"}, "agentpg")
    registry = _text(ag.get("registry"), "agentpg.registry", "ghcr.io/elchinoo", 100).rstrip("/")
    if not re.match(r"^[a-z0-9.-]+(:[0-9]+)?(/[a-z0-9._-]+)*$", registry):
        raise ManifestError("agentpg.registry must look like ghcr.io/owner")
    tag = _text(ag.get("tag"), "agentpg.tag", "latest", 60)
    size = _text(ag.get("size"), "agentpg.size", "small", 20)
    fleet_pause = ag.get("auto_pause", True)
    if not isinstance(fleet_pause, bool):
        raise ManifestError("agentpg.auto_pause must be true or false")
    fleet_plan = ag.get("plan")
    if fleet_plan is not None and fleet_plan not in PLANS:
        raise ManifestError(f"agentpg.plan must be one of {', '.join(sorted(PLANS))}")
    ident = _app("identity", _dict(doc.get("identity"), "identity"), "identity", identity=True)
    if ident.calls:
        raise ManifestError("identity: the provider does not call other apps")
    apps_raw = _dict(doc.get("apps"), "apps")
    if not apps_raw:
        raise ManifestError("apps: list at least one app")
    apps = {k: _app(k, _dict(v, f"apps.{k}"), f"apps.{k}") for k, v in apps_raw.items()}
    if "identity" in apps:
        raise ManifestError("apps: 'identity' is reserved for the identity provider (use the identity: section)")
    for key, app in apps.items():
        for callee, scopes in app.calls.items():
            if callee not in apps:
                raise ManifestError(f"apps.{key}.calls: {callee!r} is not an app in this manifest")
            missing = [s for s in scopes if s not in apps[callee].exposes]
            if missing:
                raise ManifestError(f"apps.{key}.calls.{callee}: {', '.join(missing)} not listed under apps.{callee}.exposes")
        for callee in app.url_env:
            if callee not in app.calls:
                raise ManifestError(f"apps.{key}.url_env.{callee}: only apps it calls can be mapped")
    groups = []
    for name, g in _dict(_dict(doc.get("access"), "access").get("groups"), "access.groups").items():
        g = _dict(g, f"access.groups.{name}")
        _only(g, {"description", "grants"}, f"access.groups.{name}")
        if not re.match(r"^[a-z0-9][a-z0-9-]{0,62}$", str(name)):
            raise ManifestError(f"access.groups: {name!r} is not a valid group name")
        grants = {str(k): str(v) for k, v in _dict(g.get("grants"), f"access.groups.{name}.grants").items()}
        for target, role in grants.items():
            targets = list(apps) if target == "*" else [target]
            for t in targets:
                if t not in apps and t != "identity":
                    raise ManifestError(f"access.groups.{name}.grants: {t!r} is not an app")
                if t in apps and apps[t].web and role not in {r.name for r in apps[t].roles} and target != "*":
                    raise ManifestError(f"access.groups.{name}.grants.{t}: {role!r} is not a role of {t}")
        groups.append(Group(str(name), str(g.get("description", ""))[:200], grants))
    m = Manifest(fleet=fleet, registry=registry, tag=tag, size=size, identity=ident, apps=apps, groups=tuple(groups), auto_pause=fleet_pause, plan=fleet_plan)
    m.order()                                                  # raises on a cycle
    return m


def load(path: str | Path) -> Manifest:
    try:
        doc = yaml.safe_load(Path(path).read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise ManifestError(f"cannot read {path}: {exc}") from exc
    return parse(doc)
