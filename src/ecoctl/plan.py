"""The plan: an ordered list of steps. Printing it changes nothing; running it creates instances and sandboxes on AgentPG and
registers apps with the identity provider. Every step checks what already exists, so a second run converges."""
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx

from .agentpg import AgentPG, AgentPGError, token_from
from .connect import connect_step
from .identity_client import PENDING_REDIRECT, Identity, IdentityError
from .llm_client import Gateway, GatewayError
from .manifest import App, Manifest
from .state import State


@dataclass
class Step:
    title: str
    detail: list[str] = field(default_factory=list)
    run: Callable[["Context"], str] | None = None          # returns a one-line outcome


@dataclass
class Context:
    manifest: Manifest
    state: State
    agentpg: AgentPG
    http: httpx.Client | None = None                        # injected in tests
    rotate: bool = False
    only: set[str] | None = None                            # deploy just these apps (and what they call); None: all
    llm_http: httpx.Client | None = None                    # the LLM gateway's admin and data plane; injected in tests
    connect_http: httpx.Client | None = None                # the identity token endpoint, PayBroker's and the shop's admin APIs; injected in tests
    connect_sleep: Callable[[float], None] | None = None    # injected in tests (waiting for a locked vault or a service that is starting)
    connect_clock: Callable[[], float] | None = None
    _identity: Identity | None = None

    def gateway(self) -> Gateway:
        key = self.manifest.llm.gateway
        url = self.state.app(key).get("url")
        if not url:
            raise GatewayError(f"the LLM gateway ({key}) has no URL yet")
        return Gateway(url, self.state.secret(key, llm_admin_name(self.manifest)), self.llm_http)

    def identity(self) -> Identity:
        if self._identity is None:
            url = self.state.app("identity").get("url")
            if not url:
                raise IdentityError("the identity provider has no URL yet")
            self._identity = Identity(url, self.state.admin_key(), self.http)
        return self._identity


def _env_hash(env: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(env, sort_keys=True).encode()).hexdigest()[:12]


def llm_admin_name(m: Manifest) -> str:
    """The gateway's static operator key, which ecoctl generates and keeps: LLMGW_API_KEY for the shipped gateway."""
    return f"{m.apps[m.llm.gateway].env_prefix}_API_KEY"


def app_env(ctx: Context, app: App) -> tuple[dict[str, str], set[str]]:
    """The environment a sandbox is created with, and which of its names carry secrets."""
    m, st = ctx.manifest, ctx.state
    env = dict(app.env)
    secret_names: set[str] = set()
    for name in app.generate:
        env[name] = st.secret(app.key, name)
        secret_names.add(name)
    if m.consumers() and app.key == m.llm.gateway:
        name = llm_admin_name(m)
        env[name] = st.secret(app.key, name)                                  # the operator key ecoctl creates one client per app with
        secret_names.add(name)
        # the default aliases the gateway seeds on its built-in echo provider, so every app works before a real provider exists
        env[f"{app.env_prefix}_DEFAULT_CHAT_ALIAS"] = m.llm.chat_alias
        env[f"{app.env_prefix}_DEFAULT_EMBED_ALIAS"] = m.llm.embed_alias
        env[f"{app.env_prefix}_DEFAULT_EMBED_DIM"] = str(m.llm.embed_dimensions)
    if app.key == "identity":
        env["IDENTITY_API_KEY"] = st.admin_key()
        secret_names.add("IDENTITY_API_KEY")
        return env, secret_names
    issuer = st.app("identity").get("url")
    if issuer:
        env[f"{app.env_prefix}_OIDC_ISSUER"] = issuer
        if app.web:
            env[f"{app.env_prefix}_OIDC_CLIENT_ID"] = app.key
            sec = st.app(app.key).get("client_secret")
            if sec:
                env[f"{app.env_prefix}_OIDC_CLIENT_SECRET"] = sec
                secret_names.add(f"{app.env_prefix}_OIDC_CLIENT_SECRET")
        if app.service:
            env[f"{app.env_prefix}_SERVICE_CLIENT_ID"] = app.service_client_id
            sec = st.app(app.key).get("service_secret")
            if sec:
                env[f"{app.env_prefix}_SERVICE_CLIENT_SECRET"] = sec
                secret_names.add(f"{app.env_prefix}_SERVICE_CLIENT_SECRET")
    for callee, var in m.url_vars(app).items():                    # `calls` and `uses`: their addresses, so a recreated callee needs this app recreated
        url = st.app(callee).get("url")
        if url:
            env[var] = url
    if app.llm:
        # the names TrustDesk and PagerLens read (config.py / embedcfg.py); environment settings are trusted by the apps, so the
        # gateway's public https URL needs no allow-private switch
        gw_url = st.app(m.llm.gateway).get("url")
        if gw_url:
            env["LLM_BASE_URL"] = f"{gw_url}/v1"
        env["LLM_CHAT_MODEL"] = m.llm.chat_alias
        env["EMBEDDING_MODEL"] = m.llm.embed_alias
        env["EMBEDDING_DIM"] = str(m.llm.embed_dimensions)
        key = st.app(app.key).get("llm_key")
        if key:
            env["LLM_API_KEY"] = key
            secret_names.add("LLM_API_KEY")
    return env, secret_names


def web_spec(app: App) -> dict:
    return {"client_id": app.key, "name": app.name, "kind": "web", "description": app.description, "redirect_uris": [PENDING_REDIRECT],
            "post_logout_uris": [], "allowed_scopes": [], "allowed_audiences": [], "roles": [{"name": r.name, "description": r.description} for r in app.roles],
            "default_roles": list(app.default_roles), "url": ""}


def service_spec(app: App) -> dict:
    scopes = [s for scopes in app.calls.values() for s in scopes]
    return {"client_id": app.service_client_id, "name": f"{app.name} (service)", "kind": "service", "description": f"{app.name} calling other apps",
            "allowed_scopes": scopes, "allowed_audiences": sorted(app.calls), "roles": [], "default_roles": [], "redirect_uris": [], "post_logout_uris": [], "url": ""}


def ecoctl_spec(m: Manifest) -> dict:
    """ecoctl's own service client: the admin scope of both ends of every `connect`, so it can manage merchants and push settings."""
    scopes, audiences = m.connect_scopes()
    return {"client_id": "ecoctl", "name": "ecoctl (service)", "kind": "service", "description": "ecoctl connecting apps to each other",
            "allowed_scopes": scopes, "allowed_audiences": audiences, "roles": [], "default_roles": [], "redirect_uris": [], "post_logout_uris": [], "url": ""}


def _register_ecoctl(ctx: Context) -> str:
    entry = ctx.state.app("ecoctl")
    r = ctx.identity().ensure_app(ecoctl_spec(ctx.manifest), known_secret=bool(entry.get("service_secret")), rotate=ctx.rotate)
    if r.secret:
        entry["service_secret"] = r.secret
    ctx.state.save()
    return "service client ecoctl" + (" (secret issued)" if r.secret else "")


def _deploy(ctx: Context, app: App) -> str:
    """Instance, extensions, sandbox, URL. Existing instances and sandboxes are kept; a changed environment is reported, not applied."""
    m, st, cli = ctx.manifest, ctx.state, ctx.agentpg
    entry = st.app(app.key)
    if not entry.get("instance_id"):
        entry["instance_id"] = cli.instance_id(m.instance_name(app.key))
        if not entry["instance_id"]:
            token = token_from(cli.run(AgentPG.create_instance(m.instance_name(app.key), m.size)))
            if token:
                entry["db_key"] = token      # also the database password; AgentPG shows it only now
            entry["instance_id"] = cli.instance_id(m.instance_name(app.key))
        if not entry["instance_id"]:
            raise AgentPGError(f"created instance {m.instance_name(app.key)} but could not find its id in `agentpg instances list`")
        st.save()
    if not entry.get("extensions_done"):
        cli.wait_instance(entry["instance_id"])
        for ext in app.extensions:
            cli.run(AgentPG.extension(entry["instance_id"], ext))
        entry["extensions_done"] = True
        st.save()
    if entry.get("sandbox_id") and entry["sandbox_id"] not in cli.sandbox_ids():           # deleted behind our back: start over
        for k in ("sandbox_id", "url", "env_hash"):
            entry.pop(k, None)
        st.save()
    env, secret_names = app_env(ctx, app)
    outcome = []
    if not entry.get("sandbox_id"):
        cli.wait_instance(entry["instance_id"])
        entry["sandbox_id"] = cli.sandbox_id(m.sandbox_name(app.key))
        if not entry["sandbox_id"]:
            cli.run(AgentPG.create_sandbox(m.sandbox_name(app.key), m.image(app), entry["instance_id"], app.port, env, secret_names,
                                           auto_pause=m.pause_enabled(app), plan=m.plan_for(app)))
            entry["sandbox_id"] = cli.sandbox_id(m.sandbox_name(app.key))
            entry["env_hash"] = _env_hash(env)
            outcome.append("created")
        else:
            outcome.append("found")
    elif entry.get("env_hash") and entry["env_hash"] != _env_hash(env):
        outcome.append("exists with a different environment: delete the sandbox and re-run to apply it (the database keeps its data)")
    if not entry["sandbox_id"]:
        raise AgentPGError(f"could not find sandbox {m.sandbox_name(app.key)} in `agentpg sandbox list`")
    entry["url"] = cli.wait_url(entry["sandbox_id"])
    st.save()
    return f"{entry['url']} ({', '.join(outcome) or 'unchanged'})"


def _register(ctx: Context, app: App) -> str:
    st, ident = ctx.state, ctx.identity()
    entry = st.app(app.key)
    notes = []
    if app.web:
        r = ident.ensure_app(web_spec(app), known_secret=bool(entry.get("client_secret")), rotate=ctx.rotate)
        if r.secret:
            entry["client_secret"] = r.secret
            notes.append("web client registered" if not entry.get("registered") else "secret issued")
        entry["registered"] = True
    if app.service:
        r = ident.ensure_app(service_spec(app), known_secret=bool(entry.get("service_secret")), rotate=ctx.rotate)
        if r.secret:
            entry["service_secret"] = r.secret
        notes.append(f"service client {app.service_client_id}")
    st.save()
    return ", ".join(notes) or "registered"


def _llm_client(ctx: Context, app: App) -> str:
    """One gateway client per app, named after it, allowed the two default aliases and limited by the manifest. Its key is kept in the
    state file (the gateway shows it once); a lost key is replaced by rotating the client's active key."""
    m, entry = ctx.manifest, ctx.state.app(app.key)
    gw = ctx.gateway()
    gw.wait_ready()
    lim = m.limits(app.key)
    res = gw.ensure_client(app.key, f"{app.name} (created by ecoctl)", list(lim.aliases), lim.rpm, lim.budget, lim.budget_mode,
                           known_key=bool(entry.get("llm_key")), rotate=ctx.rotate, default_aliases=(m.llm.chat_alias, m.llm.embed_alias))
    note = res.note
    if res.key:
        entry["llm_key"] = res.key
        if entry.get("sandbox_id"):
            note += f"; the running app still holds the old key: run `ecoctl recreate {app.key}`"
    ctx.state.save()
    return f"gateway client '{app.key}': {note}"


def _finish_urls(ctx: Context, app: App) -> str:
    url = ctx.state.app(app.key)["url"]
    if app.web:
        ctx.identity().set_urls(app.key, url)
    return f"{app.key} -> {url}"


def build(ctx: Context) -> list[Step]:
    m = ctx.manifest
    steps: list[Step] = []
    ident = m.identity
    cmd_env = dict(ident.env)
    steps.append(Step("Identity provider: database and sandbox",
                      [AgentPG.create_instance(m.instance_name("identity"), m.size).display(),
                       *[AgentPG.extension("<instance-id>", e).display() for e in ident.extensions],
                       AgentPG.create_sandbox(m.sandbox_name("identity"), m.image(ident), "<instance-id>", ident.port,
                                              {**cmd_env, "IDENTITY_API_KEY": "<generated>"}, {"IDENTITY_API_KEY"},
                                              auto_pause=m.pause_enabled(ident), plan=m.plan_for(ident)).display()],
                      lambda c: _deploy(c, c.manifest.identity)))
    steps.append(Step("Identity provider: wait until it is ready and pin its issuer URL",
                      ["GET <identity>/readyz", "GET <identity>/.well-known/openid-configuration  (the first request fixes the issuer)"],
                      lambda c: _ready(c)))
    selected = m.closure(ctx.only) if ctx.only else set(m.apps)
    for app in m.order():
        if app.key not in selected:
            continue
        detail = []
        if app.web:
            detail.append(f"identity: register web client '{app.key}' with roles {', '.join(r.name for r in app.roles) or '(none)'}")
        if app.service:
            detail.append(f"identity: register service client '{app.service_client_id}' for " +
                          "; ".join(f"{callee}: {' '.join(scopes)}" for callee, scopes in sorted(app.calls.items())))
        steps.append(Step(f"{app.name}: register with the identity provider", detail, (lambda a: lambda c: _register(c, a))(app)))
        if not app.deploy:
            continue
        if app.llm:
            lm = m.limits(app.key)
            steps.append(Step(f"{app.name}: LLM gateway client and key",
                              [f"gateway {m.llm.gateway}: ensure client '{app.key}' (aliases {', '.join(lm.aliases)}; {lm.rpm} requests/min; " +
                               (f"{lm.budget:,} tokens/month, {lm.budget_mode}" if lm.budget else "no token budget") + ")",
                               "an existing client keeps its limits (`ecoctl llm sync` changes them) but gets any missing default alias",
                               "keep its key in the state file (shown once by the gateway; rotated if lost)"],
                              (lambda a: lambda c: _llm_client(c, a))(app)))
        env, sec = app_env(ctx, app)
        placeholders = {k: ("<generated>" if k in sec else v) for k, v in env.items()}
        placeholders.setdefault(f"{app.env_prefix}_OIDC_ISSUER", "<identity url>")
        if app.web:
            placeholders[f"{app.env_prefix}_OIDC_CLIENT_ID"] = app.key
            placeholders.setdefault(f"{app.env_prefix}_OIDC_CLIENT_SECRET", "<generated>")
        if app.service:
            placeholders[f"{app.env_prefix}_SERVICE_CLIENT_ID"] = app.service_client_id
            placeholders.setdefault(f"{app.env_prefix}_SERVICE_CLIENT_SECRET", "<generated>")
        for callee, var in m.url_vars(app).items():
            placeholders.setdefault(var, f"<{callee} url>")
        for g in app.generate:
            placeholders[g] = "<generated>"
        if app.llm:
            placeholders.setdefault("LLM_BASE_URL", f"<{m.llm.gateway} url>/v1")
            placeholders["LLM_API_KEY"] = "<generated>"
        if m.consumers() and app.key == m.llm.gateway:
            placeholders[llm_admin_name(m)] = "<generated>"
        deploy = [AgentPG.create_instance(m.instance_name(app.key), m.size).display(),
                  *[AgentPG.extension("<instance-id>", e).display() for e in app.extensions],
                  AgentPG.create_sandbox(m.sandbox_name(app.key), m.image(app), "<instance-id>", app.port, placeholders,
                                         {k for k, v in placeholders.items() if v == "<generated>"}, auto_pause=m.pause_enabled(app),
                                         plan=m.plan_for(app)).display()]
        steps.append(Step(f"{app.name}: database and sandbox", deploy, (lambda a: lambda c: _deploy(c, a))(app)))
        if app.web:
            steps.append(Step(f"{app.name}: point its sign-in at the real URL", [f"identity: set redirect URI to <{app.key} url>/auth/callback and its launcher URL"],
                              (lambda a: lambda c: _finish_urls(c, a))(app)))
    for g in m.groups:
        if not any(t in selected or (t == "*" and selected) for t in g.grants):
            continue
        steps.append(Step(f"Access: group '{g.name}'", [f"{target}: {role}" for target, role in g.grants.items()], (lambda grp: lambda c: _group(c, grp))(g)))
    connections = [(a, peer) for a in m.order() if a.key in selected and a.deploy for peer in a.connect]
    if connections:
        scopes, audiences = m.connect_scopes()
        steps.append(Step("ecoctl: register its service client", [f"identity: register service client 'ecoctl' for {', '.join(scopes)} (audiences {', '.join(audiences)})"],
                          _register_ecoctl))
    for a, peer in connections:
        merchant = a.connect[peer].merchant
        steps.append(Step(f"{a.name}: connect to {m.apps[peer].name}", [
            f"ecoctl service tokens for {peer}.admin and {a.key}.admin (client_credentials)",
            f"{peer}: find or create merchant '{merchant}'; managed allowed origins = <{a.key} url> only",
            f"{peer}: two keys ecoctl created (ecoctl:{a.key}:secret / :publishable); kept in the state file, created again when lost",
            f"{peer}: webhook <{a.key} url>/webhooks/paybroker (the signing secret rotates only when the URL changed or the secret is lost)",
            f"{a.key}: push base URL, keys and webhook secret (only fields whose fingerprint differs), then run its connection test"],
            (lambda ak, pk: lambda c: connect_step(c, ak, pk))(a.key, peer)))
    return steps


def _ready(ctx: Context) -> str:
    ident = ctx.identity()
    ident.wait_ready()
    return f"issuer {ident.pin_issuer()}"


def _group(ctx: Context, g) -> str:
    ident, m = ctx.identity(), ctx.manifest
    ident.ensure_group(g.name, g.description)
    done = 0
    for target, role in g.grants.items():
        chosen = m.closure(ctx.only) if ctx.only else set(m.apps)
        for key in ([k for k, a in m.apps.items() if k in chosen and a.web and role in {r.name for r in a.roles}] if target == "*" else [target]):
            if key not in chosen:
                continue
            done += ident.ensure_grant(g.name, key, role)
    return f"{done} grant(s) added"
