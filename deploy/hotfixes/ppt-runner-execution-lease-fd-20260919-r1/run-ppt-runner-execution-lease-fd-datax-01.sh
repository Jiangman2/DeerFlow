#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
test "$(hostname -s)" = datax-01
test "$(id -u)" -eq 0
export KUBECONFIG=/etc/kubernetes/admin.conf
unset SSL_CERT_FILE REQUESTS_CA_BUNDLE CURL_CA_BUNDLE
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${ROOT}"
sha256sum -c PACKAGE.SHA256
python3 verify_execution_lease_hotfix.py --root "${ROOT}"

NS=deer-flow
MODULE_PATH=/app/backend/packages/harness/deerflow/community/aio_sandbox/aio_sandbox_provider.py
MODE="${1:-}"
EVIDENCE=
PATCH_APPLIED=0
ROLLBACK_ACTIVE=0

ready_pod() {
  kubectl -n "${NS}" get pods -l "$1" -o json | python3 -c '
import json, sys
doc = json.load(sys.stdin)
rows = []
for item in doc.get("items", []):
    conditions = {row.get("type"): row.get("status") for row in item.get("status", {}).get("conditions") or []}
    if item.get("metadata", {}).get("deletionTimestamp") is None and item.get("status", {}).get("phase") == "Running" and conditions.get("Ready") == "True":
        rows.append(item["metadata"]["name"])
if not rows:
    raise SystemExit(1)
print(sorted(rows)[0])
'
}

db_scalar() {
  local sql="$1"
  kubectl -n "${NS}" exec "${PG_POD}" -c postgres -- sh -ec '
    export PGOPTIONS="-c default_transaction_read_only=on -c statement_timeout=10000"
    exec psql -X -At -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "$1"
  ' sh "${sql}"
}

nfs_flock_probe() {
  local pod="$1"
  kubectl -n "${NS}" exec -i "${pod}" -c ppt-master-runner -- /app/backend/.venv/bin/python - <<'PY'
import fcntl
import os
import subprocess
import sys
import uuid
from pathlib import Path
from deerflow.config.paths import get_paths

lease_dir = get_paths().base_dir / ".sandbox-execution-leases"
lease_dir.mkdir(parents=True, exist_ok=True)
path = lease_dir / (".fd-hotfix-probe-%s.lock" % uuid.uuid4().hex)
child = r'''
import fcntl, sys
handle = open(sys.argv[1], "a+", encoding="utf-8")
try:
    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    raise SystemExit(42)
finally:
    handle.close()
raise SystemExit(0)
'''
try:
    shared = open(path, "a+", encoding="utf-8")
    try:
        fcntl.flock(shared, fcntl.LOCK_SH)
        blocked = subprocess.run([sys.executable, "-c", child, str(path)], check=False).returncode
        if blocked != 42:
            raise RuntimeError("exclusive child was not blocked by shared lock: rc=%s" % blocked)
        fcntl.flock(shared, fcntl.LOCK_UN)
    finally:
        shared.close()
    acquired = subprocess.run([sys.executable, "-c", child, str(path)], check=False).returncode
    if acquired != 0:
        raise RuntimeError("exclusive child did not acquire after unlock: rc=%s" % acquired)
finally:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
print("execution_lease_nfs_cross_process_flock=PASS")
print("execution_lease_probe_cleanup=PASS")
PY
}

rollback_on_failure() {
  local rc=$?
  trap - ERR EXIT INT TERM
  if test "${PATCH_APPLIED}" -eq 1 && test "${ROLLBACK_ACTIVE}" -eq 0 && test -n "${EVIDENCE}"; then
    ROLLBACK_ACTIVE=1
    echo "===== AUTOMATIC EXACT RUNNER ROLLBACK ====="
    if kubectl -n "${NS}" get deployment ppt-master-runner -o json > "${EVIDENCE}/runner.failed.json" && \
       python3 render_runner_overlay.py rollback \
         "${EVIDENCE}/runner.before.json" \
         "${EVIDENCE}/runner.failed.json" \
         "$(cat "${EVIDENCE}/configmap.name")" \
         "${EVIDENCE}/rollback.patch.json" && \
       kubectl -n "${NS}" patch deployment ppt-master-runner --type=json --patch-file "${EVIDENCE}/rollback.patch.json" && \
       kubectl -n "${NS}" rollout status deployment/ppt-master-runner --timeout=300s; then
      echo "ppt_runner_execution_lease_fd_automatic_rollback=PASS"
    else
      echo "ppt_runner_execution_lease_fd_automatic_rollback=FAIL_MANUAL_REVIEW_REQUIRED"
    fi
  fi
  echo "ppt_runner_execution_lease_fd=FAIL exit_code=${rc}"
  exit "${rc}"
}
trap rollback_on_failure ERR
trap 'exit 130' INT
trap 'exit 143' TERM

case "${MODE}" in
  --preflight-only|--rollout) ;;
  *) echo "Usage: $0 --preflight-only | --rollout"; exit 2 ;;
esac

echo "===== PPT RUNNER EXECUTION LEASE FD R1 ====="
date
hostname -s
uname -m
id

kubectl -n "${NS}" rollout status deployment/gateway --timeout=180s
kubectl -n "${NS}" rollout status deployment/provisioner --timeout=180s
kubectl -n "${NS}" rollout status deployment/ppt-master-runner --timeout=180s
RUNNER_POD="$(ready_pod app=ppt-master-runner)"
PG_POD="$(kubectl -n "${NS}" get pods -l app=postgres --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}')"
test -n "${RUNNER_POD}"
test -n "${PG_POD}"
echo "runner_pod=${RUNNER_POD}"
echo "postgres_pod=${PG_POD}"

EVIDENCE="$(mktemp -d /opt/deerflow-hotfix-evidence/ppt-runner-execution-lease-fd-20260919-r1.XXXXXXXX)"
echo "evidence_dir=${EVIDENCE}"
kubectl -n "${NS}" get deployment ppt-master-runner -o json > "${EVIDENCE}/runner.before.json"
kubectl -n "${NS}" get deployment gateway -o json > "${EVIDENCE}/gateway.before.json"
kubectl -n "${NS}" get deployment provisioner -o json > "${EVIDENCE}/provisioner.before.json"

kubectl -n "${NS}" exec -i "${RUNNER_POD}" -c ppt-master-runner -- \
  /app/backend/.venv/bin/python - "${MODULE_PATH}" > "${EVIDENCE}/aio_sandbox_provider.before.py" <<'PY'
import sys
from pathlib import Path
sys.stdout.write(Path(sys.argv[1]).read_text(encoding="utf-8"))
PY

kubectl -n "${NS}" exec -i "${RUNNER_POD}" -c ppt-master-runner -- \
  /app/backend/.venv/bin/python - <<'PY'
from pathlib import Path
source = Path("/app/backend/app/gateway/deps.py").read_text(encoding="utf-8")
assert "PPT runner startup: shared run reconciliation skipped" in source
print("ppt_runner_startup_safety_overlay=PASS")
PY

python3 render_execution_lease_patch.py \
  "${EVIDENCE}/aio_sandbox_provider.before.py" \
  "${EVIDENCE}/aio_sandbox_provider.target.py" \
  "${EVIDENCE}/source.metadata.json"
python3 render_runner_overlay.py configmap \
  "${EVIDENCE}/aio_sandbox_provider.target.py" \
  "${EVIDENCE}/target.configmap.json"
CM_NAME="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["metadata"]["name"])' "${EVIDENCE}/target.configmap.json")"
printf '%s\n' "${CM_NAME}" > "${EVIDENCE}/configmap.name"
python3 render_runner_overlay.py apply \
  "${EVIDENCE}/runner.before.json" "${CM_NAME}" "${EVIDENCE}/runner.patch.json"
echo "target_configmap=${CM_NAME}"
echo "runner_exact_overlay_render=PASS"

nfs_flock_probe "${RUNNER_POD}"

DB_VERSION="$(db_scalar 'SELECT version_num FROM public.alembic_version;')"
test "${DB_VERSION}" = 0019_digital_employee_agents
ACTIVE_PPT_RUNS="$(db_scalar "SELECT count(*) FROM runs WHERE status IN ('pending','running') AND model_name = 'ppt-master-agent-host';")"
ACTIVE_OTHER_RUNS="$(db_scalar "SELECT count(*) FROM runs WHERE status IN ('pending','running') AND model_name IS DISTINCT FROM 'ppt-master-agent-host';")"
ACTIVE_OA_JOBS="$(db_scalar "SELECT count(*) FROM oa_policy_flow_jobs WHERE status IN ('queued','launching','running');")"
echo "database_version=${DB_VERSION}"
echo "active_ppt_master_runs=${ACTIVE_PPT_RUNS}"
echo "active_other_deerflow_runs=${ACTIVE_OTHER_RUNS}"
echo "active_oa_jobs=${ACTIVE_OA_JOBS}"
echo "ordinary_active_work_non_blocking=PASS"

if test "${ACTIVE_PPT_RUNS}" -eq 0; then
  echo "maintenance_gate_ready=yes"
else
  echo "maintenance_gate_ready=no"
fi

if test "${MODE}" = --preflight-only; then
  (
    cd "${EVIDENCE}"
    sha256sum runner.before.json gateway.before.json provisioner.before.json \
      aio_sandbox_provider.before.py aio_sandbox_provider.target.py source.metadata.json \
      target.configmap.json runner.patch.json configmap.name > PREFLIGHT.SHA256
  )
  echo "ppt_runner_execution_lease_fd_preflight=PASS"
  echo "production_workloads_mutated=no"
  echo "production_database_mutation=NONE"
  echo "production_nfs_mutation=TEMPORARY_LOCK_PROBE_CLEANED"
  if test "${ACTIVE_PPT_RUNS}" -eq 0; then
    echo "next_step=rerun_with_PPT_RUNNER_EXECUTION_LEASE_FD_ACK_YES_and_--rollout"
  else
    echo "next_step=wait_for_zero_active_ppt_master_runs_then_rollout"
  fi
  trap - ERR
  exit 0
fi

test "${PPT_RUNNER_EXECUTION_LEASE_FD_ACK:-}" = YES
test "${ACTIVE_PPT_RUNS}" -eq 0

if kubectl -n "${NS}" get configmap "${CM_NAME}" -o json > "${EVIDENCE}/target.configmap.live.json" 2>/dev/null; then
  python3 - "${EVIDENCE}" <<'PY'
import json, sys
from pathlib import Path
root = Path(sys.argv[1])
expected = json.loads((root / "target.configmap.json").read_text(encoding="utf-8"))
live = json.loads((root / "target.configmap.live.json").read_text(encoding="utf-8"))
assert live.get("immutable") is True
assert live.get("data") == expected.get("data")
print("target_configmap_reuse=PASS")
PY
else
  kubectl create -f "${EVIDENCE}/target.configmap.json"
  echo "target_configmap_create=PASS"
fi

ACTIVE_PPT_RUNS_NOW="$(db_scalar "SELECT count(*) FROM runs WHERE status IN ('pending','running') AND model_name = 'ppt-master-agent-host';")"
echo "active_ppt_master_runs_race_recheck=${ACTIVE_PPT_RUNS_NOW}"
test "${ACTIVE_PPT_RUNS_NOW}" -eq 0

kubectl -n "${NS}" patch deployment ppt-master-runner --type=json --patch-file "${EVIDENCE}/runner.patch.json"
PATCH_APPLIED=1
kubectl -n "${NS}" rollout status deployment/ppt-master-runner --timeout=300s
RUNNER_POD_AFTER="$(ready_pod app=ppt-master-runner)"
echo "runner_pod_after=${RUNNER_POD_AFTER}"

kubectl -n "${NS}" get deployment ppt-master-runner -o json > "${EVIDENCE}/runner.after.json"
kubectl -n "${NS}" get deployment gateway -o json > "${EVIDENCE}/gateway.after.json"
kubectl -n "${NS}" get deployment provisioner -o json > "${EVIDENCE}/provisioner.after.json"
python3 - "${EVIDENCE}" "${CM_NAME}" <<'PY'
import json, sys
from pathlib import Path
root = Path(sys.argv[1])
read = lambda name: json.loads((root / name).read_text(encoding="utf-8"))
before, after = read("runner.before.json"), read("runner.after.json")
sys.path.insert(0, str(Path.cwd()))
from render_runner_overlay import expected_after
expected = expected_after(before, sys.argv[2])
assert after["spec"] == expected["spec"], "Runner spec differs from exact target"
assert read("gateway.before.json")["spec"] == read("gateway.after.json")["spec"], "Gateway spec changed"
assert read("provisioner.before.json")["spec"] == read("provisioner.after.json")["spec"], "Provisioner spec changed"
print("runner_exact_change_scope=PASS")
print("gateway_spec_unchanged=PASS")
print("provisioner_spec_unchanged=PASS")
PY

kubectl -n "${NS}" exec -i "${RUNNER_POD_AFTER}" -c ppt-master-runner -- \
  /app/backend/.venv/bin/python - <<'PY'
import ast, inspect
from pathlib import Path
from deerflow.community.aio_sandbox import aio_sandbox_provider as module
source = Path(inspect.getsourcefile(module)).read_text(encoding="utf-8")
tree = ast.parse(source)
node = next(row for row in tree.body if isinstance(row, ast.FunctionDef) and row.name == "_open_lock_file")
modes = []
for child in ast.walk(node):
    if isinstance(child, ast.Call) and isinstance(child.func, ast.Name) and child.func.id == "open" and len(child.args) >= 2:
        value = child.args[1]
        modes.append(value.s if isinstance(value, ast.Str) else value.value)
assert modes == ["a+"], modes
assert "fcntl.LOCK_SH" in source
assert "fcntl.LOCK_EX | fcntl.LOCK_NB" in source
print("live_runner_execution_lease_fd=PASS mode=a+")
PY
nfs_flock_probe "${RUNNER_POD_AFTER}"

NGINX_POD="$(ready_pod app=nginx)"
kubectl -n "${NS}" get endpointslice -l kubernetes.io/service-name=ppt-master-runner -o wide
kubectl -n "${NS}" exec "${NGINX_POD}" -c nginx -- wget -qO- -T 10 http://ppt-master-runner:8003/health
echo
kubectl -n "${NS}" exec "${NGINX_POD}" -c nginx -- wget -qO- -T 10 http://gateway:8001/health
echo

if kubectl -n "${NS}" logs "${RUNNER_POD_AFTER}" -c ppt-master-runner --since=10m --tail=1000 2>&1 | \
   grep -F 'Bad file descriptor' > "${EVIDENCE}/bad-file-descriptor.after.txt"; then
  echo "post_rollout_bad_file_descriptor=FAIL"
  exit 1
fi
echo "post_rollout_bad_file_descriptor=NONE"

(
  cd "${EVIDENCE}"
  sha256sum runner.before.json runner.after.json gateway.before.json gateway.after.json \
    provisioner.before.json provisioner.after.json aio_sandbox_provider.before.py \
    aio_sandbox_provider.target.py source.metadata.json target.configmap.json \
    runner.patch.json configmap.name > ROLLBACK.SHA256
)
PATCH_APPLIED=0
trap - ERR
echo "ppt_runner_execution_lease_fd=PASS"
echo "runner_restart_only=yes"
echo "gateway_restart_performed=no"
echo "provisioner_restart_performed=no"
echo "production_database_mutation=NONE"
echo "production_nfs_mutation=TEMPORARY_LOCK_PROBE_CLEANED"
echo "rollback_state=${EVIDENCE}"
echo "next_step=run_status_then_submit_one_real_ppt_master_acceptance_task"
