"""`ecoctl mcp`: read-only AI access to the apps' databases through AgentPG's own database MCP server (`agentpg mcp serve`).

Per app instance there is one database user, `ecoctl_ai`, created with `--permission read` (a member of `a1p_readonly`). Its credentials live only in
the (encrypted) state file. The AgentPG MCP config (`restricted` mode, `url` and `read_only_url` BOTH that user's connection string, so execute_sql
runs as it) is never written to disk in the clear:

* `config` writes it as an `apgkit-seal1$...` blob under the operator's passphrase (same format and passphrase as the state file);
* `serve`, `claude` entries and `check` decrypt/build it in memory and hand it to `agentpg mcp serve --config /dev/fd/N`, an inherited pipe that
  holds no file at all: the text exists only in this process, in the pipe and in the server's memory. Where /dev/fd does not exist the fallback is a
  0600 file in a fresh 0700 directory that is removed on exit and on SIGTERM/SIGINT/SIGHUP (see docs/SECURITY.md for what that leaves).

Nothing here ever prints a password or the HTTP secret.
"""
import json
import os
import queue
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlsplit

import yaml
from apgkit import sealed as kit

from .agentpg import AgentPG, AgentPGError, parse_json
from .state import ENV_PASSPHRASE_CMD, StateError

DB_USER = "ecoctl_ai"
SENSITIVE = {"identity"}                 # `--all` leaves these out: the identity provider's database holds client registrations and grants
MAX_CONNECTIONS = 3
DEFAULT_FILE = "databases.sealed"
_FIELD = "config"
POSTGRES_URL = re.compile(r"postgres(?:ql)?://[^\s\"'<>\\]+")
WRITE_PROBE = "create table ecoctl_write_probe (id int)"
LOOPBACK = {"127.0.0.1", "localhost", "::1"}


class McpError(RuntimeError):
    pass


# ---- the read-only user ------------------------------------------------------------------------------------------------
def parse_connection(text: str) -> dict | None:
    """Host, port, database and query string from `agentpg describe <instance> --json` (its `connection_string`) (or its plain text): whatever postgres:// URL it shows, or
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
        raise McpError(f"{key}: could not find the database host in `agentpg describe {iid} --json`; the user was created and kept, run this again after "
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


def config_text(ctx, apps: list[str]) -> str:
    return yaml.safe_dump(config_dict(ctx, apps), sort_keys=False)


def config_path(ctx, file: str) -> Path:
    if not file or Path(file).name != file or file in {".", ".."}:
        raise McpError("--file is a file name inside the state directory, not a path")
    return ctx.state.dir / file


def write_private(path: Path, text: str) -> None:
    """`text` must already be ciphertext (a blob): this is the only thing ecoctl writes for the MCP config."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".mcp-")
    with os.fdopen(fd, "w") as fh:
        fh.write(text)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def seal_config(ctx, apps: list[str]) -> str:
    try:
        return kit.seal({_FIELD: config_text(ctx, apps)}, ctx.state.passphrase(creating=False, ask=getattr(ctx, "ask_password", None)))
    except (kit.SealError, StateError) as exc:
        raise McpError(str(exc)) from None


def write_config(ctx, apps: list[str], file: str = DEFAULT_FILE) -> Path:
    """The MCP config as an encrypted blob (never the YAML) in the state directory."""
    path = config_path(ctx, file)
    write_private(path, seal_config(ctx, apps))
    return path


def read_sealed_config(ctx, file: str) -> str:
    path = config_path(ctx, file)
    try:
        raw = path.read_text()
        return kit.open_blob(raw, ctx.state.passphrase(creating=False, ask=getattr(ctx, "ask_password", None)))[_FIELD]
    except OSError as exc:
        raise McpError(f"cannot read {path}: {exc.strerror}") from None
    except (kit.SealError, KeyError, StateError):
        raise McpError(f"{path} could not be decrypted: wrong passphrase, or it is not a blob written by `ecoctl mcp config`") from None


@contextmanager
def private_config(text: str) -> Iterator[tuple[str, tuple[int, ...]]]:
    """Hand `text` to a child process without a file: yields (path to pass to --config, fds the child must inherit).

    Preferred: an inheritable pipe, `/dev/fd/N`. Nothing is on any filesystem; the data is gone once the child has read it. Fallback (no /dev/fd): a 0600 file in a fresh
    0700 directory (tmpfs/RAM-backed when the OS has one), removed on exit, on exceptions and on SIGTERM/SIGINT/SIGHUP."""
    if os.path.isdir("/dev/fd"):
        r, w = os.pipe()
        data = text.encode()

        def feed() -> None:
            try:
                with os.fdopen(w, "wb") as fh:
                    fh.write(data)
            except OSError:
                pass                                                   # the child went away without reading it

        threading.Thread(target=feed, daemon=True).start()
        try:
            yield f"/dev/fd/{r}", (r,)
        finally:
            os.close(r)
        return
    base = "/dev/shm" if os.path.isdir("/dev/shm") and os.access("/dev/shm", os.W_OK) else None
    tmp = tempfile.mkdtemp(prefix="ecoctl-mcp-", dir=base)                # mode 0700
    old = {}

    def bail(signum, frame):
        raise SystemExit(128 + signum)

    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        try:
            old[sig] = signal.signal(sig, bail)
        except ValueError:                                              # not the main thread
            pass
    try:
        path = Path(tmp) / "mcp.yaml"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
        yield str(path), ()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        for sig, handler in old.items():
            signal.signal(sig, handler)


def run_child(args: list[str], env: dict[str, str], pass_fds: tuple[int, ...] = ()) -> int:
    """Run the server in the foreground, forwarding termination signals to it so it never outlives us (and so the fallback file is removed)."""
    proc = subprocess.Popen(args, env=env, pass_fds=pass_fds)
    old = {}

    def forward(signum, frame):
        try:
            proc.send_signal(signum)
        except OSError:
            pass

    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        try:
            old[sig] = signal.signal(sig, forward)
        except ValueError:
            pass
    try:
        return proc.wait()
    finally:
        for sig, handler in old.items():
            signal.signal(sig, handler)


# ---- serve -------------------------------------------------------------------------------------------------------------
def serve(ctx, apps: list[str], transport: str, port: int, host: str, file: str | None = None, *, run: Callable[..., int] = run_child,
          say: Callable[[str], None] | None = None) -> int:
    """Run `agentpg mcp serve --config <private channel>`. Notes go to stderr: on stdio, stdout belongs to the protocol. http always gets a generated
    A1P_JWT_SECRET (the server accepts anyone without one), and binds to loopback unless --host says otherwise. `file` names a blob written by
    `ecoctl mcp config` (decrypted here); without it the config is built from the encrypted state."""
    say = say or (lambda s: print(s, file=sys.stderr))
    if transport not in {"stdio", "http"}:
        raise McpError("--transport is stdio or http")
    text = read_sealed_config(ctx, file) if file else config_text(ctx, apps)
    env = dict(os.environ)
    env.pop("A1P_JWT_SECRET", None)
    env.pop("MCP_CONFIG", None)                                          # the server would read it instead of --config
    extra: list[str] = []
    if transport == "http":
        if not 1 <= port <= 65535:
            raise McpError("--port must be 1-65535")
        env["A1P_JWT_SECRET"] = ctx.state.mcp_jwt_secret()           # never start http without it
        ctx.state.save()
        extra = ["--port", str(port), "--host", host]
        say(f"HTTP MCP server on {host}:{port} for {', '.join(apps)} (read-only user {DB_USER}); clients must send `Authorization: Bearer <JWT>`, a JWT signed (HS256) with the secret kept "
            f"in {ctx.state.path} under mcp.jwt_secret (not printed). The claims AgentPG expects were not verified: see `agentpg mcp serve --help` and its docs. "
            "Select the database with the X-A1P-Instance header.")
        if host not in LOOPBACK:
            say(f"warning: bound to {host}, not loopback: anyone who can reach it and holds a valid token can read these databases. Put TLS in front of it.")
    else:
        say(f"stdio MCP server for {', '.join(apps)} (read-only user {DB_USER}); the first database ({ctx.manifest.instance_name(apps[0])}) is the default one.")
    with private_config(text) as (path, fds):
        return run([ctx.agentpg.bin, "mcp", "serve", "--config", path, "--transport", transport, *extra], env=env, pass_fds=fds)


# ---- Claude Code -------------------------------------------------------------------------------------------------------
def ecoctl_command() -> list[str]:
    exe = shutil.which("ecoctl")
    return [str(Path(exe).resolve())] if exe else [sys.executable, "-c", "import sys; from ecoctl.cli import main; sys.exit(main())"]


def claude_entries(ctx, apps: list[str], manifest_file: str | Path = "ecosystem.yaml") -> dict[str, dict]:
    """One stdio server per app. The stanza launches `ecoctl mcp serve`, which decrypts the credentials in memory and feeds them to the server through a pipe: no config
    file with credentials exists, and the stanza holds none. The passphrase is NOT in the stanza: ecoctl gets it from ECOCTL_SEAL_PASSPHRASE_CMD (a command, copied into the
    stanza when set: it is not a secret) or from ECOCTL_SEAL_PASSPHRASE in the environment Claude Code was started with (a prompt is impossible: stdio belongs to the protocol)."""
    base = ecoctl_command()
    entries = {}
    for k in apps:
        _user(ctx, k)                                                    # fail now, not when Claude starts it
        args = [*base[1:], "--state", str(ctx.state.dir.resolve()), "-f", str(Path(manifest_file).resolve()), "mcp", "serve", "--transport", "stdio", k]
        entry = {"command": base[0], "args": args}
        if os.environ.get(ENV_PASSPHRASE_CMD):                                # a command (not a secret) that prints the passphrase, e.g. a password manager's CLI
            entry["env"] = {ENV_PASSPHRASE_CMD: os.environ[ENV_PASSPHRASE_CMD]}
        entries[f"agentpg-{ctx.manifest.fleet}-{k}"] = entry
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

    def __init__(self, args: list[str], env: dict[str, str], pass_fds: tuple[int, ...] = (), timeout: float = 60):
        self.timeout, self._id = timeout, 0
        self._err = tempfile.TemporaryFile()
        try:
            self.proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self._err, text=True, env=env, pass_fds=pass_fds)
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


def check_one(ctx, key: str, opener: Callable[..., StdioSession] = StdioSession) -> Check:
    """Start the server for this one database, list its tools, run `select current_user`, and try a write (which must be refused)."""
    env = dict(os.environ)
    env.pop("A1P_JWT_SECRET", None)
    env.pop("MCP_CONFIG", None)
    res = Check(key, [])
    session = None
    channel = private_config(config_text(ctx, [key]))
    path, fds = channel.__enter__()
    try:
        session = opener([ctx.agentpg.bin, "mcp", "serve", "--config", path, "--transport", "stdio"], env, fds)
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
        channel.__exit__(None, None, None)
    return res


def check(ctx, apps: list[str], opener: Callable[..., StdioSession] = StdioSession, emit: Callable[[str], None] = print) -> int:
    results = [check_one(ctx, k, opener) for k in apps]
    emit(f"{'app':<16}{'tools':<7}{'current_user':<16}{'write':<10}result")
    for r in results:
        write = "refused" if r.write_refused else ("-" if r.error and not r.current_user else "ACCEPTED")
        verdict = "ok" if r.ok else "FAILED: " + (r.error or "see columns")
        emit(f"{r.app:<16}{len(r.tools):<7}{r.current_user or '-':<16}{write:<10}{verdict}")
    return 0 if all(r.ok for r in results) else 1
