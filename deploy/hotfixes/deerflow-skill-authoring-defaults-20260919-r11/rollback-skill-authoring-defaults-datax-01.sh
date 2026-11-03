#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
test "${1:-}" = '--state' || { echo 'usage requires --state' >&2; exit 2; }
STATE_FILE="${2:-}"
case "${STATE_FILE}" in /opt/deerflow-hotfix-evidence/deerflow-skill-authoring-defaults-20260919-r11/*/state.env) ;; *) echo 'unsafe state path' >&2; exit 1 ;; esac
test -s "${STATE_FILE}"
. "${STATE_FILE}"
test "${STATE_FILE}" = "${EVIDENCE_DIR}/state.env"
(cd "${EVIDENCE_DIR}" && sha256sum -c ROLLBACK.SHA256)
NS=deer-flow
REVISION_ENV='DEERFLOW_UNIFIED_MODEL_CONFIG_REVISION'

"${ROOT_DIR}/manage-skillhub-descriptions-datax-01.sh" --restore "${EVIDENCE_DIR}"
"${ROOT_DIR}/manage-skill-creator-datax-01.sh" --restore "${EVIDENCE_DIR}"
"${ROOT_DIR}/manage-weknora-model-datax-01.sh" --restore "${EVIDENCE_DIR}"
python3 "${ROOT_DIR}/shared_model_secret.py" --restore-patch "${EVIDENCE_DIR}/deer-flow-env.before.json" | \
  kubectl -n "${NS}" patch secret deer-flow-env --type=merge --patch-file=/dev/stdin
python3 - "${EVIDENCE_DIR}/config.before.yaml" <<'PY' | \
  kubectl -n deer-flow patch configmap deer-flow-config --type=merge --patch-file=/dev/stdin
import json,sys
print(json.dumps({"data":{"config.yaml":open(sys.argv[1],encoding="utf-8").read()}},separators=(",",":")))
PY
cat > "${EVIDENCE_DIR}/gateway.rollback.patch.json" <<JSON
{"spec":{"template":{"spec":{"containers":[{"name":"gateway","image":"${OLD_GATEWAY_IMAGE}","env":[{"name":"PPT_MASTER_DEEPSEEK_UPSTREAM_URL","value":"${OLD_PPT_MASTER_DEEPSEEK_UPSTREAM_URL}"}]}]}}}}
JSON
cat > "${EVIDENCE_DIR}/frontend.rollback.patch.json" <<JSON
{"spec":{"template":{"spec":{"containers":[{"name":"frontend","image":"${OLD_FRONTEND_IMAGE}"}]}}}}
JSON
cat > "${EVIDENCE_DIR}/weknora-frontend.rollback.patch.json" <<JSON
{"spec":{"template":{"spec":{"containers":[{"name":"frontend","image":"${OLD_WEKNORA_FRONTEND_IMAGE}"}]}}}}
JSON
cat > "${EVIDENCE_DIR}/ppt-master-runner.rollback.patch.json" <<JSON
{"spec":{"template":{"spec":{"containers":[{"name":"ppt-master-runner","env":[{"name":"${REVISION_ENV}","${PATCH_DIRECTIVE}":"delete"}]}]}}}}
JSON
kubectl -n "${NS}" patch deployment gateway --type=strategic --patch-file "${EVIDENCE_DIR}/gateway.rollback.patch.json"
kubectl -n "${NS}" patch deployment frontend --type=strategic --patch-file "${EVIDENCE_DIR}/frontend.rollback.patch.json"
kubectl -n "${NS}" patch deployment weknora-frontend --type=strategic --patch-file "${EVIDENCE_DIR}/weknora-frontend.rollback.patch.json"
kubectl -n "${NS}" patch deployment ppt-master-runner --type=strategic --patch-file "${EVIDENCE_DIR}/ppt-master-runner.rollback.patch.json"
for deployment in gateway frontend weknora-frontend ppt-master-runner; do
  kubectl -n "${NS}" rollout status "deployment/${deployment}" --timeout=900s
done
current_init="$(kubectl -n "${NS}" get deployment gateway -o jsonpath='{.spec.template.spec.initContainers[?(@.name=="seed-extensions-config")].image}')"
test "${current_init}" = "${OLD_GATEWAY_INIT_IMAGE}"
echo 'deerflow_skill_authoring_defaults_rollback=PASS'
echo 'gateway_frontend_config_skill_skillhub_weknora_and_shared_secret=RESTORED'
echo 'weknora_frontend_sidebar_labels=RESTORED'
echo 'mail_digital_employee=ROLLED_BACK_WITH_IMAGES'
