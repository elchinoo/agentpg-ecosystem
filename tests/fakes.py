"""An in-memory AgentPG CLI and identity provider, faithful where it matters: ids come from listings, URLs from `sandbox status`, and an
app's client secret is returned exactly once."""
import json
import re

import httpx


class FakeAgentPG:
    def __init__(self):
        self.instances: dict[str, str] = {}      # name -> id
        self.sandboxes: dict[str, dict] = {}     # name -> {id, env, image, instance, port}
        self.extensions: list[tuple[str, str]] = []
        self.calls: list[list[str]] = []
        self.fail_on: str | None = None
        import itertools
        self._ids = itertools.count(1)
        self.creating_polls = 0                  # how many `status` calls answer `creating` before `running`
        self.failed: set[str] = set()

    def __call__(self, args: list[str]) -> str:
        self.calls.append(args)
        a = args[1:]
        if self.fail_on and a[:2] == self.fail_on.split():
            raise RuntimeError("boom")
        if a[0] == "create":
            name = a[a.index("--name") + 1]
            self.instances[name] = f"inst-{len(self.instances) + 1}"
            return f"created {name}\n"
        if a[:2] == ["instances", "list"]:
            return "ID NAME SIZE VERSION STATUS\n-- ---- ---- ------- ------\n" + "".join(f"{i} {n} small 18 running\n" for n, i in self.instances.items())
        if a[:2] == ["extensions", "create"]:
            self.extensions.append((a[3], a[2]))          # recorded as (instance, extension); the CLI takes the name first
            return "ok\n"
        if a[:2] == ["sandbox", "create"]:
            env = dict(x.split("=", 1) for i, x in enumerate(a) if i and a[i - 1] == "--env")
            self.sandboxes[a[2]] = {"id": f"sbx-{next(self._ids)}", "env": env, "image": a[a.index("--image") + 1],
                                    "instance": a[a.index("--instance") + 1], "port": a[a.index("--port") + 1]}
            return "ok\n"
        if a[:2] == ["sandbox", "delete"]:
            gone = next(n for n, s in self.sandboxes.items() if s["id"] == a[2])
            del self.sandboxes[gone]
            return "deleted\n"
        if a[:2] == ["sandbox", "list"]:
            return "ID NAME IMAGE PLAN STATUS INSTANCE ERROR\n-- ---- ----- ---- ------ -------- -----\n" + "".join(
                f"{s['id']} {n} {s['image']} small running {s['instance']} -\n" for n, s in self.sandboxes.items())
        if a[:2] == ["sandbox", "status"]:
            name = next(n for n, s in self.sandboxes.items() if s["id"] == a[2])
            sb = self.sandboxes[name]
            sb["polls"] = sb.get("polls", 0) + 1
            if sb["polls"] <= self.creating_polls:
                return f"ID: {sb['id']}\nName: {name}\nStatus: creating\n"
            return f"ID: {sb['id']}\nName: {name}\nStatus: running\nURL: https://{name}.sandbox.example\n"
        raise AssertionError(f"unexpected agentpg call {args}")

    def commands(self, *prefix: str) -> list[list[str]]:
        return [c for c in self.calls if c[1:1 + len(prefix)] == list(prefix)]


class FakeIdentity:
    """Just enough of the admin API for ecoctl, with the real provider's rules about secrets and kinds."""

    def __init__(self):
        self.apps: dict[str, dict] = {}
        self.groups: dict[str, dict] = {}
        self.grants: list[dict] = []
        self.requests: list[tuple[str, str]] = []
        self.issuer: str | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        self.requests.append((method, path))
        if request.headers.get("authorization") != "Bearer " + self.admin_key and path not in {"/readyz", "/.well-known/openid-configuration"}:
            return httpx.Response(401, json={"detail": "authentication required"})
        body = json.loads(request.content) if request.content else {}
        if path == "/readyz":
            return httpx.Response(200, json={"status": "ready"})
        if path == "/.well-known/openid-configuration":
            self.issuer = self.issuer or f"{request.url.scheme}://{request.url.host}"
            return httpx.Response(200, json={"issuer": self.issuer})
        if path == "/api/apps" and method == "GET":
            return httpx.Response(200, json=[{**a, "client_id": k} for k, a in self.apps.items()])
        if path == "/api/apps" and method == "POST":
            if body["client_id"] in self.apps:
                return httpx.Response(422, json={"detail": "an app with that client id already exists"})
            if body["kind"] in {"web", "public"} and not body["redirect_uris"]:
                return httpx.Response(422, json={"detail": "a web or public app needs at least one redirect URI"})
            self.apps[body["client_id"]] = {**body, "secret_issued": True}
            return httpx.Response(201, json={"app": body, "client_secret": f"cs_{body['client_id']}_1"})
        m = re.fullmatch(r"/api/apps/([a-z0-9-]+)(/secret)?", path)
        if m:
            cid = m.group(1)
            if cid not in self.apps:
                return httpx.Response(404, json={"detail": "no such app"})
            if m.group(2):
                self.apps[cid]["rotations"] = self.apps[cid].get("rotations", 0) + 1
                return httpx.Response(200, json={"client_secret": f"cs_{cid}_{self.apps[cid]['rotations'] + 1}"})
            if method == "PATCH":
                self.apps[cid].update(body)
                return httpx.Response(200, json=self.apps[cid])
        if path == "/api/groups" and method == "GET":
            return httpx.Response(200, json=[{"name": n} for n in self.groups])
        if path == "/api/groups" and method == "POST":
            self.groups[body["name"]] = body
            return httpx.Response(201, json={})
        if path == "/api/grants" and method == "GET":
            cid = request.url.params.get("client_id")
            return httpx.Response(200, json=[g for g in self.grants if g["client_id"] == cid])
        if path == "/api/grants" and method == "POST":
            if body["role"] not in {r["name"] for r in self.apps[body["client_id"]]["roles"]}:
                return httpx.Response(422, json={"detail": "no such role"})
            self.grants.append({"subject_type": body["subject_type"], "subject_name": body["subject"], "client_id": body["client_id"], "role": body["role"]})
            return httpx.Response(201, json={"ok": True})
        return httpx.Response(404, json={"detail": f"unhandled {method} {path}"})

    admin_key = ""

    def client(self, admin_key: str) -> httpx.Client:
        self.admin_key = admin_key
        return httpx.Client(transport=httpx.MockTransport(self.handler))
