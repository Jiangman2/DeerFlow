#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
MODE="${1:-}"
STATE_FILE="${2:-}"
NS=deer-flow

die() {
  echo "deerflow_media_generation_rollback=FAIL detail=$*" >&2
  exit 1
}

test "${MODE}" = '--state' || die 'usage_requires_--state'
case "${STATE_FILE}" in
  /opt/deerflow-hotfix-evidence/deerflow-media-generation-20260918-r1/*/state.env) ;;
  *) die 'unsafe_state_path' ;;
esac
test -s "${STATE_FILE}" || die 'state_file_missing'
# shellcheck disable=SC1090
. "${STATE_FILE}"
test "${STATE_FILE}" = "${EVIDENCE_DIR}/state.env" || die 'state_path_mismatch'
(cd "${EVIDENCE_DIR}" && sha256sum -c ROLLBACK.SHA256)
test "$(id -u)" = '0' || die 'must_run_as_root'
test "$(hostname -s)" = 'datax-01' || die 'must_run_on_datax-01'

cat > "${EVIDENCE_DIR}/gateway.rollback.patch.json" <<JSON
{
  "spec": {
    "template": {
      "spec": {
        "containers": [
          {
            "name": "gateway",
            "image": "${OLD_GATEWAY_IMAGE}",
            "env": [
              {"name": "DEERFLOW_IMAGE_GENERATION_API_URL", "${PATCH_DIRECTIVE}": "delete"},
              {"name": "DEERFLOW_VIDEO_GENERATION_API_URL", "${PATCH_DIRECTIVE}": "delete"},
              {"name": "DEERFLOW_IMAGE_GENERATION_TIMEOUT_SECONDS", "${PATCH_DIRECTIVE}": "delete"},
              {"name": "DEERFLOW_VIDEO_GENERATION_POLL_SECONDS", "${PATCH_DIRECTIVE}": "delete"}
            ]
          }
        ]
      }
    }
  }
}
JSON
kubectl -n "${NS}" patch deployment gateway --type=strategic \
  --patch-file "${EVIDENCE_DIR}/gateway.rollback.patch.json"
kubectl -n "${NS}" rollout status deployment/gateway --timeout=900s
current_init="$(kubectl -n "${NS}" get deployment gateway -o jsonpath='{.spec.template.spec.initContainers[?(@.name=="seed-extensions-config")].image}')"
test "${current_init}" = "${OLD_GATEWAY_INIT_IMAGE}" || die "gateway_init_image_changed=${current_init}"

"${ROOT_DIR}/manage-media-generation-skills-datax-01.sh" --restore "${EVIDENCE_DIR}"

echo 'deerflow_media_generation_rollback=PASS'
echo 'gateway_image_and_environment=RESTORED'
echo 'image_and_video_skill_instructions=RESTORED_EXACTLY'
echo 'production_database_mutation=NONE'
echo 'weknora_mutation=NONE'
