"""The `agentpg` CLI, as the Ansible modules use it. A port of the parts of ecoctl (src/ecoctl/agentpg.py, plan._deploy) that the recipe needs, kept
plain Python so the modules and the tests share one implementation: the command shapes are compared with ecoctl's in tests/test_ansible_recipe.py,
and the behaviour is exercised against ecoctl's in-memory fake CLI.

The one secret that passes through here is the database key AgentPG shows once at creation: it is RETURNED once (`new_db_key`, the task is no_log) so the
playbook can keep it in the encrypted state (eco_state, module_utils/eco_state.py); it is never written to this per-app file, which holds only ids, URLs and
hashes. A sandbox's environment is sealed (APGKIT_SEALED plus non-secret settings), so what reaches the CLI as `--env K=V` is not secret.
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


def delete_instance_args(instance_id: str) -> list[str]:
    return ["instances", "delete", instance_id, "--force"]               # --force skips the confirmation prompt; the data is gone for good


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
    """One app's non-secret entry (instance_id, extensions, sandbox_id, url, env_hash), one JSON file per app, mode 0600. It never holds the database key."""

    def __init__(self, path: str):
        self.path = path
        self.data: dict = {}
        if os.path.exists(path):
            with open(path) as fh:
                self.data = json.load(fh)

    def save(self) -> None:
        self.data.pop("db_key", None)                          # an older recipe kept it here: it lives in the encrypted state now
        directory = os.path.dirname(self.path) or "."
        os.makedirs(directory, mode=0o700, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".state-")
        with os.fdopen(fd, "w") as fh:
            json.dump(self.data, fh, indent=2, sort_keys=True)
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)


# ---- the three idempotent operations (each returns the module's result; `check` reads only) ------------------------------------
def _keep_key(exc: AgentPGError, key: str) -> AgentPGError:
    """The key is shown once: a failure after creation must still hand it to the playbook (which keeps it encrypted before failing the run)."""
    exc.new_db_key = key
    return exc


def _after_create(cli: Cli, name: str, key: str) -> str:
    try:
        iid = cli.instance_id(name)
    except AgentPGError as exc:
        raise _keep_key(exc, key) from None
    if not iid:
        raise _keep_key(AgentPGError(f"created instance {name} but could not find its id in `agentpg instances list`"), key)
    return iid


def ensure_instance(cli: Cli, st: State, name: str, size: str, wait: bool = True, check: bool = False) -> dict:
    d = st.data
    iid = d.get("instance_id") or cli.instance_id(name)
    created, new_key = False, ""
    if not iid:
        if check:
            return {"changed": True, "instance_id": "", "created": True, "msg": f"would create instance {name} ({size})", "new_db_key": ""}
        new_key = token_from(cli.run(create_instance_args(name, size))) or ""       # also the database password; AgentPG shows it only now: returned once, kept encrypted by the playbook
        created = True
        iid = _after_create(cli, name, new_key)
    if d.get("instance_id") != iid and not check:
        d["instance_id"] = iid
        st.save()
    if wait and not check:
        try:
            cli.wait_instance(iid)
        except AgentPGError as exc:
            raise _keep_key(exc, new_key) from None
    return {"changed": created, "instance_id": iid, "created": created, "new_db_key": new_key,
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
                   plan: str | None = None, recreate: bool = False, on_change: str = "report", check: bool = False, settings_hash: str | None = None) -> dict:
    """Instance and extensions are done. Existing sandboxes are kept; a changed environment is reported (on_change=report, as `ecoctl apply`) or
    the sandbox is deleted and created again with the same database (on_change=recreate, or `recreate`: as `ecoctl recreate`).
    `settings_hash` is the hash of the plain environment (computed by the playbook): a sealed blob differs every time, so the ciphertext is not what is compared."""
    d = st.data
    if d.get("sandbox_id") and d["sandbox_id"] not in cli.sandbox_ids():           # deleted behind our back: start over
        for k in ("sandbox_id", "url", "env_hash"):
            d.pop(k, None)
        if not check:
            st.save()
    want = settings_hash or env_hash(env)
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


# ---- removal (destroy.yml) ----------------------------------------------------------------------------------------------------
def _expect(name: str, allowed: list[str] | None) -> None:
    """Refuse a name that is not one of the fleet's own (the playbook passes exactly `<fleet>-<app>` / `<fleet>-<app>-db` of the manifest's apps)."""
    if allowed is not None and name not in allowed:
        raise AgentPGError(f"refusing to delete '{name}': it is not one of this fleet's objects ({', '.join(sorted(allowed)) or 'none'})")


def _wait_gone(cli: Cli, find, name: str) -> None:
    deadline = cli.clock() + cli.timeout
    while find(name):
        if cli.clock() > deadline:
            raise AgentPGError(f"'{name}' is still listed after {int(cli.timeout)} s")
        cli.sleep(cli.interval)


def ensure_sandbox_absent(cli: Cli, st: State, name: str, allowed: list[str] | None = None, check: bool = False) -> dict:
    """Delete the sandbox NAMED `name`. Its id is resolved from `agentpg sandbox list` now, never taken from the state file (which is only compared)."""
    _expect(name, allowed)
    sid = cli.sandbox_id(name)
    kept = st.data.get("sandbox_id")
    if not sid:
        if kept and not check:
            for k in ("sandbox_id", "url", "env_hash"):
                st.data.pop(k, None)
            st.save()
        return {"changed": False, "sandbox_id": "", "msg": f"sandbox {name} is already gone"}
    note = f" (the state file said {kept}: AgentPG's id is used)" if kept and kept != sid else ""
    if check:
        return {"changed": True, "sandbox_id": sid, "msg": f"would delete sandbox {name} ({sid}){note}"}
    cli.run(delete_sandbox_args(sid))
    _wait_gone(cli, cli.sandbox_id, name)
    for k in ("sandbox_id", "url", "env_hash"):
        st.data.pop(k, None)
    if st.data or os.path.exists(st.path):
        st.save()
    return {"changed": True, "sandbox_id": sid, "msg": f"deleted sandbox {name} ({sid}){note}"}


def ensure_instance_absent(cli: Cli, st: State, name: str, allowed: list[str] | None = None, check: bool = False) -> dict:
    """Delete the instance NAMED `name` (its data and keys go with it), resolved from `agentpg instances list` now. The state file of the app is removed
    afterwards: its instance id, database key and extension list describe an instance that no longer exists."""
    _expect(name, allowed)
    iid = cli.instance_id(name)
    kept = st.data.get("instance_id")
    if not iid:
        if kept and not check:
            os.remove(st.path)
        return {"changed": False, "instance_id": "", "msg": f"instance {name} is already gone"}
    note = f" (the state file said {kept}: AgentPG's id is used)" if kept and kept != iid else ""
    if check:
        return {"changed": True, "instance_id": iid, "msg": f"would delete instance {name} ({iid}) and its data{note}"}
    cli.run(delete_instance_args(iid))
    _wait_gone(cli, cli.instance_id, name)
    if os.path.exists(st.path):
        os.remove(st.path)
    return {"changed": True, "instance_id": iid, "msg": f"deleted instance {name} ({iid}) and its data{note}"}
