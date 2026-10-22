"""The `agentpg` command line, wrapped. Output is parsed the way the deployment tutorial does it: the id is the first column of
the listing line that names the object, and the URL follows `URL:` in `sandbox status`."""
import re
import subprocess
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
        return Command(("create", "--name", name, "--size", size))

    @staticmethod
    def extension(instance_id: str, ext: str) -> Command:
        return Command(("extensions", "create", instance_id, ext))

    @staticmethod
    def create_sandbox(name: str, image: str, instance_id: str, port: int, env: dict[str, str], secret_names: set[str]) -> Command:
        args = ["sandbox", "create", name, "--image", image, "--instance", instance_id, "--port", str(port)]
        secrets_ = []
        for k, v in env.items():
            args += ["--env", f"{k}={v}"]
            if k in secret_names:
                secrets_.append(v)
        return Command(tuple(args), tuple(secrets_))

    # ---- lookups -----------------------------------------------------------------------------------------------------------
    def _find(self, listing: list[str], name: str) -> str | None:
        for line in self._run([self.bin, *listing]).splitlines():
            cols = line.split()
            if len(cols) > 1 and name in cols[1:]:
                return cols[0]
        return None

    def instance_id(self, name: str) -> str | None:
        return self._find(["instances", "list"], name)

    def sandbox_id(self, name: str) -> str | None:
        return self._find(["sandbox", "list"], name)

    def sandbox_url(self, sandbox_id: str) -> str | None:
        m = URL_LINE.search(self._run([self.bin, "sandbox", "status", sandbox_id]))
        return m.group(1).rstrip("/") if m else None
