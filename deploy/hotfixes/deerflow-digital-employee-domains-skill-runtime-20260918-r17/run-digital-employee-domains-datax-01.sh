#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
. "${ROOT_DIR}/IMAGE_LOCKS.env"
MODE="${1:-}"
NS=deer-flow
EVIDENCE_DIR="/opt/deerflow-hotfix-evidence/digital-employee-domains-skill-runtime-20260918-r17/$(date -u +%Y%m%dT%H%M%SZ)"
TARGET_APPLIED=no
ROLLBACK_ATTEMPTED=no
TEMP_PROBE=''
SMOKE_ID=''
CANARY_USER='digital-domains-skillhub-canary'
CANARY_CREATED=no

die() {
  echo "digital_employee_domains=FAIL detail=$*" >&2
  return 1
}

probe_diagnostics() {
  local pod="$1"
  echo "===== PROBE FAILURE: ${pod} =====" >&2
  kubectl -n "${NS}" get pod "${pod}" -o wide >&2 || true
  kubectl -n "${NS}" logs "${pod}" --all-containers=true --tail=200 --timestamps >&2 || true
  kubectl -n "${NS}" describe pod "${pod}" >&2 || true
}

wait_for_probe() {
  local pod="$1"
  local expected="$2"
  local timeout_seconds="$3"
  local deadline
  local phase
  local ready
  local waiting_reasons
  deadline="$(( $(date +%s) + timeout_seconds ))"
  while :; do
    phase="$(kubectl -n "${NS}" get pod "${pod}" -o jsonpath='{.status.phase}' 2>/dev/null || true)"
    ready="$(kubectl -n "${NS}" get pod "${pod}" -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null || true)"
    waiting_reasons="$(kubectl -n "${NS}" get pod "${pod}" -o jsonpath='{range .status.containerStatuses[*]}{.state.waiting.reason}{" "}{end}' 2>/dev/null || true)"
    if test "${expected}" = 'Succeeded' && test "${phase}" = 'Succeeded'; then
      echo "probe_wait=PASS pod=${pod} expected=${expected}"
      return 0
    fi
    if test "${expected}" = 'Ready' && test "${ready}" = 'True'; then
      echo "probe_wait=PASS pod=${pod} expected=${expected}"
      return 0
    fi
    case "${phase}:${waiting_reasons}" in
      Failed:*|*:ErrImageNeverPull*|*:ImagePullBackOff*|*:InvalidImageName*|*:CreateContainerConfigError*|*:CreateContainerError*|*:CrashLoopBackOff*)
        echo "probe_wait=FAIL pod=${pod} phase=${phase:-unknown} waiting_reasons=${waiting_reasons:-none}" >&2
        probe_diagnostics "${pod}"
        return 1
        ;;
    esac
    if test "$(date +%s)" -ge "${deadline}"; then
      echo "probe_wait=TIMEOUT pod=${pod} expected=${expected} phase=${phase:-unknown} waiting_reasons=${waiting_reasons:-none}" >&2
      probe_diagnostics "${pod}"
      return 1
    fi
    sleep 2
  done
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

db_scalar() {
  local sql="$1"
  kubectl -n "${NS}" exec "${PG_POD}" -c postgres -- \
    sh -ec 'exec psql -X -At -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "$1"' sh "${sql}"
}

provisioner_api() {
  local method="$1"
  local path="$2"
  local payload="${3:-}"
  local pod
  pod="$(running_pod app=provisioner provisioner)"
  test -n "${pod}" || die 'running_provisioner_pod_not_found'
  kubectl -n "${NS}" exec -i "${pod}" -c provisioner -- \
    python3 - "${method}" "${path}" "${payload}" < "${ROOT_DIR}/provisioner_api_request.py"
}

cleanup() {
  if test -n "${SMOKE_ID}"; then
    provisioner_api DELETE "/api/sandboxes/${SMOKE_ID}" '' >/dev/null 2>&1 || true
    kubectl -n "${NS}" delete pod "sandbox-${SMOKE_ID}" --ignore-not-found=true --wait=false >/dev/null 2>&1 || true
    kubectl -n "${NS}" delete service "sandbox-${SMOKE_ID}-svc" --ignore-not-found=true --wait=false >/dev/null 2>&1 || true
    SMOKE_ID=''
  fi
  if test -n "${TEMP_PROBE}"; then
    kubectl -n "${NS}" delete pod "${TEMP_PROBE}" --ignore-not-found=true --wait=false >/dev/null 2>&1 || true
    TEMP_PROBE=''
  fi
  local cleanup_gateway_pod
  cleanup_gateway_pod=''
  if test "${CANARY_CREATED}" = 'yes'; then
    cleanup_gateway_pod="$(running_pod app=gateway gateway 2>/dev/null || true)"
  fi
  if test -n "${cleanup_gateway_pod}"; then
    kubectl -n "${NS}" exec "${cleanup_gateway_pod}" -c gateway -- \
      rm -rf -- "/app/backend/.deer-flow/users/${CANARY_USER}" >/dev/null 2>&1 || true
    CANARY_CREATED=no
  fi
}

automatic_rollback() {
  local reason="$1"
  if test "${ROLLBACK_ATTEMPTED}" = 'yes'; then
    return 1
  fi
  ROLLBACK_ATTEMPTED=yes
  echo "automatic_digital_employee_domains_rollback=START reason=${reason}" >&2
  if "${ROOT_DIR}/rollback-digital-employee-domains-datax-01.sh" --state "${EVIDENCE_DIR}/state.env"; then
    TARGET_APPLIED=no
    echo 'automatic_digital_employee_domains_rollback=PASS' >&2
    return 0
  fi
  echo "automatic_digital_employee_domains_rollback=FAIL state=${EVIDENCE_DIR}/state.env" >&2
  return 1
}

on_error() {
  local exit_code="$?"
  local line="${1:-unknown}"
  trap - ERR
  cleanup
  echo "digital_employee_domains_failed_line=${line} exit_code=${exit_code}" >&2
  if test "${TARGET_APPLIED}" = 'yes'; then
    automatic_rollback "line=${line} exit_code=${exit_code}" || true
  fi
  exit "${exit_code}"
}

on_signal() {
  local signal_name="$1"
  local exit_code="$2"
  trap - ERR INT TERM
  cleanup
  echo "digital_employee_domains_signal=${signal_name} exit_code=${exit_code}" >&2
  if test "${TARGET_APPLIED}" = 'yes'; then
    automatic_rollback "signal=${signal_name} exit_code=${exit_code}" || true
  fi
  exit "${exit_code}"
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
command -v kubectl >/dev/null || die 'kubectl_not_found'
command -v python3 >/dev/null || die 'python3_not_found'
command -v sha256sum >/dev/null || die 'sha256sum_not_found'
command -v base64 >/dev/null || die 'base64_not_found'
command -v awk >/dev/null || die 'awk_not_found'

(cd "${ROOT_DIR}" && sha256sum -c PACKAGE.SHA256)
python3 "${ROOT_DIR}/verify-digital-employee-domains.py" --root "${ROOT_DIR}"

kubectl -n "${NS}" rollout status deployment/gateway --timeout=300s
kubectl -n "${NS}" rollout status deployment/frontend --timeout=300s
kubectl -n "${NS}" rollout status deployment/provisioner --timeout=300s

CURRENT_GATEWAY_IMAGE="$(kubectl -n "${NS}" get deployment gateway -o jsonpath='{.spec.template.spec.containers[?(@.name=="gateway")].image}')"
CURRENT_GATEWAY_INIT_IMAGE="$(kubectl -n "${NS}" get deployment gateway -o jsonpath='{.spec.template.spec.initContainers[?(@.name=="seed-extensions-config")].image}')"
CURRENT_FRONTEND_IMAGE="$(kubectl -n "${NS}" get deployment frontend -o jsonpath='{.spec.template.spec.containers[?(@.name=="frontend")].image}')"
CURRENT_PROVISIONER_IMAGE="$(kubectl -n "${NS}" get deployment provisioner -o jsonpath='{.spec.template.spec.containers[?(@.name=="provisioner")].image}')"
test "${CURRENT_GATEWAY_IMAGE}" = "${BASE_GATEWAY_IMAGE}" || die "unexpected_gateway_image=${CURRENT_GATEWAY_IMAGE}"
test "${CURRENT_GATEWAY_INIT_IMAGE}" = "${BASE_GATEWAY_INIT_IMAGE}" || die "unexpected_gateway_init_image=${CURRENT_GATEWAY_INIT_IMAGE}"
test "${CURRENT_FRONTEND_IMAGE}" = "${BASE_FRONTEND_IMAGE}" || die "unexpected_frontend_image=${CURRENT_FRONTEND_IMAGE}"
test "${CURRENT_PROVISIONER_IMAGE}" = "${BASE_PROVISIONER_IMAGE}" || die "unexpected_provisioner_image=${CURRENT_PROVISIONER_IMAGE}"
echo "gateway_image=${CURRENT_GATEWAY_IMAGE}"
echo "gateway_init_image=${CURRENT_GATEWAY_INIT_IMAGE}"
echo "frontend_image=${CURRENT_FRONTEND_IMAGE}"
echo "provisioner_image=${CURRENT_PROVISIONER_IMAGE}"
echo 'gateway_init_image_preservation=PASS'
kubectl -n "${NS}" get deployment provisioner -o json | python3 -c '
import json, sys
deployment = json.load(sys.stdin)
spec = deployment.get("spec") or {}
containers = (spec.get("template") or {}).get("spec", {}).get("containers") or []
matches = [item for item in containers if item.get("name") == "provisioner"]
assert len(matches) == 1
env = {item.get("name"): item.get("value") for item in matches[0].get("env") or []}
assert env.get("SKILLS_PVC_NAME") == "deer-flow-skills-pvc"
assert env.get("USERDATA_PVC_NAME") == "deer-flow-userdata-pvc"
assert env.get("DISABLE_BROWSER") == "true"
assert env.get("DISABLE_MCP_BROWSER") == "true"
strategy = spec.get("strategy") or {}
rolling = strategy.get("rollingUpdate") or {}
assert strategy.get("type", "RollingUpdate") == "RollingUpdate"
assert str(rolling.get("maxSurge", "25%")) not in {"0", "0%"}
print("provisioner_production_environment=PASS")
print("provisioner_rolling_update_continuity=PASS")
'

echo '===== THREE-NODE IMMUTABLE IMAGE PROBES ====='
for NODE in datax-01 datax-02 datax-03; do
  TEMP_PROBE="digital-domains-gateway-probe-${NODE}"
  kubectl -n "${NS}" delete pod "${TEMP_PROBE}" --ignore-not-found=true --wait=true
  cat <<YAML | kubectl -n "${NS}" apply -f -
apiVersion: v1
kind: Pod
metadata:
  name: ${TEMP_PROBE}
spec:
  restartPolicy: Never
  nodeName: ${NODE}
  automountServiceAccountToken: false
  containers:
    - name: probe
      image: ${TARGET_GATEWAY_IMAGE}
      imagePullPolicy: Never
      command: ["sh", "-ec"]
      args:
        - >-
          cd /app/backend && PYTHONPATH=packages/harness:. .venv/bin/python -c
          'from app.gateway.routers.agents import CORPORATE_DIGITAL_EMPLOYEE_CATEGORIES as C;
          from deerflow.persistence.agents.model import AgentRow;
          from deerflow.persistence.agents.base import AgentStore;
          from deerflow.persistence.agents.file import FileAgentStore;
          from deerflow.persistence.agents.sql import SqlAgentStore;
          from deerflow.persistence.agents.sql import get_sync_sessionmaker;
          from deerflow.skills.installer import build_skill_tree_manifest,verify_skill_tree_copy;
          items=C.items;
          assert len(items)==26;
          assert sum(x.domain_group=="unified" for x in items)==17;
          assert sum(x.domain_group=="distributed" for x in items)==9;
          assert AgentRow.__tablename__=="agents";
          assert SqlAgentStore.patch_config is not AgentStore.patch_config;
          assert FileAgentStore.patch_config is not AgentStore.patch_config;
          assert callable(get_sync_sessionmaker);
          assert callable(build_skill_tree_manifest) and callable(verify_skill_tree_copy);
          print("gateway_business_domain_probe=PASS")'
YAML
  wait_for_probe "${TEMP_PROBE}" Succeeded 180
  kubectl -n "${NS}" logs "${TEMP_PROBE}" -c probe
  kubectl -n "${NS}" delete pod "${TEMP_PROBE}" --wait=true --timeout=120s
  TEMP_PROBE="digital-domains-frontend-probe-${NODE}"
  kubectl -n "${NS}" delete pod "${TEMP_PROBE}" --ignore-not-found=true --wait=true
  cat <<YAML | kubectl -n "${NS}" apply -f -
apiVersion: v1
kind: Pod
metadata:
  name: ${TEMP_PROBE}
spec:
  restartPolicy: Never
  nodeName: ${NODE}
  automountServiceAccountToken: false
  containers:
    - name: probe
      image: ${TARGET_FRONTEND_IMAGE}
      imagePullPolicy: Never
      command: ["sh", "-ec"]
      args:
        - >-
          test -s /app/frontend/.next/BUILD_ID &&
          grep -q '业务域分类' /app/frontend/src/core/i18n/locales/zh-CN.ts &&
          grep -q 'business-domain-badge' /app/frontend/src/components/workspace/agents/agent-gallery.tsx &&
          test ! -e /app/frontend/src/components/workspace/agents/category-manager-dialog.tsx &&
          echo frontend_business_domain_probe=PASS
YAML
  wait_for_probe "${TEMP_PROBE}" Succeeded 180
  kubectl -n "${NS}" logs "${TEMP_PROBE}" -c probe
  kubectl -n "${NS}" delete pod "${TEMP_PROBE}" --wait=true --timeout=120s
  TEMP_PROBE="digital-domains-provisioner-probe-${NODE}"
  kubectl -n "${NS}" delete pod "${TEMP_PROBE}" --ignore-not-found=true --wait=true
  cat <<YAML | kubectl -n "${NS}" apply -f -
apiVersion: v1
kind: Pod
metadata:
  name: ${TEMP_PROBE}
spec:
  restartPolicy: Never
  nodeName: ${NODE}
  automountServiceAccountToken: false
  containers:
    - name: probe
      image: ${TARGET_PROVISIONER_IMAGE}
      imagePullPolicy: Never
      command: ["sh", "-ec"]
      args: ["sleep 600"]
      env:
        - name: SKILLS_PVC_NAME
          value: deer-flow-skills-pvc
        - name: USERDATA_PVC_NAME
          value: deer-flow-userdata-pvc
        - name: SANDBOX_IMAGE
          value: deerflow.local/deer-flow-aio-sandbox:20260817-enterprise-arm64-rc15-sandboxstorage1
        - name: SANDBOX_SERVICE_TYPE
          value: ClusterIP
YAML
  wait_for_probe "${TEMP_PROBE}" Ready 180
  kubectl -n "${NS}" exec -i "${TEMP_PROBE}" -c probe -- python3 - <<'PY'
import app

mounts = {item.mount_path: item for item in app._build_volume_mounts("thread-probe", user_id="user-probe")}
assert mounts["/mnt/skills/public"].name == "skills-public"
assert mounts["/mnt/skills/public"].sub_path == "public"
assert mounts["/mnt/skills/custom"].name == "user-data"
assert mounts["/mnt/skills/custom"].sub_path == "deer-flow/users/user-probe/skills/custom"
assert mounts["/mnt/user-data"].sub_path == "deer-flow/users/user-probe/threads/thread-probe/user-data"
pod = app._build_pod("probe", "thread-probe", user_id="user-probe")
assert [item.name for item in pod.spec.init_containers] == ["prepare-user-data", "verify-skill-mounts"]
print("provisioner_skillhub_pvc_runtime_probe=PASS")
PY
  kubectl -n "${NS}" delete pod "${TEMP_PROBE}" --wait=true --timeout=120s
  TEMP_PROBE=''
  echo "node_image_probe=${NODE}:PASS"
done

echo '===== RETAINED FILE CONFIG READ-ONLY TARGET-IMAGE PREFLIGHT ====='
GATEWAY_POD="$(running_pod app=gateway gateway)"
test -n "${GATEWAY_POD}" || die 'running_gateway_pod_not_found_before_file_config_probe'
LIVE_FILE_CONFIG_COUNT="$(
  kubectl -n "${NS}" exec -i "${GATEWAY_POD}" -c gateway -- \
    sh -ec 'cd /app/backend && PYTHONPATH=packages/harness:. .venv/bin/python -' <<'PY'
from pathlib import Path

root = Path("/app/backend/.deer-flow")
patterns = ("users/*/agents/*/config.yaml", "team_agents/*/config.yaml", "agents/*/config.yaml")
files = {path for pattern in patterns for path in root.glob(pattern) if path.is_file()}
print(len(files))
PY
)"
case "${LIVE_FILE_CONFIG_COUNT}" in
  ''|*[!0-9]*) die "invalid_live_file_config_count=${LIVE_FILE_CONFIG_COUNT}" ;;
esac
TEMP_PROBE='digital-domains-file-config-preflight'
kubectl -n "${NS}" delete pod "${TEMP_PROBE}" --ignore-not-found=true --wait=true --timeout=120s
cat <<YAML | kubectl -n "${NS}" apply -f -
apiVersion: v1
kind: Pod
metadata:
  name: ${TEMP_PROBE}
spec:
  restartPolicy: Never
  nodeName: datax-01
  automountServiceAccountToken: false
  containers:
    - name: probe
      image: ${TARGET_GATEWAY_IMAGE}
      imagePullPolicy: Never
      command: ["sh", "-ec"]
      args: ["sleep 600"]
      volumeMounts:
        - name: userdata
          mountPath: /app/backend/.deer-flow
          readOnly: true
          subPath: deer-flow
  volumes:
    - name: userdata
      persistentVolumeClaim:
        claimName: deer-flow-userdata-pvc
YAML
wait_for_probe "${TEMP_PROBE}" Ready 180
TARGET_FILE_PREFLIGHT_OUTPUT="$(
  kubectl -n "${NS}" exec -i "${TEMP_PROBE}" -c probe -- \
    sh -ec 'cd /app/backend && PYTHONPATH=packages/harness:. .venv/bin/python -' <<'PY'
from pathlib import Path

import yaml
from app.gateway.routers.agents import (
    CORPORATE_DIGITAL_EMPLOYEE_CATEGORIES,
    resolve_digital_employee_category,
)

root = Path("/app/backend/.deer-flow")
patterns = ("users/*/agents/*/config.yaml", "team_agents/*/config.yaml", "agents/*/config.yaml")
files = sorted({path for pattern in patterns for path in root.glob(pattern) if path.is_file()})
allowed = {item.id for item in CORPORATE_DIGITAL_EMPLOYEE_CATEGORIES.items}
planned = 0
for path in files:
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise RuntimeError("Agent config must be a mapping: " + str(path))
    classification_input = dict(payload)
    classification_input.setdefault("name", path.parent.name)
    target = resolve_digital_employee_category(classification_input)
    if target not in allowed:
        raise RuntimeError("unsupported resolved category for %s: %s" % (path, target))
    planned += int(payload.get("category_id") != target)
print("file_agent_category_rows_total=%d" % len(files))
print("file_agent_category_rows_planned=%d" % planned)
print("file_agent_category_read_only_target_image_preflight=PASS")
PY
)"
printf '%s\n' "${TARGET_FILE_PREFLIGHT_OUTPUT}"
TARGET_FILE_CONFIG_COUNT="$(
  printf '%s\n' "${TARGET_FILE_PREFLIGHT_OUTPUT}" | \
    awk -F= '$1 == "file_agent_category_rows_total" {print $2}'
)"
case "${TARGET_FILE_CONFIG_COUNT}" in
  ''|*[!0-9]*) die "invalid_target_file_config_count=${TARGET_FILE_CONFIG_COUNT}" ;;
esac
test "${TARGET_FILE_CONFIG_COUNT}" = "${LIVE_FILE_CONFIG_COUNT}" || \
  die "target_live_file_config_count_mismatch target=${TARGET_FILE_CONFIG_COUNT} live=${LIVE_FILE_CONFIG_COUNT}"
echo "target_image_file_config_population_match=PASS count=${TARGET_FILE_CONFIG_COUNT}"
kubectl -n "${NS}" delete pod "${TEMP_PROBE}" --wait=true --timeout=120s
TEMP_PROBE=''

echo '===== SHARED PUBLIC SKILL READ-ONLY PREFLIGHT ====='
"${ROOT_DIR}/manage-research-fund-settlement-skill-datax-01.sh" --preflight

PG_POD="$(running_pod app=postgres postgres)"
test -n "${PG_POD}" || die 'running_postgres_pod_not_found'
GATEWAY_POD="$(running_pod app=gateway gateway)"
test -n "${GATEWAY_POD}" || die 'running_gateway_pod_not_found_during_preflight'
read -r CONFIGURED_AGENT_STORAGE_BACKEND DATABASE_BACKEND < <(
  kubectl -n "${NS}" exec -i "${GATEWAY_POD}" -c gateway -- \
    sh -ec 'cd /app/backend && PYTHONPATH=packages/harness:. .venv/bin/python -' <<'PY'
from deerflow.config.app_config import get_app_config

config = get_app_config()
agent_storage = getattr(getattr(config, "agent_storage", None), "backend", "file")
database = getattr(getattr(config, "database", None), "backend", "missing")
print(str(agent_storage), str(database))
PY
)
test "${CONFIGURED_AGENT_STORAGE_BACKEND}" = 'db' || die "unexpected_agent_storage_backend=${CONFIGURED_AGENT_STORAGE_BACKEND} expected=db"
test "${DATABASE_BACKEND}" = 'postgres' || die "unsupported_database_backend=${DATABASE_BACKEND} expected=postgres"
DB_AGENT_ROWS="$(db_scalar 'SELECT count(*) FROM agents;')"
INVALID_DB_CATEGORIES_BEFORE="$(db_scalar "SELECT count(*) FROM agents WHERE COALESCE(config->>'category_id','') NOT IN ('comprehensive-management','human-resources','party-building','strategic-development','production-operations','finance-capital','exploration-development','engineering-technology','science-technology-innovation','safety-environment','digitalization','international-business','enterprise-management','legal-affairs','materials-supply-chain','sales-trading','audit-inspection','oil-gas-resource-development','new-energy','oilfield-services','offshore-engineering-construction','energy-technology-services','natural-gas-power','refining-petrochemical','chemicals','financial-services');")"
echo "configured_agent_storage_backend=${CONFIGURED_AGENT_STORAGE_BACKEND}"
echo "database_backend=${DATABASE_BACKEND}"
echo "database_agent_category_rows_total=${DB_AGENT_ROWS}"
echo "database_agent_category_rows_planned=${INVALID_DB_CATEGORIES_BEFORE}"
echo 'database_category_normalization_preflight=PASS'

kubectl -n "${NS}" exec -i "${GATEWAY_POD}" -c gateway -- \
  sh -ec 'cd /app/backend && PYTHONPATH=packages/harness:. .venv/bin/python -' <<'PY'
import os
from pathlib import Path

root = Path("/app/backend/.deer-flow")
patterns = ("users/*/agents/*/config.yaml", "team_agents/*/config.yaml", "agents/*/config.yaml")
files = sorted({path for pattern in patterns for path in root.glob(pattern) if path.is_file()})
unwritable = []
for path in files:
    parent = path.parent
    if not os.access(str(parent), os.W_OK | os.X_OK):
        unwritable.append(str(parent))
if unwritable:
    raise SystemExit("file agent directories do not support atomic replacement: " + ", ".join(unwritable[:20]))
print("file_agent_config_rows_total=%d" % len(files))
print("file_agent_atomic_replace_capability=PASS euid=%d" % os.geteuid())
PY
ACTIVE_RUNS="$(db_scalar "SELECT count(*) FROM runs WHERE status IN ('pending','running');")"
ACTIVE_OA_JOBS="$(db_scalar "SELECT count(*) FROM oa_policy_flow_jobs WHERE status IN ('queued','launching','running');")"
echo "active_deerflow_runs=${ACTIVE_RUNS}"
echo "active_oa_jobs=${ACTIVE_OA_JOBS}"

if test "${MODE}" = '--preflight-only'; then
  echo 'digital_employee_domains_preflight=PASS'
  echo 'production_workloads_mutated=no'
  if (( ACTIVE_RUNS == 0 && ACTIVE_OA_JOBS == 0 )); then
    echo 'maintenance_gate_ready=yes'
  else
    echo 'maintenance_gate_ready=no'
  fi
  echo 'next_step=wait_for_zero_active_work_then_rerun_with_ack_and_--rollout'
  exit 0
fi

test "${DEERFLOW_DIGITAL_DOMAINS_ROLLOUT_ACK:-}" = 'YES' || die 'set_DEERFLOW_DIGITAL_DOMAINS_ROLLOUT_ACK=YES'
test "${ACTIVE_RUNS}" = '0' || die "active_deerflow_runs=${ACTIVE_RUNS}"
test "${ACTIVE_OA_JOBS}" = '0' || die "active_oa_jobs=${ACTIVE_OA_JOBS}"

install -d -m 0750 "${EVIDENCE_DIR}"
kubectl -n "${NS}" get deployment gateway -o json > "${EVIDENCE_DIR}/gateway.before.json"
kubectl -n "${NS}" get deployment frontend -o json > "${EVIDENCE_DIR}/frontend.before.json"
kubectl -n "${NS}" get deployment provisioner -o json > "${EVIDENCE_DIR}/provisioner.before.json"
kubectl -n "${NS}" exec "${PG_POD}" -c postgres -- \
  sh -ec 'exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --data-only --table=public.agents' \
  > "${EVIDENCE_DIR}/agents.before.sql"
test -s "${EVIDENCE_DIR}/agents.before.sql" || die 'empty_agents_backup'
GATEWAY_POD="$(running_pod app=gateway gateway)"
test -n "${GATEWAY_POD}" || die 'running_gateway_pod_not_found_before_backup'
kubectl -n "${NS}" exec -i "${GATEWAY_POD}" -c gateway -- \
  sh -ec 'cd /app/backend && PYTHONPATH=packages/harness:. .venv/bin/python - --capture' \
  < "${ROOT_DIR}/restore-agent-categories.py" \
  > "${EVIDENCE_DIR}/agent-categories.before.json"
python3 - "${EVIDENCE_DIR}/agent-categories.before.json" "${DB_AGENT_ROWS}" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    payload = json.load(handle)
assert payload.get("version") == 1
rows = payload.get("rows")
assert isinstance(rows, list)
assert len(rows) == int(sys.argv[2])
ids = [row.get("id") for row in rows]
assert all(isinstance(row_id, str) and row_id for row_id in ids)
assert len(ids) == len(set(ids))
print("database_agent_category_snapshot=PASS rows=%d" % len(rows))
PY
kubectl -n "${NS}" exec -i "${GATEWAY_POD}" -c gateway -- \
  sh -ec 'cd /app/backend && PYTHONPATH=packages/harness:. .venv/bin/python -' \
  > "${EVIDENCE_DIR}/agent-config-files.before.zip" <<'PY'
import sys
import zipfile
from pathlib import Path

root = Path("/app/backend/.deer-flow")
patterns = ("users/*/agents/*/config.yaml", "team_agents/*/config.yaml", "agents/*/config.yaml")
files = sorted({path for pattern in patterns for path in root.glob(pattern) if path.is_file()})
with zipfile.ZipFile(sys.stdout.buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
    for path in files:
        archive.write(path, path.relative_to(root).as_posix())
PY
python3 - "${EVIDENCE_DIR}/agent-config-files.before.zip" "${LIVE_FILE_CONFIG_COUNT}" <<'PY'
import sys
import zipfile

with zipfile.ZipFile(sys.argv[1], "r") as archive:
    corrupt = archive.testzip()
    if corrupt is not None:
        raise SystemExit("corrupt file-config backup member: " + corrupt)
    members = archive.infolist()
    if len(members) != int(sys.argv[2]):
        raise SystemExit(
            "file-config backup population mismatch: archive=%d live=%s"
            % (len(members), sys.argv[2])
        )
    print("file_agent_config_backup_members=%d" % len(members))
print("file_agent_config_backup_integrity=PASS")
PY
printf 'EVIDENCE_DIR=%q\n' "${EVIDENCE_DIR}" > "${EVIDENCE_DIR}/state.env"
printf 'OLD_GATEWAY_IMAGE=%q\n' "${CURRENT_GATEWAY_IMAGE}" >> "${EVIDENCE_DIR}/state.env"
printf 'OLD_GATEWAY_INIT_IMAGE=%q\n' "${CURRENT_GATEWAY_INIT_IMAGE}" >> "${EVIDENCE_DIR}/state.env"
printf 'OLD_FRONTEND_IMAGE=%q\n' "${CURRENT_FRONTEND_IMAGE}" >> "${EVIDENCE_DIR}/state.env"
printf 'OLD_PROVISIONER_IMAGE=%q\n' "${CURRENT_PROVISIONER_IMAGE}" >> "${EVIDENCE_DIR}/state.env"

echo '===== BACK UP SHARED PUBLIC SKILL ====='
"${ROOT_DIR}/manage-research-fund-settlement-skill-datax-01.sh" \
  --backup "${EVIDENCE_DIR}"
(
  cd "${EVIDENCE_DIR}"
  sha256sum gateway.before.json frontend.before.json provisioner.before.json agents.before.sql \
    agent-categories.before.json \
    agent-config-files.before.zip research-fund-settlement.before.tar \
    state.env > ROLLBACK.SHA256
  sha256sum -c ROLLBACK.SHA256
)

ACTIVE_RUNS_NOW="$(db_scalar "SELECT count(*) FROM runs WHERE status IN ('pending','running');")"
ACTIVE_OA_JOBS_NOW="$(db_scalar "SELECT count(*) FROM oa_policy_flow_jobs WHERE status IN ('queued','launching','running');")"
echo "active_deerflow_runs_after_backup=${ACTIVE_RUNS_NOW}"
echo "active_oa_jobs_after_backup=${ACTIVE_OA_JOBS_NOW}"
test "${ACTIVE_RUNS_NOW}" = '0' || die "active_deerflow_runs_after_backup=${ACTIVE_RUNS_NOW}"
test "${ACTIVE_OA_JOBS_NOW}" = '0' || die "active_oa_jobs_after_backup=${ACTIVE_OA_JOBS_NOW}"
echo 'post_backup_activity_race_recheck=PASS'

TARGET_APPLIED=yes
echo '===== ATOMIC PUBLIC SKILL CUTOVER ====='
"${ROOT_DIR}/manage-research-fund-settlement-skill-datax-01.sh" \
  --install "${EVIDENCE_DIR}"

ACTIVE_RUNS_NOW="$(db_scalar "SELECT count(*) FROM runs WHERE status IN ('pending','running');")"
ACTIVE_OA_JOBS_NOW="$(db_scalar "SELECT count(*) FROM oa_policy_flow_jobs WHERE status IN ('queued','launching','running');")"
echo "active_deerflow_runs_before_provisioner=${ACTIVE_RUNS_NOW}"
echo "active_oa_jobs_before_provisioner=${ACTIVE_OA_JOBS_NOW}"
test "${ACTIVE_RUNS_NOW}" = '0' || die "active_deerflow_runs_before_provisioner=${ACTIVE_RUNS_NOW}"
test "${ACTIVE_OA_JOBS_NOW}" = '0' || die "active_oa_jobs_before_provisioner=${ACTIVE_OA_JOBS_NOW}"
echo 'pre_provisioner_activity_race_recheck=PASS'

kubectl -n "${NS}" set image deployment/provisioner \
  "provisioner=${TARGET_PROVISIONER_IMAGE}"
kubectl -n "${NS}" rollout status deployment/provisioner --timeout=600s
PROVISIONER_POD="$(running_pod app=provisioner provisioner)"
test -n "${PROVISIONER_POD}" || die 'running_provisioner_pod_not_found_after_rollout'
kubectl -n "${NS}" exec -i "${PROVISIONER_POD}" -c provisioner -- python3 - <<'PY'
import app

mounts = {item.mount_path: item for item in app._build_volume_mounts("thread-live-probe", user_id="user-live-probe")}
assert mounts["/mnt/skills/public"].sub_path == "public"
assert mounts["/mnt/skills/custom"].sub_path == "deer-flow/users/user-live-probe/skills/custom"
pod = app._build_pod("live-probe", "thread-live-probe", user_id="user-live-probe")
assert [item.name for item in pod.spec.init_containers] == ["prepare-user-data", "verify-skill-mounts"]
print("provisioner_skillhub_pvc_runtime=PASS")
PY

echo '===== PROVISIONER CUSTOM-SKILL MOUNT CANARY ====='
kubectl -n "${NS}" exec -i "${GATEWAY_POD}" -c gateway -- \
  sh -ec 'cd /app/backend && exec .venv/bin/python - "$1"' sh "${CANARY_USER}" <<'PY'
import sys
from pathlib import Path

user = sys.argv[1]
root = Path("/app/backend/.deer-flow/users") / user
if root.exists():
    raise SystemExit("canary user path already exists: " + str(root))
skill = root / "skills" / "custom" / "hotfix-skill-mount-canary"
(skill / "scripts").mkdir(parents=True)
(skill / "SKILL.md").write_text(
    "---\nname: hotfix-skill-mount-canary\n"
    "description: deployment execution canary\n---\n",
    encoding="utf-8",
)
(skill / "reference.txt").write_text("skillhub-pvc-canary\n", encoding="utf-8")
(skill / "scripts" / "check.py").write_text(
    "import subprocess\n"
    "import sys\n"
    "from pathlib import Path\n"
    "root = Path('/mnt/skills/custom/hotfix-skill-mount-canary')\n"
    "assert root.joinpath('SKILL.md').is_file()\n"
    "assert root.joinpath('reference.txt').read_text(encoding='utf-8').strip() == 'skillhub-pvc-canary'\n"
    "print('skillhub_custom_skill_script_execution=PASS')\n"
    "public = Path('/mnt/skills/public/research-fund-settlement')\n"
    "workspace = Path('/mnt/user-data/workspace/research-fund-settlement-canary')\n"
    "workspace.mkdir(parents=True, exist_ok=True)\n"
    "case = public / 'examples/cases/group-company-complete.json'\n"
    "output = workspace / 'group-company-canary.docx'\n"
    "report = workspace / 'validation.json'\n"
    "commands = [\n"
    "    [sys.executable, str(public / 'scripts/report_cli.py'), 'check', '--case', str(case), '--mode', 'final', '--report', str(report)],\n"
    "    [sys.executable, str(public / 'scripts/report_cli.py'), 'build', '--case', str(case), '--output', str(output), '--mode', 'final'],\n"
    "    [sys.executable, str(public / 'scripts/report_cli.py'), 'inspect', '--case', str(case), '--docx', str(output)],\n"
    "]\n"
    "for command in commands:\n"
    "    completed = subprocess.run(command, check=True, text=True, capture_output=True)\n"
    "    print(completed.stdout, end='')\n"
    "assert output.stat().st_size > 50000\n"
    "print('research_fund_settlement_skill_execution=PASS')\n",
    encoding="utf-8",
)
print("skillhub_custom_skill_canary_stage=PASS")
PY
CANARY_CREATED=yes
kubectl -n "${NS}" exec "${GATEWAY_POD}" -c gateway -- \
  test -s "/app/backend/.deer-flow/users/${CANARY_USER}/skills/custom/hotfix-skill-mount-canary/SKILL.md"
kubectl -n "${NS}" exec "${GATEWAY_POD}" -c gateway -- \
  test -s "/app/backend/.deer-flow/users/${CANARY_USER}/skills/custom/hotfix-skill-mount-canary/reference.txt"
kubectl -n "${NS}" exec "${GATEWAY_POD}" -c gateway -- \
  test -s "/app/backend/.deer-flow/users/${CANARY_USER}/skills/custom/hotfix-skill-mount-canary/scripts/check.py"
echo 'skillhub_custom_skill_gateway_files=PASS'
SMOKE_ID="skillhub-pvc-$(date -u +%H%M%S)"
SMOKE_PAYLOAD="{\"sandbox_id\":\"${SMOKE_ID}\",\"thread_id\":\"digital-domains-skillhub-canary\",\"user_id\":\"${CANARY_USER}\"}"
provisioner_api POST /api/sandboxes "${SMOKE_PAYLOAD}" > "${EVIDENCE_DIR}/skillhub-canary-create.json"
cat "${EVIDENCE_DIR}/skillhub-canary-create.json"
SANDBOX_URL="$(python3 - "${EVIDENCE_DIR}/skillhub-canary-create.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    payload = json.load(handle)
url = payload.get("sandbox_url") or ""
if not url.startswith("http://sandbox-") or ".svc.cluster.local:" not in url:
    raise SystemExit("invalid managed Sandbox URL: " + url)
print(url)
PY
)"
wait_for_probe "sandbox-${SMOKE_ID}" Ready 900
kubectl -n "${NS}" get pod "sandbox-${SMOKE_ID}" -o yaml > "${EVIDENCE_DIR}/skillhub-canary-pod.yaml"
kubectl -n "${NS}" get pod "sandbox-${SMOKE_ID}" -o json > "${EVIDENCE_DIR}/skillhub-canary-pod.json"
python3 - "${EVIDENCE_DIR}/skillhub-canary-pod.json" "${CANARY_USER}" "digital-domains-skillhub-canary" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    pod = json.load(handle)
annotations = (pod.get("metadata") or {}).get("annotations") or {}
assert annotations.get("deerflow.io/user-id") == sys.argv[2]
assert annotations.get("deerflow.io/thread-id") == sys.argv[3]
assert annotations.get("deerflow.io/include-legacy-skills") == "false"
spec = pod.get("spec") or {}
container = next(item for item in spec.get("containers") or [] if item.get("name") == "sandbox")
mounts = {item.get("mountPath"): item for item in container.get("volumeMounts") or []}
assert mounts["/mnt/skills/custom"].get("subPath") == "deer-flow/users/%s/skills/custom" % sys.argv[2]
assert mounts["/mnt/user-data"].get("subPath") == "deer-flow/users/%s/threads/%s/user-data" % (sys.argv[2], sys.argv[3])
init_names = [item.get("name") for item in spec.get("initContainers") or []]
assert init_names == ["prepare-user-data", "verify-skill-mounts"]
statuses = {
    item.get("name"): item
    for item in (pod.get("status") or {}).get("initContainerStatuses") or []
}
for name in init_names:
    terminated = (statuses.get(name) or {}).get("state", {}).get("terminated") or {}
    assert terminated.get("exitCode") == 0, (name, terminated)
print("skillhub_custom_skill_pod_identity=PASS")
PY
for INIT_CONTAINER in prepare-user-data verify-skill-mounts; do
  kubectl -n "${NS}" logs "sandbox-${SMOKE_ID}" -c "${INIT_CONTAINER}" \
    > "${EVIDENCE_DIR}/skillhub-canary-${INIT_CONTAINER}.log" 2>&1
done
CANARY_COMMAND='python3 /mnt/skills/custom/hotfix-skill-mount-canary/scripts/check.py'
CANARY_COMMAND_B64="$(printf '%s' "${CANARY_COMMAND}" | base64 | tr -d '\n')"
kubectl -n "${NS}" exec -i "${PROVISIONER_POD}" -c provisioner -- \
  python3 - "${SANDBOX_URL}" "${CANARY_COMMAND_B64}" \
  < "${ROOT_DIR}/sandbox_api_exec.py" | tee "${EVIDENCE_DIR}/skillhub-canary-execution.log"
grep -qx 'skillhub_custom_skill_script_execution=PASS' "${EVIDENCE_DIR}/skillhub-canary-execution.log"
grep -qx 'research_fund_settlement_skill_execution=PASS' "${EVIDENCE_DIR}/skillhub-canary-execution.log"
grep -qx 'sandbox_aio_command_api=PASS' "${EVIDENCE_DIR}/skillhub-canary-execution.log"
provisioner_api DELETE "/api/sandboxes/${SMOKE_ID}" '' | tee "${EVIDENCE_DIR}/skillhub-canary-delete.json"
SMOKE_ID=''
echo 'skillhub_custom_skill_sandbox_execution_canary=PASS'

ACTIVE_RUNS_NOW="$(db_scalar "SELECT count(*) FROM runs WHERE status IN ('pending','running');")"
ACTIVE_OA_JOBS_NOW="$(db_scalar "SELECT count(*) FROM oa_policy_flow_jobs WHERE status IN ('queued','launching','running');")"
echo "active_deerflow_runs_before_gateway=${ACTIVE_RUNS_NOW}"
echo "active_oa_jobs_before_gateway=${ACTIVE_OA_JOBS_NOW}"
test "${ACTIVE_RUNS_NOW}" = '0' || die "active_deerflow_runs_before_gateway=${ACTIVE_RUNS_NOW}"
test "${ACTIVE_OA_JOBS_NOW}" = '0' || die "active_oa_jobs_before_gateway=${ACTIVE_OA_JOBS_NOW}"
echo 'pre_gateway_activity_race_recheck=PASS'

kubectl -n "${NS}" set image deployment/gateway \
  "gateway=${TARGET_GATEWAY_IMAGE}"
kubectl -n "${NS}" rollout status deployment/gateway --timeout=600s

GATEWAY_INIT_IMAGE_AFTER="$(kubectl -n "${NS}" get deployment gateway -o jsonpath='{.spec.template.spec.initContainers[?(@.name=="seed-extensions-config")].image}')"
test "${GATEWAY_INIT_IMAGE_AFTER}" = "${CURRENT_GATEWAY_INIT_IMAGE}" || die "gateway_init_image_changed=${GATEWAY_INIT_IMAGE_AFTER}"
echo 'gateway_init_image_preservation=PASS'

GATEWAY_POD="$(running_pod app=gateway gateway)"
test -n "${GATEWAY_POD}" || die 'running_gateway_pod_not_found'
kubectl -n "${NS}" exec -i "${GATEWAY_POD}" -c gateway -- \
  sh -ec 'cd /app/backend && PYTHONPATH=packages/harness:. .venv/bin/python -' <<'PY'
from app.gateway.app import app
from app.gateway.routers.agents import CORPORATE_DIGITAL_EMPLOYEE_CATEGORIES
from deerflow.persistence.agents.base import AgentStore
from deerflow.persistence.agents.file import FileAgentStore
from deerflow.persistence.agents.sql import SqlAgentStore

items = CORPORATE_DIGITAL_EMPLOYEE_CATEGORIES.items
assert len(items) == 26
assert sum(item.domain_group == "unified" for item in items) == 17
assert sum(item.domain_group == "distributed" for item in items) == 9
assert SqlAgentStore.patch_config is not AgentStore.patch_config
assert FileAgentStore.patch_config is not AgentStore.patch_config
# Starlette applications contain both HTTP routes and APIWebSocketRoute
# instances.  The latter intentionally have no ``methods`` attribute, so the
# business-domain acceptance check must audit the path contract directly.
paths = {getattr(route, "path", None) for route in app.routes}
assert "/api/admin/digital-employee-categories" not in paths
print("gateway_business_domain_runtime=PASS")
print("admin_category_mutation_route=ABSENT")
PY

kubectl -n "${NS}" exec -i "${GATEWAY_POD}" -c gateway -- \
  sh -ec 'cd /app/backend && PYTHONPATH=packages/harness:. .venv/bin/python - "$1"' sh "${CANARY_USER}" <<'PY'
import sys

from deerflow.skills.storage import get_or_new_user_skill_storage, reset_user_skill_storage

user = sys.argv[1]
reset_user_skill_storage(user)
storage = get_or_new_user_skill_storage(user)
skills = {skill.name: skill for skill in storage.load_skills(enabled_only=True)}
canary = skills["hotfix-skill-mount-canary"]
assert canary.category.value == "custom"
assert canary.get_container_file_path() == "/mnt/skills/custom/hotfix-skill-mount-canary/SKILL.md"
print("skillhub_custom_skill_gateway_discovery=PASS")
PY
kubectl -n "${NS}" exec "${GATEWAY_POD}" -c gateway -- \
  rm -rf -- "/app/backend/.deer-flow/users/${CANARY_USER}"
CANARY_CREATED=no

ACTIVE_RUNS_NOW="$(db_scalar "SELECT count(*) FROM runs WHERE status IN ('pending','running');")"
ACTIVE_OA_JOBS_NOW="$(db_scalar "SELECT count(*) FROM oa_policy_flow_jobs WHERE status IN ('queued','launching','running');")"
echo "active_deerflow_runs_after_gateway=${ACTIVE_RUNS_NOW}"
echo "active_oa_jobs_after_gateway=${ACTIVE_OA_JOBS_NOW}"
test "${ACTIVE_RUNS_NOW}" = '0' || die "active_deerflow_runs_after_gateway=${ACTIVE_RUNS_NOW}"
test "${ACTIVE_OA_JOBS_NOW}" = '0' || die "active_oa_jobs_after_gateway=${ACTIVE_OA_JOBS_NOW}"
echo 'post_gateway_activity_race_recheck=PASS'

echo '===== NORMALIZE DATABASE AGENT CATEGORIES ====='
kubectl -n "${NS}" exec -i "${GATEWAY_POD}" -c gateway -- \
  sh -ec 'cd /app/backend && PYTHONPATH=packages/harness:. .venv/bin/python -' \
  < "${ROOT_DIR}/normalize-database-agent-categories.py"

echo '===== NORMALIZE RETAINED FILE AGENT CATEGORIES ====='
kubectl -n "${NS}" exec -i "${GATEWAY_POD}" -c gateway -- \
  sh -ec 'cd /app/backend && PYTHONPATH=packages/harness:. .venv/bin/python -' \
  < "${ROOT_DIR}/normalize-file-agent-categories.py"

INVALID_DB_CATEGORIES="$(db_scalar "SELECT count(*) FROM agents WHERE COALESCE(config->>'category_id','') NOT IN ('comprehensive-management','human-resources','party-building','strategic-development','production-operations','finance-capital','exploration-development','engineering-technology','science-technology-innovation','safety-environment','digitalization','international-business','enterprise-management','legal-affairs','materials-supply-chain','sales-trading','audit-inspection','oil-gas-resource-development','new-energy','oilfield-services','offshore-engineering-construction','energy-technology-services','natural-gas-power','refining-petrochemical','chemicals','financial-services');")"
test "${INVALID_DB_CATEGORIES}" = '0' || die "unmigrated_database_agent_categories=${INVALID_DB_CATEGORIES}"
kubectl -n "${NS}" exec -i "${GATEWAY_POD}" -c gateway -- \
  sh -ec 'cd /app/backend && PYTHONPATH=packages/harness:. .venv/bin/python -' <<'PY'
from pathlib import Path

import yaml
from app.gateway.routers.agents import CORPORATE_DIGITAL_EMPLOYEE_CATEGORIES

allowed = {item.id for item in CORPORATE_DIGITAL_EMPLOYEE_CATEGORIES.items}
root = Path("/app/backend/.deer-flow")
patterns = ("users/*/agents/*/config.yaml", "team_agents/*/config.yaml", "agents/*/config.yaml")
files = sorted({path for pattern in patterns for path in root.glob(pattern) if path.is_file()})
invalid = []
for path in files:
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict) or payload.get("category_id") not in allowed:
        invalid.append(str(path))
if invalid:
    raise SystemExit("unmigrated file agent categories: " + ", ".join(invalid[:20]))
print("file_agent_category_migration=PASS count=%d" % len(files))
PY
echo 'database_agent_category_migration=PASS'

kubectl -n "${NS}" exec -i "${GATEWAY_POD}" -c gateway -- \
  sh -ec 'cd /app/skills/public/research-fund-settlement; sha256sum -c -' \
  < "${ROOT_DIR}/SKILL.SHA256"
echo 'research_fund_settlement_skill_runtime=PASS'

kubectl -n "${NS}" set image deployment/frontend "frontend=${TARGET_FRONTEND_IMAGE}"
kubectl -n "${NS}" rollout status deployment/frontend --timeout=600s

FRONTEND_POD="$(running_pod app=frontend frontend)"
NGINX_POD="$(running_pod app=nginx nginx)"
test -n "${FRONTEND_POD}" || die 'running_frontend_pod_not_found'
test -n "${NGINX_POD}" || die 'running_nginx_pod_not_found'
kubectl -n "${NS}" exec "${FRONTEND_POD}" -c frontend -- \
  wget -Y off -qO- -T 10 http://127.0.0.1:3000/api/runtime-config > "${EVIDENCE_DIR}/frontend.runtime-config.json"
kubectl -n "${NS}" exec "${NGINX_POD}" -c nginx -- \
  wget -Y off -qO- -T 10 http://gateway:8001/health > "${EVIDENCE_DIR}/gateway.health.json"
grep -q 'healthy' "${EVIDENCE_DIR}/gateway.health.json"
echo 'service_health_proxy_bypass=PASS'

"${ROOT_DIR}/manage-research-fund-settlement-skill-datax-01.sh" \
  --finalize "${EVIDENCE_DIR}"

TARGET_APPLIED=no
echo 'deerflow_digital_employee_domains_rollout=PASS'
echo 'business_domain_catalog=26'
echo 'business_domain_badges=17_unified_9_distributed'
echo 'admin_category_management=REMOVED'
echo 'legacy_category_migration=IDEMPOTENT_STARTUP_APPLIED'
echo 'research_fund_settlement_skill=UPDATED_ATOMICALLY'
echo 'skillhub_skill_tree_integrity=ENFORCED'
echo 'skillhub_custom_skill_runtime=DISCOVERED_AND_EXECUTED_THROUGH_MANAGED_SANDBOX_API'
echo 'research_fund_settlement_runtime=CHECK_BUILD_INSPECT_PASS'
echo 'existing_sandbox_mutation=NONE'
echo "rollback_state=${EVIDENCE_DIR}/state.env"
