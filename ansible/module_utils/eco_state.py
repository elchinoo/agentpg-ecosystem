"""The recipe's secrets, encrypted at rest: ONE file, `.secrets/<fleet>/state.sealed`, an `apgkit-seal1$...` blob (AES-256-GCM under a key derived with scrypt
from the operator's passphrase: agentpg-kit `apgkit.sealed`, the same format and passphrase as ecoctl's state file). It holds what must never be on disk in the
clear: the database key of every instance (shown by AgentPG once), the generate-once secrets of the apps, and the secrets the services show once (client
secrets, gateway keys, merchant keys, webhook secrets).

Plain Python with no Ansible import, so the controller-side action plugin (plugins/action/eco_state.py) and the tests share it. Nothing here prints or returns
a secret except the maps the playbook asks for; a plaintext copy is never written (the temporary file next to the blob only ever holds ciphertext).

Also here, because they need the passphrase and the database key and so must run on the controller, in memory: building the sealed blob of an app and sending
the passphrase to a sealed app (`unseal`), as ecoctl's seal.py does.
"""
from __future__ import annotations

import json
import os
import re
import secrets as _secrets
import tempfile
import time
from urllib.parse import quote, urlsplit

ENV_PASSPHRASE = "ECOCTL_SEAL_PASSPHRASE"
FIELD = "state"
BLOB_ENV = "APGKIT_SEALED"
POSTGRES_URL = re.compile(r"postgres(?:ql)?://[^\s\"'<>\\]+")
GENERATED_LENGTH = 43
SAFE_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"


class EcoStateError(RuntimeError):
    """A failure whose message is safe to show: it never contains state content or the passphrase."""


def _kit():
    try:
        from apgkit import sealed
    except ImportError:
        raise EcoStateError("the Python package agentpg-kit (apgkit) is needed on the control node: pip install -e <agentpg-kit> in the environment that runs ansible-playbook") from None
    return sealed


def check_passphrase(pw: str) -> str:
    kit = _kit()
    try:
        kit.check_passphrase(pw)
    except kit.SealError as exc:
        raise EcoStateError(str(exc)) from None
    return pw


def passphrase(given: str | None = None, environ=None) -> str:
    """The operator's passphrase: the one handed in (a prompt), else ECOCTL_SEAL_PASSPHRASE. Never read from, or written to, a file."""
    pw = given or (environ if environ is not None else os.environ).get(ENV_PASSPHRASE)
    if not pw:
        raise EcoStateError(f"no passphrase: set {ENV_PASSPHRASE} in the environment of ansible-playbook, or run with -e eco_seal_passphrase_prompt=true (`ecoctl seal-passphrase` makes a strong one)")
    return check_passphrase(pw)


class EncState:
    """The decrypted content of state.sealed: {"apps": {key: {"db_key": ...}}, "generated": {"app/NAME": v}, "issued": {"app/kind": v}, "legacy": {...}}."""

    def __init__(self, path: str, pw: str):
        self.path, self._pw = str(path), pw
        self.exists = os.path.exists(self.path)
        self.data: dict = {"apps": {}, "generated": {}, "issued": {}}
        self._written: str | None = None
        if self.exists:
            self._load()
        for k in ("apps", "generated", "issued"):
            self.data.setdefault(k, {})

    def _load(self) -> None:
        kit = _kit()
        try:
            raw = open(self.path, encoding="ascii").read()
        except (OSError, UnicodeDecodeError):
            raise EcoStateError(f"cannot read {self.path} (it is not an encrypted state file)") from None
        if not raw.strip().startswith(kit.SEAL_PREFIX + "$"):
            raise EcoStateError(f"{self.path} is not an encrypted state file (apgkit-seal1$...)")
        try:
            text = kit.open_blob(raw.strip(), self._pw)[FIELD]
            self.data = json.loads(text)
        except (kit.SealError, KeyError, ValueError):
            raise EcoStateError(f"{self.path} could not be decrypted: wrong passphrase, or the file is damaged") from None
        if not isinstance(self.data, dict):
            raise EcoStateError(f"{self.path} is not a valid state file")
        self._written = json.dumps(self.data, sort_keys=True)

    def save(self) -> bool:
        """Write the blob if anything changed (atomically; only ciphertext ever touches the disk). True when it wrote."""
        text = json.dumps(self.data, sort_keys=True)
        if text == self._written:
            return False
        blob = _kit().seal({FIELD: text}, self._pw)
        directory = os.path.dirname(self.path) or "."
        os.makedirs(directory, mode=0o700, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".state-")             # created 0600
        try:
            with os.fdopen(fd, "w") as fh:
                fh.write(blob)
                fh.flush()
                os.fsync(fh.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        self._written = text
        self.exists = True
        return True

    # ---- content ---------------------------------------------------------------------------------------------------------
    def generate(self, names: list[str], overrides: dict | None = None) -> dict[str, str]:
        """Every generate-once secret of `names`, created on first use and stable afterwards. `overrides` (from an Ansible Vault file) win and are never stored."""
        gen = self.data["generated"]
        for n in names:
            if n not in gen and n not in (overrides or {}):
                gen[n] = "".join(_secrets.choice(SAFE_CHARS) for _ in range(GENERATED_LENGTH))
        ov = overrides or {}
        return {n: (ov[n] if n in ov else gen[n]) for n in names}

    def issued(self, names: list[str]) -> dict[str, str]:
        return {n: str(self.data["issued"].get(n, "")) for n in dict.fromkeys([*names, *self.data["issued"]])}

    def put_issued(self, name: str, value: str) -> None:
        self.data["issued"][name] = value

    def put_db_key(self, app: str, key: str) -> None:
        self.data["apps"].setdefault(app, {})["db_key"] = key

    def db_key(self, app: str) -> str | None:
        return (self.data["apps"].get(app) or {}).get("db_key")


# ---- an older recipe kept these as plain files: move them in, overwrite the old bytes, remove them ---------------------------------------------------
def _shred(path: str) -> None:
    """Overwrite a plaintext file in place before it is removed. Best effort: a copy-on-write filesystem, an SSD's wear levelling, snapshots and backups can keep the
    old content, which is why secrets that were ever plaintext should be rotated."""
    try:
        size = os.path.getsize(path)
        with open(path, "r+b") as fh:
            fh.write(b"\0" * size)
            fh.flush()
            os.fsync(fh.fileno())
    except OSError:
        pass
    try:
        os.unlink(path)
    except OSError:
        pass


def import_legacy(st: EncState, secrets_dir: str) -> list[str]:
    """Take the plaintext files of an older recipe (generated/<app>/<NAME>, issued/<app>/<kind>, vault/admin_password, the db_key in agentpg/<app>.json) into the
    encrypted state, overwrite them, remove them. Returns what was moved (names only, never values)."""
    moved: list[str] = []
    for sub, bucket in (("generated", "generated"), ("issued", "issued")):
        base = os.path.join(secrets_dir, sub)
        for root, _dirs, files in os.walk(base):
            for f in files:
                p = os.path.join(root, f)
                name = os.path.relpath(p, base).replace(os.sep, "/")
                try:
                    value = open(p, encoding="utf-8").read().split(" salt=")[0].strip()
                except (OSError, UnicodeDecodeError):
                    continue
                if value:
                    st.data[bucket].setdefault(name, value)
                    moved.append(f"{bucket}/{name}")
                _shred(p)
    pw_file = os.path.join(secrets_dir, "vault", "admin_password")
    if os.path.exists(pw_file):
        try:
            value = open(pw_file, encoding="utf-8").read().strip()
        except (OSError, UnicodeDecodeError):
            value = ""
        if value:
            st.data.setdefault("legacy", {}).setdefault("admin_password", value)         # the password an older recipe created the vaults with; kept encrypted, not lost
            moved.append("vault/admin_password")
        _shred(pw_file)
    agent_dir = os.path.join(secrets_dir, "agentpg")
    if os.path.isdir(agent_dir):
        for f in sorted(os.listdir(agent_dir)):
            p = os.path.join(agent_dir, f)
            if not f.endswith(".json"):
                continue
            try:
                doc = json.load(open(p, encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(doc, dict) and doc.get("db_key"):
                st.put_db_key(f[:-5], doc.pop("db_key"))
                moved.append(f"agentpg/{f[:-5]}/db_key")
                _shred(p)                                                        # the old file held the key: overwrite it, then write the rest without it
                with open(p, "w", encoding="utf-8") as fh:
                    json.dump(doc, fh, indent=2, sort_keys=True)
                os.chmod(p, 0o600)
    return moved


def remove_empty_dirs(secrets_dir: str) -> None:
    for sub in ("generated", "issued", "vault"):
        base = os.path.join(secrets_dir, sub)
        for root, _dirs, _files in os.walk(base, topdown=False):
            try:
                os.rmdir(root)
            except OSError:
                pass


# ---- the sealed blob of an app (ecoctl.seal.seal_env / database_url) ------------------------------------------------------------------------------------
def parse_connection(text: str) -> dict | None:
    """Host, port, database and query from `agentpg describe <instance> --json`: its postgres:// URL, else host/port/database fields. User and password are dropped."""
    m = POSTGRES_URL.search(text)
    if m:
        u = urlsplit(m.group(0))
        if u.hostname:
            return {"host": u.hostname, "port": u.port or 5432, "dbname": u.path.lstrip("/") or "postgres", "query": u.query}
    try:
        data = json.loads(text)
    except ValueError:
        return None

    def find(node):
        if isinstance(node, dict):
            low = {k.lower(): v for k, v in node.items()}
            host = low.get("host") or low.get("hostname")
            if isinstance(host, str) and host:
                db = low.get("database") or low.get("dbname") or low.get("db") or "postgres"
                port = low.get("port") or 5432
                return {"host": host, "port": int(port) if str(port).isdigit() else 5432, "dbname": str(db), "query": ""}
            for v in node.values():
                r = find(v)
                if r:
                    return r
        elif isinstance(node, list):
            for v in node:
                r = find(v)
                if r:
                    return r
        return None

    return find(data)


def database_url(describe_text: str, db_key: str | None) -> str | None:
    """As ecoctl.seal.database_url: host/port/database from `agentpg describe`, the user from its URL, the instance key as the password. None when either is missing."""
    if not db_key:
        return None
    conn = parse_connection(describe_text)
    m = POSTGRES_URL.search(describe_text)
    user = urlsplit(m.group(0)).username if m else None
    if not conn or not user:
        return None
    q = f"?{conn['query']}" if conn.get("query") else ""
    return f"postgresql://{quote(user, safe='')}:{quote(db_key, safe='')}@{conn['host']}:{conn['port']}/{quote(conn['dbname'], safe='')}{q}"


def seal_env(pw: str, secret_env: dict[str, str], dsn: str | None, drop: str | None = None) -> tuple[str, list[str]]:
    """The blob for APGKIT_SEALED: the secret environment (without the app's encryption key: the passphrase is the only unlock secret) plus DATABASE_URL."""
    notes: list[str] = []
    payload = {k: str(v) for k, v in secret_env.items() if k != drop}
    if dsn:
        payload["DATABASE_URL"] = dsn
    elif "DATABASE_URL" not in payload:
        notes.append("DATABASE_URL not sealed (no instance key or connection string): the app uses the platform's PG* settings")
    if not payload:
        raise EcoStateError("nothing to seal")
    return _kit().seal(payload, pw), notes


# ---- the operator's side of the unseal (ecoctl.seal.unseal) ---------------------------------------------------------------------------------------------
def seal_state(http, url: str) -> str | None:
    """`sealed`, `unsealed`, `disabled` (404) or None. `http(method, url, body=None) -> (status, json_or_None)`; it never raises."""
    try:
        status, doc = http("GET", url.rstrip("/") + "/_seal/status", None)
    except OSError:
        return None
    if status == 404:
        return "disabled"
    return (doc or {}).get("state") if status == 200 and isinstance(doc, dict) else None


def unseal(http, url: str, pw: str, *, timeout: float = 240, interval: float = 3, sleep=time.sleep, clock=time.monotonic) -> str:
    """Open the app with the passphrase (encrypted to a key that exists only in the app's process). Returns a one-line outcome; a wrong passphrase raises."""
    deadline = clock() + timeout
    while True:
        st = seal_state(http, url)
        if st in {"unsealed", "disabled"}:
            return "already open" if st == "unsealed" else "sealing is not enabled in this app"
        if st == "sealed":
            break
        if clock() > deadline:
            raise EcoStateError(f"{url} did not answer /_seal/status within {int(timeout)} s")
        sleep(interval)
    status, key = http("GET", url.rstrip("/") + "/_seal/key", None)
    if status != 200 or not isinstance(key, dict):
        raise EcoStateError(f"{url}: /_seal/key answered HTTP {status}")
    kit = _kit()
    status, doc = http("POST", url.rstrip("/") + "/_seal/unseal", kit.make_envelope(key["public_key"], pw))
    if status == 429:
        raise EcoStateError(f"{url}: too many failed attempts; wait a minute")
    if status == 401:
        raise EcoStateError(f"{url}: the passphrase did not open the blob (it is not the one this sandbox was sealed with)")
    if status >= 400:
        detail = str((doc or {}).get("detail", ""))[:160] if isinstance(doc, dict) else ""
        raise EcoStateError(f"{url}: unseal failed: HTTP {status} {detail}".strip())
    return f"unsealed (key fingerprint {key.get('fingerprint', '?')})"
