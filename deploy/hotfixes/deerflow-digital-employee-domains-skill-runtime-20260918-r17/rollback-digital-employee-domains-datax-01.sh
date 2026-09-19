#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
MODE="${1:-}"
STATE_FILE="${2:-}"
NS=deer-flow

die() {
  echo "digital_employee_domains_rollback=FAIL detail=$*" >&2
  exit 1
}

running_pod() {
  local selector="$1"
  local container="$2"
  kubectl -n "${NS}" get pods -l "${selector}" -o json | python3 -c '
import json, sys
container = sys.argv[1]
for pod in json.load(sys.stdin).get("items", []):
    meta = pod.get("metadata") or {}
    if meta.get("deletionTimestamp"):
        continue
    statuses = {item.get("name"): item for item in (pod.get("status") or {}).get("containerStatuses") or []}
    status = statuses.get(container) or {}
    if (pod.get("status") or {}).get("phase") == "Running" and status.get("ready"):
        print(meta.get("name", ""))
        break
' "${container}"
}

ROLLBACK_GATEWAY_POD=''
REMOTE_CATEGORY_SNAPSHOT=''
REMOTE_FILE_CONFIG_ARCHIVE=''

cleanup_remote() {
  if test -n "${ROLLBACK_GATEWAY_POD}"; then
    if test -n "${REMOTE_CATEGORY_SNAPSHOT}"; then
      kubectl -n "${NS}" exec "${ROLLBACK_GATEWAY_POD}" -c gateway -- \
        rm -f -- "${REMOTE_CATEGORY_SNAPSHOT}" >/dev/null 2>&1 || true
    fi
    if test -n "${REMOTE_FILE_CONFIG_ARCHIVE}"; then
      kubectl -n "${NS}" exec "${ROLLBACK_GATEWAY_POD}" -c gateway -- \
        rm -f -- "${REMOTE_FILE_CONFIG_ARCHIVE}" >/dev/null 2>&1 || true
    fi
  fi
}
trap cleanup_remote EXIT INT TERM

test "${MODE}" = '--state' || die 'usage_requires_--state'
case "${STATE_FILE}" in
  /opt/deerflow-hotfix-evidence/digital-employee-domains-skill-runtime-20260918-r17/*/state.env) ;;
  *) die 'unsafe_state_path' ;;
esac
test -f "${STATE_FILE}" || die 'state_file_missing'
# shellcheck disable=SC1090
. "${STATE_FILE}"
test "${STATE_FILE}" = "${EVIDENCE_DIR}/state.env" || die 'state_path_mismatch'
(cd "${EVIDENCE_DIR}" && sha256sum -c ROLLBACK.SHA256)

test "$(id -u)" = '0' || die 'must_run_as_root'
test "$(hostname -s)" = 'datax-01' || die 'must_run_on_datax-01'

CURRENT_GATEWAY_INIT_IMAGE="$(kubectl -n "${NS}" get deployment gateway -o jsonpath='{.spec.template.spec.initContainers[?(@.name=="seed-extensions-config")].image}')"
test "${CURRENT_GATEWAY_INIT_IMAGE}" = "${OLD_GATEWAY_INIT_IMAGE}" || die "gateway_init_image_changed=${CURRENT_GATEWAY_INIT_IMAGE}"
kubectl -n "${NS}" set image deployment/gateway \
  "gateway=${OLD_GATEWAY_IMAGE}"
kubectl -n "${NS}" rollout status deployment/gateway --timeout=600s
ROLLBACK_GATEWAY_POD="$(running_pod app=gateway gateway)"
test -n "${ROLLBACK_GATEWAY_POD}" || die 'restored_gateway_pod_not_found'

TRANSACTION_ID="$(basename -- "${EVIDENCE_DIR}")"
case "${TRANSACTION_ID}" in
  *[!0-9TZ]*) die 'invalid_transaction_id' ;;
esac

echo '===== RESTORE EXACT DATABASE AGENT CATEGORIES ====='
REMOTE_CATEGORY_SNAPSHOT="/tmp/digital-domains-agent-categories-${TRANSACTION_ID}.json"
kubectl -n "${NS}" exec -i "${ROLLBACK_GATEWAY_POD}" -c gateway -- \
  sh -ec 'umask 077; cat > "$1"' sh "${REMOTE_CATEGORY_SNAPSHOT}" \
  < "${EVIDENCE_DIR}/agent-categories.before.json"
kubectl -n "${NS}" exec -i "${ROLLBACK_GATEWAY_POD}" -c gateway -- \
  sh -ec 'cd /app/backend && PYTHONPATH=packages/harness:. .venv/bin/python - --restore "$1"' \
  sh "${REMOTE_CATEGORY_SNAPSHOT}" \
  < "${ROOT_DIR}/restore-agent-categories.py"
kubectl -n "${NS}" exec "${ROLLBACK_GATEWAY_POD}" -c gateway -- \
  rm -f -- "${REMOTE_CATEGORY_SNAPSHOT}"
REMOTE_CATEGORY_SNAPSHOT=''

echo '===== RESTORE EXACT FILE AGENT CONFIG BYTES ====='
REMOTE_FILE_CONFIG_ARCHIVE="/tmp/digital-domains-agent-configs-${TRANSACTION_ID}.zip"
kubectl -n "${NS}" exec -i "${ROLLBACK_GATEWAY_POD}" -c gateway -- \
  sh -ec 'umask 077; cat > "$1"' sh "${REMOTE_FILE_CONFIG_ARCHIVE}" \
  < "${EVIDENCE_DIR}/agent-config-files.before.zip"
kubectl -n "${NS}" exec -i "${ROLLBACK_GATEWAY_POD}" -c gateway -- \
  sh -ec 'cd /app/backend && .venv/bin/python - --archive "$1" --root /app/backend/.deer-flow' \
  sh "${REMOTE_FILE_CONFIG_ARCHIVE}" \
  < "${ROOT_DIR}/restore-file-agent-configs.py"
kubectl -n "${NS}" exec "${ROLLBACK_GATEWAY_POD}" -c gateway -- \
  rm -f -- "${REMOTE_FILE_CONFIG_ARCHIVE}"
REMOTE_FILE_CONFIG_ARCHIVE=''

"${ROOT_DIR}/manage-research-fund-settlement-skill-datax-01.sh" \
  --restore "${EVIDENCE_DIR}"
kubectl -n "${NS}" set image deployment/provisioner \
  "provisioner=${OLD_PROVISIONER_IMAGE}"
kubectl -n "${NS}" rollout status deployment/provisioner --timeout=600s
kubectl -n "${NS}" set image deployment/frontend "frontend=${OLD_FRONTEND_IMAGE}"
kubectl -n "${NS}" rollout status deployment/frontend --timeout=600s

echo 'digital_employee_domains_rollback=PASS'
echo 'application_images=RESTORED'
echo 'provisioner_image=RESTORED'
echo 'research_fund_settlement_skill=RESTORED'
echo 'gateway_init_image=PRESERVED'
echo 'database_agent_categories=RESTORED_EXACTLY'
echo 'file_agent_configs=RESTORED_EXACTLY'
echo "pre_rollout_agents_backup=${EVIDENCE_DIR}/agents.before.sql"
echo "pre_rollout_file_configs_backup=${EVIDENCE_DIR}/agent-config-files.before.zip"
