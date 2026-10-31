"""Sealed boot, the operator's side (`agentpg.seal: true`).

Nothing an app needs to start is put in its sandbox environment in the clear. ecoctl collects the app's secrets (database URL, client
secrets, API keys, LLM key) into one blob, encrypted with the operator's passphrase (agentpg-kit `apgkit.sealed`), and creates the sandbox
with that blob as `APGKIT_SEALED` plus the non-secret settings. The app starts sealed. `ecoctl unseal` (and `apply`, right after it creates
a sandbox) sends the passphrase, encrypted to a key that exists only in that process, and the app opens the blob, connects, unlocks its
vault with the same passphrase and starts serving. After a platform restart the app is sealed again until `ecoctl unseal`.

The passphrase comes from ECOCTL_SEAL_PASSPHRASE or a prompt. It is never written to the state file, a sandbox, a log or an error message.
"""
import getpass
import os
import time
from urllib.parse import quote, urlsplit

import httpx

from .agentpg import AgentPG, AgentPGError
from .mcp import POSTGRES_URL, parse_connection

ENV_PASSPHRASE = "ECOCTL_SEAL_PASSPHRASE"
BLOB_ENV = "APGKIT_SEALED"


class SealError(RuntimeError):
    pass


def _kit():
    """agentpg-kit's sealing module (an optional dependency: `pip install agentpg-ecosystem[seal]`), needed only for a sealed fleet."""
    try:
        from apgkit import sealed
    except ImportError:
        raise SealError("sealed boot needs agentpg-kit >= 0.4.1: pip install 'agentpg-ecosystem[seal]'") from None
    return sealed


def passphrase(ctx, *, creating: bool) -> str:
    """The operator's passphrase: the environment, the copy already asked for in this run, else a prompt (twice when a blob is being made)."""
    if os.environ.get(ENV_PASSPHRASE):
        pw = os.environ[ENV_PASSPHRASE]
    elif getattr(ctx, "_seal_pw", None):
        return ctx._seal_pw
    else:
        ask = ctx.ask_password or getpass.getpass
        pw = ask("Seal passphrase (not stored anywhere; at least 20 characters, `ecoctl seal-passphrase` makes one): ")
        if creating and ask("Again: ") != pw:
            raise SealError("the two entries differ")
    try:
        _kit().check_passphrase(pw)
    except _kit().SealError as exc:
        raise SealError(str(exc)) from None
    ctx._seal_pw = pw
    return pw


def database_url(ctx, app) -> str | None:
    """The database URL for the app: host, port and database from `agentpg describe`, with the instance key ecoctl holds (kept in the state file, shown by
    AgentPG only once). None when either is unavailable (the app then falls back to the platform's PG* settings; the outcome says so)."""
    entry = ctx.state.app(app.key)
    key, iid = entry.get("db_key"), entry.get("instance_id")
    if not key or not iid:
        return None
    try:
        text = ctx.agentpg.run(AgentPG.connect_info(iid))
    except AgentPGError:
        return None
    conn = parse_connection(text)
    m = POSTGRES_URL.search(text)
    user = urlsplit(m.group(0)).username if m else None
    if not conn or not user:
        return None
    q = f"?{conn['query']}" if conn.get("query") else ""
    return f"postgresql://{quote(user, safe='')}:{quote(key, safe='')}@{conn['host']}:{conn['port']}/{quote(conn['dbname'], safe='')}{q}"


def seal_env(ctx, app, env: dict[str, str], secret_names: set[str]) -> tuple[dict[str, str], set[str], list[str]]:
    """Split `env` for a sealed sandbox: names in `secret_names` (and the database URL) go into the blob; the rest stays in the environment.
    Returns (environment to create the sandbox with, the names in it that are secret, notes)."""
    notes: list[str] = []
    payload = {k: v for k, v in env.items() if k in secret_names}
    payload.pop(app.encryption_key_name, None)                     # the passphrase is the only unlock secret
    dsn = database_url(ctx, app)
    if dsn:
        payload["DATABASE_URL"] = dsn
    else:
        notes.append("DATABASE_URL not sealed (no instance key or connection string): the app uses the platform's PG* settings")
    if not payload:
        raise SealError(f"{app.key}: nothing to seal")
    blob = _kit().seal(payload, passphrase(ctx, creating=not ctx.state.app(app.key).get("sandbox_id")))
    public = {k: v for k, v in env.items() if k not in secret_names and k != app.encryption_key_name}
    public[BLOB_ENV] = blob
    return public, {BLOB_ENV}, notes


def state_of(url: str, http: httpx.Client | None = None) -> str | None:
    """`sealed`, `unsealed`, `disabled` (an app without sealing answers 404 here) or None when unreachable. Never raises."""
    try:
        r = (http or httpx).get(url.rstrip("/") + "/_seal/status", timeout=10)
    except httpx.HTTPError:
        return None
    if r.status_code == 404:
        return "disabled"
    try:
        return r.json().get("state") if r.status_code == 200 else None
    except ValueError:
        return None


def unseal(ctx, app, *, timeout: float = 240, interval: float = 3) -> str:
    """Open the app with the passphrase. Returns a one-line outcome. A wrong passphrase, or an app that cannot finish starting, raises."""
    url = ctx.state.app(app.key).get("url")
    if not url:
        raise SealError(f"{app.key} has no URL yet")
    http = ctx.http or httpx.Client(timeout=60, follow_redirects=False)
    sleep, clock = ctx.vault_sleep or time.sleep, ctx.vault_clock or time.monotonic
    deadline = clock() + timeout
    while True:
        st = state_of(url, http)
        if st in {"unsealed", "disabled"}:
            return "already open" if st == "unsealed" else "sealing is not enabled in this app"
        if st == "sealed":
            break
        if clock() > deadline:
            raise SealError(f"{app.key} did not answer {url}/_seal/status within {int(timeout)} s")
        sleep(interval)
    key = http.get(url.rstrip("/") + "/_seal/key").json()
    fp = key.get("fingerprint", "?")
    pw = passphrase(ctx, creating=False)
    r = http.post(url.rstrip("/") + "/_seal/unseal", json=_kit().make_envelope(key["public_key"], pw))
    if r.status_code == 429:
        raise SealError(f"{app.key}: too many failed attempts; wait a minute")
    if r.status_code == 401:
        raise SealError(f"{app.key}: the passphrase did not open the blob (it is not the one this sandbox was sealed with)")
    if r.status_code >= 400:
        try:
            detail = str(r.json().get("detail", ""))[:160]
        except ValueError:
            detail = ""
        raise SealError(f"{app.key}: unseal failed: HTTP {r.status_code} {detail}".strip())
    return f"unsealed (key fingerprint {fp})"
