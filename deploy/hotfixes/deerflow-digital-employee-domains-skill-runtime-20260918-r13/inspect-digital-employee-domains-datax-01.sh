#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
. "${ROOT_DIR}/IMAGE_LOCKS.env"
NS=deer-flow

die() {
  echo "digital_employee_domains_server_survey=FAIL detail=$*" >&2
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

db_scalar() {
  local sql="$1"
  kubectl -n "${NS}" exec "${PG_POD}" -c postgres -- \
    sh -ec 'exec psql -X -At -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "$1"' sh "${sql}"
}

echo '===== DEERFLOW DIGITAL EMPLOYEE BUSINESS-DOMAIN SERVER SURVEY ====='
date
hostname
uname -m
id

test "$(id -u)" = '0' || die 'must_run_as_root'
test "$(hostname -s)" = 'datax-01' || die 'must_run_on_datax-01'
test "$(uname -m)" = 'aarch64' || die 'requires_aarch64'
for command_name in kubectl python3 sha256sum ctr buildctl tar df; do
  command -v "${command_name}" >/dev/null || die "command_not_found=${command_name}"
done
BUILDCTL_BUILD_HELP="$(buildctl build --help 2>&1)"
grep -q -- '--oci-layout' <<<"${BUILDCTL_BUILD_HELP}" || die 'buildctl_oci_layout_not_supported'
(cd "${ROOT_DIR}" && sha256sum -c PACKAGE.SHA256)
python3 "${ROOT_DIR}/verify-digital-employee-domains.py" --root "${ROOT_DIR}"

echo '===== CONTROL-PLANE AND NODE BASELINE ====='
kubectl version --client
kubectl get nodes datax-01 datax-02 datax-03 -o wide
kubectl get nodes datax-01 datax-02 datax-03 -o json | python3 -c '
import json, sys
nodes = json.load(sys.stdin).get("items", [])
expected = {"datax-01", "datax-02", "datax-03"}
observed = {(item.get("metadata") or {}).get("name") for item in nodes}
assert observed == expected, (observed, expected)
for node in nodes:
    metadata = node.get("metadata") or {}
    status = node.get("status") or {}
    name = metadata.get("name")
    assert not metadata.get("deletionTimestamp"), name
    assert (status.get("nodeInfo") or {}).get("architecture") == "arm64", name
    conditions = {item.get("type"): item.get("status") for item in status.get("conditions") or []}
    assert conditions.get("Ready") == "True", (name, conditions)
    print("node_baseline=%s:PASS taints=%d" % (name, len((node.get("spec") or {}).get("taints") or [])))
'

FREE_KIB="$(df -Pk /opt | awk 'NR == 2 {print $4}')"
test -n "${FREE_KIB}" || die 'could_not_measure_opt_free_space'
(( FREE_KIB >= 15728640 )) || die "insufficient_opt_free_kib=${FREE_KIB} required=15728640"
echo "opt_free_kib=${FREE_KIB}"
buildctl --version
buildctl debug workers
ctr version

echo '===== KUBERNETES AUTHORIZATION AND STORAGE ====='
test "$(kubectl auth can-i create pods -n "${NS}")" = 'yes' || die 'cannot_create_probe_pods'
test "$(kubectl auth can-i delete pods -n "${NS}")" = 'yes' || die 'cannot_delete_probe_pods'
test "$(kubectl auth can-i patch deployments.apps -n "${NS}")" = 'yes' || die 'cannot_patch_deployments'
kubectl -n "${NS}" get pvc deer-flow-userdata-pvc deer-flow-skills-pvc -o wide
for pvc in deer-flow-userdata-pvc deer-flow-skills-pvc; do
  phase="$(kubectl -n "${NS}" get pvc "${pvc}" -o jsonpath='{.status.phase}')"
  test "${phase}" = 'Bound' || die "pvc_not_bound=${pvc}:${phase}"
done

echo '===== APPLICATION DEPLOYMENT BASELINE ====='
kubectl -n "${NS}" rollout status deployment/gateway --timeout=300s
kubectl -n "${NS}" rollout status deployment/frontend --timeout=300s
kubectl -n "${NS}" rollout status deployment/provisioner --timeout=300s
kubectl -n "${NS}" rollout status deployment/nginx --timeout=300s
kubectl -n "${NS}" get deployment gateway frontend provisioner nginx -o json | python3 -c '
import json, sys
for deployment in json.load(sys.stdin).get("items", []):
    metadata = deployment.get("metadata") or {}
    spec = deployment.get("spec") or {}
    status = deployment.get("status") or {}
    name = metadata.get("name")
    replicas = int(spec.get("replicas", 1))
    assert status.get("observedGeneration") == metadata.get("generation"), name
    assert int(status.get("updatedReplicas", 0)) == replicas, name
    assert int(status.get("readyReplicas", 0)) == replicas, name
    assert int(status.get("availableReplicas", 0)) == replicas, name
    assert int(status.get("unavailableReplicas", 0)) == 0, name
    print("deployment_baseline=%s:PASS replicas=%d" % (name, replicas))
'
kubectl -n "${NS}" get pods -o wide

CURRENT_GATEWAY_IMAGE="$(kubectl -n "${NS}" get deployment gateway -o jsonpath='{.spec.template.spec.containers[?(@.name=="gateway")].image}')"
CURRENT_GATEWAY_INIT_IMAGE="$(kubectl -n "${NS}" get deployment gateway -o jsonpath='{.spec.template.spec.initContainers[?(@.name=="seed-extensions-config")].image}')"
CURRENT_FRONTEND_IMAGE="$(kubectl -n "${NS}" get deployment frontend -o jsonpath='{.spec.template.spec.containers[?(@.name=="frontend")].image}')"
CURRENT_PROVISIONER_IMAGE="$(kubectl -n "${NS}" get deployment provisioner -o jsonpath='{.spec.template.spec.containers[?(@.name=="provisioner")].image}')"
test "${CURRENT_GATEWAY_IMAGE}" = "${BASE_GATEWAY_IMAGE}" || die "unexpected_gateway_image=${CURRENT_GATEWAY_IMAGE}"
test "${CURRENT_GATEWAY_INIT_IMAGE}" = "${BASE_GATEWAY_INIT_IMAGE}" || die "unexpected_gateway_init_image=${CURRENT_GATEWAY_INIT_IMAGE}"
test "${CURRENT_FRONTEND_IMAGE}" = "${BASE_FRONTEND_IMAGE}" || die "unexpected_frontend_image=${CURRENT_FRONTEND_IMAGE}"
test "${CURRENT_PROVISIONER_IMAGE}" = "${BASE_PROVISIONER_IMAGE}" || die "unexpected_provisioner_image=${CURRENT_PROVISIONER_IMAGE}"
ctr -n k8s.io images inspect "${BASE_GATEWAY_IMAGE}" >/dev/null || die 'base_gateway_image_missing_on_datax_01'
ctr -n k8s.io images inspect "${BASE_GATEWAY_INIT_IMAGE}" >/dev/null || die 'base_gateway_init_image_missing_on_datax_01'
ctr -n k8s.io images inspect "${BASE_FRONTEND_IMAGE}" >/dev/null || die 'base_frontend_image_missing_on_datax_01'
ctr -n k8s.io images inspect "${BASE_PROVISIONER_IMAGE}" >/dev/null || die 'base_provisioner_image_missing_on_datax_01'
ctr -n k8s.io images inspect "${BASE_SKILLS_IMAGE}" >/dev/null || die 'base_skills_image_missing_on_datax_01'
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
rolling = (spec.get("strategy") or {}).get("rollingUpdate") or {}
assert str(rolling.get("maxSurge", "25%")) not in {"0", "0%"}
print("provisioner_production_environment=PASS")
print("provisioner_rolling_update_continuity=PASS")
'

echo '===== SHARED PUBLIC SKILL READ-ONLY BASELINE ====='
"${ROOT_DIR}/manage-research-fund-settlement-skill-datax-01.sh" --preflight

echo '===== SERVICE HEALTH ====='
NGINX_POD="$(running_pod app=nginx nginx)"
GATEWAY_POD="$(running_pod app=gateway gateway)"
FRONTEND_POD="$(running_pod app=frontend frontend)"
test -n "${NGINX_POD}" || die 'running_nginx_pod_not_found'
test -n "${GATEWAY_POD}" || die 'running_gateway_pod_not_found'
test -n "${FRONTEND_POD}" || die 'running_frontend_pod_not_found'
kubectl -n "${NS}" exec "${NGINX_POD}" -c nginx -- \
  wget -Y off -qO- -T 10 http://gateway:8001/health | grep -q healthy
kubectl -n "${NS}" exec "${FRONTEND_POD}" -c frontend -- \
  wget -Y off -qO- -T 10 http://127.0.0.1:3000/api/runtime-config >/dev/null
echo 'gateway_health=PASS'
echo 'frontend_runtime_config=PASS'
echo 'service_health_proxy_bypass=PASS'

echo '===== DATABASE AND ACTIVE WORK ====='
PG_POD="$(running_pod app=postgres postgres)"
test -n "${PG_POD}" || die 'running_postgres_pod_not_found'
ACTIVE_RUNS="$(db_scalar "SELECT count(*) FROM runs WHERE status IN ('pending','running');")"
ACTIVE_OA_JOBS="$(db_scalar "SELECT count(*) FROM oa_policy_flow_jobs WHERE status IN ('queued','launching','running');")"
AGENT_ROWS="$(db_scalar "SELECT count(*) FROM agents;")"
echo "active_deerflow_runs=${ACTIVE_RUNS}"
echo "active_oa_jobs=${ACTIVE_OA_JOBS}"
echo "personal_agent_rows=${AGENT_ROWS}"
echo 'agent_category_distribution_begin'
db_scalar "SELECT COALESCE(config->>'category_id','<missing>') || E'\\t' || count(*) FROM agents GROUP BY COALESCE(config->>'category_id','<missing>') ORDER BY COALESCE(config->>'category_id','<missing>');"
echo 'agent_category_distribution_end'
kubectl -n "${NS}" exec "${PG_POD}" -c postgres -- \
  sh -ec 'exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --data-only --table=public.agents' \
  >/dev/null
echo 'agents_backup_capability=PASS'

kubectl -n "${NS}" exec -i "${GATEWAY_POD}" -c gateway -- \
  sh -ec 'cd /app/backend && PYTHONPATH=packages/harness:. .venv/bin/python -' <<'PY'
from pathlib import Path

import yaml

root = Path("/app/backend/.deer-flow")
patterns = ("users/*/agents/*/config.yaml", "team_agents/*/config.yaml", "agents/*/config.yaml")
files = sorted({path for pattern in patterns for path in root.glob(pattern) if path.is_file()})
for path in files:
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise SystemExit("invalid agent config mapping: " + str(path))
print("file_agent_config_readability=PASS count=%d" % len(files))
PY

echo '===== RECENT WARNING EVENTS (OBSERVATIONAL) ====='
kubectl -n "${NS}" get events --field-selector type=Warning --sort-by=.lastTimestamp | tail -n 40 || true

echo 'digital_employee_domains_server_survey=PASS'
if (( ACTIVE_RUNS == 0 && ACTIVE_OA_JOBS == 0 )); then
  echo 'maintenance_gate_ready=yes'
else
  echo 'maintenance_gate_ready=no'
fi
echo 'production_mutation=NONE'
echo 'production_skill_mutated=no'
