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

test "${MODE}" = '--state' || die 'usage_requires_--state'
case "${STATE_FILE}" in
  /opt/deerflow-hotfix-evidence/digital-employee-domains-skill-runtime-20260918-r14/*/state.env) ;;
  *) die 'unsafe_state_path' ;;
esac
test -f "${STATE_FILE}" || die 'state_file_missing'
# shellcheck disable=SC1090
. "${STATE_FILE}"
test "${STATE_FILE}" = "${EVIDENCE_DIR}/state.env" || die 'state_path_mismatch'
(cd "${EVIDENCE_DIR}" && sha256sum -c ROLLBACK.SHA256)

test "$(id -u)" = '0' || die 'must_run_as_root'
test "$(hostname -s)" = 'datax-01' || die 'must_run_on_datax-01'

kubectl -n "${NS}" set image deployment/frontend "frontend=${OLD_FRONTEND_IMAGE}"
kubectl -n "${NS}" rollout status deployment/frontend --timeout=600s
kubectl -n "${NS}" set image deployment/provisioner \
  "provisioner=${OLD_PROVISIONER_IMAGE}"
kubectl -n "${NS}" rollout status deployment/provisioner --timeout=600s
"${ROOT_DIR}/manage-research-fund-settlement-skill-datax-01.sh" \
  --restore "${EVIDENCE_DIR}"
CURRENT_GATEWAY_INIT_IMAGE="$(kubectl -n "${NS}" get deployment gateway -o jsonpath='{.spec.template.spec.initContainers[?(@.name=="seed-extensions-config")].image}')"
test "${CURRENT_GATEWAY_INIT_IMAGE}" = "${OLD_GATEWAY_INIT_IMAGE}" || die "gateway_init_image_changed=${CURRENT_GATEWAY_INIT_IMAGE}"
kubectl -n "${NS}" set image deployment/gateway \
  "gateway=${OLD_GATEWAY_IMAGE}"
kubectl -n "${NS}" rollout status deployment/gateway --timeout=600s

echo 'digital_employee_domains_rollback=PASS'
echo 'application_images=RESTORED'
echo 'provisioner_image=RESTORED'
echo 'research_fund_settlement_skill=RESTORED'
echo 'gateway_init_image=PRESERVED'
echo 'category_data_normalization=PRESERVED_SCHEMA_COMPATIBLE'
echo "pre_rollout_agents_backup=${EVIDENCE_DIR}/agents.before.sql"
echo "pre_rollout_file_configs_backup=${EVIDENCE_DIR}/agent-config-files.before.zip"
