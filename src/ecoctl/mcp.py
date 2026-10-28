"""`ecoctl mcp`: read-only AI access to the apps' databases through AgentPG's own database MCP server (`agentpg mcp serve`).

Per app instance there is one database user, `ecoctl_ai`, created with `--permission read` (a member of `a1p_readonly`). Its credentials live only in
the state file. `config` writes an AgentPG MCP config in `restricted` mode whose `url` and `read_only_url` are BOTH that user's connection string, so
execute_sql runs as it. Nothing here ever prints a password or the HTTP secret; the config files hold credentials and are written 0600.
"""
import json
import os
import queue
import re
import shlex
import subprocess
import sys
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlsplit

import yaml

from .agentpg import AgentPG, AgentPGError, parse_json

DB_USER = "ecoctl_ai"
SENSITIVE = {"identity"}                 # `--all` leaves these out: the identity provider's database holds client registrations and grants
MAX_CONNECTIONS = 3
DEFAULT_FILE = "databases.yaml"
POSTGRES_URL = re.compile(r"postgres(?:ql)?://[^\s\"'<>\\]+")
WRITE_PROBE = "create table ecoctl_write_probe (id int)"
LOOPBACK = {"127.0.0.1", "localhost", "::1"}


class McpError(RuntimeError):
    pass


# ---- the read-only user ------------------------------------------------------------------------------------------------
def parse_connection(text: str) -> dict | None:
    """Host, port, database and query string from `agentpg connect <instance> --json` (or its plain text): whatever postgres:// URL it shows, or
    host/port/database fields. The user and password in it, if any, are dropped."""
    m = POSTGRES_URL.search(text)
    if m:
        u = urlsplit(m.group(0))
        if u.hostname:
            return {"host": u.hostname, "port": u.port or 5432, "dbname": u.path.lstrip("/") or "postgres", "query": u.query}
    data = parse_json(text)

    def find(node):
        if isinstance(node, dict):
            low = {k.lower(): v for k, v in node.items()}
            host = low.get("host") or low.get("hostname")
            if isinstance(host, str) and host:
                db = low.get("database") or low.get("dbname") or low.get("db") or "postgres"
                port = low.get("port") or 5432
                return {"host": host, "port": int(port) if str(port).isdigit() else 5432, "dbname": str(db), "query": ""}
            for v in node.values():
                if (r := find(v)):
                    return r
        elif isinstance(node, list):
            for v in node:
                if (r := find(v)):
                    return r
        return None

    return find(data) if data is not None else None


def db_url(user: dict) -> str:
    q = f"?{user['query']}" if user.get("query") else ""
    return f"postgresql://{quote(user['username'], safe='')}:{quote(user['password'], safe='')}@{user['host']}:{user['port']}/{quote(user['dbname'], safe='')}{q}"


def _has_user(listing: str, username: str) -> bool:
    data = parse_json(listing)
    if data is not None:
        def walk(n) -> bool:
            if isinstance(n, dict):
                return n.get("username") == username or n.get("name") == username or any(walk(v) for v in n.values())
            if isinstance(n, list):
                return any(walk(v) for v in n)
            return n == username
        return walk(data)
    return re.search(rf"(?<![\w-]){re.escape(username)}(?![\w-])", listing) is not None


def ensure_user(ctx, key: str, emit: Callable[[str], None] = print) -> str:
    """The read-only user of an app's instance, created once and then reused from the state file. Returns what happened."""
    entry = ctx.state.app(key)
    iid = entry.get("instance_id")
    if not iid:
        raise McpError(f"{key} has no instance yet (run `ecoctl apply --execute`)")
    have = entry.get("mcp_user")
    if have and have.get("password") and have.get("host"):
        return "reusing the user in the state file"
    created = False
    if not have or not have.get("password"):
        if _has_user(ctx.agentpg.run(AgentPG.db_users(iid)), DB_USER):
            raise McpError(f"{key}: the database user {DB_USER} exists but its password is not in the state file (AgentPG shows it once); "
                           f"delete it with `agentpg db-users delete {iid} {DB_USER}` and run this again")
        out = ctx.agentpg.run(AgentPG.db_user_create(iid, DB_USER, "read"))
        data = parse_json(out)
        if not isinstance(data, dict) or not data.get("password"):
            raise McpError(f"{key}: `agentpg db-users create` did not return a password (output format changed?)")
        if data.get("permission", "read") != "read":
            raise McpError(f"{key}: AgentPG created the user with permission '{data.get('permission')}', not 'read': delete it and stop")
        have = {"username": data.get("username") or DB_USER, "password": data["password"], "permission": "read"}
        entry["mcp_user"] = have
        created = True
        ctx.state.save()                                      # the password is shown only once: keep it before anything else can fail
    conn = parse_connection(ctx.agentpg.run(AgentPG.connect_info(iid)))
    if not conn:
        raise McpError(f"{key}: could not find the database host in `agentpg connect {iid} --json`; the user was created and kept, run this again after "
                       "checking that output")
    have.update(conn)
    ctx.state.save()
    return f"created user {DB_USER} (read)" if created else "found the connection details for the user in the state file"


def select(ctx, apps: list[str], all_: bool, *, need_user: bool, emit: Callable[[str], None] = print) -> list[str]:
    """The apps a command works on: the named ones, or (none named / --all) every app that has an instance (for `users`) or a stored user (others)."""
    known = ctx.manifest.everything()
    if apps and all_:
        raise McpError("name apps or use --all, not both")
    bad = [a for a in apps if a not in known]
    if bad:
        raise McpError(f"no such app: {', '.join(bad)}")
    if apps:
        for a in apps:
            if a in SENSITIVE:
                emit(f"note: {a} holds client registrations and grants; the read-only user can read all of it (not masked)")
        return list(dict.fromkeys(apps))
    chosen = [k for k in known if k not in SENSITIVE and ctx.state.app(k).get("instance_id")] if not need_user else \
             [k for k in known if ctx.state.app(k).get("mcp_user", {}).get("password")]
    if not chosen:
        raise McpError("no app with a database yet (run `ecoctl apply --execute`)" if not need_user else "no read-only user yet: run `ecoctl mcp users` first")
    return chosen


def users(ctx, apps: list[str], emit: Callable[[str], None] = print) -> int:
    bad = 0
    for k in apps:
        try:
            emit(f"{k:<16}{ensure_user(ctx, k, emit)}")
        except (McpError, AgentPGError) as exc:
            bad += 1
            emit(f"{k:<16}FAILED: {exc}")
        ctx.state.save()
    emit("\nCredentials are in the state file only (never printed). The user can read every table the app has not masked: see docs/GUIDE.md, section 'AI read-only access'.")
    return 2 if bad else 0


# ---- the MCP config ----------------------------------------------------------------------------------------------------
def _user(ctx, key: str) -> dict:
    u = ctx.state.app(key).get("mcp_user") or {}
    if not (u.get("password") and u.get("host")):
        raise McpError(f"{key} has no read-only user ready: run `ecoctl mcp users {key}`")
    return u


def config_dict(ctx, apps: list[str]) -> dict:
    dbs = {}
    for k in apps:
        url = db_url(_user(ctx, k))
        dbs[ctx.manifest.instance_name(k)] = {"url": url, "read_only_url": url, "access_mode": "restricted", "max_connections": MAX_CONNECTIONS}
    return {"databases": dbs, "default_database": next(iter(dbs))}


def config_path(ctx, file: str) -> Path:
    if not file or Path(file).name != file or file in {".", ".."}:
        raise McpError("--file is a file name inside the state directory, not a path")
    return ctx.state.dir / file


def write_private(path: Path, text: str) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".mcp-")
    with os.fdopen(fd, "w") as fh:
        fh.write(text)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def write_config(ctx, apps: list[str], file: str = DEFAULT_FILE) -> Path:
    path = config_path(ctx, file)
    write_private(path, "# written by `ecoctl mcp config`: contains database credentials (mode 0600, git-ignored); regenerate it, never edit or share it\n" +
                  yaml.safe_dump(config_dict(ctx, apps), sort_keys=False))
    return path


# ---- serve -------------------------------------------------------------------------------------------------------------
def serve(ctx, apps: list[str], transport: str, port: int, host: str, file: str, *, run: Callable[..., int] = subprocess.call, say: Callable[[str], None] | None = None) -> int:
    """Run `agentpg mcp serve --config <file>`. Notes go to stderr: on stdio, stdout belongs to the protocol. http always gets a generated
    A1P_JWT_SECRET (the server accepts anyone without one), and binds to loopback unless --host says otherwise."""
    say = say or (lambda s: print(s, file=sys.stderr))
    if transport not in {"stdio", "http"}:
        raise McpError("--transport is stdio or http")
    path = write_config(ctx, apps, file)
    args = [ctx.agentpg.bin, "mcp", "serve", "--config", str(path), "--transport", transport]
    env = dict(os.environ)
    env.pop("A1P_JWT_SECRET", None)
    if transport == "http":
        if not 1 <= port <= 65535:
            raise McpError("--port must be 1-65535")
        env["A1P_JWT_SECRET"] = ctx.state.mcp_jwt_secret()           # never start http without it
        ctx.state.save()
        args += ["--port", str(port), "--host", host]
        say(f"HTTP MCP server on {host}:{port} for {', '.join(apps)} (read-only user {DB_USER}); clients must send `Authorization: Bearer <JWT>`, a JWT signed (HS256) with the secret kept "
            f"in {ctx.state.path} under mcp.jwt_secret (not printed). The claims AgentPG expects were not verified: see `agentpg mcp serve --help` and its docs. "
            "Select the database with the X-A1P-Instance header.")
        if host not in LOOPBACK:
            say(f"warning: bound to {host}, not loopback: anyone who can reach it and holds a valid token can read these databases. Put TLS in front of it.")
    else:
        say(f"stdio MCP server for {', '.join(apps)} (read-only user {DB_USER}); the first database ({ctx.manifest.instance_name(apps[0])}) is the default one.")
    return run(args, env=env)


# ---- Claude Code -------------------------------------------------------------------------------------------------------
def claude_entries(ctx, apps: list[str]) -> dict[str, dict]:
    """One stdio server per app, each with its own single-database config, so which database a tool talks to never depends on a header or an argument."""
    entries = {}
    for k in apps:
        path = write_config(ctx, [k], f"databases-{k}.yaml").resolve()
        entries[f"agentpg-{ctx.manifest.fleet}-{k}"] = {"command": ctx.agentpg.bin, "args": ["mcp", "serve", "--config", str(path), "--transport", "stdio"]}
    return entries


def claude_add_command(name: str, entry: dict, scope: str | None) -> str:
    scope_flag = f"--scope {scope} " if scope else ""
    return f"claude mcp add {scope_flag}{name} -- {entry['command']} " + " ".join(shlex.quote(a) for a in entry["args"])


def merge_mcp_json(path: Path, entries: dict[str, dict]) -> None:
    doc = {}
    if path.exists():
        try:
            doc = json.loads(path.read_text())
        except ValueError as exc:
            raise McpError(f"{path} is not valid JSON; fix or remove it first: {exc}") from exc
    if not isinstance(doc, dict) or not isinstance(doc.get("mcpServers", {}), dict):
        raise McpError(f"{path} has no `mcpServers` object ecoctl can add to")
    doc.setdefault("mcpServers", {}).update(entries)
    path.write_text(json.dumps(doc, indent=2) + "\n")


# ---- check -------------------------------------------------------------------------------------------------------------
class StdioSession:
    """A JSON-RPC session with an MCP server over stdio (one JSON message per line)."""

    def __init__(self, args: list[str], env: dict[str, str], timeout: float = 60):
        self.timeout, self._id = timeout, 0
        self._err = tempfile.TemporaryFile()
        try:
            self.proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self._err, text=True, env=env)
        except OSError as exc:
            raise McpError(f"could not start {args[0]}: {exc}") from exc
        self._lines: queue.Queue = queue.Queue()
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        for line in self.proc.stdout:
            self._lines.put(line)
        self._lines.put(None)

    def _send(self, msg: dict) -> None:
        try:
            self.proc.stdin.write(json.dumps(msg) + "\n")
            self.proc.stdin.flush()
        except OSError as exc:
            raise McpError(f"the server closed its input: {self._stderr()}") from exc

    def _stderr(self) -> str:
        try:
            self._err.seek(0)
            return self._err.read().decode(errors="replace").strip()[-300:] or "(no error output)"
        except (OSError, ValueError):
            return "(no error output)"

    def notify(self, method: str, params: dict | None = None) -> None:
        self._send({"jsonrpc": "2.0", "method": method, **({"params": params} if params else {})})

    def request(self, method: str, params: dict | None = None) -> dict:
        self._id += 1
        self._send({"jsonrpc": "2.0", "id": self._id, "method": method, "params": params or {}})
        while True:
            try:
                line = self._lines.get(timeout=self.timeout)
            except queue.Empty as exc:
                raise McpError(f"no answer to {method} within {int(self.timeout)} s") from exc
            if line is None:
                raise McpError(f"the server exited during {method}: {self._stderr()}")
            try:
                msg = json.loads(line)
            except ValueError:
                continue                                       # a log line on stdout
            if msg.get("id") == self._id:
                if "error" in msg:
                    raise McpError(f"{method}: {str(msg['error'].get('message', msg['error']))[:300]}")
                return msg.get("result") or {}

    def close(self) -> None:
        for fn in (self.proc.stdin.close, self.proc.terminate):
            try:
                fn()
            except OSError:
                pass
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self._err.close()


def _text(result: dict) -> str:
    return " ".join(c.get("text", "") for c in result.get("content", []) if isinstance(c, dict)) or json.dumps(result.get("structuredContent", ""))


@dataclass
class Check:
    app: str
    tools: list[str]
    current_user: str = ""
    write_refused: bool = False
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and self.current_user == DB_USER and self.write_refused and "execute_sql" in self.tools


def _sql_arg(tool: dict) -> str:
    props = (tool.get("inputSchema") or {}).get("properties") or {}
    for name in ("sql", "query", "statement"):
        if name in props:
            return name
    return next(iter(props), "sql")


def check_one(ctx, key: str, opener: Callable[[list[str], dict], StdioSession] = StdioSession) -> Check:
    """Start the server for this one database, list its tools, run `select current_user`, and try a write (which must be refused)."""
    path = write_config(ctx, [key], f".check-{key}.yaml")
    env = dict(os.environ)
    env.pop("A1P_JWT_SECRET", None)
    res = Check(key, [])
    session = None
    try:
        session = opener([ctx.agentpg.bin, "mcp", "serve", "--config", str(path), "--transport", "stdio"], env)
        session.request("initialize", {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "ecoctl", "version": "1"}})
        session.notify("notifications/initialized")
        tools = session.request("tools/list").get("tools", [])
        res.tools = [t.get("name", "") for t in tools]
        sql = next((t for t in tools if t.get("name") == "execute_sql"), None)
        if sql is None:
            res.error = "the server has no execute_sql tool"
            return res
        arg = _sql_arg(sql)
        who = session.request("tools/call", {"name": "execute_sql", "arguments": {arg: "select current_user"}})
        text = _text(who)
        res.current_user = DB_USER if (not who.get("isError") and re.search(rf"\b{DB_USER}\b", text)) else (text[:60] or "?")
        try:
            w = session.request("tools/call", {"name": "execute_sql", "arguments": {arg: WRITE_PROBE}})
            res.write_refused = bool(w.get("isError"))
            if not res.write_refused:
                try:
                    session.request("tools/call", {"name": "execute_sql", "arguments": {arg: "drop table if exists ecoctl_write_probe"}})
                except McpError:
                    pass
                res.error = "a WRITE WAS ACCEPTED: the server is not read-only (probe table ecoctl_write_probe was created and a drop was attempted)"
        except McpError:
            res.write_refused = True                           # a protocol-level refusal is a refusal too
    except McpError as exc:
        res.error = str(exc)
    finally:
        if session:
            session.close()
        try:
            path.unlink()
        except OSError:
            pass
    return res


def check(ctx, apps: list[str], opener: Callable[[list[str], dict], StdioSession] = StdioSession, emit: Callable[[str], None] = print) -> int:
    results = [check_one(ctx, k, opener) for k in apps]
    emit(f"{'app':<16}{'tools':<7}{'current_user':<16}{'write':<10}result")
    for r in results:
        write = "refused" if r.write_refused else ("-" if r.error and not r.current_user else "ACCEPTED")
        verdict = "ok" if r.ok else "FAILED: " + (r.error or "see columns")
        emit(f"{r.app:<16}{len(r.tools):<7}{r.current_user or '-':<16}{write:<10}{verdict}")
    return 0 if all(r.ok for r in results) else 1
