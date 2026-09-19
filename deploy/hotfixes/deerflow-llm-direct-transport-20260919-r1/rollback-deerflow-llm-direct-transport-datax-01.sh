#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
test "${1:-}" = --state || { echo 'usage requires --state STATE_FILE' >&2; exit 2; }
STATE_FILE="${2:-}"
case "${STATE_FILE}" in
  /opt/deerflow-hotfix-evidence/deerflow-llm-direct-transport-20260919-r1/*/state.env) ;;
  *) echo 'unsafe state path' >&2; exit 1 ;;
esac
test -s "${STATE_FILE}"
. "${STATE_FILE}"
test "${STATE_FILE}" = "${EVIDENCE_DIR}/state.env"
(
  cd "${EVIDENCE_DIR}"
  sha256sum -c ROLLBACK.SHA256
)
NS=deer-flow
current_image="$(kubectl -n "${NS}" get deployment gateway -o jsonpath='{.spec.template.spec.containers[?(@.name=="gateway")].image}')"
test "${current_image}" = "${EXPECTED_GATEWAY_IMAGE}"
kubectl -n "${NS}" patch deployment gateway --type=strategic \
  --patch-file "${EVIDENCE_DIR}/gateway.rollback.patch.json"
kubectl -n "${NS}" rollout status deployment/gateway --timeout=900s
kubectl -n "${NS}" get deployment gateway -o json > "${EVIDENCE_DIR}/gateway.after-rollback.json"
python3 - "${EVIDENCE_DIR}/gateway.before.json" "${EVIDENCE_DIR}/gateway.after-rollback.json" <<'PY'
import json,sys
names={"NO_PROXY","no_proxy"}
def selected(path):
    doc=json.load(open(path,encoding="utf-8"))
    containers=doc["spec"]["template"]["spec"]["containers"]
    gateway=next(x for x in containers if x["name"]=="gateway")
    return {x["name"]:x for x in gateway.get("env") or [] if x.get("name") in names}
before=selected(sys.argv[1])
after=selected(sys.argv[2])
if before != after:
    raise SystemExit("NO_PROXY environment rollback mismatch")
print("gateway_no_proxy_exact_rollback=PASS")
PY
echo 'deerflow_llm_direct_transport_rollback=PASS'
echo 'gateway_image=PRESERVED'
echo 'production_database_mutation=NONE'
