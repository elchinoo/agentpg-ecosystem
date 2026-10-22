"""The `agentpg` command line, wrapped. Output is parsed the way the deployment tutorial does it: the id is the first column of
the listing line that names the object, and the URL follows `URL:` in `sandbox status`."""
import json
import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field

URL_LINE = re.compile(r"URL:\s*(https?://\S+)")


class AgentPGError(RuntimeError):
    pass


@dataclass(frozen=True)
class Command:
    args: tuple[str, ...]
    secret_values: tuple[str, ...] = field(default=(), repr=False)

    def display(self) -> str:
        """The command as a person would read it, with secret values masked."""
        out = []
        for a in self.args:
            for s in self.secret_values:
                a = a.replace(s, "***")
            out.append(a if re.fullmatch(r"[\w@%+=:,./-]+", a) else repr(a))
        return "agentpg " + " ".join(out)


def _run(args: list[str]) -> str:
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=300, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AgentPGError(f"could not run {args[0]}: {exc}") from exc
    if r.returncode != 0:
        raise AgentPGError((r.stderr or r.stdout).strip()[:500] or f"{args[0]} exited with {r.returncode}")
    return r.stdout


class AgentPG:
    def __init__(self, run: Callable[[list[str]], str] = _run, binary: str = "agentpg"):
        self._run, self.bin = run, binary

    def run(self, command: Command) -> str:
        return self._run([self.bin, *command.args])

    # ---- commands (also used to print a plan) --------------------------------------------------------------------------
    @staticmethod
    def create_instance(name: str, size: str) -> Command:
        return Command(("create", "--name", name, "--size", size, "--json"))      # the answer carries the database key, shown only once

    @staticmethod
    def list_keys(instance_id: str) -> Command:
        return Command(("keys", "list", instance_id, "--json"))

    @staticmethod
    def reset_key(instance_id: str, key_id: str) -> Command:
        return Command(("keys", "reset", instance_id, key_id, "--json"))

    @staticmethod
    def extension(instance_id: str, ext: str) -> Command:
        return Command(("extensions", "create", ext, instance_id))      # name first, then the instance id

    @staticmethod
    def create_sandbox(name: str, image: str, instance_id: str, port: int, env: dict[str, str], secret_names: set[str], *, auto_pause: bool = True,
                       plan: str | None = None) -> Command:
        args = ["sandbox", "create", name, "--image", image, "--instance", instance_id, "--port", str(port)]
        if not auto_pause:
            args.append("--auto-pause=false")
        if plan:
            args += ["--plan", plan]
        secrets_ = []
        for k, v in env.items():
            args += ["--env", f"{k}={v}"]
            if k in secret_names:
                secrets_.append(v)
        return Command(tuple(args), tuple(secrets_))

    @staticmethod
    def delete_sandbox(sandbox_id: str) -> Command:
        return Command(("sandbox", "delete", sandbox_id, "--force"))

    # ---- lookups -----------------------------------------------------------------------------------------------------------
    def _find(self, listing: list[str], name: str) -> str | None:
        for line in self._run([self.bin, *listing]).splitlines():
            cols = line.split()
            if len(cols) > 1 and name in cols[1:]:
                return cols[0]
        return None

    def sandbox_ids(self) -> set[str]:
        return {ln.split()[0] for ln in self._run([self.bin, "sandbox", "list"]).splitlines()[2:] if ln.split()}

    def instance_id(self, name: str) -> str | None:
        return self._find(["instances", "list"], name)

    def sandbox_id(self, name: str) -> str | None:
        return self._find(["sandbox", "list"], name)

    def wait_instance(self, instance_id: str, timeout: float = 420, interval: float = 5, sleep=None, clock=None) -> None:
        """Wait until the instance's row in `instances list` says running (extensions and sandboxes need that)."""
        sleep, clock = sleep or time.sleep, clock or time.monotonic
        deadline = clock() + timeout
        while True:
            row = next((ln.split() for ln in self._run([self.bin, "instances", "list"]).splitlines() if ln.split()[:1] == [instance_id]), [])
            status = next((c.lower() for c in row if c.lower() in {"running", "creating", "provisioning", "starting", "failed", "error", "stopped"}), "unknown")
            if status == "running":
                return
            if status in {"failed", "error"}:
                raise AgentPGError(f"instance {instance_id} is {status} (see `agentpg instances describe {instance_id}`)")
            if clock() > deadline:
                raise AgentPGError(f"instance {instance_id} is still '{status}' after {int(timeout)} s; check `agentpg instances list`")
            sleep(interval)

    def sandbox_state(self, sandbox_id: str) -> tuple[str, str | None]:
        """(status, url) from `sandbox status`: the URL only appears once the sandbox is running."""
        text = self._run([self.bin, "sandbox", "status", sandbox_id])
        st = re.search(r"^\s*Status:\s*(\S+)", text, re.M)
        url = URL_LINE.search(text)
        return (st.group(1).lower() if st else "unknown"), (url.group(1).rstrip("/") if url else None)

    def wait_url(self, sandbox_id: str, timeout: float = 420, interval: float = 5, sleep=None, clock=None) -> str:
        """Wait until the sandbox is running and has a URL. A failed sandbox is an error naming the platform's reason."""
        sleep, clock = sleep or time.sleep, clock or time.monotonic
        deadline = clock() + timeout
        while True:
            status, url = self.sandbox_state(sandbox_id)
            if url and status in {"running", "ready", "active"}:
                return url
            if status in {"failed", "error", "deleted"}:
                detail = next((ln for ln in self._run([self.bin, "sandbox", "list"]).splitlines() if sandbox_id in ln), "")
                raise AgentPGError(f"sandbox {sandbox_id} is {status}: {detail.strip()[:300]} (see `agentpg sandbox logs {sandbox_id}`)")
            if clock() > deadline:
                raise AgentPGError(f"sandbox {sandbox_id} is still '{status}' after {int(timeout)} s; check `agentpg sandbox status {sandbox_id}`")
            sleep(interval)

    def sandbox_url(self, sandbox_id: str) -> str | None:
        return self.sandbox_state(sandbox_id)[1]


def token_from(output: str) -> str | None:
    """The database key (it is also the database password) from a `--json` answer of create or reset, or None if there is none."""
    try:
        data = json.loads(output)
    except ValueError:
        return None
    token = data.get("token") if isinstance(data, dict) else None
    return token if isinstance(token, str) and token else None
