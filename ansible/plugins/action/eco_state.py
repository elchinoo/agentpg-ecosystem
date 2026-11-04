"""Action plugin `eco_state`: the recipe's encrypted secret store, run on the control node, in memory.

Why an action plugin and not a module: a module's arguments are serialised into a payload for the target. Here the passphrase and every secret stay in the
ansible-playbook process (and the one encrypted file). The passphrase is ECOCTL_SEAL_PASSPHRASE from the environment of ansible-playbook, or `passphrase:`
from a hidden prompt (common/tasks/passphrase.yml). All tasks that use this action carry `no_log: true`.

  op: load        read (and, once, create) the state; generate the generate-once secrets; return {secrets, issued}; move the plaintext files of an older recipe in
  op: put_issued  keep one secret a service showed once (name, value)
  op: put_db_key  keep the database key AgentPG showed once (app, value)
  op: seal        the APGKIT_SEALED blob of an app: its secret environment plus DATABASE_URL built from `agentpg describe` and the kept database key
  op: unseal      send the passphrase, encrypted to the app's ephemeral key, to a sealed app
The shared code is module_utils/eco_state.py (also what tests/test_ansible_recipe.py runs).
"""
from __future__ import annotations

import importlib.util
import json
import os
import ssl
import urllib.error
import urllib.request

from ansible.plugins.action import ActionBase

_HERE = os.path.dirname(os.path.abspath(__file__))


def _shared(name: str):
    path = os.path.join(_HERE, "..", "..", "module_utils", f"{name}.py")
    spec = importlib.util.spec_from_file_location(f"_eco_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _http(validate_certs: bool):
    ctx = ssl.create_default_context() if validate_certs else ssl._create_unverified_context()      # noqa: S323  (the demo proxy's own CA: eco_validate_certs says so)
    # proxies from the environment only: the system lookup (_scproxy) crashes a forked Ansible worker on macOS
    opener = urllib.request.build_opener(urllib.request.ProxyHandler(urllib.request.getproxies_environment()), urllib.request.HTTPSHandler(context=ctx))

    def call(method: str, url: str, body):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json", "Accept": "application/json"})
        try:
            with opener.open(req, timeout=60) as r:      # noqa: S310  (https/http URL of the app, from the playbook)
                raw, status = r.read(), r.status
        except urllib.error.HTTPError as exc:
            raw, status = exc.read(), exc.code
        try:
            return status, json.loads(raw) if raw else None
        except ValueError:
            return status, None
    return call


class ActionModule(ActionBase):
    _supports_check_mode = True
    TRANSFERS_FILES = False

    def run(self, tmp=None, task_vars=None):
        result = super().run(tmp, task_vars)
        a = dict(self._task.args)
        op = a.get("op", "load")
        es = _shared("eco_state")
        check = bool(self._task.check_mode or (task_vars or {}).get("ansible_check_mode"))
        try:
            return {**result, **self._dispatch(es, op, a, check)}
        except es.EcoStateError as exc:
            return {**result, "failed": True, "msg": str(exc)}

    def _state(self, es, a):
        pw = es.passphrase(a.get("passphrase"))
        return es.EncState(a["path"], pw), pw

    def _dispatch(self, es, op: str, a: dict, check: bool) -> dict:
        if op == "unseal":
            if check:
                return {"changed": False, "msg": "check mode: not unsealed"}
            pw = es.passphrase(a.get("passphrase"))
            msg = es.unseal(_http(bool(a.get("validate_certs", True))), a["url"], pw, timeout=float(a.get("timeout", 240)), interval=float(a.get("interval", 3)))
            return {"changed": msg.startswith("unsealed"), "msg": msg}
        st, pw = self._state(es, a)
        if op == "load":
            moved = es.import_legacy(st, a["secrets_dir"]) if a.get("secrets_dir") and not check else []
            secrets = st.generate(list(a.get("names") or []), a.get("overrides") or {})
            wrote = False if check else st.save()
            if moved:
                es.remove_empty_dirs(a["secrets_dir"])
            return {"changed": bool(wrote), "secrets": secrets, "issued": st.issued(list(a.get("issued_names") or [])),
                    "moved": moved, "msg": ("moved into the encrypted state: " + ", ".join(moved)) if moved else "state loaded"}
        if op == "put_issued":
            if check:
                return {"changed": False}
            st.put_issued(str(a["name"]), str(a["value"]))
            st.save()
            return {"changed": True, "msg": f"kept {a['name']} in the encrypted state"}
        if op == "put_db_key":
            if check:
                return {"changed": False}
            st.put_db_key(str(a["app"]), str(a["value"]))
            st.save()
            return {"changed": True, "msg": f"kept the database key of {a['app']} in the encrypted state"}
        if op == "seal":
            ap = _shared("eco_agentpg")
            describe = ""
            if st.db_key(a["app"]) and a.get("instance_id"):
                try:
                    describe = ap.Cli(binary=a.get("agentpg_bin", "agentpg")).run(["describe", a["instance_id"], "--json"])
                except ap.AgentPGError:
                    describe = ""
            blob, notes = es.seal_env(pw, a.get("secret_env") or {}, es.database_url(describe, st.db_key(a["app"])), a.get("drop"))
            return {"changed": False, "blob": blob, "notes": notes}
        raise es.EcoStateError(f"eco_state: unknown op {op}")


