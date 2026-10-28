"""The `agentpg` CLI, as the Ansible modules use it. A port of the parts of ecoctl (src/ecoctl/agentpg.py, plan._deploy) that the recipe needs, kept
plain Python so the modules and the tests share one implementation: the command shapes are compared with ecoctl's in tests/test_ansible_recipe.py,
and the behaviour is exercised against ecoctl's in-memory fake CLI.

Nothing here prints or returns a secret: environment values reach the CLI as `--env K=V` (as in ecoctl, so they pass through this machine's process
list for a moment), the database key goes only to the state file.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
import time

URL_LINE = re.compile(r"URL:\s*(https?://\S+)")
ALREADY = re.compile(r"already (exists|installed|enabled)", re.I)
LEGACY_EXTENSIONS = {"vector", "pg_trgm"}
RUNNING = {"running", "ready", "active"}


class AgentPGError(RuntimeError):
    pass


# ---- command shapes (identical to ecoctl.agentpg.AgentPG's builders) -------------------------------------------------------
def create_instance_args(name: str, size: str) -> list[str]:
    return ["create", "--name", name, "--size", size, "--json"]       # the answer carries the database key, shown only once


def extension_args(instance_id: str, ext: str) -> list[str]:
    return ["extensions", "create", ext, instance_id]                   # name first, then the instance id


def create_sandbox_args(name: str, image: str, instance_id: str, port: int, env: dict[str, str], *, auto_pause: bool = True, plan: str | None = None) -> list[str]:
    args = ["sandbox", "create", name, "--image", image, "--instance", instance_id, "--port", str(port)]
    if not auto_pause:
        args.append("--auto-pause=false")
    if plan:
        args += ["--plan", plan]
    for k, v in env.items():
        args += ["--env", f"{k}={v}"]
    return args


def delete_sandbox_args(sandbox_id: str) -> list[str]:
    return ["sandbox", "delete", sandbox_id, "--force"]


def env_hash(env: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(env, sort_keys=True).encode()).hexdigest()[:12]


def token_from(output: str) -> str | None:
    """The database key (it is also the database password) from a `--json` answer of create, or None."""
    try:
        data = json.loads(output)
    except ValueError:
        return None
    token = data.get("token") if isinstance(data, dict) else None
    return token if isinstance(token, str) and token else None


# ---- the CLI ---------------------------------------------------------------------------------------------------------------
def _run(args: list[str], timeout: float = 300) -> str:
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AgentPGError(f"could not run {args[0]}: {exc}") from exc
    if r.returncode != 0:
        raise AgentPGError((r.stderr or r.stdout).strip()[:500] or f"{args[0]} exited with {r.returncode}")
    return r.stdout


class Cli:
    def __init__(self, run=None, binary: str = "agentpg", interval: float = 5, timeout: float = 420, sleep=time.sleep, clock=time.monotonic):
        self._run, self.bin = run or _run, binary
        self.interval, self.timeout, self.sleep, self.clock = interval, timeout, sleep, clock

    def run(self, args: list[str]) -> str:
        return self._run([self.bin, *args])

    def _find(self, listing: list[str], name: str) -> str | None:
        for line in self.run(listing).splitlines():
            cols = line.split()
            if len(cols) > 1 and name in cols[1:]:
                return cols[0]
        return None

    def instance_id(self, name: str) -> str | None:
        return self._find(["instances", "list"], name)

    def sandbox_id(self, name: str) -> str | None:
        return self._find(["sandbox", "list"], name)

    def sandbox_ids(self) -> set[str]:
        return {ln.split()[0] for ln in self.run(["sandbox", "list"]).splitlines()[2:] if ln.split()}

    def wait_instance(self, instance_id: str) -> None:
        deadline = self.clock() + self.timeout
        while True:
            row = next((ln.split() for ln in self.run(["instances", "list"]).splitlines() if ln.split()[:1] == [instance_id]), [])
            status = next((c.lower() for c in row if c.lower() in {"running", "creating", "provisioning", "starting", "failed", "error", "stopped"}), "unknown")
            if status == "running":
                return
            if status in {"failed", "error"}:
                raise AgentPGError(f"instance {instance_id} is {status} (see `agentpg instances describe {instance_id}`)")
            if self.clock() > deadline:
                raise AgentPGError(f"instance {instance_id} is still '{status}' after {int(self.timeout)} s; check `agentpg instances list`")
            self.sleep(self.interval)

    def sandbox_state(self, sandbox_id: str) -> tuple[str, str | None]:
        text = self.run(["sandbox", "status", sandbox_id])
        st = re.search(r"^\s*Status:\s*(\S+)", text, re.M)
        url = URL_LINE.search(text)
        return (st.group(1).lower() if st else "unknown"), (url.group(1).rstrip("/") if url else None)

    def wait_url(self, sandbox_id: str) -> str:
        deadline = self.clock() + self.timeout
        while True:
            status, url = self.sandbox_state(sandbox_id)
            if url and status in RUNNING:
                return url
            if status in {"failed", "error", "deleted"}:
                detail = next((ln for ln in self.run(["sandbox", "list"]).splitlines() if sandbox_id in ln), "")
                raise AgentPGError(f"sandbox {sandbox_id} is {status}: {detail.strip()[:300]} (see `agentpg sandbox logs {sandbox_id}`)")
            if self.clock() > deadline:
                raise AgentPGError(f"sandbox {sandbox_id} is still '{status}' after {int(self.timeout)} s; check `agentpg sandbox status {sandbox_id}`")
            self.sleep(self.interval)


class State:
    """One app's entry of ecoctl's state file (instance_id, db_key, extensions, sandbox_id, url, env_hash), one JSON file per app, mode 0600."""

    def __init__(self, path: str):
        self.path = path
        self.data: dict = {}
        if os.path.exists(path):
            with open(path) as fh:
                self.data = json.load(fh)

    def save(self) -> None:
        directory = os.path.dirname(self.path) or "."
        os.makedirs(directory, mode=0o700, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".state-")
        with os.fdopen(fd, "w") as fh:
            json.dump(self.data, fh, indent=2, sort_keys=True)
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)


# ---- the three idempotent operations (each returns the module's result; `check` reads only) ------------------------------------
def ensure_instance(cli: Cli, st: State, name: str, size: str, wait: bool = True, check: bool = False) -> dict:
    d = st.data
    iid = d.get("instance_id") or cli.instance_id(name)
    created = False
    if not iid:
        if check:
            return {"changed": True, "instance_id": "", "created": True, "msg": f"would create instance {name} ({size})", "db_key_saved": bool(d.get("db_key"))}
        token = token_from(cli.run(create_instance_args(name, size)))
        if token:
            d["db_key"] = token                                  # also the database password; AgentPG shows it only now
        iid = cli.instance_id(name)
        if not iid:
            raise AgentPGError(f"created instance {name} but could not find its id in `agentpg instances list`")
        created = True
    if d.get("instance_id") != iid and not check:
        d["instance_id"] = iid
        st.save()
    if wait and not check:
        cli.wait_instance(iid)
    return {"changed": created, "instance_id": iid, "created": created, "db_key_saved": bool(d.get("db_key")),
            "msg": f"created instance {name}" if created else f"instance {name} exists"}


def installed_extensions(d: dict, wanted: list[str]) -> list[str]:
    if "extensions" not in d and d.get("extensions_done"):
        d["extensions"] = [e for e in wanted if e in LEGACY_EXTENSIONS]
    return list(d.get("extensions", []))


def ensure_extensions(cli: Cli, st: State, instance_id: str, names: list[str], check: bool = False) -> dict:
    d = st.data
    have = set(installed_extensions(d, names))
    todo = [e for e in names if e not in have]
    if not todo:
        return {"changed": False, "created": [], "msg": "extensions already created"}
    if check:
        return {"changed": True, "created": todo, "msg": f"would create {', '.join(todo)}"}
    cli.wait_instance(instance_id)
    for ext in todo:
        try:
            cli.run(extension_args(instance_id, ext))
        except AgentPGError as exc:
            if not ALREADY.search(str(exc)):
                raise
        d.setdefault("extensions", []).append(ext)               # one at a time: a failure keeps what was done and the next run does the rest
        st.save()
    return {"changed": True, "created": todo, "msg": f"created {', '.join(todo)}"}


def ensure_sandbox(cli: Cli, st: State, name: str, image: str, instance_id: str, port: int, env: dict[str, str], *, auto_pause: bool = True,
                   plan: str | None = None, recreate: bool = False, on_change: str = "report", check: bool = False) -> dict:
    """Instance and extensions are done. Existing sandboxes are kept; a changed environment is reported (on_change=report, as `ecoctl apply`) or
    the sandbox is deleted and created again with the same database (on_change=recreate, or `recreate`: as `ecoctl recreate`)."""
    d = st.data
    if d.get("sandbox_id") and d["sandbox_id"] not in cli.sandbox_ids():           # deleted behind our back: start over
        for k in ("sandbox_id", "url", "env_hash"):
            d.pop(k, None)
        if not check:
            st.save()
    want = env_hash(env)
    changed, outcome, differs, recreated = False, [], False, False
    if d.get("sandbox_id") and (recreate or (d.get("env_hash") and d["env_hash"] != want and on_change == "recreate")):
        recreated, changed = True, True
        if not check:
            cli.run(delete_sandbox_args(d["sandbox_id"]))
            for k in ("sandbox_id", "url", "env_hash"):
                d.pop(k, None)
            st.save()
        outcome.append("deleted for recreation")
    if not d.get("sandbox_id"):
        found = cli.sandbox_id(name)
        if found and not recreated:
            d["sandbox_id"] = found
            outcome.append("found")
            if not check:
                st.save()
        elif check:
            return {"changed": True, "sandbox_id": "", "url": d.get("url", ""), "env_hash": want, "env_differs": False, "created": True, "recreated": recreated,
                    "msg": f"would create sandbox {name} from {image}" + (" (recreating it)" if recreated else "")}
        else:
            cli.wait_instance(instance_id)
            cli.run(create_sandbox_args(name, image, instance_id, port, env, auto_pause=auto_pause, plan=plan))
            d["sandbox_id"] = cli.sandbox_id(name)
            d["env_hash"] = want
            changed = True
            outcome.append("created")
    elif d.get("env_hash") and d["env_hash"] != want:
        differs = True
        outcome.append("exists with a different environment: set eco_recreate or eco_env_change=recreate to apply it (the database keeps its data)")
    if not d.get("sandbox_id"):
        raise AgentPGError(f"could not find sandbox {name} in `agentpg sandbox list`")
    if check:
        url = cli.sandbox_state(d["sandbox_id"])[1] or d.get("url", "")
    else:
        d["url"] = url = cli.wait_url(d["sandbox_id"])
        st.save()
    return {"changed": changed, "sandbox_id": d["sandbox_id"], "url": url, "env_hash": d.get("env_hash", want), "env_differs": differs,
            "created": "created" in outcome, "recreated": recreated, "msg": f"{url} ({', '.join(outcome) or 'unchanged'})"}
