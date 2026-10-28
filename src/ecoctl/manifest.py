"""ecosystem.yaml: the fleet in one file, validated strictly (unknown keys are errors, secrets are never accepted as plain values)."""
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

NAME = re.compile(r"^[a-z0-9][a-z0-9-]{1,39}$")          # also the identity provider's client id and role namespace
ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
SCOPE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,59}$")
ROLE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,39}$")
# Exactly what `agentpg extensions create <name> <instance>` installed on a throwaway instance (Postgres 18, role without superuser), probed 2026-10-28.
EXTENSIONS = {"vector", "pg_trgm", "unaccent", "citext", "fuzzystrmatch", "tcn", "btree_gin", "btree_gist", "cube", "tsm_system_rows", "tsm_system_time",
              "postgis", "edb_stat_monitor", "query_advisor", "edb_pg_tuner", "sql_profiler", "aidb"}
# Refused by the platform: these need a superuser, which an instance's role is not. Kept out on purpose; a new probe may move one into EXTENSIONS.
NEEDS_SUPERUSER = {"data_redaction", "pg_prewarm", "pgstattuple", "pgrowlocks", "password_profile", "sslinfo", "earthdistance", "pg_surgery"}
# These install but nothing in them can be used (permission denied on their tables or functions): no app should list them.
INSTALLS_BUT_UNUSABLE = {"address_standardizer", "address_standardizer_data_us", "system_stats"}
PLANS = {"small", "medium", "large"}
SECRETISH = re.compile(r"(SECRET|PASSWORD|PASSWD|TOKEN|KEY|DSN|CREDENTIAL)", re.I)
URL_WITH_PASSWORD = re.compile(r"://[^/\s:@]+:[^/\s@]+@")


class ManifestError(ValueError):
    pass


@dataclass(frozen=True)
class Role:
    name: str
    description: str = ""


LLM_CONSUMER_ENV = ("LLM_BASE_URL", "LLM_API_KEY", "LLM_CHAT_MODEL", "EMBEDDING_MODEL", "EMBEDDING_DIM")      # the names the apps read; ecoctl owns them
ALIAS = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,59}$")


@dataclass(frozen=True)
class LLM:
    """How apps that use an LLM are wired to the gateway. Every key is optional."""
    gateway: str = "llmgw"                    # the app that runs the LLM gateway
    chat_alias: str = "chat-default"
    embed_alias: str = "embed-default"
    embed_dimensions: int = 768               # what TrustDesk and PagerLens expect (EMBEDDING_DIM) and what the echo embedding returns
    rpm: int = 60                             # requests per minute of each app's gateway client
    budget: int | None = 5_000_000            # monthly tokens of each app's gateway client; None: no budget
    budget_mode: str = "hard"                 # hard: refuse over budget; soft: allow and flag


@dataclass(frozen=True)
class LLMOverride:
    """What an app's `llm:` mapping sets; only the keys it names (budget_set tells `budget: null` from not given)."""
    rpm: int | None = None
    budget: int | None = None
    budget_set: bool = False
    budget_mode: str | None = None
    aliases: tuple[str, ...] = ()             # extra aliases besides the two defaults


@dataclass(frozen=True)
class Limits:
    """The effective gateway client settings of one app: its override, else the global `llm:` block."""
    rpm: int
    budget: int | None
    budget_mode: str
    aliases: tuple[str, ...]                  # the two defaults, then the app's extras


CONNECT_PEERS = ("paybroker",)               # the apps an app can be connected to (a merchant, its keys and webhook); more can follow
MERCHANT_NAME = 60


@dataclass(frozen=True)
class Connect:
    """`apps.<app>.connect.<peer>`: how ecoctl wires the app to the peer after both are up (see connect.py)."""
    merchant: str                             # the PayBroker merchant the app is: found by this exact name, created when missing


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
    llm: bool = False                         # uses an LLM: wired to the gateway automatically
    llm_override: LLMOverride = LLMOverride() # limits and extra aliases for this app's gateway client (`llm: {rpm: ...}`)
    uses: tuple[str, ...] = ()                # apps (or `identity`) whose URL is injected without a scope: called by other means (a merchant key, ...)
    connect: dict[str, Connect] = field(default_factory=dict)      # peer app -> how ecoctl connects this app to it (merchant, keys, webhook)

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
    llm: LLM = field(default_factory=LLM)

    def consumers(self) -> list[str]:
        """The apps that use the LLM gateway."""
        return sorted(k for k, a in self.apps.items() if a.llm)

    def limits(self, key: str) -> Limits:
        """Effective gateway limits and aliases of a consumer: its own `llm:` mapping over the global defaults."""
        o, g = self.apps[key].llm_override, self.llm
        extras = tuple(a for a in o.aliases if a not in (g.chat_alias, g.embed_alias))
        return Limits(rpm=g.rpm if o.rpm is None else o.rpm, budget=o.budget if o.budget_set else g.budget,
                      budget_mode=o.budget_mode or g.budget_mode, aliases=(g.chat_alias, g.embed_alias, *extras))

    def needs(self, key: str) -> set[str]:
        """What must exist before `key`: the apps it calls or uses, and the LLM gateway when it uses an LLM. (The identity provider is
        always deployed first, and recreating it recreates everything, so it is not listed here.)"""
        a = self.apps[key]
        return set(a.calls) | (set(a.uses) - {"identity"}) | ({self.llm.gateway} if a.llm else set())

    def url_vars(self, app: App) -> dict[str, str]:
        """callee key -> the environment variable that carries its URL in `app`: every app it calls or uses (`identity` included)."""
        return {callee: app.url_env.get(callee, f"{self.everything()[callee].env_prefix}_URL") for callee in (*app.calls, *app.uses)}

    def connect_scopes(self) -> tuple[list[str], list[str]]:
        """(scopes, audiences) of ecoctl's own service client: `<app>.admin` of both ends of every connection (empty when nothing is connected)."""
        scopes: set[str] = set()
        for key, app in self.apps.items():
            for peer in app.connect:
                scopes |= {f"{peer}.admin", f"{key}.admin"}
        return sorted(scopes), sorted(s.split(".", 1)[0] for s in scopes)

    def pause_enabled(self, app: App) -> bool:
        return self.auto_pause if app.auto_pause is None else app.auto_pause

    def plan_for(self, app: App) -> str | None:
        return app.plan or self.plan

    def dependents(self, keys) -> set[str]:
        """The apps that call or use any of `keys` (or use the LLM gateway, when it is one of them), transitively: their environment names
        the callee's URL, which changes when it is recreated."""
        out: set[str] = set()
        changed = True
        while changed:
            changed = False
            for k in self.apps:
                if k not in out and (self.needs(k) & (set(keys) | out)):
                    out.add(k)
                    changed = True
        return out

    def closure(self, keys) -> set[str]:
        """The apps to deploy for a selection: the selection plus every app it calls, transitively (their URLs are needed)."""
        out: set[str] = set()

        def add(k: str) -> None:
            if k in out:
                return
            if k not in self.apps:
                raise ManifestError(f"{k!r} is not an app in this manifest")
            out.add(k)
            for callee in sorted(self.needs(k)):
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
            for callee in sorted(self.needs(key)):
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
                "env_prefix", "exposes", "calls", "uses", "url_env", "deploy", "auto_pause", "plan", "llm", "connect"}, where)
    if not NAME.match(key):
        raise ManifestError(f"{where}: {key!r} must be 2-40 lowercase letters, digits or dashes")
    port = raw.get("port", 8000)
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ManifestError(f"{where}.port must be a port number")
    exts = tuple(_list(raw.get("extensions"), f"{where}.extensions"))
    for e in exts:
        if e in EXTENSIONS:
            continue
        if e in NEEDS_SUPERUSER:
            raise ManifestError(f"{where}.extensions: {e} needs superuser on this platform (AgentPG refuses it for an instance's role)")
        if isinstance(e, str) and (e in INSTALLS_BUT_UNUSABLE or e.startswith("address_standardizer")):
            raise ManifestError(f"{where}.extensions: {e} installs but is unusable here (its tables or functions are permission-denied for the instance's role)")
        if e == "uuid-ossp":
            raise ManifestError(f"{where}.extensions: uuid-ossp is rejected by the agentpg CLI (the hyphen); use gen_random_uuid(), which Postgres has built in")
        raise ManifestError(f"{where}.extensions: unsupported {e} (allowed: {', '.join(sorted(EXTENSIONS))})")
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
    uses = tuple(_list(raw.get("uses"), f"{where}.uses"))
    for u in uses:
        if not isinstance(u, str) or not NAME.match(u):
            raise ManifestError(f"{where}.uses: {u!r} is not an app key")
    if len(set(uses)) != len(uses):
        raise ManifestError(f"{where}.uses lists an app twice")
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
    env = _env(raw.get("env"), f"{where}.env")
    llm_raw = raw.get("llm", False)
    override = LLMOverride()
    if isinstance(llm_raw, dict):
        override = _llm_override(llm_raw, f"{where}.llm")
        llm = True
    elif isinstance(llm_raw, bool):
        llm = llm_raw
    else:
        raise ManifestError(f"{where}.llm must be true, false or a mapping (rpm, budget, budget_mode, aliases)")
    if llm:
        clash = [n for n in LLM_CONSUMER_ENV if n in env or n in generate]
        if clash:
            raise ManifestError(f"{where}: {', '.join(clash)} is set by ecoctl for an app with `llm: true`; remove them from env/generate")
        if not deploy:
            raise ManifestError(f"{where}: `llm: true` needs a deployed app (deploy: true): there is no sandbox to give the gateway settings to")
    connect = _connect(raw.get("connect"), f"{where}.connect")
    return App(llm=llm, llm_override=override, connect=connect, deploy=deploy, auto_pause=auto_pause, plan=plan,
               key=key, name=_text(raw.get("name"), f"{where}.name", key), description=str(raw.get("description", ""))[:300],
               image=_text(raw.get("image"), f"{where}.image", key, 100), port=port, extensions=exts, env=env,
               generate=generate, health=_text(raw.get("health"), f"{where}.health", "/healthz", 100), roles=tuple(roles), default_roles=default_roles,
               env_prefix=str(prefix), exposes=exposes, calls=calls, url_env=url_env, uses=uses, web=web)


def _int(v, where: str, lo: int, hi: int) -> int:
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        raise ManifestError(f"{where} must be a whole number from {lo} to {hi}")
    return v


def _connect(raw, where: str) -> dict[str, Connect]:
    out: dict[str, Connect] = {}
    for peer, spec in _dict(raw, where).items():
        if peer not in CONNECT_PEERS:
            raise ManifestError(f"{where}.{peer}: only {', '.join(CONNECT_PEERS)} can be connected")
        spec = _dict(spec, f"{where}.{peer}")
        _only(spec, {"merchant"}, f"{where}.{peer}")
        merchant = spec.get("merchant")
        if not isinstance(merchant, str) or not merchant.strip() or len(merchant.strip()) > MERCHANT_NAME or any(ord(c) < 32 for c in merchant):
            raise ManifestError(f"{where}.{peer}.merchant must be a text of 1-{MERCHANT_NAME} characters (the merchant's exact name in PayBroker)")
        out[peer] = Connect(merchant.strip())
    return out


def _llm_override(raw: dict, where: str) -> LLMOverride:
    _only(raw, {"rpm", "budget", "budget_mode", "aliases"}, where)
    rpm = None if "rpm" not in raw else _int(raw["rpm"], f"{where}.rpm", 1, 100_000)
    budget = None if raw.get("budget") is None else _int(raw["budget"], f"{where}.budget", 1, 10**13)
    mode = raw.get("budget_mode")
    if mode is not None and mode not in {"hard", "soft"}:
        raise ManifestError(f"{where}.budget_mode must be hard or soft")
    aliases = _list(raw.get("aliases"), f"{where}.aliases")
    for a in aliases:
        if not isinstance(a, str) or not ALIAS.match(a):
            raise ManifestError(f"{where}.aliases: {a!r} must be 1-60 characters: letters, digits, '.', '_', ':' or '-'")
    if len(set(aliases)) != len(aliases):
        raise ManifestError(f"{where}.aliases lists an alias twice")
    return LLMOverride(rpm=rpm, budget=budget, budget_set="budget" in raw, budget_mode=mode, aliases=tuple(aliases))


def _llm(raw, apps: dict[str, App]) -> LLM:
    raw = _dict(raw, "llm")
    _only(raw, {"gateway", "chat_alias", "embed_alias", "embed_dimensions", "rpm", "budget", "budget_mode"}, "llm")
    d = LLM()
    gateway = _text(raw.get("gateway"), "llm.gateway", d.gateway, 40)
    aliases = {}
    for k, default in (("chat_alias", d.chat_alias), ("embed_alias", d.embed_alias)):
        aliases[k] = _text(raw.get(k), f"llm.{k}", default, 60)
        if not ALIAS.match(aliases[k]):
            raise ManifestError(f"llm.{k}: {aliases[k]!r} must be 1-60 characters: letters, digits, '.', '_', ':' or '-'")
    if aliases["chat_alias"] == aliases["embed_alias"]:
        raise ManifestError("llm.chat_alias and llm.embed_alias must differ")
    budget = d.budget if "budget" not in raw else (None if raw["budget"] is None else _int(raw["budget"], "llm.budget", 1, 10**13))
    mode = raw.get("budget_mode", d.budget_mode)
    if mode not in {"hard", "soft"}:
        raise ManifestError("llm.budget_mode must be hard or soft")
    llm = LLM(gateway=gateway, embed_dimensions=_int(raw.get("embed_dimensions", d.embed_dimensions), "llm.embed_dimensions", 1, 8192),
              rpm=_int(raw.get("rpm", d.rpm), "llm.rpm", 1, 100_000), budget=budget, budget_mode=mode, **aliases)
    consumers = [k for k, a in apps.items() if a.llm]
    if consumers:
        gw = apps.get(gateway)
        if gw is None:
            raise ManifestError(f"llm.gateway: {gateway!r} is not an app in this manifest (needed by {', '.join(consumers)})")
        if not gw.deploy:
            raise ManifestError(f"llm.gateway: {gateway!r} must be deployed (deploy: true) before the apps that use it")
        if gw.llm:
            raise ManifestError(f"apps.{gateway}.llm: the gateway itself cannot be an LLM consumer")
    return llm


def parse(doc: dict) -> Manifest:
    doc = _dict(doc, "the manifest")
    _only(doc, {"version", "fleet", "agentpg", "identity", "apps", "access", "llm"}, "the manifest")
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
    if "ecoctl" in apps:
        raise ManifestError("apps: 'ecoctl' is reserved for ecoctl's own service client (it connects apps to each other)")
    for key, app in apps.items():
        for peer in app.connect:
            where = f"apps.{key}.connect.{peer}"
            if peer == key:
                raise ManifestError(f"{where}: an app cannot be connected to itself")
            if peer not in apps:
                raise ManifestError(f"{where}: {peer!r} is not an app in this manifest")
            if peer not in app.uses and peer not in app.calls:
                raise ManifestError(f"{where}: {key} must list {peer!r} under `uses` or `calls` (connecting needs its URL)")
            for who in (key, peer):
                if not apps[who].deploy:
                    raise ManifestError(f"{where}: {who!r} is not deployed (deploy: false), so there is nothing to connect")
                if f"{who}.admin" not in apps[who].exposes:
                    raise ManifestError(f"{where}: ecoctl needs the {who}.admin scope: list it under apps.{who}.exposes")
        for callee, scopes in app.calls.items():
            if callee not in apps:
                raise ManifestError(f"apps.{key}.calls: {callee!r} is not an app in this manifest")
            missing = [s for s in scopes if s not in apps[callee].exposes]
            if missing:
                raise ManifestError(f"apps.{key}.calls.{callee}: {', '.join(missing)} not listed under apps.{callee}.exposes")
        for used in app.uses:
            if used == key:
                raise ManifestError(f"apps.{key}.uses: an app cannot use itself")
            if used in app.calls:
                raise ManifestError(f"apps.{key}.uses: {used!r} is already under calls (calls injects its URL too); list it in one place")
            if used != "identity" and used not in apps:
                raise ManifestError(f"apps.{key}.uses: {used!r} is not an app in this manifest")
            if used != "identity" and not apps[used].deploy:
                raise ManifestError(f"apps.{key}.uses: {used!r} is not deployed (deploy: false), so it has no URL to inject")
        for callee in app.url_env:
            if callee not in app.calls and callee not in app.uses:
                raise ManifestError(f"apps.{key}.url_env.{callee}: only apps it calls or uses can be mapped")
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
    m = Manifest(fleet=fleet, registry=registry, tag=tag, size=size, identity=ident, apps=apps, groups=tuple(groups), auto_pause=fleet_pause, plan=fleet_plan, llm=_llm(doc.get("llm"), apps))
    m.order()                                                  # raises on a cycle
    return m


def load(path: str | Path) -> Manifest:
    try:
        doc = yaml.safe_load(Path(path).read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise ManifestError(f"cannot read {path}: {exc}") from exc
    return parse(doc)
