"""The operator's side of `vault: password`.

An app with `vault: password` (the identity provider's signing keys, the LLM gateway's provider keys) has no platform wrap of its vault: no
`<PREFIX>_ENCRYPTION_KEY` is put in its sandbox environment, so nothing the platform stores or injects can open the data key. The vault is created
and opened with an admin password that the operator holds. This module does that over the apps' documented HTTP API, with the static operator key
ecoctl already holds (the same one it registers clients with):

    GET  /api/session         public: {"vault": {"initialized", "unlocked", "auto_unlock", "reason"}}
    POST /api/admin-password  {"new": ...}   first time only: creates the data key wrapped under the password alone, and unlocks
    POST /api/vault/unlock    {"password": ...}   after every restart

Where the password comes from, in this order: the ECOCTL_ADMIN_PASSWORD environment variable, a prompt (`--admin-password-prompt`; never kept), the
state file (`.ecosystem/state.json`, git-ignored, mode 0600). When none exists and the vault does not exist yet, one is generated (token_urlsafe(24)),
kept in the state file and shown once. A vault that already exists never gets a made-up password. The password is never put in a sandbox environment,
a log line, an error message or `ecoctl env`.
"""
import getpass
import os
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx

ENV_PASSWORD = "ECOCTL_ADMIN_PASSWORD"


class VaultError(RuntimeError):
    pass


@dataclass(frozen=True)
class VaultState:
    initialized: bool
    unlocked: bool
    auto_unlock: bool = False          # a platform wrap exists and the platform key is set
    reason: str | None = None          # why a locked vault is locked (the kit's words: not_initialized, no_platform_key, ...)


def parse_state(body) -> VaultState | None:
    """The vault part of a `GET /api/session` (or /readyz) answer; None when the app does not report one."""
    v = body.get("vault") if isinstance(body, dict) else None
    if isinstance(v, dict) and "unlocked" in v:
        return VaultState(bool(v.get("initialized")), bool(v.get("unlocked")), bool(v.get("auto_unlock")), v.get("reason"))
    return None


def probe(url: str) -> VaultState | None:
    """Best effort for `ecoctl status`: the vault state an app reports, or None (unreachable, or an app that does not say). Never raises."""
    try:
        r = httpx.get(url.rstrip("/") + "/api/session", timeout=10)
        return parse_state(r.json()) if r.status_code == 200 else None
    except (httpx.HTTPError, ValueError):
        return None


def describe(mode: str, st: VaultState | None) -> tuple[str, bool]:
    """(what `ecoctl status` shows, is it fine). A password vault that is not open means the app cannot do its job (identity cannot sign)."""
    if st is None:
        return "?", mode != "password"
    if mode == "password":
        if st.unlocked:
            return "unlocked", True
        return ("not initialised" if not st.initialized else "LOCKED"), False
    if st.unlocked:
        return ("auto" if st.auto_unlock else "unlocked"), True
    return ("empty" if not st.initialized else "LOCKED"), True        # a platform vault retries by itself; not counted as a failure


class VaultAdmin:
    def __init__(self, base_url: str, operator_key: str, http: httpx.Client | None = None, sleep: Callable[[float], None] | None = None,
                 clock: Callable[[], float] | None = None):
        self.base = base_url.rstrip("/")
        self.http = http or httpx.Client(timeout=20, follow_redirects=False)
        self.headers = {"Authorization": f"Bearer {operator_key}"}
        self.sleep, self.clock = sleep or time.sleep, clock or time.monotonic

    def state(self) -> VaultState:
        r = self.http.get(self.base + "/api/session")
        if r.status_code != 200:
            raise VaultError(f"GET /api/session: HTTP {r.status_code}")
        st = parse_state(r.json())
        if st is None:
            raise VaultError("GET /api/session does not report a vault state: this app version has no admin-password vault")
        return st

    def wait_state(self, timeout: float = 180, interval: float = 3) -> VaultState:
        """A sandbox that just became `running` (or whose database is waking) may need a moment to answer."""
        deadline = self.clock() + timeout
        while True:
            try:
                return self.state()
            except (httpx.HTTPError, VaultError, ValueError) as exc:
                if isinstance(exc, VaultError) and "does not report" in str(exc):
                    raise
                seen = type(exc).__name__ if not isinstance(exc, VaultError) else str(exc)
            if self.clock() > deadline:
                raise VaultError(f"{self.base} did not answer /api/session within {int(timeout)} s ({seen})")
            self.sleep(interval)

    def _post(self, path: str, body: dict) -> httpx.Response:
        return self.http.post(self.base + path, headers=self.headers, json=body)

    @staticmethod
    def _detail(r: httpx.Response) -> str:
        try:
            return str(r.json().get("detail", ""))[:200]
        except (ValueError, AttributeError):
            return ""

    def initialize(self, password: str) -> None:
        """First time: the data key is created and wrapped under this password alone (no platform key is set), and the app unlocks."""
        r = self._post("/api/admin-password", {"new": password})
        if r.status_code in {401, 403}:
            raise VaultError(f"POST /api/admin-password: HTTP {r.status_code} {self._detail(r)}: the app does not accept ecoctl's operator key (it was created without it: `ecoctl recreate <app>`)")
        if r.status_code >= 400:
            raise VaultError(f"POST /api/admin-password: HTTP {r.status_code} {self._detail(r)}".strip())

    def unlock(self, password: str, who: str, source: str) -> None:
        r = self._post("/api/vault/unlock", {"password": password})
        if r.status_code == 403 and "password" in self._detail(r):
            raise VaultError(f"{who}: the admin password ({source}) did not open the vault. Either it is not the password the vault was created with, or the vault "
                             "was created in platform mode and has no password wrap (see docs/DEPLOY.md, moving an existing fleet)")
        if r.status_code in {401, 403}:
            raise VaultError(f"POST /api/vault/unlock: HTTP {r.status_code}: the app does not accept ecoctl's operator key (`ecoctl recreate {who}`)")
        if r.status_code >= 400:
            raise VaultError(f"POST /api/vault/unlock: HTTP {r.status_code} {self._detail(r)}".strip())


def operator_password(ctx, key: str, st: VaultState, *, allow_generate: bool) -> tuple[str, str]:
    """(password, where it came from). `ctx` is plan.Context: state, prompt_password, ask_password, announce."""
    env = os.environ.get(ENV_PASSWORD)
    if env:
        return env, ENV_PASSWORD
    if ctx.prompt_password:
        ask = ctx.ask_password or getpass.getpass
        pw = ask(f"Admin password for the {key} vault: ")
        if not pw:
            raise VaultError("an empty admin password was entered")
        if not st.initialized and ask(f"Again, to create the {key} vault with it: ") != pw:
            raise VaultError("the two entries differ; nothing was created")
        return pw, "prompt"
    kept = ctx.state.vault_admin_password()
    if kept:
        return kept, "the state file"
    if st.initialized:
        raise VaultError(f"the {key} vault is protected by an admin password this machine does not have: set {ENV_PASSWORD}, or add --admin-password-prompt "
                         "(a made-up password could never open it)")
    if not allow_generate:
        raise VaultError(f"the {key} vault does not exist yet: run `ecoctl apply --execute`")
    pw = secrets.token_urlsafe(24)
    ctx.state.keep_vault_admin_password(pw)
    ctx.state.save()                                   # before it is used: a crash between creating the vault and saving must not lose it
    ctx.announce(f"\n  Admin password for the vaults of the apps with `vault: password` (generated; kept in {ctx.state.path}, mode 0600; shown only now):\n"
                 f"    {pw}\n  Store it in your password manager: after a platform restart `ecoctl unlock` needs it, and it is not stored on the platform.\n")
    return pw, "generated"


def ensure_unlocked(ctx, app, *, allow_init: bool = True) -> str:
    """Create (first run) or open (after a restart) the vault of `app` with the operator's admin password. Returns a one-line outcome."""
    admin = ctx.vault_admin(app)
    st = admin.wait_state()
    if st.unlocked:
        return "vault already unlocked"
    if not st.initialized:
        if not allow_init:
            raise VaultError(f"the {app.key} vault does not exist yet: run `ecoctl apply --execute`")
        pw, src = operator_password(ctx, app.key, st, allow_generate=True)
        admin.initialize(pw)
        outcome = f"vault created with the admin password ({src}) and unlocked"
    else:
        pw, src = operator_password(ctx, app.key, st, allow_generate=False)
        admin.unlock(pw, app.key, src)
        outcome = f"vault unlocked with the admin password ({src})"
    if not admin.state().unlocked:
        raise VaultError(f"the {app.key} vault is still locked after {'creating' if not st.initialized else 'unlocking'} it")
    stale = ctx.state.app(app.key).get("generated", {}).get(app.encryption_key_name)
    return outcome + (f"; the state file still holds an unused {app.encryption_key_name} from an earlier setup (remove it)" if stale else "")
