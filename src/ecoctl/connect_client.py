"""Every HTTP call `ecoctl connect` makes, in one place: the identity provider's token endpoint, PayBroker's admin API and ShopFront's
admin API. If a service's field names change, this is the only file to adjust (the assumed names are collected in the constants below).

Secrets travel only in an Authorization header or a JSON body, over https (or a localhost address); they never appear in a URL, an
exception message or a log line: errors name the method, the path and the HTTP status, plus the service's short `detail` text.
"""
import hashlib
import time
from collections.abc import Callable
from urllib.parse import quote, urlsplit

import httpx

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}

# ---- the field names assumed from the service contracts ---------------------------------------------------------------------
PB_SECRET_FIELDS = ("signing_secret", "secret", "webhook_secret")          # PUT /api/merchants/{id}/webhook: the new signing secret
PB_KEY_FIELDS = ("key", "value", "secret")                                  # POST /api/merchants/{id}/keys: the key value (maybe {"key": {"id", "key"}})
SHOP_FP = {"publishable_key": "publishable_key", "secret_key": "secret_key", "webhook_secret": "webhook_secret"}    # keys of `fingerprints`


class ConnectError(RuntimeError):
    pass


class Conflict(ConnectError):
    """HTTP 409: the thing already exists (a concurrent creation)."""


def fingerprint(value: str) -> str:
    """The first 8 hex of the SHA-256 ShopFront reports instead of a secret, so values can be compared without being shown."""
    return hashlib.sha256(value.encode()).hexdigest()[:8]


def check_url(url: str, what: str) -> str:
    """The base URL of a service, without a trailing slash; plain http is refused unless it is localhost."""
    parts = urlsplit(url or "")
    host = parts.hostname or ""
    if parts.scheme == "https" and host:
        return url.rstrip("/")
    if parts.scheme == "http" and host in LOCAL_HOSTS:
        return url.rstrip("/")
    raise ConnectError(f"the {what} URL must be https (plain http is accepted for localhost only), got {parts.scheme or 'none'}://{host}")


def _detail(r: httpx.Response) -> str:
    try:
        d = r.json().get("detail")
    except (ValueError, AttributeError):
        return ""
    return d[:120] if isinstance(d, str) else ""


def _first(doc: dict, names: tuple[str, ...]) -> str | None:
    for n in names:
        if isinstance(doc.get(n), str) and doc[n]:
            return doc[n]
    return None


class ServiceTokens:
    """Access tokens of ecoctl's own service client, one per audience (`<peer>.admin`), cached until shortly before they expire."""

    def __init__(self, identity_url: str, client_id: str, secret: str, http: httpx.Client, clock: Callable[[], float] = time.monotonic):
        self.url = check_url(identity_url, "identity")
        self.client_id, self._secret, self.http, self.clock = client_id, secret, http, clock
        self._cache: dict[str, tuple[str, float]] = {}
        self.fetched = 0

    def token(self, audience: str) -> str:
        have = self._cache.get(audience)
        if have and have[1] - self.clock() > 30:
            return have[0]
        r = self.http.post(f"{self.url}/oauth/token", data={"grant_type": "client_credentials", "scope": f"{audience}.admin", "audience": audience},
                           auth=(self.client_id, self._secret))
        if r.status_code != 200:
            raise ConnectError(f"the identity provider refused the {self.client_id} service client for {audience}.admin (HTTP {r.status_code}{' ' + _detail(r) if _detail(r) else ''}); "
                               "run `ecoctl apply --execute` to register it again")
        doc = r.json()
        self._cache[audience] = (doc["access_token"], self.clock() + float(doc.get("expires_in", 300)))
        self.fetched += 1
        return self._cache[audience][0]

    def forget(self, audience: str) -> None:
        self._cache.pop(audience, None)


class _Admin:
    audience = ""                 # whose `<audience>.admin` scope the calls carry (the app's key; ShopFront's is `shopfront`)
    name = ""
    vault = False                 # answers 423 while its vault is locked

    def __init__(self, base_url: str, tokens: ServiceTokens, http: httpx.Client, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic, audience: str | None = None):
        self.base = check_url(base_url, self.name)
        self.tokens, self.http, self.sleep, self.clock = tokens, http, sleep, clock
        self.audience = audience or self.audience

    def _call(self, method: str, path: str, ok_404: bool = False, **kw) -> dict | list | None:
        start, delay, retried = self.clock(), 1.0, False
        while True:
            r = self.http.request(method, self.base + path, headers={"Authorization": f"Bearer {self.tokens.token(self.audience)}"}, **kw)
            if r.status_code == 401 and not retried:                    # an expired or revoked token: one fresh one
                self.tokens.forget(self.audience)
                retried = True
                continue
            if r.status_code == 423 and self.vault:       # the vault is locked (a fresh start): wait for it to be opened
                if self.clock() - start > 60:
                    raise ConnectError(f"{method} {path}: the shop's vault is still locked after 60 s (HTTP 423); unlock it, then run `ecoctl connect` again")
                self.sleep(delay)
                delay = min(delay * 2, 10)
                continue
            if r.status_code == 404 and ok_404:
                return None
            if r.status_code == 409:
                raise Conflict(f"{method} {path}: HTTP 409 {_detail(r)}")
            if r.status_code >= 400:
                raise ConnectError(f"{method} {path}: HTTP {r.status_code} {_detail(r)}".strip())
            return r.json() if r.content else {}

    def wait_ready(self, timeout: float = 180, interval: float = 3) -> None:
        start = self.clock()
        while True:
            try:
                r = self.http.get(self.base + "/readyz")
                if r.status_code == 200:
                    return
                seen = f"HTTP {r.status_code}"
            except httpx.HTTPError as exc:
                seen = type(exc).__name__
            if self.clock() - start >= timeout:
                raise ConnectError(f"{self.name} is not ready after {int(timeout)} s ({seen})")
            self.sleep(interval)


class PayBroker(_Admin):
    """PayBroker's admin API: merchants, their allowed origins, webhook and keys (key values are returned once, on creation)."""
    audience = name = "paybroker"

    def find_merchant(self, name: str) -> dict | None:
        out = self._call("GET", f"/api/merchants?name={quote(name, safe='')}")
        items = out.get("items", []) if isinstance(out, dict) else out
        return next((m for m in items if m.get("name") == name), None)          # the exact name, whatever the service matched

    def create_merchant(self, name: str) -> dict:
        return self._call("POST", "/api/merchants", json={"name": name})

    def set_origins(self, merchant_id: str, origins: list[str]) -> None:
        self._call("PATCH", f"/api/merchants/{quote(str(merchant_id), safe='')}", json={"allowed_origins": origins})

    def set_webhook(self, merchant_id: str, url: str) -> str:
        """Sets the URL and rotates the signing secret; returns the NEW secret (shown once)."""
        out = self._call("PUT", f"/api/merchants/{quote(str(merchant_id), safe='')}/webhook", json={"url": url})
        secret = _first(out, PB_SECRET_FIELDS)
        if not secret:
            raise ConnectError("PayBroker set the webhook but returned no signing secret; assumed one of: " + ", ".join(PB_SECRET_FIELDS))
        return secret

    def create_key(self, merchant_id: str, kind: str, name: str) -> tuple[str, str]:
        """(key id, key value); the value is shown once."""
        out = self._call("POST", f"/api/merchants/{quote(str(merchant_id), safe='')}/keys", json={"kind": kind, "name": name})
        inner = out.get("key") if isinstance(out.get("key"), dict) else out
        value = _first(inner, PB_KEY_FIELDS)
        kid = inner.get("id") or out.get("id")
        if not value or not kid:
            raise ConnectError("PayBroker created the key but returned no id or value; assumed fields: id and one of " + ", ".join(PB_KEY_FIELDS))
        return str(kid), value

    def delete_key(self, merchant_id: str, key_id: str) -> None:
        self._call("DELETE", f"/api/merchants/{quote(str(merchant_id), safe='')}/keys/{quote(str(key_id), safe='')}", ok_404=True)

    @staticmethod
    def webhook_url(merchant: dict) -> str | None:
        """The merchant's webhook URL when PayBroker shows it, else True/False-ish as 'configured' (None: it does not say)."""
        if isinstance(merchant.get("webhook_url"), str):
            return merchant["webhook_url"]
        webhook = merchant.get("webhook")
        if isinstance(webhook, dict) and isinstance(webhook.get("url"), str):
            return webhook["url"]
        return None

    @staticmethod
    def webhook_configured(merchant: dict) -> bool | None:
        for f in ("webhook_configured", "has_webhook", "webhook"):
            if isinstance(merchant.get(f), bool):
                return merchant[f]
        if PayBroker.webhook_url(merchant) is not None:
            return bool(PayBroker.webhook_url(merchant))
        return None


class ShopFront(_Admin):
    """ShopFront's admin API: the PayBroker settings (secrets are write-only: only fingerprints come back)."""
    audience = name = "shopfront"
    vault = True

    def settings(self) -> dict:
        return self._call("GET", "/api/settings/paybroker")

    def put_settings(self, fields: dict) -> None:
        self._call("PUT", "/api/settings/paybroker", json=fields)

    def test(self) -> dict:
        return self._call("POST", "/api/settings/paybroker/test")


def summarise_checks(out: dict) -> str:
    """One short line from the shop's test result (`checks` as a mapping name -> bool/text, or a list of {name, ok, detail})."""
    checks = out.get("checks")
    parts: list[str] = []
    if isinstance(checks, dict):
        for k, v in checks.items():
            v = v.get("ok", v) if isinstance(v, dict) else v
            parts.append(f"{k}: {'ok' if v is True else 'FAILED' if v is False else str(v)[:60]}")
    elif isinstance(checks, list):
        for c in checks:
            if isinstance(c, dict):
                ok = c.get("ok")
                parts.append(f"{str(c.get('name', '?'))[:30]}: {'ok' if ok is True else 'FAILED' if ok is False else str(ok)[:20]}")
    return "; ".join(parts)[:300]
