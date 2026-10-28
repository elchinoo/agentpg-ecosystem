#!/usr/bin/python
# -*- coding: utf-8 -*-
from __future__ import annotations

DOCUMENTATION = r"""
module: agentpg_extension
short_description: Ensure extensions are created on an AgentPG instance (through the agentpg CLI)
description:
  - Runs C(agentpg extensions create EXT INSTANCE) (name first) for each extension not yet recorded in I(state_file), one at a time, after the instance is running.
  - An answer saying the extension already exists counts as done. A failure keeps what was done and the next run does the rest.
  - Check mode reports what it would create and changes nothing.
options:
  instance_id: {description: The instance id (empty in check mode when the instance does not exist yet)., required: true, type: str}
  names: {description: Extension names., required: true, type: list, elements: str}
  state_file: {description: The same JSON file as agentpg_instance., required: true, type: path}
  agentpg_bin: {description: The agentpg executable., default: agentpg, type: str}
  poll_interval: {description: Seconds between polls., default: 5, type: float}
  timeout: {description: Seconds to wait., default: 420, type: int}
"""
RETURN = r"""
created: {description: Extensions created by this run., returned: always, type: list}
"""

from ansible.module_utils.basic import AnsibleModule  # noqa: E402
from ansible.module_utils.eco_agentpg import AgentPGError, Cli, State, ensure_extensions  # noqa: E402


def main():
    m = AnsibleModule(argument_spec=dict(instance_id=dict(required=True), names=dict(type="list", elements="str", required=True), state_file=dict(type="path", required=True),
                                         agentpg_bin=dict(default="agentpg"), poll_interval=dict(type="float", default=5), timeout=dict(type="int", default=420)),
                      supports_check_mode=True)
    p = m.params
    try:
        out = ensure_extensions(Cli(binary=p["agentpg_bin"], interval=p["poll_interval"], timeout=p["timeout"]), State(p["state_file"]), p["instance_id"], p["names"], m.check_mode)
    except AgentPGError as exc:
        m.fail_json(msg=str(exc))
    m.exit_json(**out)


if __name__ == "__main__":
    main()
