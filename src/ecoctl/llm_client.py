"""The LLM gateway's admin API (with the static operator key ecoctl generated for it) and its data plane (with an app's client key).

Used idempotently: a client is created once and its `lg_` key is shown once; if the key is lost the client's active key is rotated.
"""
import time
from dataclasses import dataclass

import httpx


class GatewayError(RuntimeError):
    pass


@dataclass
class ClientKey:
    key: str | None               # only when this call created or rotated it
    note: str                     # "created", "kept", "rotated (key was lost)", ...


class Gateway:
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
            hint = ""
            if r.status_code in {401, 403}:
                hint = " (the gateway does not know the operator key ecoctl holds: it was created without LLMGW_API_KEY; run `ecoctl recreate <gateway>`)"
            raise GatewayError(f"{method} {path}: HTTP {r.status_code} {detail}{hint}")
        return r.json()

    def wait_ready(self, timeout: float = 180, interval: float = 3, sleep=None, clock=None) -> None:
        """A sandbox that just became `running` may need a moment to answer: wait for /readyz."""
        sleep, clock = sleep or time.sleep, clock or time.monotonic
        deadline = clock() + timeout
        while True:
            try:
                r = self.http.get(self.base + "/readyz")
                if r.status_code == 200:
                    return
                seen = f"HTTP {r.status_code}"
            except httpx.HTTPError as exc:
                seen = type(exc).__name__
            if clock() > deadline:
                raise GatewayError(f"the LLM gateway is not ready after {int(timeout)} s ({seen})")
            sleep(interval)

    def clients(self) -> dict[str, dict]:
        return {c["name"]: c for c in self._call("GET", "/api/clients")["items"]}

    def models(self) -> set[str]:
        return {m["alias"] for m in self._call("GET", "/api/models")["items"]}

    def ensure_client(self, name: str, description: str, aliases: list[str], rpm: int, budget: int | None, budget_mode: str, *,
                      known_key: bool, rotate: bool = False) -> ClientKey:
        """Create the client if missing (its key comes back once). An existing client is left as it is (an operator may have tuned its
        limits); its key is replaced only on request or when we hold none, by rotating the active key (or issuing one if none is left)."""
        existing = self.clients().get(name)
        if existing is None:
            body = {"name": name, "description": description, "allowed_aliases": aliases, "rpm_limit": rpm, "budget_mode": budget_mode, "issue_key": True}
            if budget is not None:
                body["monthly_token_budget"] = budget
            out = self._call("POST", "/api/clients", json=body)
            return ClientKey(out["key"]["key"], "client created")
        if known_key and not rotate:
            return ClientKey(None, "kept")
        cid = existing["id"]
        active = [k for k in self._call("GET", f"/api/clients/{cid}/keys")["items"] if not k["revoked"]]
        why = "rotated" if known_key else "rotated (the key was lost)"
        if active:
            out = self._call("POST", f"/api/clients/{cid}/keys/{active[0]['id']}/rotate", json={"grace_minutes": 0})
            for extra in active[1:]:                                  # one live key per app: the app only ever holds one
                self._call("DELETE", f"/api/clients/{cid}/keys/{extra['id']}")
        else:
            out = self._call("POST", f"/api/clients/{cid}/keys", json={"name": "ecoctl"})
            why = "new key issued (none was active)"
        return ClientKey(out["key"], why)

    # ---- data plane, with the app's own key ----------------------------------------------------------------------------------
    def chat_test(self, app_key: str, model: str) -> tuple[bool, str]:
        """One tiny chat completion with an app's key. Returns (ok, short detail); the key is never part of the detail."""
        try:
            r = self.http.post(self.base + "/v1/chat/completions", headers={"Authorization": f"Bearer {app_key}"},
                               json={"model": model, "messages": [{"role": "user", "content": "ping"}], "max_tokens": 1}, timeout=60)
        except httpx.HTTPError as exc:
            return False, f"unreachable ({type(exc).__name__})"
        if r.status_code == 200:
            return True, "ok"
        try:
            err = r.json().get("error", {})
            detail = err.get("code") or err.get("message") or ""
        except ValueError:
            detail = ""
        return False, f"HTTP {r.status_code} {str(detail)[:80]}".strip()
