#!/usr/bin/python
# -*- coding: utf-8 -*-
from __future__ import annotations

DOCUMENTATION = r"""
module: agentpg_instance
short_description: Ensure an AgentPG database instance exists (through the agentpg CLI)
description:
  - Finds the instance by name, creates it with C(agentpg create --name N --size S --json) when missing, and waits until it is running.
  - The database key (also the database password) is shown by AgentPG only once, at creation. It is written to I(state_file) (mode 0600) and never returned.
  - Check mode only reads (C(agentpg instances list)) and reports whether it would create the instance.
options:
  name: {description: Instance name., required: true, type: str}
  size: {description: Database size., default: small, type: str}
  state_file: {description: JSON file that keeps the instance id and the database key (git-ignored, 0600)., required: true, type: path}
  wait: {description: Wait until the instance is running., default: true, type: bool}
  agentpg_bin: {description: The agentpg executable., default: agentpg, type: str}
  poll_interval: {description: Seconds between polls., default: 5, type: float}
  timeout: {description: Seconds to wait., default: 420, type: int}
"""
RETURN = r"""
instance_id: {description: The instance id., returned: always, type: str}
created: {description: Whether this run created it., returned: always, type: bool}
db_key_saved: {description: Whether the state file holds the database key (the value is never returned)., returned: always, type: bool}
"""

from ansible.module_utils.basic import AnsibleModule  # noqa: E402
from ansible.module_utils.eco_agentpg import AgentPGError, Cli, State, ensure_instance  # noqa: E402


def main():
    m = AnsibleModule(argument_spec=dict(name=dict(required=True), size=dict(default="small"), state_file=dict(type="path", required=True),
                                         wait=dict(type="bool", default=True), agentpg_bin=dict(default="agentpg"),
                                         poll_interval=dict(type="float", default=5), timeout=dict(type="int", default=420)), supports_check_mode=True)
    p = m.params
    try:
        out = ensure_instance(Cli(binary=p["agentpg_bin"], interval=p["poll_interval"], timeout=p["timeout"]), State(p["state_file"]), p["name"], p["size"],
                              p["wait"], m.check_mode)
    except AgentPGError as exc:
        m.fail_json(msg=str(exc))
    m.exit_json(**out)


if __name__ == "__main__":
    main()
