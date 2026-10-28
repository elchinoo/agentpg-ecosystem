#!/usr/bin/python
# -*- coding: utf-8 -*-
from __future__ import annotations

DOCUMENTATION = r"""
module: agentpg_sandbox
short_description: Ensure an AgentPG sandbox runs an image with an environment (through the agentpg CLI)
description:
  - "With I(state=absent) deletes the sandbox named I(name) (C(agentpg sandbox delete ID --force)). The id is resolved by name from C(agentpg sandbox list) at run time. A name not in I(allowed_names) is refused. Already gone is not a change. Only I(name), I(state_file) and I(allowed_names) are used then."
  - With I(state=present), creates the sandbox (C(agentpg sandbox create NAME --image I --instance ID --port P [--auto-pause=false] [--plan X] --env K=V ...)) when missing and waits for its URL.
  - An existing sandbox is kept. When its environment differs from the one it was created with (compared by hash, kept in I(state_file)) the module reports
    it (C(env_differs)); with I(on_change=recreate), or I(recreate=true), it deletes the sandbox and creates it again on the same database.
  - A sandbox's URL contains its id, so a recreated sandbox has a new URL and the apps that use it need their environment (and so a recreation) too.
  - "I(secret_env) is C(no_log), so none of its values appear in results or logs. I(env) holds the non-secret ones, which must stay visible, because Ansible would scrub short values such as C(1) or C(true) from every result."
  - All values pass through the process list of this machine for a moment, as with ecoctl.
  - Check mode only reads and reports.
options:
  state: {description: Whether the sandbox should exist., choices: [present, absent], default: present, type: str}
  allowed_names: {description: With state=absent, the only names that may be deleted; any other name is refused., type: list, elements: str}
  name: {description: Sandbox name., required: true, type: str}
  image: {description: Image reference (state=present)., type: str}
  instance_id: {description: The instance id (state=present; empty in check mode when the instance does not exist yet)., type: str}
  port: {description: Port the app listens on., default: 8000, type: int}
  env: {description: Non-secret environment (state=present)., type: dict}
  secret_env: {description: Environment values that are secrets (generated keys, client secrets, API keys)., default: {}, type: dict, no_log: true}
  auto_pause: {description: Pause the sandbox when idle., default: true, type: bool}
  plan: {description: Compute plan (small, medium, large)., type: str}
  recreate: {description: Delete and create it again now., default: false, type: bool}
  on_change: {description: What to do when the environment differs., choices: [report, recreate], default: report, type: str}
  state_file: {description: The same JSON file as agentpg_instance., required: true, type: path}
  agentpg_bin: {description: The agentpg executable., default: agentpg, type: str}
  poll_interval: {description: Seconds between polls., default: 5, type: float}
  timeout: {description: Seconds to wait., default: 420, type: int}
"""
RETURN = r"""
sandbox_id: {description: The sandbox id., returned: always, type: str}
url: {description: The sandbox's public URL., returned: always, type: str}
env_differs: {description: The sandbox exists with another environment than requested and was left alone., returned: always, type: bool}
created: {returned: always, type: bool, description: This run created it.}
recreated: {returned: always, type: bool, description: This run deleted it for recreation.}
"""

from ansible.module_utils.basic import AnsibleModule  # noqa: E402
from ansible.module_utils.eco_agentpg import AgentPGError, Cli, State, ensure_sandbox, ensure_sandbox_absent  # noqa: E402


def main():
    m = AnsibleModule(argument_spec=dict(state=dict(default="present", choices=["present", "absent"]), allowed_names=dict(type="list", elements="str"),
                                         name=dict(required=True), image=dict(), instance_id=dict(), port=dict(type="int", default=8000),
                                         env=dict(type="dict"), secret_env=dict(type="dict", default={}, no_log=True), auto_pause=dict(type="bool", default=True), plan=dict(),
                                         recreate=dict(type="bool", default=False), on_change=dict(default="report", choices=["report", "recreate"]),
                                         state_file=dict(type="path", required=True), agentpg_bin=dict(default="agentpg"),
                                         poll_interval=dict(type="float", default=5), timeout=dict(type="int", default=420)),
                      required_if=[("state", "present", ("image", "instance_id", "env"))], supports_check_mode=True)
    p = m.params
    if p["state"] == "absent":
        try:
            m.exit_json(**ensure_sandbox_absent(Cli(binary=p["agentpg_bin"], interval=p["poll_interval"], timeout=p["timeout"]), State(p["state_file"]), p["name"],
                                                p["allowed_names"], m.check_mode))
        except AgentPGError as exc:
            m.fail_json(msg=str(exc))
    env = {k: str(v) for k, v in {**p["env"], **p["secret_env"]}.items()}
    try:
        out = ensure_sandbox(Cli(binary=p["agentpg_bin"], interval=p["poll_interval"], timeout=p["timeout"]), State(p["state_file"]), p["name"], p["image"],
                             p["instance_id"], p["port"], env, auto_pause=p["auto_pause"], plan=p["plan"], recreate=p["recreate"], on_change=p["on_change"],
                             check=m.check_mode)
    except AgentPGError as exc:
        m.fail_json(msg=str(exc))
    m.exit_json(**out)


if __name__ == "__main__":
    main()
