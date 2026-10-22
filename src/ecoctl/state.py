"""What `apply` learned and generated: instance and sandbox ids, URLs, client secrets, generated secrets.

Lives in .ecosystem/state.json (git-ignored, mode 0600). Client secrets are shown by the identity provider only once, so
this file is the only copy: lose it and `apply --rotate` issues new ones.
"""
import json
import os
import secrets
import tempfile
from pathlib import Path

DEFAULT_DIR = Path(".ecosystem")


class State:
    def __init__(self, directory: Path = DEFAULT_DIR):
        self.dir = Path(directory)
        self.path = self.dir / "state.json"
        self.data: dict = {"apps": {}, "identity_admin_key": None}
        if self.path.exists():
            self.data = json.loads(self.path.read_text())
            self.data.setdefault("apps", {})

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

    def save(self) -> None:
        self.dir.mkdir(mode=0o700, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.dir, prefix=".state-")
        with os.fdopen(fd, "w") as fh:
            json.dump(self.data, fh, indent=2, sort_keys=True)
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)
