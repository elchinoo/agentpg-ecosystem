#!/usr/bin/env bash
# The throwaway fleet `ecoans`, built from a copy of ../ecosystem.yaml, so it never touches the real fleet `eco`.
#   ./throwaway.sh apply      site.yml (create or bring up to date)
#   ./throwaway.sh verify     verify.yml
#   ./throwaway.sh preview    destroy.yml --check: lists what a teardown would delete, deletes nothing
#   ./throwaway.sh destroy    destroy.yml: deletes the ecoans sandboxes and instances and its local secrets
set -euo pipefail
cd "$(dirname "$0")"
FLEET=ecoans
MANIFEST=/tmp/ecosystem-ansible-test.yaml
sed "s/^fleet: eco /fleet: $FLEET /" ../ecosystem.yaml > "$MANIFEST"
grep -q "^fleet: $FLEET " "$MANIFEST" || { echo "could not set fleet: $FLEET in $MANIFEST" >&2; exit 1; }
PB=(.venv/bin/ansible-playbook -e "eco_manifest_path=$MANIFEST")
case "${1:-}" in
  apply)   agentpg account; "${PB[@]}" site.yml ;;
  verify)  "${PB[@]}" verify.yml ;;
  preview) "${PB[@]}" destroy.yml --check -e "eco_destroy_confirm=$FLEET" ;;
  destroy) "${PB[@]}" destroy.yml -e "eco_destroy_confirm=$FLEET" -e eco_destroy_secrets=true ;;
  *) echo "usage: $0 apply|verify|preview|destroy" >&2; exit 2 ;;
esac
