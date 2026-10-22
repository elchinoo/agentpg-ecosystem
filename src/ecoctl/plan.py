"""The plan: an ordered list of steps. Printing it changes nothing; running it creates instances and sandboxes on AgentPG and
registers apps with the identity provider. Every step checks what already exists, so a second run converges."""
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx

from .agentpg import AgentPG, AgentPGError
from .identity_client import PENDING_REDIRECT, Identity, IdentityError
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
    _identity: Identity | None = None

    def identity(self) -> Identity:
        if self._identity is None:
            url = self.state.app("identity").get("url")
            if not url:
                raise IdentityError("the identity provider has no URL yet")
            self._identity = Identity(url, self.state.admin_key(), self.http)
        return self._identity


def _env_hash(env: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(env, sort_keys=True).encode()).hexdigest()[:12]


def app_env(ctx: Context, app: App) -> tuple[dict[str, str], set[str]]:
    """The environment a sandbox is created with, and which of its names carry secrets."""
    m, st = ctx.manifest, ctx.state
    env = dict(app.env)
    secret_names: set[str] = set()
    for name in app.generate:
        env[name] = st.secret(app.key, name)
        secret_names.add(name)
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
    for callee in app.calls:
        url = st.app(callee).get("url")
        if url:
            env[app.url_env.get(callee, f"{m.apps[callee].env_prefix}_URL")] = url
    return env, secret_names


def web_spec(app: App) -> dict:
    return {"client_id": app.key, "name": app.name, "kind": "web", "description": app.description, "redirect_uris": [PENDING_REDIRECT],
            "post_logout_uris": [], "allowed_scopes": [], "allowed_audiences": [], "roles": [{"name": r.name, "description": r.description} for r in app.roles],
            "default_roles": list(app.default_roles), "url": ""}


def service_spec(app: App) -> dict:
    scopes = [s for scopes in app.calls.values() for s in scopes]
    return {"client_id": app.service_client_id, "name": f"{app.name} (service)", "kind": "service", "description": f"{app.name} calling other apps",
            "allowed_scopes": scopes, "allowed_audiences": sorted(app.calls), "roles": [], "default_roles": [], "redirect_uris": [], "post_logout_uris": [], "url": ""}


def _deploy(ctx: Context, app: App) -> str:
    """Instance, extensions, sandbox, URL. Existing instances and sandboxes are kept; a changed environment is reported, not applied."""
    m, st, cli = ctx.manifest, ctx.state, ctx.agentpg
    entry = st.app(app.key)
    if not entry.get("instance_id"):
        entry["instance_id"] = cli.instance_id(m.instance_name(app.key))
        if not entry["instance_id"]:
            cli.run(AgentPG.create_instance(m.instance_name(app.key), m.size))
            entry["instance_id"] = cli.instance_id(m.instance_name(app.key))
        if not entry["instance_id"]:
            raise AgentPGError(f"created instance {m.instance_name(app.key)} but could not find its id in `agentpg instances list`")
        for ext in app.extensions:
            cli.run(AgentPG.extension(entry["instance_id"], ext))
        st.save()
    env, secret_names = app_env(ctx, app)
    outcome = []
    if not entry.get("sandbox_id"):
        entry["sandbox_id"] = cli.sandbox_id(m.sandbox_name(app.key))
        if not entry["sandbox_id"]:
            cli.run(AgentPG.create_sandbox(m.sandbox_name(app.key), m.image(app), entry["instance_id"], app.port, env, secret_names))
            entry["sandbox_id"] = cli.sandbox_id(m.sandbox_name(app.key))
            entry["env_hash"] = _env_hash(env)
            outcome.append("created")
        else:
            outcome.append("found")
    elif entry.get("env_hash") and entry["env_hash"] != _env_hash(env):
        outcome.append("exists with a different environment: delete the sandbox and re-run to apply it (the database keeps its data)")
    if not entry["sandbox_id"]:
        raise AgentPGError(f"could not find sandbox {m.sandbox_name(app.key)} in `agentpg sandbox list`")
    entry["url"] = cli.sandbox_url(entry["sandbox_id"]) or entry.get("url")
    if not entry["url"]:
        raise AgentPGError(f"sandbox {entry['sandbox_id']} has no URL yet (is it running? try `agentpg sandbox status {entry['sandbox_id']}`)")
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
                                              {**cmd_env, "IDENTITY_API_KEY": "<generated>"}, {"IDENTITY_API_KEY"}).display()],
                      lambda c: _deploy(c, c.manifest.identity)))
    steps.append(Step("Identity provider: wait until it is ready and pin its issuer URL",
                      ["GET <identity>/readyz", "GET <identity>/.well-known/openid-configuration  (the first request fixes the issuer)"],
                      lambda c: _ready(c)))
    for app in m.order():
        detail = []
        if app.web:
            detail.append(f"identity: register web client '{app.key}' with roles {', '.join(r.name for r in app.roles) or '(none)'}")
        if app.service:
            detail.append(f"identity: register service client '{app.service_client_id}' for " +
                          "; ".join(f"{callee}: {' '.join(scopes)}" for callee, scopes in sorted(app.calls.items())))
        steps.append(Step(f"{app.name}: register with the identity provider", detail, (lambda a: lambda c: _register(c, a))(app)))
        if not app.deploy:
            continue
        env, sec = app_env(ctx, app)
        placeholders = {k: ("<generated>" if k in sec else v) for k, v in env.items()}
        placeholders.setdefault(f"{app.env_prefix}_OIDC_ISSUER", "<identity url>")
        if app.web:
            placeholders[f"{app.env_prefix}_OIDC_CLIENT_ID"] = app.key
            placeholders.setdefault(f"{app.env_prefix}_OIDC_CLIENT_SECRET", "<generated>")
        if app.service:
            placeholders[f"{app.env_prefix}_SERVICE_CLIENT_ID"] = app.service_client_id
            placeholders.setdefault(f"{app.env_prefix}_SERVICE_CLIENT_SECRET", "<generated>")
        for callee in app.calls:
            placeholders.setdefault(app.url_env.get(callee, f"{m.apps[callee].env_prefix}_URL"), f"<{callee} url>")
        for g in app.generate:
            placeholders[g] = "<generated>"
        deploy = [AgentPG.create_instance(m.instance_name(app.key), m.size).display(),
                  *[AgentPG.extension("<instance-id>", e).display() for e in app.extensions],
                  AgentPG.create_sandbox(m.sandbox_name(app.key), m.image(app), "<instance-id>", app.port, placeholders,
                                         {k for k, v in placeholders.items() if v == "<generated>"}).display()]
        steps.append(Step(f"{app.name}: database and sandbox", deploy, (lambda a: lambda c: _deploy(c, a))(app)))
        if app.web:
            steps.append(Step(f"{app.name}: point its sign-in at the real URL", [f"identity: set redirect URI to <{app.key} url>/auth/callback and its launcher URL"],
                              (lambda a: lambda c: _finish_urls(c, a))(app)))
    for g in m.groups:
        steps.append(Step(f"Access: group '{g.name}'", [f"{target}: {role}" for target, role in g.grants.items()], (lambda grp: lambda c: _group(c, grp))(g)))
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
        for key in ([k for k, a in m.apps.items() if a.web and role in {r.name for r in a.roles}] if target == "*" else [target]):
            done += ident.ensure_grant(g.name, key, role)
    return f"{done} grant(s) added"
