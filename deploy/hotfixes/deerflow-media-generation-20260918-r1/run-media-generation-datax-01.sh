#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
. "${ROOT_DIR}/IMAGE_LOCKS.env"
MODE="${1:-}"
NS=deer-flow
IMAGE_API_URL='http://10.57.2.43:30008/v1/images/generations'
VIDEO_API_URL='http://10.57.2.43:30009/v1/videos'
EVIDENCE_DIR="/opt/deerflow-hotfix-evidence/deerflow-media-generation-20260918-r1/$(date -u +%Y%m%dT%H%M%SZ)"
TEMP_POD=''
TARGET_APPLIED=no
ROLLBACK_ATTEMPTED=no

die() {
  echo "deerflow_media_generation=FAIL detail=$*" >&2
  return 1
}

cleanup() {
  if test -n "${TEMP_POD}"; then
    kubectl -n "${NS}" delete pod "${TEMP_POD}" --ignore-not-found=true --wait=false >/dev/null 2>&1 || true
    TEMP_POD=''
  fi
}

automatic_rollback() {
  local reason="$1"
  if test "${ROLLBACK_ATTEMPTED}" = 'yes'; then return 1; fi
  ROLLBACK_ATTEMPTED=yes
  echo "automatic_media_generation_rollback=START reason=${reason}" >&2
  if "${ROOT_DIR}/rollback-media-generation-datax-01.sh" --state "${EVIDENCE_DIR}/state.env"; then
    TARGET_APPLIED=no
    echo 'automatic_media_generation_rollback=PASS' >&2
    return 0
  fi
  echo "automatic_media_generation_rollback=FAIL state=${EVIDENCE_DIR}/state.env" >&2
  return 1
}

on_error() {
  local exit_code="$?"
  local line="${1:-unknown}"
  trap - ERR
  cleanup
  echo "deerflow_media_generation_failed_line=${line} exit_code=${exit_code}" >&2
  if test "${TARGET_APPLIED}" = 'yes'; then automatic_rollback "line=${line} exit_code=${exit_code}" || true; fi
  exit "${exit_code}"
}

on_signal() {
  local name="$1"
  local code="$2"
  trap - ERR INT TERM
  cleanup
  echo "deerflow_media_generation_signal=${name} exit_code=${code}" >&2
  if test "${TARGET_APPLIED}" = 'yes'; then automatic_rollback "signal=${name}" || true; fi
  exit "${code}"
}

trap 'on_error "$LINENO"' ERR
trap cleanup EXIT
trap 'on_signal INT 130' INT
trap 'on_signal TERM 143' TERM

case "${MODE}" in
  --preflight-only|--rollout) ;;
  *) echo "usage: $0 --preflight-only|--rollout" >&2; exit 2 ;;
esac

test "$(id -u)" = '0' || die 'must_run_as_root'
test "$(hostname -s)" = 'datax-01' || die 'must_run_on_datax-01'
test "$(uname -m)" = 'aarch64' || die 'requires_aarch64'
for command in kubectl python3 sha256sum curl; do command -v "${command}" >/dev/null || die "${command}_not_found"; done
(
  cd "${ROOT_DIR}"
  sha256sum -c PACKAGE.SHA256
)
python3 "${ROOT_DIR}/verify-media-generation-hotfix.py" --root "${ROOT_DIR}"
kubectl -n "${NS}" rollout status deployment/gateway --timeout=300s
kubectl -n "${NS}" rollout status deployment/nginx --timeout=300s

test -s "${ARTIFACT_DIR}/BASE_GATEWAY_IMAGE" || die 'build_base_record_missing'
BASE_GATEWAY_IMAGE="$(cat "${ARTIFACT_DIR}/BASE_GATEWAY_IMAGE")"
CURRENT_GATEWAY_IMAGE="$(kubectl -n "${NS}" get deployment gateway -o jsonpath='{.spec.template.spec.containers[?(@.name=="gateway")].image}')"
CURRENT_GATEWAY_INIT_IMAGE="$(kubectl -n "${NS}" get deployment gateway -o jsonpath='{.spec.template.spec.initContainers[?(@.name=="seed-extensions-config")].image}')"
test "${CURRENT_GATEWAY_IMAGE}" = "${BASE_GATEWAY_IMAGE}" || die "live_gateway_changed_after_build=${CURRENT_GATEWAY_IMAGE} expected=${BASE_GATEWAY_IMAGE}"
test -n "${CURRENT_GATEWAY_INIT_IMAGE}" || die 'gateway_init_image_missing'
echo "base_gateway_image=${BASE_GATEWAY_IMAGE}"
echo "target_gateway_image=${TARGET_GATEWAY_IMAGE}"
echo "gateway_init_image=${CURRENT_GATEWAY_INIT_IMAGE}"

kubectl -n "${NS}" get deployment gateway -o json | python3 -c '
import json, sys
names = {
    "DEERFLOW_IMAGE_GENERATION_API_URL",
    "DEERFLOW_VIDEO_GENERATION_API_URL",
    "DEERFLOW_IMAGE_GENERATION_TIMEOUT_SECONDS",
    "DEERFLOW_VIDEO_GENERATION_POLL_SECONDS",
}
doc = json.load(sys.stdin)
container = next(item for item in doc["spec"]["template"]["spec"]["containers"] if item["name"] == "gateway")
present = sorted(item.get("name") for item in container.get("env") or [] if item.get("name") in names)
assert not present, "media generation variables already exist: " + ",".join(present)
print("gateway_media_generation_environment_baseline=ABSENT")
'

wait_for_probe() {
  local pod="$1"
  local deadline="$(( $(date +%s) + 240 ))"
  while :; do
    phase="$(kubectl -n "${NS}" get pod "${pod}" -o jsonpath='{.status.phase}' 2>/dev/null || true)"
    reasons="$(kubectl -n "${NS}" get pod "${pod}" -o jsonpath='{range .status.containerStatuses[*]}{.state.waiting.reason}{" "}{end}' 2>/dev/null || true)"
    if test "${phase}" = 'Succeeded'; then echo "target_image_probe=PASS pod=${pod}"; return 0; fi
    case "${phase}:${reasons}" in
      Failed:*|*:ErrImageNeverPull*|*:ImagePullBackOff*|*:InvalidImageName*|*:CreateContainerConfigError*|*:CreateContainerError*|*:CrashLoopBackOff*)
        kubectl -n "${NS}" get pod "${pod}" -o wide >&2 || true
        kubectl -n "${NS}" logs "${pod}" --all-containers=true --tail=200 --timestamps >&2 || true
        kubectl -n "${NS}" describe pod "${pod}" >&2 || true
        return 1
        ;;
    esac
    if test "$(date +%s)" -ge "${deadline}"; then
      kubectl -n "${NS}" describe pod "${pod}" >&2 || true
      return 1
    fi
    sleep 2
  done
}

echo '===== THREE-NODE IMMUTABLE TARGET IMAGE PROBES ====='
for node in datax-01 datax-02 datax-03; do
  TEMP_POD="media-generation-image-probe-${node}"
  kubectl -n "${NS}" delete pod "${TEMP_POD}" --ignore-not-found=true --wait=true --timeout=120s >/dev/null
  cat <<YAML | kubectl -n "${NS}" apply -f -
apiVersion: v1
kind: Pod
metadata:
  name: ${TEMP_POD}
spec:
  nodeName: ${node}
  restartPolicy: Never
  automountServiceAccountToken: false
  containers:
    - name: probe
      image: ${TARGET_GATEWAY_IMAGE}
      imagePullPolicy: Never
      env:
        - name: DEERFLOW_IMAGE_GENERATION_API_URL
          value: ${IMAGE_API_URL}
        - name: DEERFLOW_VIDEO_GENERATION_API_URL
          value: ${VIDEO_API_URL}
      command: ["sh", "-ec"]
      args:
        - >-
          cd /app/backend && PYTHONPATH=packages/harness:. .venv/bin/python -c
          'from deerflow.community.media_generation import get_configured_media_generation_tools;
          from app.gateway.routers.artifacts import _read_artifact_payload;
          names=[tool.name for tool in get_configured_media_generation_tools()];
          assert names==["generate_image","generate_video"], names;
          assert callable(_read_artifact_payload);
          print("media_generation_target_runtime=PASS")'
YAML
  wait_for_probe "${TEMP_POD}"
  kubectl -n "${NS}" logs "${TEMP_POD}" -c probe
  kubectl -n "${NS}" delete pod "${TEMP_POD}" --wait=true --timeout=120s
  TEMP_POD=''
  echo "media_generation_target_image_node=${node}:PASS"
done

running_pod() {
  local selector="$1"
  local container="$2"
  kubectl -n "${NS}" get pods -l "${selector}" -o json | python3 -c '
import json, sys
name = sys.argv[1]
for pod in json.load(sys.stdin).get("items", []):
    meta = pod.get("metadata") or {}
    if meta.get("deletionTimestamp"):
        continue
    statuses = {item.get("name"): item for item in (pod.get("status") or {}).get("containerStatuses") or []}
    if (pod.get("status") or {}).get("phase") == "Running" and (statuses.get(name) or {}).get("ready"):
        print(meta.get("name", ""))
        break
' "${container}"
}

GATEWAY_POD="$(running_pod app=gateway gateway)"
PG_POD="$(running_pod app=postgres postgres)"
test -n "${GATEWAY_POD}" || die 'running_gateway_pod_not_found'
test -n "${PG_POD}" || die 'running_postgres_pod_not_found'
kubectl -n "${NS}" exec -i "${GATEWAY_POD}" -c gateway -- python3 - <<'PY'
import socket

for host, port in (("10.57.2.43", 30008), ("10.57.2.43", 30009)):
    for _sample in range(3):
        connection = socket.create_connection((host, port), timeout=5)
        connection.close()
    print("media_generation_endpoint_tcp=PASS endpoint=%s:%d samples=3" % (host, port))
PY
"${ROOT_DIR}/manage-media-generation-skills-datax-01.sh" --preflight

db_scalar() {
  kubectl -n "${NS}" exec "${PG_POD}" -c postgres -- \
    sh -ec 'exec psql -X -At -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "$1"' sh "$1"
}
activity_snapshot() {
  ACTIVE_RUNS="$(db_scalar "SELECT count(*) FROM runs WHERE status IN ('pending','running');")"
  ACTIVE_OA_JOBS="$(db_scalar "SELECT count(*) FROM oa_policy_flow_jobs WHERE status IN ('queued','launching','running');")"
  echo "active_deerflow_runs=${ACTIVE_RUNS}"
  echo "active_oa_jobs=${ACTIVE_OA_JOBS}"
}
activity_snapshot

if test "${MODE}" = '--preflight-only'; then
  echo 'deerflow_media_generation_preflight=PASS'
  echo 'production_workloads_mutated=no'
  echo 'production_database_mutation=NONE'
  echo 'weknora_mutation=NONE'
  if test "${ACTIVE_RUNS}" = '0' && test "${ACTIVE_OA_JOBS}" = '0'; then
    echo 'maintenance_gate_ready=yes'
  else
    echo 'maintenance_gate_ready=no'
  fi
  echo 'next_step=wait_for_zero_active_work_then_rerun_with_DEERFLOW_MEDIA_GENERATION_ROLLOUT_ACK_YES_and_--rollout'
  exit 0
fi

test "${DEERFLOW_MEDIA_GENERATION_ROLLOUT_ACK:-}" = 'YES' || die 'set_DEERFLOW_MEDIA_GENERATION_ROLLOUT_ACK=YES'
test "${ACTIVE_RUNS}" = '0' || die "active_deerflow_runs=${ACTIVE_RUNS}"
test "${ACTIVE_OA_JOBS}" = '0' || die "active_oa_jobs=${ACTIVE_OA_JOBS}"
install -d -m 0750 "${EVIDENCE_DIR}"
kubectl -n "${NS}" get deployment gateway -o json > "${EVIDENCE_DIR}/gateway.before.json"
printf 'EVIDENCE_DIR=%q\n' "${EVIDENCE_DIR}" > "${EVIDENCE_DIR}/state.env"
printf 'OLD_GATEWAY_IMAGE=%q\n' "${CURRENT_GATEWAY_IMAGE}" >> "${EVIDENCE_DIR}/state.env"
printf 'OLD_GATEWAY_INIT_IMAGE=%q\n' "${CURRENT_GATEWAY_INIT_IMAGE}" >> "${EVIDENCE_DIR}/state.env"
printf 'PATCH_DIRECTIVE=%q\n' '$patch' >> "${EVIDENCE_DIR}/state.env"
"${ROOT_DIR}/manage-media-generation-skills-datax-01.sh" --backup "${EVIDENCE_DIR}"
(
  cd "${EVIDENCE_DIR}"
  sha256sum gateway.before.json media-generation-skills.before.tar state.env > ROLLBACK.SHA256
  sha256sum -c ROLLBACK.SHA256
)

activity_snapshot
test "${ACTIVE_RUNS}" = '0' || die "active_deerflow_runs_after_backup=${ACTIVE_RUNS}"
test "${ACTIVE_OA_JOBS}" = '0' || die "active_oa_jobs_after_backup=${ACTIVE_OA_JOBS}"
echo 'post_backup_activity_race_recheck=PASS'
TARGET_APPLIED=yes
"${ROOT_DIR}/manage-media-generation-skills-datax-01.sh" --install "${EVIDENCE_DIR}"

activity_snapshot
test "${ACTIVE_RUNS}" = '0' || die "active_deerflow_runs_before_gateway=${ACTIVE_RUNS}"
test "${ACTIVE_OA_JOBS}" = '0' || die "active_oa_jobs_before_gateway=${ACTIVE_OA_JOBS}"
echo 'pre_gateway_activity_race_recheck=PASS'

cat > "${EVIDENCE_DIR}/gateway.target.patch.json" <<JSON
{
  "spec": {
    "template": {
      "spec": {
        "containers": [
          {
            "name": "gateway",
            "image": "${TARGET_GATEWAY_IMAGE}",
            "env": [
              {"name": "DEERFLOW_IMAGE_GENERATION_API_URL", "value": "${IMAGE_API_URL}"},
              {"name": "DEERFLOW_VIDEO_GENERATION_API_URL", "value": "${VIDEO_API_URL}"},
              {"name": "DEERFLOW_IMAGE_GENERATION_TIMEOUT_SECONDS", "value": "600"},
              {"name": "DEERFLOW_VIDEO_GENERATION_POLL_SECONDS", "value": "3"}
            ]
          }
        ]
      }
    }
  }
}
JSON
kubectl -n "${NS}" patch deployment gateway --type=strategic --patch-file "${EVIDENCE_DIR}/gateway.target.patch.json"
kubectl -n "${NS}" rollout status deployment/gateway --timeout=900s

CURRENT_GATEWAY_IMAGE="$(kubectl -n "${NS}" get deployment gateway -o jsonpath='{.spec.template.spec.containers[?(@.name=="gateway")].image}')"
test "${CURRENT_GATEWAY_IMAGE}" = "${TARGET_GATEWAY_IMAGE}" || die "unexpected_gateway_image=${CURRENT_GATEWAY_IMAGE}"
current_init="$(kubectl -n "${NS}" get deployment gateway -o jsonpath='{.spec.template.spec.initContainers[?(@.name=="seed-extensions-config")].image}')"
test "${current_init}" = "${CURRENT_GATEWAY_INIT_IMAGE}" || die "gateway_init_image_changed=${current_init}"
GATEWAY_POD="$(running_pod app=gateway gateway)"
test -n "${GATEWAY_POD}" || die 'new_gateway_pod_not_found'
kubectl -n "${NS}" exec -i "${GATEWAY_POD}" -c gateway -- \
  sh -ec 'cd /app/backend && PYTHONPATH=packages/harness:. .venv/bin/python -' <<'PY'
import os
from deerflow.community.media_generation import get_configured_media_generation_tools

assert os.environ["DEERFLOW_IMAGE_GENERATION_API_URL"] == "http://10.57.2.43:30008/v1/images/generations"
assert os.environ["DEERFLOW_VIDEO_GENERATION_API_URL"] == "http://10.57.2.43:30009/v1/videos"
assert [tool.name for tool in get_configured_media_generation_tools()] == ["generate_image", "generate_video"]
print("media_generation_live_tool_registration=PASS")
PY
NGINX_POD="$(running_pod app=nginx nginx)"
test -n "${NGINX_POD}" || die 'running_nginx_pod_not_found'
kubectl -n "${NS}" exec "${NGINX_POD}" -c nginx -- \
  wget -qO- -T 10 http://gateway:8001/health >/dev/null
kubectl -n "${NS}" exec "${NGINX_POD}" -c nginx -- \
  wget -qO- -T 10 http://127.0.0.1:2026/api/v1/auth/setup-status >/dev/null
"${ROOT_DIR}/manage-media-generation-skills-datax-01.sh" --status

TARGET_APPLIED=no
echo 'deerflow_media_generation_cutover=PASS'
echo 'official_image_generation_skill=PLATFORM_MODEL_ENABLED'
echo 'official_video_generation_skill=PLATFORM_MODEL_ENABLED'
echo 'ordinary_chat_media_tools=ENABLED'
echo 'large_video_artifact_streaming=ENABLED'
echo 'production_database_mutation=NONE'
echo 'weknora_mutation=NONE'
echo 'sandbox_mutation=NONE'
echo "rollback_state=${EVIDENCE_DIR}/state.env"
echo 'next_step=run_status_then_acceptance_test_in_a_new_DeerFlow_chat'
