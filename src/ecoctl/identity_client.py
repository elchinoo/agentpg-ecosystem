"""The identity provider's admin API, used idempotently: re-running `apply` converges instead of failing."""
from dataclasses import dataclass

import httpx

PENDING_REDIRECT = "https://pending.invalid/auth/callback"       # replaced once the app's real URL is known


class IdentityError(RuntimeError):
    pass


@dataclass
class Registered:
    client_id: str
    secret: str | None            # only when this call created the app or rotated its secret


class Identity:
    def __init__(self, base_url: str, admin_key: str, http: httpx.Client | None = None):
        self.base = base_url.rstrip("/")
        self.http = http or httpx.Client(timeout=20, follow_redirects=False)
        self.headers = {"Authorization": f"Bearer {admin_key}"}

    def _call(self, method: str, path: str, **kw) -> dict | list:
        r = self.http.request(method, self.base + path, headers=self.headers, **kw)
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail")
            except ValueError:
                detail = r.text[:200]
            raise IdentityError(f"{method} {path}: HTTP {r.status_code} {detail}")
        return r.json()

    def wait_ready(self) -> None:
        r = self.http.get(self.base + "/readyz")
        if r.status_code != 200:
            raise IdentityError(f"the identity provider is not ready (HTTP {r.status_code}: {r.text[:100]})")

    def pin_issuer(self) -> str:
        """The first request defines the issuer when IDENTITY_PUBLIC_URL is unset, so make it one we chose: over the public URL."""
        doc = self.http.get(self.base + "/.well-known/openid-configuration").json()
        if doc["issuer"].rstrip("/") != self.base:
            raise IdentityError(f"the provider reports issuer {doc['issuer']} but is reached at {self.base}; set IDENTITY_PUBLIC_URL and restart it")
        return doc["issuer"].rstrip("/")

    def apps(self) -> dict[str, dict]:
        return {a["client_id"]: a for a in self._call("GET", "/api/apps")}

    def ensure_app(self, spec: dict, *, known_secret: bool, rotate: bool = False) -> Registered:
        """Create the app if missing (returns its secret once), otherwise bring it to the spec. A secret is rotated only on request, or
        when it is lost (the app exists but we hold no secret for it)."""
        client_id = spec["client_id"]
        existing = self.apps().get(client_id)
        if existing is None:
            out = self._call("POST", "/api/apps", json=spec)
            return Registered(client_id, out.get("client_secret"))
        if existing["kind"] != spec["kind"]:
            raise IdentityError(f"{client_id} is registered as a {existing['kind']} client but the manifest wants {spec['kind']}; delete it in the console first")
        patch = {k: v for k, v in spec.items() if k not in {"client_id", "kind"}}
        # a redirect URI we already pointed at the real URL must not be reset to the placeholder
        if patch.get("redirect_uris") == [PENDING_REDIRECT]:
            patch.pop("redirect_uris", None)
            patch.pop("post_logout_uris", None)
            patch.pop("url", None)
        self._call("PATCH", f"/api/apps/{client_id}", json=patch)
        if spec["kind"] != "public" and (rotate or not known_secret):
            return Registered(client_id, self._call("POST", f"/api/apps/{client_id}/secret")["client_secret"])
        return Registered(client_id, None)

    def set_urls(self, client_id: str, url: str) -> None:
        self._call("PATCH", f"/api/apps/{client_id}", json={"url": url, "redirect_uris": [f"{url}/auth/callback"], "post_logout_uris": [f"{url}/"]})

    def ensure_group(self, name: str, description: str) -> None:
        if name not in {g["name"] for g in self._call("GET", "/api/groups")}:
            self._call("POST", "/api/groups", json={"name": name, "description": description})

    def ensure_grant(self, group: str, client_id: str, role: str) -> bool:
        have = {(g.get("subject_type"), g.get("subject_name"), g.get("role")) for g in self._call("GET", f"/api/grants?client_id={client_id}")}
        if ("group", group, role) in have:
            return False
        self._call("POST", "/api/grants", json={"subject_type": "group", "subject": group, "client_id": client_id, "role": role})
        return True
