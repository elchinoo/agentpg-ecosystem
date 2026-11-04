#!/usr/bin/python
# -*- coding: utf-8 -*-
from __future__ import annotations

DOCUMENTATION = r"""
module: eco_state
short_description: The recipe's encrypted secret store (an action plugin that runs on the control node)
description:
  - The implementation is plugins/action/eco_state.py. It runs in the ansible-playbook process, so the passphrase and the secrets are never serialised into a module payload.
  - Reads or creates C(state.sealed), an C(apgkit-seal1$...) blob encrypted under the operator's passphrase (ECOCTL_SEAL_PASSPHRASE or I(passphrase)), generates the generate-once secrets, keeps the secrets a service shows once, builds the C(APGKIT_SEALED) blob of an app and unseals a sealed app. Use it with C(no_log true).
options:
  op: {description: What to do., choices: [load, put_issued, put_db_key, seal, unseal], default: load, type: str}
  path: {description: The state file (state_file of the playbook)., type: path}
  passphrase: {description: The passphrase from a prompt; default is ECOCTL_SEAL_PASSPHRASE., type: str, no_log: true}
  secrets_dir: {description: op=load, move the plaintext files of an older recipe from here into the state., type: path}
  names: {description: op=load, the generate-once secret names ("<app>/<NAME>")., type: list, elements: str}
  overrides: {description: op=load, values that win over generated ones and are never stored., type: dict, no_log: true}
  issued_names: {description: op=load, names to return as empty strings when not kept yet., type: list, elements: str}
  name: {description: op=put_issued, the name., type: str}
  value: {description: op=put_issued or put_db_key, the secret., type: str, no_log: true}
  app: {description: op=put_db_key and op=seal, the app key., type: str}
  instance_id: {description: op=seal, the instance whose connection details to read., type: str}
  secret_env: {description: op=seal, the secret environment of the app., type: dict, no_log: true}
  drop: {description: op=seal, a name to leave out of the blob (the app's own encryption key)., type: str}
  agentpg_bin: {description: op=seal, the agentpg executable., default: agentpg, type: str}
  url: {description: op=unseal, the app's URL., type: str}
  validate_certs: {description: op=unseal, verify the TLS certificate., default: true, type: bool}
  timeout: {description: op=unseal, seconds to wait for the app., default: 240, type: float}
"""
RETURN = r"""
secrets: {description: op=load, the generate-once secrets by name., returned: op=load, type: dict}
issued: {description: op=load, the kept secrets by name., returned: op=load, type: dict}
blob: {description: op=seal, the sealed blob., returned: op=seal, type: str}
"""
from ansible.module_utils.basic import AnsibleModule  # noqa: E402


def main():
    m = AnsibleModule(argument_spec={}, bypass_checks=True)
    m.fail_json(msg="eco_state is an action plugin (plugins/action/eco_state.py) and runs on the control node only")


if __name__ == "__main__":
    main()
