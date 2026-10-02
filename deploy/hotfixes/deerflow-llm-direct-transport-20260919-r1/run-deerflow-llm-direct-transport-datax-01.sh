#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
MODE="${1:-}"
NS=deer-flow
TARGET_HOST=aiapi.cnooc.com.cn
EXPECTED_GATEWAY_IMAGE='deerflow.local/deer-flow-gateway:20260918-enterprise-arm64-rc15-skillauthoring-model1'
EVIDENCE_DIR="/opt/deerflow-hotfix-evidence/deerflow-llm-direct-transport-20260919-r1/$(date -u +%Y%m%dT%H%M%SZ)"
TARGET_APPLIED=no
ROLLBACK_ATTEMPTED=no

die() { echo "deerflow_llm_direct_transport=FAIL detail=$*" >&2; return 1; }
automatic_rollback() {
  local reason="$1"
  test "${ROLLBACK_ATTEMPTED}" = no || return 1
  ROLLBACK_ATTEMPTED=yes
  echo "automatic_llm_direct_transport_rollback=START reason=${reason}" >&2
  if "${ROOT_DIR}/rollback-deerflow-llm-direct-transport-datax-01.sh" --state "${EVIDENCE_DIR}/state.env"; then
    TARGET_APPLIED=no
    echo 'automatic_llm_direct_transport_rollback=PASS' >&2
    return 0
  fi
  echo "automatic_llm_direct_transport_rollback=FAIL state=${EVIDENCE_DIR}/state.env" >&2
  return 1
}
on_error() {
  local exit_code="$?" line="${1:-unknown}"
  trap - ERR
  echo "deerflow_llm_direct_transport_failed_line=${line} exit_code=${exit_code}" >&2
  test "${TARGET_APPLIED}" != yes || automatic_rollback "line=${line} exit_code=${exit_code}" || true
  exit "${exit_code}"
}
on_signal() {
  local name="$1" code="$2"
  trap - ERR INT TERM
  echo "deerflow_llm_direct_transport_signal=${name} exit_code=${code}" >&2
  test "${TARGET_APPLIED}" != yes || automatic_rollback "signal=${name}" || true
  exit "${code}"
}
trap 'on_error "$LINENO"' ERR
trap 'on_signal INT 130' INT
trap 'on_signal TERM 143' TERM

case "${MODE}" in
  --preflight-only|--rollout) ;;
  *) echo "usage: $0 --preflight-only|--rollout" >&2; exit 2 ;;
esac
test "$(id -u)" = 0 || die 'must_run_as_root'
test "$(hostname -s)" = datax-01 || die 'must_run_on_datax-01'
test "$(uname -m)" = aarch64 || die 'requires_aarch64'
for command in kubectl python3 sha256sum; do
  command -v "${command}" >/dev/null || die "${command}_not_found"
done
(
  cd "${ROOT_DIR}"
  sha256sum -c PACKAGE.SHA256
)
python3 "${ROOT_DIR}/verify-deerflow-llm-direct-transport.py" --root "${ROOT_DIR}"

kubectl -n "${NS}" rollout status deployment/gateway --timeout=300s
kubectl -n "${NS}" rollout status deployment/nginx --timeout=300s
current_image="$(kubectl -n "${NS}" get deployment gateway -o jsonpath='{.spec.template.spec.containers[?(@.name=="gateway")].image}')"
test "${current_image}" = "${EXPECTED_GATEWAY_IMAGE}" || \
  die "unexpected_gateway_image=${current_image} expected=${EXPECTED_GATEWAY_IMAGE}"
kubectl -n "${NS}" get deployment gateway -o json | python3 -c '
import json,sys
d=json.load(sys.stdin)
assert d["spec"].get("replicas")==2, d["spec"].get("replicas")
strategy=d["spec"].get("strategy") or {}
rolling=strategy.get("rollingUpdate") or {}
assert strategy.get("type")=="RollingUpdate", strategy
assert str(rolling.get("maxUnavailable"))=="0", rolling
print("gateway_two_replica_rollout_contract=PASS replicas=2 maxUnavailable=0")
'

gateway_pods() {
  kubectl -n "${NS}" get pods -l app=gateway -o json | python3 -c '
import json,sys
rows=[]
for pod in json.load(sys.stdin).get("items",[]):
    if (pod.get("metadata") or {}).get("deletionTimestamp"): continue
    states={x.get("name"):x for x in (pod.get("status") or {}).get("containerStatuses") or []}
    if (pod.get("status") or {}).get("phase")=="Running" and (states.get("gateway") or {}).get("ready"):
        rows.append((pod.get("metadata") or {}).get("name",""))
assert len(rows)==2, "ready gateway pods=%d" % len(rows)
print("\n".join(sorted(rows)))
'
}
pod_env() {
  local pod="$1" name="$2"
  kubectl -n "${NS}" exec "${pod}" -c gateway -- printenv "${name}" 2>/dev/null || true
}
append_host() {
  python3 - "${TARGET_HOST}" "$1" <<'PY'
import sys
host=sys.argv[1]
tokens=[item.strip() for item in sys.argv[2].split(",") if item.strip()]
if host not in tokens: tokens.append(host)
print(",".join(tokens),end="")
PY
}
contains_target() {
  python3 - "${TARGET_HOST}" "$1" <<'PY'
import sys
raise SystemExit(0 if sys.argv[1] in [x.strip() for x in sys.argv[2].split(",")] else 1)
PY
}
probe_pod_with_temporary_bypass() {
  local pod="$1" upper lower fallback target_upper target_lower
  upper="$(pod_env "${pod}" NO_PROXY)"
  lower="$(pod_env "${pod}" no_proxy)"
  fallback="${upper:-${lower}}"
  test -n "${fallback}" || die "no_proxy_environment_empty pod=${pod}"
  target_upper="$(append_host "${upper:-${fallback}}")"
  target_lower="$(append_host "${lower:-${fallback}}")"
  kubectl -n "${NS}" exec -i "${pod}" -c gateway -- \
    env NO_PROXY="${target_upper}" no_proxy="${target_lower}" \
    sh -ec 'cd /app/backend && PYTHONPATH=packages/harness:. exec .venv/bin/python -' \
    < "${ROOT_DIR}/factory_runtime_probe.py"
  echo "temporary_direct_transport_factory_probe=PASS pod=${pod}"
}

PODS="$(gateway_pods)"
REFERENCE_UPPER=''
REFERENCE_LOWER=''
while IFS= read -r pod; do
  test -n "${pod}" || continue
  upper="$(pod_env "${pod}" NO_PROXY)"
  lower="$(pod_env "${pod}" no_proxy)"
  if test -z "${REFERENCE_UPPER}" && test -z "${REFERENCE_LOWER}"; then
    REFERENCE_UPPER="${upper}"
    REFERENCE_LOWER="${lower}"
  else
    test "${upper}" = "${REFERENCE_UPPER}" || die "gateway_NO_PROXY_divergence pod=${pod}"
    test "${lower}" = "${REFERENCE_LOWER}" || die "gateway_no_proxy_divergence pod=${pod}"
  fi
  probe_pod_with_temporary_bypass "${pod}"
done <<< "${PODS}"

PG_POD="$(
  kubectl -n "${NS}" get pods -l app=postgres --field-selector=status.phase=Running \
    -o jsonpath='{.items[0].metadata.name}'
)"
test -n "${PG_POD}" || die 'running_postgres_pod_not_found'
db_scalar() {
  kubectl -n "${NS}" exec "${PG_POD}" -c postgres -- \
    sh -ec 'export PGOPTIONS="-c default_transaction_read_only=on"; exec psql -X -At -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "$1"' sh "$1"
}
activity_snapshot() {
  ACTIVE_RUNS="$(db_scalar "SELECT count(*) FROM runs WHERE status IN ('pending','running');")"
  ACTIVE_OA_JOBS="$(db_scalar "SELECT count(*) FROM oa_policy_flow_jobs WHERE status IN ('queued','launching','running');")"
  echo "active_deerflow_runs=${ACTIVE_RUNS}"
  echo "active_oa_jobs=${ACTIVE_OA_JOBS}"
}
activity_snapshot

if test "${MODE}" = --preflight-only; then
  echo 'deerflow_llm_direct_transport_preflight=PASS'
  echo 'production_workloads_mutated=no'
  echo 'production_database_mutation=NONE'
  echo 'gateway_image_mutation=NONE'
  echo 'weknora_mutation=NONE'
  echo 'ppt_runner_mutation=NONE'
  if test "${ACTIVE_RUNS}" = 0 && test "${ACTIVE_OA_JOBS}" = 0; then
    echo 'maintenance_gate_ready=yes'
  else
    echo 'maintenance_gate_ready=no'
  fi
  echo 'next_step=wait_for_zero_active_work_then_rerun_with_DEERFLOW_LLM_DIRECT_TRANSPORT_ACK_YES_and_--rollout'
  exit 0
fi

test "${DEERFLOW_LLM_DIRECT_TRANSPORT_ACK:-}" = YES || die 'set_DEERFLOW_LLM_DIRECT_TRANSPORT_ACK=YES'
test "${ACTIVE_RUNS}" = 0 || die "active_deerflow_runs=${ACTIVE_RUNS}"
test "${ACTIVE_OA_JOBS}" = 0 || die "active_oa_jobs=${ACTIVE_OA_JOBS}"

umask 077
install -d -m 0700 "${EVIDENCE_DIR}"
kubectl -n "${NS}" get deployment gateway -o json > "${EVIDENCE_DIR}/gateway.before.json"
printf '%s' "${REFERENCE_UPPER}" > "${EVIDENCE_DIR}/NO_PROXY.effective.before.txt"
printf '%s' "${REFERENCE_LOWER}" > "${EVIDENCE_DIR}/no_proxy.effective.before.txt"
python3 "${ROOT_DIR}/render_no_proxy_patch.py" \
  --deployment "${EVIDENCE_DIR}/gateway.before.json" \
  --upper-effective-file "${EVIDENCE_DIR}/NO_PROXY.effective.before.txt" \
  --lower-effective-file "${EVIDENCE_DIR}/no_proxy.effective.before.txt" \
  --host "${TARGET_HOST}" \
  --target-output "${EVIDENCE_DIR}/gateway.target.patch.json" \
  --rollback-output "${EVIDENCE_DIR}/gateway.rollback.patch.json"
cat > "${EVIDENCE_DIR}/state.env" <<EOF
EVIDENCE_DIR='${EVIDENCE_DIR}'
TARGET_HOST='${TARGET_HOST}'
EXPECTED_GATEWAY_IMAGE='${EXPECTED_GATEWAY_IMAGE}'
EOF
(
  cd "${EVIDENCE_DIR}"
  sha256sum \
    gateway.before.json \
    gateway.target.patch.json \
    gateway.rollback.patch.json \
    NO_PROXY.effective.before.txt \
    no_proxy.effective.before.txt \
    state.env > ROLLBACK.SHA256
  sha256sum -c ROLLBACK.SHA256
)

activity_snapshot
test "${ACTIVE_RUNS}" = 0 || die "active_deerflow_runs_before_gateway_patch=${ACTIVE_RUNS}"
test "${ACTIVE_OA_JOBS}" = 0 || die "active_oa_jobs_before_gateway_patch=${ACTIVE_OA_JOBS}"
echo 'pre_gateway_patch_activity_race_recheck=PASS'

TARGET_APPLIED=yes
kubectl -n "${NS}" patch deployment gateway --type=strategic \
  --patch-file "${EVIDENCE_DIR}/gateway.target.patch.json"
kubectl -n "${NS}" rollout status deployment/gateway --timeout=900s
"${ROOT_DIR}/status-deerflow-llm-direct-transport-datax-01.sh"
TARGET_APPLIED=no

echo 'deerflow_llm_direct_transport_cutover=PASS'
echo 'gateway_model_transport=DIRECT_FOR_aiapi.cnooc.com.cn'
echo 'gateway_image=PRESERVED'
echo 'production_database_mutation=NONE'
echo 'weknora_mutation=NONE'
echo 'ppt_runner_mutation=NONE'
echo "rollback_state=${EVIDENCE_DIR}/state.env"
