#!/usr/bin/env bash
# Run before any PPT Master rollout. Only disposable, PVC-isolated Pods are created.
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
NS=deer-flow
CREATED_PODS=()
TEMP_FILES=()

fail() { echo "ppt_master_dual_container_canary=FAIL detail=$*" >&2; exit 1; }
cleanup() {
  local pod file
  for pod in "${CREATED_PODS[@]}"; do
    kubectl -n "${NS}" delete pod "${pod}" --ignore-not-found=true \
      --wait=true --timeout=180s >/dev/null 2>&1 || true
  done
  for file in "${TEMP_FILES[@]}"; do rm -f -- "${file}"; done
}
trap cleanup EXIT

test "$(id -u)" -eq 0 || fail must_run_as_root
test "$(hostname -s)" = datax-01 || fail must_run_on_datax_01
export KUBECONFIG=/etc/kubernetes/admin.conf
unset SSL_CERT_FILE REQUESTS_CA_BUNDLE CURL_CA_BUNDLE
for command in kubectl python3 mktemp; do
  command -v "${command}" >/dev/null || fail "missing_command=${command}"
done

AIO_IMAGE="${1:?usage: $0 AIO_IMAGE AGENT_HOST_IMAGE CODEX_CONFIG MODEL_CATALOG}"
AGENT_IMAGE="${2:?missing AGENT_HOST_IMAGE}"
CODEX_CONFIG="${3:?missing CODEX_CONFIG}"
MODEL_CATALOG="${4:?missing MODEL_CATALOG}"
test -s "${CODEX_CONFIG}" && test -s "${MODEL_CATALOG}" || fail missing_agent_profile
test "${AIO_IMAGE}" != "${AGENT_IMAGE}" || fail shared_aio_agent_image

SOURCE_POD="$(kubectl -n "${NS}" get pods -l app=deer-flow-sandbox -o json |
  python3 -c '
import json,sys
aio=sys.argv[1]
for pod in json.load(sys.stdin).get("items",[]):
    if pod["metadata"].get("deletionTimestamp"):
        continue
    if not any(row.get("type")=="Ready" and row.get("status")=="True" for row in pod.get("status",{}).get("conditions") or []):
        continue
    containers=pod.get("spec",{}).get("containers") or []
    if len(containers)==2 and any(row.get("name")=="sandbox" and row.get("image")==aio for row in containers):
        print(pod["metadata"]["name"])
        break
' "${AIO_IMAGE}")"
test -n "${SOURCE_POD}" || fail no_ready_nonterminating_baseline_sandbox

SOURCE_JSON="$(mktemp /tmp/ppt-master-r16-source.XXXXXXXX.json)"
TEMP_FILES+=("${SOURCE_JSON}")
kubectl -n "${NS}" get pod "${SOURCE_POD}" -o json >"${SOURCE_JSON}"
echo "ppt_master_canary_source=PASS pod=${SOURCE_POD}"

for NODE in datax-01 datax-02 datax-03; do
  POD="ppt-v660-r16-canary-${NODE}"
  if kubectl -n "${NS}" get pod "${POD}" >/dev/null 2>&1; then
    fail "canary_name_already_exists=${POD}"
  fi
  SPEC="$(mktemp /tmp/ppt-master-r16-spec.XXXXXXXX.json)"
  TEMP_FILES+=("${SPEC}")
  python3 "${ROOT_DIR}/render_ppt_master_dual_container_canary.py" \
    --name "${POD}" --node "${NODE}" \
    --aio-image "${AIO_IMAGE}" --agent-image "${AGENT_IMAGE}" \
    --codex-config "${CODEX_CONFIG}" --model-catalog "${MODEL_CATALOG}" \
    <"${SOURCE_JSON}" >"${SPEC}"
  kubectl -n "${NS}" create -f "${SPEC}" >/dev/null
  CREATED_PODS+=("${POD}")
  if ! kubectl -n "${NS}" wait --for=condition=Ready "pod/${POD}" --timeout=600s; then
    kubectl -n "${NS}" describe pod "${POD}" >&2 || true
    kubectl -n "${NS}" logs "${POD}" --all-containers=true --tail=100 >&2 || true
    fail "pod_not_ready=${POD}"
  fi
  kubectl -n "${NS}" exec -i "${POD}" -c sandbox -- python3 - \
    <"${ROOT_DIR}/ppt_master_dual_container_probe.py"
  echo "ppt_master_dual_container_canary_node=${NODE}:PASS"
done

echo ppt_master_dual_container_canary=PASS
echo production_workloads_mutated=no
echo production_thread_data_mutated=no
