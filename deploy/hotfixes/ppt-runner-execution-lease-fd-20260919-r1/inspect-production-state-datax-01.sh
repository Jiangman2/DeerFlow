#!/usr/bin/env bash
set -Eeuo pipefail
test "$(hostname -s)" = datax-01
test "$(id -u)" -eq 0
export KUBECONFIG=/etc/kubernetes/admin.conf
unset SSL_CERT_FILE REQUESTS_CA_BUNDLE CURL_CA_BUNDLE
NS=deer-flow

fail() {
  local rc=$?
  echo "production_state_survey=FAIL line=${BASH_LINENO[0]} exit_code=${rc}"
  exit "${rc}"
}
trap fail ERR

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

echo "===== PRODUCTION EXPANSION STATE SURVEY ====="
date
hostname -s
uname -m
id

echo "===== NODES ====="
kubectl get nodes -o wide
kubectl get nodes -o json | python3 -c '
import json, sys
doc = json.load(sys.stdin)
for item in sorted(doc.get("items", []), key=lambda row: row["metadata"]["name"]):
    meta, spec, status = item["metadata"], item.get("spec", {}), item.get("status", {})
    conditions = {row.get("type"): (row.get("status"), row.get("reason")) for row in status.get("conditions") or []}
    print("node=%s ready=%s unschedulable=%s labels=%s taints=%s" % (
        meta["name"], conditions.get("Ready"), bool(spec.get("unschedulable")),
        {key: value for key, value in meta.get("labels", {}).items() if key in {"kubernetes.io/arch", "kubernetes.io/os", "kubernetes.io/hostname", "workload"}},
        spec.get("taints") or [],
    ))
'

echo "===== CORE WORKLOADS ====="
kubectl -n "${NS}" get deployment gateway nginx frontend provisioner ppt-master-runner -o wide
kubectl -n "${NS}" get pods -l 'app in (gateway,nginx,frontend,provisioner,ppt-master-runner)' -o wide
kubectl -n "${NS}" get deployment gateway provisioner ppt-master-runner -o json | python3 -c '
import json, sys
doc = json.load(sys.stdin)
for item in sorted(doc.get("items", []), key=lambda row: row["metadata"]["name"]):
    name = item["metadata"]["name"]
    spec = item["spec"]
    pod = spec["template"]["spec"]
    print("deployment=%s replicas=%s strategy=%s service_account=%s node_selector=%s tolerations=%s" % (
        name, spec.get("replicas"), spec.get("strategy"), pod.get("serviceAccountName"), pod.get("nodeSelector") or {}, pod.get("tolerations") or []))
    print("deployment=%s affinity=%s topology_spread=%s" % (name, pod.get("affinity") or {}, pod.get("topologySpreadConstraints") or []))
    volumes = {row["name"]: row for row in pod.get("volumes") or []}
    for kind in ("initContainers", "containers"):
        for container in pod.get(kind) or []:
            print("deployment=%s kind=%s container=%s image=%s pull=%s resources=%s" % (
                name, kind, container["name"], container.get("image"), container.get("imagePullPolicy"), container.get("resources") or {}))
            print("deployment=%s container=%s readiness=%s liveness=%s startup=%s" % (
                name, container["name"], container.get("readinessProbe"), container.get("livenessProbe"), container.get("startupProbe")))
            for mount in container.get("volumeMounts") or []:
                volume = volumes.get(mount["name"], {})
                source = volume.get("persistentVolumeClaim") or volume.get("configMap") or volume.get("secret") or volume.get("hostPath") or {}
                print("deployment=%s container=%s mount=%s subpath=%s readonly=%s volume=%s source=%s" % (
                    name, container["name"], mount.get("mountPath"), mount.get("subPath"), mount.get("readOnly"), mount["name"], source))
            selected = {row["name"]: row.get("value") for row in container.get("env") or [] if row.get("name") in {
                "USERDATA_PVC_NAME", "SKILLS_PVC_NAME", "DEER_FLOW_HOST_BASE_DIR", "DISABLE_BROWSER", "DISABLE_MCP_BROWSER", "DEER_FLOW_RUNTIME_PROFILE"}}
            if selected:
                print("deployment=%s container=%s selected_env=%s" % (name, container["name"], selected))
'

echo "===== SERVICES AND ENDPOINTS ====="
kubectl -n "${NS}" get service gateway nginx provisioner ppt-master-runner -o wide
for SERVICE in gateway nginx provisioner ppt-master-runner; do
  kubectl -n "${NS}" get endpointslice -l "kubernetes.io/service-name=${SERVICE}" -o wide
done

echo "===== PERSISTENT STORAGE ====="
kubectl -n "${NS}" get pvc -o wide
kubectl get pv -o wide
kubectl get pv -o json | python3 -c '
import json, sys
doc = json.load(sys.stdin)
for item in sorted(doc.get("items", []), key=lambda row: row["metadata"]["name"]):
    spec = item.get("spec", {})
    claim = spec.get("claimRef") or {}
    if claim.get("namespace") == "deer-flow":
        print("pv=%s claim=%s/%s access=%s reclaim=%s storage_class=%s nfs=%s local=%s host_path=%s" % (
            item["metadata"]["name"], claim.get("namespace"), claim.get("name"), spec.get("accessModes"),
            spec.get("persistentVolumeReclaimPolicy"), spec.get("storageClassName"), spec.get("nfs"), spec.get("local"), spec.get("hostPath")))
'

echo "===== LIVE CONFIGURATION WITHOUT SECRETS ====="
for SELECTOR in app=gateway app=ppt-master-runner; do
  POD="$(ready_pod "${SELECTOR}")"
  CONTAINER="${SELECTOR#app=}"
  echo "live_config_pod=${POD} container=${CONTAINER}"
  kubectl -n "${NS}" exec -i "${POD}" -c "${CONTAINER}" -- \
    /app/backend/.venv/bin/python - <<'PY'
from deerflow.config import get_app_config
cfg = get_app_config()
stream = cfg.stream_bridge
print("stream_bridge_type=%s" % (stream.type if stream is not None else "memory(default)"))
print("stream_bridge_redis_configured=%s" % bool(stream is not None and stream.redis_url))
print("run_ownership_lease_seconds=%s" % cfg.run_ownership.lease_seconds)
print("run_ownership_grace_seconds=%s" % cfg.run_ownership.grace_seconds)
print("run_ownership_heartbeat_enabled=%s" % cfg.run_ownership.heartbeat_enabled)
print("sandbox_use=%s" % cfg.sandbox.use)
print("sandbox_replicas=%s" % cfg.sandbox.replicas)
print("sandbox_idle_timeout=%s" % cfg.sandbox.idle_timeout)
print("sandbox_shared_thread_data_mounts=%s" % cfg.sandbox.shared_thread_data_mounts)
print("sandbox_provisioner_url=%s" % getattr(cfg.sandbox, "provisioner_url", None))
PY
done

echo "===== LIVE EXECUTION LEASE SOURCE ====="
for SELECTOR in app=gateway app=ppt-master-runner; do
  POD="$(ready_pod "${SELECTOR}")"
  CONTAINER="${SELECTOR#app=}"
  kubectl -n "${NS}" exec -i "${POD}" -c "${CONTAINER}" -- \
    /app/backend/.venv/bin/python - "${POD}" <<'PY'
import ast, hashlib, inspect, sys
from pathlib import Path
from deerflow.community.aio_sandbox import aio_sandbox_provider as module
source_path = Path(inspect.getsourcefile(module)).resolve()
source = source_path.read_text(encoding="utf-8")
tree = ast.parse(source)
functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
node = functions.get("_open_lock_file")
if node is None:
    raise SystemExit("_open_lock_file missing")
modes = []
for child in ast.walk(node):
    if isinstance(child, ast.Call) and isinstance(child.func, ast.Name) and child.func.id == "open" and len(child.args) >= 2:
        value = child.args[1]
        if isinstance(value, ast.Str):
            modes.append(value.s)
        elif isinstance(value, ast.Constant):
            modes.append(value.value)
print("execution_lease_source_pod=%s path=%s sha256=%s open_modes=%s shared_lock=%s exclusive_nonblocking=%s" % (
    sys.argv[1], source_path, hashlib.sha256(source.encode("utf-8")).hexdigest(), modes,
    "LOCK_SH" in source, "LOCK_EX | fcntl.LOCK_NB" in source))
PY
done

echo "===== NFS MOUNTS FROM LIVE PODS ====="
for SELECTOR in app=gateway app=ppt-master-runner; do
  POD="$(ready_pod "${SELECTOR}")"
  CONTAINER="${SELECTOR#app=}"
  echo "mount_audit_pod=${POD} container=${CONTAINER}"
  kubectl -n "${NS}" exec "${POD}" -c "${CONTAINER}" -- sh -ec '
    df -hT /app/backend/.deer-flow /app/skills 2>/dev/null || true
    grep -E "(/app/backend/\.deer-flow|/app/skills)" /proc/self/mountinfo || true
  '
done

echo "===== DATABASE RUNTIME STATE ====="
PG_POD="$(kubectl -n "${NS}" get pods -l app=postgres --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}')"
test -n "${PG_POD}"
kubectl -n "${NS}" exec -i "${PG_POD}" -c postgres -- sh -ec '
  export PGOPTIONS="-c default_transaction_read_only=on -c statement_timeout=10000"
  exec psql -X -P pager=off -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"
' <<'SQL'
SELECT version_num AS database_version FROM alembic_version;
SELECT model_name, status, count(*) AS runs
FROM runs
WHERE status IN ('pending', 'running')
GROUP BY model_name, status
ORDER BY model_name NULLS FIRST, status;
SELECT count(*) AS active_ppt_master_runs
FROM runs
WHERE status IN ('pending', 'running') AND model_name = 'ppt-master-agent-host';
SELECT run_id, thread_id, status, model_name, created_at, updated_at, left(coalesce(error, ''), 240) AS error_excerpt
FROM runs
WHERE model_name = 'ppt-master-agent-host'
ORDER BY created_at DESC
LIMIT 10;
SELECT status, count(*) AS active_oa_jobs
FROM oa_policy_flow_jobs
WHERE status IN ('queued', 'launching', 'running')
GROUP BY status
ORDER BY status;
SQL

echo "===== SANDBOX OBJECTS ====="
kubectl -n "${NS}" get pods -o wide | grep -E '(^NAME|sandbox-)' || true
kubectl -n "${NS}" get services -o wide | grep -E '(^NAME|sandbox-)' || true

echo "===== HEALTH ====="
NGINX_POD="$(ready_pod app=nginx)"
kubectl -n "${NS}" exec "${NGINX_POD}" -c nginx -- wget -qO- -T 10 http://gateway:8001/health
echo
kubectl -n "${NS}" exec "${NGINX_POD}" -c nginx -- wget -qO- -T 10 http://ppt-master-runner:8003/health
echo

echo "===== RECENT PPT RUNNER ERRORS ====="
kubectl -n "${NS}" logs deployment/ppt-master-runner -c ppt-master-runner --since=24h --tail=2000 2>&1 | \
  grep -E 'Bad file descriptor|External ppt-master transport failed|PPT Agent host transport failed|ERROR|Traceback' | tail -n 120 || true

GATEWAY_REPLICAS="$(kubectl -n "${NS}" get deployment gateway -o jsonpath='{.spec.replicas}')"
RUNNER_POD="$(ready_pod app=ppt-master-runner)"
CONFIG_SUMMARY="$(kubectl -n "${NS}" exec "${RUNNER_POD}" -c ppt-master-runner -- /app/backend/.venv/bin/python -c '
from deerflow.config import get_app_config
c=get_app_config(); print("%s|%s" % ((c.stream_bridge.type if c.stream_bridge else "memory"), c.run_ownership.heartbeat_enabled))
')"
IFS='|' read -r STREAM_TYPE HEARTBEAT_ENABLED <<< "${CONFIG_SUMMARY}"
if test "${GATEWAY_REPLICAS}" -gt 1 && { test "${STREAM_TYPE}" != redis || test "${HEARTBEAT_ENABLED}" != True; }; then
  echo "gateway_multi_instance_safety=RISK replicas=${GATEWAY_REPLICAS} stream_bridge=${STREAM_TYPE} heartbeat_enabled=${HEARTBEAT_ENABLED}"
else
  echo "gateway_multi_instance_safety=PASS replicas=${GATEWAY_REPLICAS} stream_bridge=${STREAM_TYPE} heartbeat_enabled=${HEARTBEAT_ENABLED}"
fi
echo "production_state_survey=PASS"
