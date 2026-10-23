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
        self.url_with_id = False                 # like the real platform: the sandbox id is part of its URL, so a recreated sandbox gets a new one

    def __call__(self, args: list[str]) -> str:
        self.calls.append(args)
        a = args[1:]
        if self.fail_on and a[:2] == self.fail_on.split():
            raise RuntimeError("boom")
        if a[0] == "create":
            name = a[a.index("--name") + 1]
            self.instances[name] = f"inst-{len(self.instances) + 1}"
            self.keys = getattr(self, "keys", {})
            self.keys[self.instances[name]] = {"id": "key-1", "token": f"a1p_sk_{name}_1"}
            return json.dumps({"id": self.instances[name], "name": name, "token": self.keys[self.instances[name]]["token"]})
        if a[:2] == ["keys", "list"]:
            return json.dumps({"keys": [{"id": self.keys[a[2]]["id"], "name": "default"}]})
        if a[:2] == ["keys", "reset"]:
            k = self.keys[a[2]]
            k["token"] += "r"
            return json.dumps({"id": k["id"], "token": k["token"]})
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
            return f"ID: {sb['id']}\nName: {name}\nStatus: running\nURL: https://{name}{'-' + sb['id'] if self.url_with_id else ''}.sandbox.example\n"
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


class FakeGateway:
    """The LLM gateway's admin API and data plane, as the real one behaves: a client key is shown once, only the operator key (the
    static LLMGW_API_KEY) reaches /api, only a live client key reaches /v1, a rotation revokes the old key at once."""

    def __init__(self, aliases=("echo-chat", "echo-embed", "chat-default", "embed-default")):
        self.aliases = set(aliases)
        self.clients: dict[int, dict] = {}
        self.keys: dict[int, list[dict]] = {}            # client id -> [{id, key, revoked}]
        self.requests: list[tuple[str, str]] = []
        self.patches: list[dict] = []                    # bodies of PATCH /api/clients/{id}
        self.chat_calls: list[dict] = []                 # {client, model}
        self.operator_key: str | None = None             # None: a gateway created without LLMGW_API_KEY
        self.ready_after = 0                             # /readyz answers 503 this many times first
        self._n = 0
        self.upstream_status = 200                       # what a chat call answers (to simulate a broken alias)

    def _new_key(self, cid: int) -> dict:
        self._n += 1
        k = {"id": self._n, "key": f"lg_fake{self._n}_{cid}", "revoked": False}
        self.keys[cid].append(k)
        return k

    def handler(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        self.requests.append((method, path))
        auth = request.headers.get("authorization", "")
        body = json.loads(request.content) if request.content else {}
        if path == "/readyz":
            if self.ready_after > 0:
                self.ready_after -= 1
                return httpx.Response(503, json={"status": "waking"})
            return httpx.Response(200, json={"status": "ready"})
        if path.startswith("/v1/"):
            live = next((cid for cid, ks in self.keys.items() if cid in self.clients for k in ks if not k["revoked"] and auth == "Bearer " + k["key"]), None)
            if live is None:
                code = "wrong_credential" if auth == "Bearer " + str(self.operator_key) else "invalid_api_key"
                return httpx.Response(403 if code == "wrong_credential" else 401, json={"error": {"code": code, "message": "no"}})
            c = self.clients[live]
            if body["model"] not in c["allowed_aliases"]:
                return httpx.Response(403, json={"error": {"code": "model_not_allowed", "message": "no"}})
            if self.upstream_status != 200:
                return httpx.Response(self.upstream_status, json={"error": {"code": "upstream_unavailable", "message": "down"}})
            self.chat_calls.append({"client": c["name"], "model": body["model"], "max_tokens": body.get("max_tokens")})
            return httpx.Response(200, json={"choices": [{"message": {"content": "Echo: ping"}}], "usage": {"total_tokens": 5}})
        if self.operator_key is None or auth != "Bearer " + self.operator_key:
            return httpx.Response(401, json={"detail": "authentication required"})
        if path == "/api/models" and method == "GET":
            return httpx.Response(200, json={"items": [{"alias": a} for a in sorted(self.aliases)]})
        if path == "/api/clients" and method == "GET":
            return httpx.Response(200, json={"items": list(self.clients.values())})
        if path == "/api/clients" and method == "POST":
            if any(c["name"] == body["name"] for c in self.clients.values()):
                return httpx.Response(422, json={"detail": "a client with that name already exists"})
            missing = [a for a in body["allowed_aliases"] if a not in self.aliases]
            if missing:
                return httpx.Response(422, json={"detail": f"unknown model alias: {missing[0]}"})
            cid = len(self.clients) + 1
            self.clients[cid] = {"id": cid, "name": body["name"], "allowed_aliases": body["allowed_aliases"], "rpm_limit": body.get("rpm_limit"),
                                 "monthly_token_budget": body.get("monthly_token_budget"), "budget_mode": body.get("budget_mode", "hard"), "enabled": True}
            self.keys[cid] = []
            out = {"client": self.clients[cid]}
            if body.get("issue_key", True):
                k = self._new_key(cid)
                out["key"] = {"id": k["id"], "key": k["key"], "name": "default"}
            return httpx.Response(201, json=out)
        m = re.fullmatch(r"/api/clients/(\d+)", path)
        if m and method == "PATCH" and int(m.group(1)) in self.clients:
            missing = [a for a in body.get("allowed_aliases", []) if a not in self.aliases]
            if missing:
                return httpx.Response(422, json={"detail": f"unknown model alias: {missing[0]}"})
            self.patches.append(dict(body))
            self.clients[int(m.group(1))].update(body)
            return httpx.Response(200, json=self.clients[int(m.group(1))])
        m = re.fullmatch(r"/api/clients/(\d+)/keys(?:/(\d+)(/rotate)?)?", path)
        if m and int(m.group(1)) in self.clients:
            cid = int(m.group(1))
            if method == "GET" and not m.group(2):
                return httpx.Response(200, json={"items": [{"id": k["id"], "name": "", "prefix": k["key"][:11], "revoked": k["revoked"]} for k in self.keys[cid]]})
            if method == "POST" and not m.group(2):
                k = self._new_key(cid)
                return httpx.Response(201, json={"id": k["id"], "key": k["key"], "name": body.get("name", "")})
            kid = int(m.group(2)) if m.group(2) else 0
            old = next((k for k in self.keys[cid] if k["id"] == kid and not k["revoked"]), None)
            if old is None:
                return httpx.Response(404, json={"detail": "key not found or already revoked"})
            if method == "POST" and m.group(3):
                old["revoked"] = True
                self.rotations = getattr(self, "rotations", 0) + 1
                k = self._new_key(cid)
                return httpx.Response(201, json={"id": k["id"], "key": k["key"], "replaces": kid})
            if method == "DELETE":
                old["revoked"] = True
                return httpx.Response(200, json={"revoked": kid})
        return httpx.Response(404, json={"detail": f"unhandled {method} {path}"})

    def client(self, operator_key: str | None) -> httpx.Client:
        self.operator_key = operator_key
        return httpx.Client(transport=httpx.MockTransport(self.handler))
