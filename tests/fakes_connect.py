"""PayBroker's and ShopFront's admin APIs as `ecoctl connect` assumes them (src/ecoctl/connect_client.py), behind one mock transport that also
routes the identity provider's token endpoint. Faithful where it matters: key values and signing secrets are returned once and listed
never, every PUT of the webhook rotates the secret, a token is valid only for its audience, the shop's vault can answer 423, and the
shop's connection test really checks the pushed values against what PayBroker holds."""
import hashlib
import json
import re

import httpx


def fp(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:8]


class FakePayBroker:
    def __init__(self):
        self.token_ok = lambda auth: bool(re.fullmatch(r"Bearer tok\.paybroker\.\d+", auth))      # replaced when the identity provider is the real one
        self.merchants: dict[str, dict] = {}
        self.secrets: dict[str, dict[str, str]] = {}          # merchant id -> {key id: value}
        self.webhook_secrets: dict[str, str] = {}
        self.writes: list[tuple[str, str]] = []                # every non-GET call (method, path)
        self.requests: list[tuple[str, str]] = []
        self.race = False                                      # the next POST /api/merchants finds the name taken (created behind our back): 409
        self.hide_webhook_url = False                          # PayBroker only says whether a webhook is configured, not its URL
        self.rotations = 0
        self._n = 0

    def _new_id(self, prefix: str) -> str:
        self._n += 1
        return f"{prefix}_{self._n}"

    def add_merchant(self, name: str) -> dict:
        m = {"id": self._new_id("mer"), "name": name, "allowed_origins": [], "keys": []}
        if not self.hide_webhook_url:
            m["webhook_url"] = ""
        else:
            m["webhook_configured"] = False
        self.merchants[m["id"]] = m
        self.secrets[m["id"]] = {}
        return m

    def view(self, m: dict) -> dict:
        return json.loads(json.dumps(m))

    def handler(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        self.requests.append((method, path))
        if path == "/readyz":
            return httpx.Response(200, json={"status": "ready"})
        if not self.token_ok(request.headers.get("authorization", "")):
            return httpx.Response(401, json={"detail": "a service token for paybroker is required"})
        body = json.loads(request.content) if request.content else {}
        if method != "GET":
            self.writes.append((method, path))
        if path == "/api/merchants" and method == "GET":
            name = request.url.params.get("name")
            return httpx.Response(200, json=[self.view(m) for m in self.merchants.values() if name is None or m["name"] == name])
        if path == "/api/merchants" and method == "POST":
            if self.race:
                self.race = False
                self.add_merchant(body["name"])
            if any(m["name"] == body["name"] for m in self.merchants.values()):
                return httpx.Response(409, json={"detail": "a merchant with that name already exists"})
            return httpx.Response(201, json=self.view(self.add_merchant(body["name"])))
        mm = re.fullmatch(r"/api/merchants/([\w-]+)(/webhook|/keys(?:/([\w-]+))?)?", path)
        if mm and mm.group(1) in self.merchants:
            m = self.merchants[mm.group(1)]
            if not mm.group(2) and method == "PATCH":
                m["allowed_origins"] = body["allowed_origins"]
                return httpx.Response(200, json=self.view(m))
            if mm.group(2) == "/webhook" and method == "PUT":
                self.rotations += 1
                secret = f"whsec_{self.rotations}_{m['id']}"
                self.webhook_secrets[m["id"]] = secret
                if self.hide_webhook_url:
                    m["webhook_configured"] = True
                else:
                    m["webhook_url"] = body["url"]
                return httpx.Response(200, json={"url": body["url"], "signing_secret": secret})
            if mm.group(2) == "/keys" and method == "POST":
                if body["kind"] not in {"secret", "publishable"}:
                    return httpx.Response(422, json={"detail": "kind must be secret or publishable"})
                kid = self._new_id("key")
                value = f"{'sk' if body['kind'] == 'secret' else 'pk'}_live_{kid}_{m['id']}"
                self.secrets[m["id"]][kid] = value
                m["keys"].append({"id": kid, "kind": body["kind"], "name": body["name"], "last4": value[-4:]})
                return httpx.Response(201, json={"id": kid, "kind": body["kind"], "name": body["name"], "key": value})
            if mm.group(3) and method == "DELETE":
                if mm.group(3) not in self.secrets[m["id"]]:
                    return httpx.Response(404, json={"detail": "no such key"})
                del self.secrets[m["id"]][mm.group(3)]
                for k in m["keys"]:                              # like PayBroker: a revoked key stays listed, with revoked_at set
                    if k["id"] == mm.group(3):
                        k["revoked_at"] = "2026-10-27T00:00:00Z"
                return httpx.Response(200, json={"revoked": mm.group(3)})
        return httpx.Response(404, json={"detail": f"unhandled {method} {path}"})

    def live_key(self, merchant_id: str, value: str) -> bool:
        return value in self.secrets.get(merchant_id, {}).values()


class FakeShopFront:
    def __init__(self, paybroker: FakePayBroker):
        self.pb = paybroker
        self.token_ok = lambda auth: bool(re.fullmatch(r"Bearer tok\.shopfront\.\d+", auth))
        self.values = {"base_url": "", "publishable_key": "", "secret_key": "", "webhook_secret": ""}
        self.locked = 0                                        # answer 423 this many times first (a vault that is still opening)
        self.writes: list[tuple[str, str]] = []
        self.requests: list[tuple[str, str]] = []
        self.puts: list[list[str]] = []                        # the field names of every PUT
        self.merchant = "ShopFront"
        self.fail_puts = 0                                     # answer 500 to this many PUTs (the push fails after the keys were created)

    def handler(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        self.requests.append((method, path))
        if path == "/readyz":
            return httpx.Response(200, json={"status": "ready"})
        if not self.token_ok(request.headers.get("authorization", "")):
            return httpx.Response(401, json={"detail": "a service token for shopfront is required"})
        if self.locked > 0:
            self.locked -= 1
            return httpx.Response(423, json={"detail": "the vault is locked"})
        body = json.loads(request.content) if request.content else {}
        if method != "GET" and not path.endswith("/test"):                 # the connection test is a probe: it changes nothing
            self.writes.append((method, path))
        if path == "/api/settings/paybroker" and method == "GET":
            v = self.values
            return httpx.Response(200, json={"base_url": v["base_url"], "has_publishable_key": bool(v["publishable_key"]), "has_secret_key": bool(v["secret_key"]),
                                             "has_webhook_secret": bool(v["webhook_secret"]), "connection": "unknown",
                                             "fingerprints": {k: fp(v[k]) for k in ("publishable_key", "secret_key", "webhook_secret") if v[k]}})
        if path == "/api/settings/paybroker" and method == "PUT":
            if self.fail_puts > 0:
                self.fail_puts -= 1
                return httpx.Response(500, json={"detail": "the shop broke"})
            unknown = set(body) - set(self.values)
            if unknown:
                return httpx.Response(422, json={"detail": f"unknown field {sorted(unknown)[0]}"})
            self.values.update(body)
            self.puts.append(sorted(body))
            return httpx.Response(200, json={"ok": True})
        if path == "/api/settings/paybroker/test" and method == "POST":
            m = next((m for m in self.pb.merchants.values() if m["name"] == self.merchant), None)
            v = self.values
            checks = {"base_url": bool(v["base_url"].startswith("https://")),
                      "secret_key": bool(m and self.pb.live_key(m["id"], v["secret_key"]) and v["secret_key"].startswith("sk_")),
                      "publishable_key": bool(m and self.pb.live_key(m["id"], v["publishable_key"]) and v["publishable_key"].startswith("pk_")),
                      "webhook_secret": bool(m and v["webhook_secret"] and v["webhook_secret"] == self.pb.webhook_secrets.get(m["id"]))}
            return httpx.Response(200, json={"ok": all(checks.values()), "checks": checks})
        return httpx.Response(404, json={"detail": f"unhandled {method} {path}"})


class FakeConnect:
    """The three services behind one client: the host name tells which one a request is for (identity / paybroker / shopfront)."""

    def __init__(self, idp, identity_handler=None):
        self.idp = idp
        self.pb = FakePayBroker()
        self.shop = FakeShopFront(self.pb)
        self.identity_handler = identity_handler or idp.handler
        self.hosts: list[str] = []
        self.urls: list[str] = []                            # every full request URL (query included), to prove no secret travels in one

    def handler(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        self.hosts.append(host)
        self.urls.append(str(request.url))
        if "paybroker" in host:
            return self.pb.handler(request)
        if "shopfront" in host:
            return self.shop.handler(request)
        if "identity" in host:
            return self.identity_handler(request)
        return httpx.Response(502, json={"detail": f"no fake for {host}"})

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))

    def all_writes(self) -> list[tuple[str, str]]:
        return [*self.pb.writes, *self.shop.writes]
