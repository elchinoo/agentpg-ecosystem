"""Connect an app to a peer after both are up: for ShopFront and PayBroker, the merchant, its one allowed origin, two API keys, the webhook
and the shop's settings, so nothing is copied by hand and a changed sandbox address is followed on the next run.

`reconcile` is idempotent: it reads what exists and writes only what differs (a re-run on a connected fleet makes no write call). The
steps, each printing one line (created / kept / updated):

  1. service tokens   ecoctl's own service client gets `<peer>.admin` and `<app>.admin` tokens (client_credentials)
  2. merchant         found by its exact name in the manifest, created when missing (a 409 from a concurrent creation is a find)
  3. origins          the merchant's allowed origins are MANAGED: exactly the app's current public URL (others are replaced)
  4. keys             one secret and one publishable key that ecoctl created (`ecoctl:<app>:<kind>`); the values live only in the state
                      file; a key PayBroker no longer lists (or whose value we lost) is created again and the stale one deleted
  5. webhook          URL `<app url>/webhooks/paybroker`. Every PUT rotates the signing secret, so it is called only when the URL changed,
                      no secret is kept, PayBroker holds another URL, or the app's fingerprint of the secret differs from the kept one
  6. settings         base_url, keys and webhook secret pushed to the app, only the fields whose fingerprint differs
  7. test             the app's own connection test

Secrets are read from and written to the state file only; they are sent in a JSON body and never printed, logged or put in an error.
"""
from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx

from .connect_client import Conflict, ConnectError, PayBroker, ServiceTokens, ShopFront, check_url, fingerprint, summarise_checks

if TYPE_CHECKING:
    from .plan import Context

KINDS = ("secret", "publishable")
SETTINGS_SECRETS = ("publishable_key", "secret_key", "webhook_secret")
WEBHOOK_PATH = "/webhooks/paybroker"
ERRORS = (ConnectError, httpx.HTTPError)


def indent(line: str) -> None:
    print(f"      {line}", flush=True)


def _norm(url: str) -> str:
    return (url or "").rstrip("/")


@dataclass
class Clients:
    tokens: ServiceTokens
    pb: PayBroker
    shop: ShopFront
    app_url: str
    peer_url: str


def _clients(ctx: Context, app_key: str, peer_key: str, wait: float) -> Clients:
    m, st = ctx.manifest, ctx.state
    app, peer = m.apps[app_key], m.apps[peer_key]
    urls = {}
    for a in (app, peer):
        url = st.app(a.key).get("url")
        if not url:
            raise ConnectError(f"{a.name} has no URL yet: run `ecoctl apply --execute` first")
        urls[a.key] = check_url(url, a.name)
    ident, secret = st.app("identity").get("url"), st.app("ecoctl").get("service_secret")
    if not ident or not secret:
        raise ConnectError("ecoctl has no service client yet (it is registered by `ecoctl apply --execute`)")
    http = ctx.connect_http or httpx.Client(timeout=20, follow_redirects=False)
    kw = {}
    if ctx.connect_sleep:
        kw["sleep"] = ctx.connect_sleep
    if ctx.connect_clock:
        kw["clock"] = ctx.connect_clock
    tokens = ServiceTokens(ident, "ecoctl", secret, http, **({"clock": ctx.connect_clock} if ctx.connect_clock else {}))
    c = Clients(tokens, PayBroker(urls[peer_key], tokens, http, **kw), ShopFront(urls[app_key], tokens, http, audience=app_key, **kw), urls[app_key], urls[peer_key])
    if wait:
        c.pb.wait_ready(wait)
        c.shop.wait_ready(wait)
    return c


def _shop_has(settings: dict, field: str, value: str | None) -> bool:
    """Does the app hold exactly this secret? Only fingerprints are comparable; a missing one counts as different."""
    fps = settings.get("fingerprints") or {}
    return bool(value) and settings.get(f"has_{field}") is not False and fps.get(field) == fingerprint(value)


def reconcile(ctx: Context, app_key: str, peer_key: str = "paybroker", *, dry_run: bool = False, rotate: bool = False,
              emit: Callable[[str], None] = indent) -> bool:
    """Bring `app_key` and `peer_key` into the connected state. Returns whether anything was (or, with dry_run, would be) written.
    Raises ConnectError (progress is saved in the state file as it is made) when a step fails or the final test does not pass."""
    m, st = ctx.manifest, ctx.state
    app, name = m.apps[app_key], m.apps[app_key].connect[peer_key].merchant
    c = _clients(ctx, app_key, peer_key, wait=0 if dry_run else 180)
    rec = st.app(app_key).setdefault("connect", {}).setdefault(peer_key, {})
    would = "would " if dry_run else ""
    changed = False
    c.tokens.token(peer_key)
    c.tokens.token(app_key)
    emit(f"service tokens: ecoctl -> {peer_key}.admin, {app_key}.admin: ok")

    # 2. merchant
    merchant = c.pb.find_merchant(name)
    if merchant is None:
        changed = True
        if dry_run:
            emit(f"merchant '{name}': would create")
        else:
            try:
                merchant = c.pb.create_merchant(name)
                emit(f"merchant '{name}': created")
            except Conflict:                                                    # somebody created it between our find and create
                merchant = c.pb.find_merchant(name)
                if merchant is None:
                    raise ConnectError(f"PayBroker says merchant '{name}' exists (HTTP 409) but does not list it by that exact name") from None
                emit(f"merchant '{name}': kept (created concurrently)")
    else:
        emit(f"merchant '{name}': kept")
    mid = merchant["id"] if merchant else None
    if mid is not None and rec.get("merchant_id") != mid and not dry_run:
        rec["merchant_id"] = mid
        st.save()

    # 3. origins
    have = [_norm(o) for o in (merchant or {}).get("allowed_origins") or []]
    if merchant is not None and have == [c.app_url]:
        emit(f"origins: kept ({c.app_url})")
    else:
        changed = True
        if not dry_run:
            c.pb.set_origins(mid, [c.app_url])
        emit(f"origins: {would}update to {c.app_url}" + (f" (was: {', '.join(have)})" if have else ""))

    # 4. keys
    listed = {str(k.get("id")): k for k in (merchant or {}).get("keys") or []}
    fresh: set[str] = set()
    for kind in KINDS:
        kname = f"ecoctl:{app_key}:{kind}"
        mine = (rec.get("keys") or {}).get(kind) or {}
        if mine.get("value") and str(mine.get("id")) in listed and not rotate:
            emit(f"key {kname}: kept")
            continue
        why = "rotating" if rotate else "PayBroker no longer lists it" if mine.get("value") else "none kept"
        changed = True
        new_id = None
        if not dry_run:
            new_id, value = c.pb.create_key(mid, kind, kname)
            rec.setdefault("keys", {})[kind] = {"id": new_id, "value": value}
            st.save()                                                           # the value is shown once: keep it before anything else can fail
        fresh.add(f"{kind}_key")
        stale = [k for k in listed.values() if k.get("name") == kname and str(k.get("id")) != str(new_id)]
        for k in stale:
            if not dry_run:
                c.pb.delete_key(mid, str(k["id"]))
        emit(f"key {kname}: {would}create ({why})" + (f", {would}delete {len(stale)} stale" if stale else ""))

    # 5. webhook
    settings = c.shop.settings()
    url = c.app_url + WEBHOOK_PATH
    secret = rec.get("webhook_secret")
    pb_url = PayBroker.webhook_url(merchant) if merchant else None
    configured = PayBroker.webhook_configured(merchant) if merchant else None
    if rotate:
        reason = "rotating"
    elif not secret:
        reason = "no signing secret kept"
    elif rec.get("webhook_url") != url or (pb_url is not None and _norm(pb_url) != url):
        reason = "URL changed"
    elif configured is False:
        reason = "PayBroker has none"
    elif not _shop_has(settings, "webhook_secret", secret):
        reason = "the app's copy differs"
    else:
        reason = ""
    if reason:
        changed = True
        if not dry_run:
            rec["webhook_secret"] = c.pb.set_webhook(mid, url)
            rec["webhook_url"] = url
            st.save()
        fresh.add("webhook_secret")
        emit(f"webhook: {would}set {url} and rotate the signing secret ({reason})")
    else:
        emit(f"webhook: kept ({url})")

    # 6. settings
    fields: dict[str, str] = {}
    if _norm(settings.get("base_url") or "") != c.peer_url:
        fields["base_url"] = c.peer_url
    for f in SETTINGS_SECRETS:
        value = rec.get("webhook_secret") if f == "webhook_secret" else ((rec.get("keys") or {}).get(f.split("_")[0]) or {}).get("value")
        if f in fresh and dry_run:
            fields[f] = ""                                                      # a value that will exist; never shown
        elif value and not _shop_has(settings, f, value):
            fields[f] = value
    if fields:
        changed = True
        if not dry_run:
            c.shop.put_settings(fields)
        emit(f"{app.name} settings: {would}update {', '.join(sorted(fields))}")
    else:
        emit(f"{app.name} settings: kept")

    # 7. test
    if dry_run:
        emit("test: skipped (dry run)")
        return changed
    out = c.shop.test()
    summary = summarise_checks(out)
    if out.get("ok") is not True:
        raise ConnectError(f"{app.name}'s connection test to {peer_key} failed" + (f": {summary}" if summary else ""))
    emit("test: ok" + (f" ({summary})" if summary else ""))
    return changed


def connect_step(ctx: Context, app_key: str, peer_key: str) -> str:
    """A step of `apply`: never rotates (that is `ecoctl connect --rotate`); the lines are printed as they happen."""
    changed = reconcile(ctx, app_key, peer_key)
    return "connected" + ("" if changed else " (nothing to change)")


@dataclass
class Row:
    app: str
    peer: str
    merchant: str
    origin: str
    keys: str
    webhook: str
    test: str
    problems: int = 0


def connection_status(ctx: Context, app_key: str, peer_key: str = "paybroker") -> Row:
    """What `ecoctl status` shows for one connection. Only reads: the tokens, the merchant, the app's fingerprints and the app's own
    connection test (which changes nothing)."""
    name = ctx.manifest.apps[app_key].connect[peer_key].merchant
    row = Row(app_key, peer_key, name, "-", "-", "-", "-")
    try:
        c = _clients(ctx, app_key, peer_key, wait=0)
        merchant = c.pb.find_merchant(name)
        settings = c.shop.settings()
        rec = (ctx.state.app(app_key).get("connect") or {}).get(peer_key) or {}
        out = c.shop.test()
    except ERRORS as exc:
        row.test = f"unavailable: {type(exc).__name__}" if isinstance(exc, httpx.HTTPError) else str(exc)[:80]
        row.problems = 1
        return row
    row.merchant = f"{name}" if merchant else "MISSING"
    row.origin = "ok" if merchant and [_norm(o) for o in merchant.get("allowed_origins") or []] == [c.app_url] else "DIFFERS"
    listed = {str(k.get("id")) for k in (merchant or {}).get("keys") or []}
    held = rec.get("keys") or {}
    keys_ok = all((held.get(k) or {}).get("value") and str(held[k].get("id")) in listed and _shop_has(settings, f"{k}_key", held[k]["value"]) for k in KINDS)
    row.keys = "ok" if keys_ok and _norm(settings.get("base_url") or "") == c.peer_url else "DIFFERS"
    pb_url = PayBroker.webhook_url(merchant) if merchant else None
    hook_ok = bool(merchant) and rec.get("webhook_url") == c.app_url + WEBHOOK_PATH and _shop_has(settings, "webhook_secret", rec.get("webhook_secret")) \
        and (pb_url is None or _norm(pb_url) == c.app_url + WEBHOOK_PATH) and PayBroker.webhook_configured(merchant) is not False
    row.webhook = "ok" if hook_ok else "DIFFERS"
    row.test = "ok" if out.get("ok") is True else "FAILED" + (f" ({summarise_checks(out)})" if summarise_checks(out) else "")
    row.problems = sum(v not in {"ok"} for v in (row.origin, row.keys, row.webhook, row.test)) + (not merchant)
    return row


def print_status(ctx: Context, out=None) -> int:
    """The CONNECTIONS table of `ecoctl status`; returns the number of problems."""
    out = out or sys.stdout
    pairs = [(k, p) for k, a in ctx.manifest.apps.items() for p in a.connect if ctx.state.app(k).get("url") and ctx.state.app(p).get("url")]
    if not pairs:
        return 0
    print("\nCONNECTIONS", file=out)
    print(f"  {'app -> peer':<26}{'merchant':<16}{'origin':<10}{'keys':<10}{'webhook':<10}test", file=out)
    bad = 0
    for k, p in pairs:
        r = connection_status(ctx, k, p)
        print(f"  {k + ' -> ' + p:<26}{r.merchant[:14]:<16}{r.origin:<10}{r.keys:<10}{r.webhook:<10}{r.test}", file=out)
        bad += r.problems
    return bad
