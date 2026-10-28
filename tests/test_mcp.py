"""`ecoctl mcp`: the read-only user, the AgentPG MCP config, serve, Claude Code entries and check. Fakes only (a tiny fake server for the stdio session)."""
import json
import stat
import sys
import textwrap

import pytest
import yaml

from ecoctl import mcp as mcp_mod
from ecoctl.agentpg import AgentPG
from ecoctl.mcp import DB_USER, McpError
from ecoctl.state import State
from tests.test_plan import MANIFEST, apply, world  # noqa: F401  (the fixture)


def deployed(world, capsys):  # noqa: F811
    ctx, cli, idp = world
    apply(ctx)
    capsys.readouterr()
    cli.calls.clear()
    return ctx, cli


def test_parse_connection_from_json_text_and_fields():
    got = mcp_mod.parse_connection('{"x": {"url": "postgresql://u:secret@h.example:6543/db1?sslmode=require"}}')
    assert got == {"host": "h.example", "port": 6543, "dbname": "db1", "query": "sslmode=require"}
    assert "secret" not in json.dumps(got)
    assert mcp_mod.parse_connection("psql postgres://u:p@h2/app")["host"] == "h2"
    assert mcp_mod.parse_connection('{"conn": {"Host": "h3", "port": "5433", "database": "d"}}') == {"host": "h3", "port": 5433, "dbname": "d", "query": ""}
    assert mcp_mod.parse_connection("nothing useful") is None


def test_users_are_created_once_and_kept_only_in_the_state(world, capsys):  # noqa: F811
    ctx, cli = deployed(world, capsys)
    apps = mcp_mod.select(ctx, [], False, need_user=False)
    assert "identity" not in apps and "shopfront" in apps and len(apps) == 7
    assert mcp_mod.users(ctx, apps) == 0
    created = [c for c in cli.calls if c[1:3] == ["db-users", "create"]]
    assert len(created) == 7 and all(c[c.index("--username") + 1] == DB_USER and c[c.index("--permission") + 1] == "read" for c in created)
    out = capsys.readouterr().out
    assert "pw-" not in out and "created user ecoctl_ai" in out                              # the password is never printed
    u = ctx.state.app("shopfront")["mcp_user"]
    assert u["host"] == "db.example.test" and u["dbname"] == "appdb" and u["query"] == "sslmode=require" and u["password"].startswith("pw-")
    state_file = ctx.state.dir / "state.json"
    assert stat.S_IMODE(state_file.stat().st_mode) == 0o600 and u["password"] in state_file.read_text()
    # again: reused, no new CLI call at all
    cli.calls.clear()
    assert mcp_mod.users(ctx, apps) == 0 and cli.calls == [] and "reusing" in capsys.readouterr().out


def test_an_existing_user_without_a_stored_password_is_not_recreated(world, capsys):  # noqa: F811
    ctx, cli = deployed(world, capsys)
    iid = ctx.state.app("shopfront")["instance_id"]
    cli.db_users[iid] = {DB_USER: {"password": "x", "permission": "read"}}
    with pytest.raises(McpError, match="password is not in the state file"):
        mcp_mod.ensure_user(ctx, "shopfront")
    assert not [c for c in cli.calls if c[1:3] == ["db-users", "create"]]


def test_a_user_with_the_wrong_permission_is_refused(world, capsys):  # noqa: F811
    ctx, cli = deployed(world, capsys)
    ctx.agentpg._run = lambda args: json.dumps({"username": DB_USER, "password": "p", "permission": "read_write"}) if args[1:3] == ["db-users", "create"] else cli(args)
    with pytest.raises(McpError, match="not 'read'"):
        mcp_mod.ensure_user(ctx, "shopfront")
    assert "mcp_user" not in ctx.state.app("shopfront")


def test_a_failed_host_lookup_keeps_the_password_for_the_next_run(world, capsys):  # noqa: F811
    ctx, cli = deployed(world, capsys)
    cli.connect_text = "no url here"
    with pytest.raises(McpError, match="could not find the database host"):
        mcp_mod.ensure_user(ctx, "shopfront")
    assert State(ctx.state.dir).app("shopfront")["mcp_user"]["password"].startswith("pw-")
    cli.connect_text = '{"host": "h", "port": 5432, "database": "d"}'
    n = len([c for c in cli.calls if c[1:3] == ["db-users", "create"]])
    mcp_mod.ensure_user(ctx, "shopfront")
    assert len([c for c in cli.calls if c[1:3] == ["db-users", "create"]]) == n                # not created twice


def ready(world, capsys, *apps):  # noqa: F811
    ctx, cli = deployed(world, capsys)
    assert mcp_mod.users(ctx, list(apps)) == 0
    capsys.readouterr()
    return ctx, cli


def test_config_is_restricted_with_both_urls_the_readonly_user_and_mode_0600(world, capsys):  # noqa: F811
    ctx, cli = ready(world, capsys, "shopfront", "trustdesk")
    path = mcp_mod.write_config(ctx, ["shopfront", "trustdesk"])
    assert path == ctx.state.dir / "databases.yaml" and stat.S_IMODE(path.stat().st_mode) == 0o600
    doc = yaml.safe_load(path.read_text())
    assert list(doc["databases"]) == ["eco-shopfront-db", "eco-trustdesk-db"] and doc["default_database"] == "eco-shopfront-db"
    d = doc["databases"]["eco-shopfront-db"]
    assert d["access_mode"] == "restricted" and d["max_connections"] == 3 and d["url"] == d["read_only_url"]
    assert d["url"].startswith(f"postgresql://{DB_USER}:pw-{DB_USER}-") and d["url"].endswith("@db.example.test:5432/appdb?sslmode=require")
    assert "admin" not in d["url"].split("@")[0] and ctx.state.app("shopfront")["db_key"] not in path.read_text()      # never the instance's own key


@pytest.mark.parametrize("name", ["../x.yaml", "/etc/x", "a/b.yaml", ".."])
def test_config_file_is_a_name_inside_the_state_dir(world, capsys, name):  # noqa: F811
    ctx, cli = ready(world, capsys, "shopfront")
    with pytest.raises(McpError, match="file name"):
        mcp_mod.write_config(ctx, ["shopfront"], name)


def test_config_needs_a_user(world, capsys):  # noqa: F811
    ctx, cli = deployed(world, capsys)
    with pytest.raises(McpError, match="no read-only user yet"):
        mcp_mod.select(ctx, [], False, need_user=True)
    with pytest.raises(McpError, match="ecoctl mcp users shopfront"):
        mcp_mod.config_dict(ctx, ["shopfront"])
    with pytest.raises(McpError, match="no such app"):
        mcp_mod.select(ctx, ["ghost"], False, need_user=True)
    with pytest.raises(McpError, match="not both"):
        mcp_mod.select(ctx, ["shopfront"], True, need_user=True)


def test_naming_identity_is_allowed_with_a_note_but_all_leaves_it_out(world, capsys):  # noqa: F811
    ctx, cli = deployed(world, capsys)
    notes = []
    assert mcp_mod.select(ctx, ["identity"], False, need_user=False, emit=notes.append) == ["identity"] and "not masked" in notes[0]


def test_http_serve_always_sets_a_generated_secret_and_binds_loopback(world, capsys, monkeypatch):  # noqa: F811
    ctx, cli = ready(world, capsys, "shopfront")
    monkeypatch.setenv("A1P_JWT_SECRET", "from-the-environment")
    seen, notes = {}, []
    rc = mcp_mod.serve(ctx, ["shopfront"], "http", 9090, "127.0.0.1", "databases.yaml", run=lambda args, env: seen.update(args=args, env=env) or 0, say=notes.append)
    secret = ctx.state.data["mcp"]["jwt_secret"]
    assert rc == 0 and seen["env"]["A1P_JWT_SECRET"] == secret and len(secret) >= 48
    assert seen["args"] == ["agentpg", "mcp", "serve", "--config", str(ctx.state.dir / "databases.yaml"), "--transport", "http", "--port", "9090", "--host", "127.0.0.1"]
    assert secret not in " ".join(notes + seen["args"]) and "Bearer" in notes[0] and not any("warning" in n for n in notes)
    # the secret is stable between runs, and a non-loopback bind warns
    notes.clear()
    mcp_mod.serve(ctx, ["shopfront"], "http", 9090, "0.0.0.0", "databases.yaml", run=lambda args, env: 0, say=notes.append)
    assert ctx.state.data["mcp"]["jwt_secret"] == secret and any("warning" in n for n in notes)


def test_stdio_serve_has_no_secret_and_no_port(world, capsys):  # noqa: F811
    ctx, cli = ready(world, capsys, "shopfront")
    seen = {}
    mcp_mod.serve(ctx, ["shopfront"], "stdio", 8080, "127.0.0.1", "databases.yaml", run=lambda args, env: seen.update(args=args, env=env) or 0, say=lambda s: None)
    assert "A1P_JWT_SECRET" not in seen["env"] and seen["args"][-2:] == ["--transport", "stdio"] and "--port" not in seen["args"]
    with pytest.raises(McpError):
        mcp_mod.serve(ctx, ["shopfront"], "http", 70000, "127.0.0.1", "databases.yaml", run=lambda a, env: 0, say=lambda s: None)
    with pytest.raises(McpError):
        mcp_mod.serve(ctx, ["shopfront"], "sse", 1, "127.0.0.1", "databases.yaml", run=lambda a, env: 0, say=lambda s: None)


def test_claude_entries_are_one_stdio_server_per_app_and_hold_no_credentials(world, capsys, tmp_path):  # noqa: F811
    ctx, cli = ready(world, capsys, "shopfront", "trustdesk")
    entries = mcp_mod.claude_entries(ctx, ["shopfront", "trustdesk"])
    assert list(entries) == ["agentpg-eco-shopfront", "agentpg-eco-trustdesk"]
    e = entries["agentpg-eco-shopfront"]
    assert e["command"] == "agentpg" and e["args"][:3] == ["mcp", "serve", "--config"] and e["args"][-2:] == ["--transport", "stdio"]
    cfg = yaml.safe_load(open(e["args"][3]).read())
    assert list(cfg["databases"]) == ["eco-shopfront-db"]
    assert "pw-" not in json.dumps(entries)
    cmd = mcp_mod.claude_add_command("agentpg-eco-shopfront", e, "project")
    assert cmd.startswith("claude mcp add --scope project agentpg-eco-shopfront -- agentpg mcp serve --config ")
    target = tmp_path / ".mcp.json"
    target.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}))
    mcp_mod.merge_mcp_json(target, entries)
    merged = json.loads(target.read_text())["mcpServers"]
    assert set(merged) == {"other", "agentpg-eco-shopfront", "agentpg-eco-trustdesk"}
    target.write_text("{not json")
    with pytest.raises(McpError, match="not valid JSON"):
        mcp_mod.merge_mcp_json(target, entries)


# ---- check -------------------------------------------------------------------------------------------------------
class FakeSession:
    """A server that says who it runs as and whether it accepts writes."""

    def __init__(self, user=DB_USER, writes_ok=False, tools=("execute_sql", "list_schemas")):
        self.user, self.writes_ok, self.tools, self.calls, self.closed = user, writes_ok, tools, [], False

    def notify(self, method, params=None):
        pass

    def request(self, method, params=None):
        if method == "initialize":
            return {"serverInfo": {"name": "fake"}}
        if method == "tools/list":
            return {"tools": [{"name": t, "inputSchema": {"properties": {"sql": {"type": "string"}}}} for t in self.tools]}
        sql = params["arguments"]["sql"]
        self.calls.append(sql)
        if sql == "select current_user":
            return {"content": [{"type": "text", "text": json.dumps([{"current_user": self.user}])}]}
        if sql.startswith("create table"):
            return {"content": [{"type": "text", "text": "ok"}]} if self.writes_ok else {"isError": True, "content": [{"type": "text", "text": "permission denied"}]}
        return {"content": [{"type": "text", "text": "ok"}]}

    def close(self):
        self.closed = True


def test_check_passes_when_the_user_is_read_only(world, capsys):  # noqa: F811
    ctx, cli = ready(world, capsys, "shopfront", "trustdesk")
    sessions = []
    opened = []

    def opener(args, env):
        opened.append((args, env))
        sessions.append(FakeSession())
        return sessions[-1]

    assert mcp_mod.check(ctx, ["shopfront", "trustdesk"], opener=opener) == 0
    out = capsys.readouterr().out
    assert out.count("refused") == 2 and "ok" in out and all(s.closed for s in sessions)
    assert all("A1P_JWT_SECRET" not in env and a[1:3] == ["mcp", "serve"] for a, env in opened)
    assert not list(ctx.state.dir.glob(".check-*"))                                          # the temporary single-database configs are removed


def test_check_fails_loudly_when_a_write_is_accepted(world, capsys):  # noqa: F811
    ctx, cli = ready(world, capsys, "shopfront")
    s = FakeSession(writes_ok=True)
    assert mcp_mod.check(ctx, ["shopfront"], opener=lambda a, e: s) == 1
    out = capsys.readouterr().out
    assert "ACCEPTED" in out and "WRITE WAS ACCEPTED" in out and "drop table if exists ecoctl_write_probe" in s.calls


@pytest.mark.parametrize("session,message", [
    (FakeSession(user="a1p_admin"), "a1p_admin"),
    (FakeSession(tools=("list_schemas",)), "no execute_sql"),
])
def test_check_fails_on_the_wrong_user_or_a_missing_tool(world, capsys, session, message):  # noqa: F811
    ctx, cli = ready(world, capsys, "shopfront")
    assert mcp_mod.check(ctx, ["shopfront"], opener=lambda a, e: session) == 1
    assert message in capsys.readouterr().out


def test_stdio_session_talks_json_rpc_to_a_real_subprocess(world, capsys, tmp_path):  # noqa: F811
    """A stand-in for `agentpg` (a script that speaks MCP over stdio and refuses writes) drives the real StdioSession and check()."""
    ctx, cli = ready(world, capsys, "shopfront")
    fake = tmp_path / "bin" / "agentpg"
    fake.parent.mkdir()
    fake.write_text(f"#!{sys.executable}\n" + textwrap.dedent('''
        import json, sys
        print("starting up (a log line on stdout)", flush=True)
        for line in sys.stdin:
            msg = json.loads(line)
            if "id" not in msg:
                continue
            m = msg["method"]
            if m == "initialize":
                res = {"serverInfo": {"name": "fake"}}
            elif m == "tools/list":
                res = {"tools": [{"name": "execute_sql", "inputSchema": {"properties": {"query": {"type": "string"}}}}]}
            else:
                q = msg["params"]["arguments"]["query"]
                res = {"content": [{"type": "text", "text": "ecoctl_ai"}]} if q == "select current_user" else {"isError": True, "content": [{"type": "text", "text": "read-only"}]}
            print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": res}), flush=True)
    '''))
    fake.chmod(0o755)
    ctx.agentpg = AgentPG(run=lambda a: "", binary=str(fake))
    assert mcp_mod.check(ctx, ["shopfront"]) == 0
    assert "ok" in capsys.readouterr().out


def test_a_server_that_exits_is_a_clear_failure(world, capsys, tmp_path):  # noqa: F811
    ctx, cli = ready(world, capsys, "shopfront")
    fake = tmp_path / "agentpg"
    fake.write_text(f"#!{sys.executable}\nimport sys\nprint('bad config', file=sys.stderr)\nsys.exit(3)\n")
    fake.chmod(0o755)
    ctx.agentpg = AgentPG(run=lambda a: "", binary=str(fake))
    assert mcp_mod.check(ctx, ["shopfront"]) == 1
    assert "FAILED" in capsys.readouterr().out



def test_cli_parses_mcp_and_does_not_confuse_the_manifest_file_with_the_config_file(tmp_path, capsys):
    from ecoctl import cli as cli_mod
    st = State(tmp_path / "eco")
    st.app("shopfront").update(instance_id="inst-1", mcp_user={"username": DB_USER, "password": "p", "permission": "read", "host": "h", "port": 5432, "dbname": "d", "query": ""})
    st.save()
    base = ["--state", str(tmp_path / "eco"), "-f", str(MANIFEST)]
    assert cli_mod.main([*base, "mcp", "config", "--file", "mine.yaml"]) == 0
    assert (tmp_path / "eco" / "mine.yaml").exists() and "p@h:5432/d" in (tmp_path / "eco" / "mine.yaml").read_text()
    out = capsys.readouterr().out
    assert "wrote" in out and "p@h" not in out
    assert cli_mod.main([*base, "mcp", "claude", "--write"]) == 1                                   # --write needs --project
    assert cli_mod.main([*base, "mcp", "config", "--file", "../x"]) == 2
