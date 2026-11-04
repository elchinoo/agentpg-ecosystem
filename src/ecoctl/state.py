"""What `apply` learned and generated: instance and sandbox ids, URLs, client secrets, generated secrets.

Lives in .ecosystem/state.json (git-ignored, mode 0600) as an `apgkit-seal1$...` blob: AES-256-GCM under a key derived (scrypt) from the operator's
passphrase (agentpg-kit `apgkit.sealed`). The passphrase is ECOCTL_SEAL_PASSPHRASE or a prompt, once per run, and is never written anywhere. The
file is encrypted in memory and only the blob is written (atomically, through a temporary file that only ever holds ciphertext), so no plaintext
copy of a secret reaches the disk. Client secrets are shown by the identity provider only once, so this file is the only copy: lose it, or the
passphrase, and `apply --rotate` issues new ones.

A state file from before this (plain JSON) is read and written back encrypted on the next save; its old bytes are overwritten first.
"""
import getpass
import json
import os
import secrets
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

from apgkit import sealed as kit
from apgkit.passwords import ScryptBusy

DEFAULT_DIR = Path(".ecosystem")
ENV_PASSPHRASE = "ECOCTL_SEAL_PASSPHRASE"
_FIELD = "state"


class StateError(RuntimeError):
    """A failure whose message is safe to show: it never contains state content or the passphrase."""


class State:
    def __init__(self, directory: Path = DEFAULT_DIR, ask: Callable[[str], str] | None = None,
                 announce: Callable[[str], None] | None = None):
        self.dir = Path(directory)
        self.path = self.dir / "state.json"
        self.ask = ask
        self.announce = announce or (lambda text: print(text, file=sys.stderr, flush=True))
        self._pw: str | None = None
        self._legacy = False                  # the file on disk is plaintext: rewrite it encrypted, overwrite the old bytes
        self._dirty = False                   # a migration changed the content: write it even if nothing else did
        self._written: str | None = None      # what the file holds now (as JSON text): an unchanged state is not written again
        self.data: dict = {"apps": {}, "identity_admin_key": None}
        if self.path.exists():
            self._load()

    # ---- the passphrase -------------------------------------------------------------------------------------------------
    def passphrase(self, creating: bool = False, ask: Callable[[str], str] | None = None) -> str:
        """The operator's passphrase: ECOCTL_SEAL_PASSPHRASE, the copy already obtained in this run, else a prompt (twice when a new file is being made)."""
        env = os.environ.get(ENV_PASSPHRASE)
        if env:
            pw = env
        elif self._pw:
            return self._pw
        else:
            prompt = ask or self.ask or getpass.getpass
            pw = prompt("Passphrase for the state file and the sealed apps (not stored anywhere; at least 20 characters, `ecoctl seal-passphrase` makes one): ")
            if creating and prompt("Again: ") != pw:
                raise StateError("the two entries differ")
        try:
            kit.check_passphrase(pw)
        except kit.SealError as exc:
            raise StateError(str(exc)) from None
        self._pw = pw
        return pw

    # ---- reading and writing the file -----------------------------------------------------------------------------------
    def _load(self) -> None:
        try:
            raw = self.path.read_bytes().decode()
        except (OSError, UnicodeDecodeError):
            raise StateError(f"cannot read {self.path} (it is neither an encrypted state file nor JSON)") from None
        if raw.lstrip().startswith(kit.SEAL_PREFIX + "$"):
            pw = self.passphrase(creating=False)
            try:
                text = kit.open_blob(raw, pw)[_FIELD]
            except ScryptBusy:
                raise StateError("the key derivation is busy; try again") from None
            except (kit.SealError, KeyError):
                raise StateError(f"{self.path} could not be decrypted: wrong passphrase, or the file is damaged") from None
        else:
            text, self._legacy = raw, True
        try:
            data = json.loads(text)
            if not isinstance(data, dict):
                raise ValueError
        except ValueError:
            raise StateError(f"{self.path} is not a valid state file") from None
        self.data = data
        self.data.setdefault("apps", {})
        if self._legacy:
            self.announce(f"state: {self.path} is a plaintext state file from an older ecoctl: it is rewritten encrypted with your passphrase on the next save, "
                          "and the old bytes are overwritten first")
        vault = self.data.get("vault")
        if isinstance(vault, dict) and "admin_password" in vault:           # ecoctl no longer keeps it anywhere
            del vault["admin_password"]
            if not vault:
                del self.data["vault"]
            self._dirty = True
            self.announce("state: the vault admin password an older ecoctl kept in this file is removed (it is now ECOCTL_ADMIN_PASSWORD, a prompt, or, for a sealed fleet, "
                          "the seal passphrase); the file is rewritten without it on the next save")
        self._written = None if (self._legacy or self._dirty) else json.dumps(self.data, sort_keys=True)

    def require_passphrase(self) -> None:
        """Ask for the passphrase now (before work that cannot be repeated), so a missing or mistyped one does not come after it."""
        self.passphrase(creating=not self.path.exists())

    def save(self) -> None:
        text = json.dumps(self.data, sort_keys=True)
        if text == self._written and not self._legacy:
            return
        pw = self.passphrase(creating=not self.path.exists())
        try:
            blob = kit.seal({_FIELD: text}, pw)
        except ScryptBusy:
            raise StateError("the key derivation is busy; try again") from None
        self.dir.mkdir(mode=0o700, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.dir, prefix=".state-")        # created 0600; only ciphertext is ever written to it
        try:
            with os.fdopen(fd, "w") as fh:
                fh.write(blob)
                fh.flush()
                os.fsync(fh.fileno())
            os.chmod(tmp, 0o600)
            if self._legacy:
                self._shred(self.path)
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        self._written = text
        if self._legacy:
            self._legacy = False
            self.announce(f"state: {self.path} was plaintext and is now encrypted; the old bytes were overwritten before the replace (best effort: see SECURITY.md)")
        self._dirty = False

    @staticmethod
    def _shred(path: Path) -> None:
        """Overwrite the old plaintext in place before it is replaced. Best effort: a copy-on-write filesystem, an SSD's wear levelling, snapshots and
        backups can keep the old content, which is why a leaked plaintext state should be treated as exposed and its secrets rotated."""
        try:
            size = path.stat().st_size
            with open(path, "r+b") as fh:
                fh.write(b"\0" * size)
                fh.flush()
                os.fsync(fh.fileno())
        except OSError:
            pass

    # ---- the content ----------------------------------------------------------------------------------------------------
    def app(self, key: str) -> dict:
        return self.data["apps"].setdefault(key, {})

    def secret(self, key: str, name: str) -> str:
        """A generated secret for an app, created on first use and stable afterwards."""
        gen = self.app(key).setdefault("generated", {})
        if name not in gen:
            gen[name] = secrets.token_urlsafe(32)
        return gen[name]

    def admin_key(self) -> str:
        if not self.data.get("identity_admin_key"):
            self.data["identity_admin_key"] = secrets.token_urlsafe(32)
        return self.data["identity_admin_key"]

    def mcp_jwt_secret(self) -> str:
        """The secret `ecoctl mcp serve --transport http` gives AgentPG's MCP server (A1P_JWT_SECRET): generated once, kept here, never printed."""
        mcp = self.data.setdefault("mcp", {})
        if not mcp.get("jwt_secret"):
            mcp["jwt_secret"] = secrets.token_urlsafe(48)
        return mcp["jwt_secret"]
