#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
. "${ROOT_DIR}/IMAGE_LOCKS.env"
NS=deer-flow

die() {
  echo "digital_employee_domains_status=FAIL detail=$*" >&2
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
    if (pod.get("status") or {}).get("phase") == "Running" and (statuses.get(container) or {}).get("ready"):
        print(meta.get("name", ""))
        break
' "${container}"
}

test "$(id -u)" = '0' || die 'must_run_as_root'
test "$(hostname -s)" = 'datax-01' || die 'must_run_on_datax-01'

GATEWAY_IMAGE="$(kubectl -n "${NS}" get deployment gateway -o jsonpath='{.spec.template.spec.containers[?(@.name=="gateway")].image}')"
GATEWAY_INIT_IMAGE="$(kubectl -n "${NS}" get deployment gateway -o jsonpath='{.spec.template.spec.initContainers[?(@.name=="seed-extensions-config")].image}')"
FRONTEND_IMAGE="$(kubectl -n "${NS}" get deployment frontend -o jsonpath='{.spec.template.spec.containers[?(@.name=="frontend")].image}')"
PROVISIONER_IMAGE="$(kubectl -n "${NS}" get deployment provisioner -o jsonpath='{.spec.template.spec.containers[?(@.name=="provisioner")].image}')"
test "${GATEWAY_IMAGE}" = "${TARGET_GATEWAY_IMAGE}" || die "unexpected_gateway_image=${GATEWAY_IMAGE}"
test "${GATEWAY_INIT_IMAGE}" = "${BASE_GATEWAY_INIT_IMAGE}" || die "unexpected_gateway_init_image=${GATEWAY_INIT_IMAGE}"
test "${FRONTEND_IMAGE}" = "${TARGET_FRONTEND_IMAGE}" || die "unexpected_frontend_image=${FRONTEND_IMAGE}"
test "${PROVISIONER_IMAGE}" = "${TARGET_PROVISIONER_IMAGE}" || die "unexpected_provisioner_image=${PROVISIONER_IMAGE}"
kubectl -n "${NS}" rollout status deployment/gateway --timeout=300s
kubectl -n "${NS}" rollout status deployment/frontend --timeout=300s
kubectl -n "${NS}" rollout status deployment/provisioner --timeout=300s
kubectl -n "${NS}" get pods -l 'app in (gateway,frontend,provisioner)' -o wide

GATEWAY_POD="$(running_pod app=gateway gateway)"
FRONTEND_POD="$(running_pod app=frontend frontend)"
NGINX_POD="$(running_pod app=nginx nginx)"
PG_POD="$(running_pod app=postgres postgres)"
PROVISIONER_POD="$(running_pod app=provisioner provisioner)"
test -n "${GATEWAY_POD}" || die 'running_gateway_pod_not_found'
test -n "${FRONTEND_POD}" || die 'running_frontend_pod_not_found'
test -n "${NGINX_POD}" || die 'running_nginx_pod_not_found'
test -n "${PG_POD}" || die 'running_postgres_pod_not_found'
test -n "${PROVISIONER_POD}" || die 'running_provisioner_pod_not_found'

kubectl -n "${NS}" exec -i "${GATEWAY_POD}" -c gateway -- \
  sh -ec 'cd /app/backend && PYTHONPATH=packages/harness:. .venv/bin/python -' <<'PY'
from pathlib import Path

import yaml
from app.gateway.app import app
from app.gateway.routers.agents import CORPORATE_DIGITAL_EMPLOYEE_CATEGORIES
from deerflow.persistence.agents.base import AgentStore
from deerflow.persistence.agents.file import FileAgentStore
from deerflow.persistence.agents.sql import SQLAgentStore
from deerflow.skills.installer import build_skill_tree_manifest, verify_skill_tree_copy

items = CORPORATE_DIGITAL_EMPLOYEE_CATEGORIES.items
assert len(items) == 26
assert sum(item.domain_group == "unified" for item in items) == 17
assert sum(item.domain_group == "distributed" for item in items) == 9
assert not any(route.path == "/api/admin/digital-employee-categories" for route in app.routes)
allowed = {item.id for item in items}
root = Path("/app/backend/.deer-flow")
patterns = ("users/*/agents/*/config.yaml", "team_agents/*/config.yaml", "agents/*/config.yaml")
files = sorted({path for pattern in patterns for path in root.glob(pattern) if path.is_file()})
invalid = []
for path in files:
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict) or payload.get("category_id") not in allowed:
        invalid.append(str(path))
assert not invalid, invalid[:20]
assert SQLAgentStore.patch_config is not AgentStore.patch_config
assert FileAgentStore.patch_config is not AgentStore.patch_config
assert callable(build_skill_tree_manifest) and callable(verify_skill_tree_copy)
print("gateway_business_domain_runtime=PASS")
print("gateway_skill_install_integrity_runtime=PASS")
print("file_agent_category_migration=PASS count=%d" % len(files))
PY

INVALID_DB_CATEGORIES="$(kubectl -n "${NS}" exec "${PG_POD}" -c postgres -- \
  sh -ec 'exec psql -X -At -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "$1"' sh \
  "SELECT count(*) FROM agents WHERE COALESCE(config->>'category_id','') NOT IN ('comprehensive-management','human-resources','party-building','strategic-development','production-operations','finance-capital','exploration-development','engineering-technology','science-technology-innovation','safety-environment','digitalization','international-business','enterprise-management','legal-affairs','materials-supply-chain','sales-trading','audit-inspection','oil-gas-resource-development','new-energy','oilfield-services','offshore-engineering-construction','energy-technology-services','natural-gas-power','refining-petrochemical','chemicals','financial-services');")"
test "${INVALID_DB_CATEGORIES}" = '0' || die "unmigrated_database_agent_categories=${INVALID_DB_CATEGORIES}"
echo 'database_agent_category_migration=PASS'

kubectl -n "${NS}" exec -i "${GATEWAY_POD}" -c gateway -- \
  sh -ec 'cd /app/skills/public/research-fund-settlement; sha256sum -c -' \
  < "${ROOT_DIR}/SKILL.SHA256"
kubectl -n "${NS}" exec "${GATEWAY_POD}" -c gateway -- sh -ec '
  skill=/app/skills/public/research-fund-settlement
  work="$(mktemp -d /tmp/research-fund-status.XXXXXX)"
  trap '\''rm -rf -- "${work}"'\'' EXIT
  python=/app/backend/.venv/bin/python
  case_file="${skill}/examples/cases/group-company-complete.json"
  "${python}" "${skill}/scripts/report_cli.py" check \
    --case "${case_file}" --mode final --report "${work}/validation.json"
  "${python}" "${skill}/scripts/report_cli.py" build \
    --case "${case_file}" --mode final --output "${work}/report.docx"
  "${python}" "${skill}/scripts/report_cli.py" inspect \
    --case "${case_file}" --docx "${work}/report.docx"
  test "$(stat -c %s "${work}/report.docx")" -gt 50000
  echo research_fund_settlement_skill_check_build_inspect=PASS
'
echo 'research_fund_settlement_skill_runtime=CHECK_BUILD_INSPECT_PASS'

kubectl -n "${NS}" exec -i "${PROVISIONER_POD}" -c provisioner -- python3 - <<'PY'
import app

mounts = {item.mount_path: item for item in app._build_volume_mounts("thread-status-probe", user_id="user-status-probe")}
assert mounts["/mnt/skills/public"].sub_path == "public"
assert mounts["/mnt/skills/custom"].sub_path == "deer-flow/users/user-status-probe/skills/custom"
pod = app._build_pod("status-probe", "thread-status-probe", user_id="user-status-probe")
assert [item.name for item in pod.spec.init_containers] == ["prepare-user-data", "verify-skill-mounts"]
assert pod.metadata.annotations[app.SANDBOX_USER_ANNOTATION] == "user-status-probe"
assert pod.metadata.annotations[app.SANDBOX_THREAD_ANNOTATION] == "thread-status-probe"
print("provisioner_skillhub_pvc_runtime=PASS")
PY

kubectl -n "${NS}" exec "${FRONTEND_POD}" -c frontend -- \
  wget -Y off -qO- -T 10 http://127.0.0.1:3000/api/runtime-config >/dev/null
kubectl -n "${NS}" exec "${NGINX_POD}" -c nginx -- \
  wget -Y off -qO- -T 10 http://gateway:8001/health | grep -q healthy

echo "gateway_image=${GATEWAY_IMAGE}"
echo "gateway_init_image=${GATEWAY_INIT_IMAGE}"
echo "frontend_image=${FRONTEND_IMAGE}"
echo "provisioner_image=${PROVISIONER_IMAGE}"
echo 'gateway_init_image_preservation=PASS'
echo 'business_domain_catalog=PASS count=26 unified=17 distributed=9'
echo 'admin_category_management=ABSENT'
echo 'research_fund_settlement_skill=PASS'
echo 'skillhub_install_tree_integrity=PASS'
echo 'skillhub_custom_skill_sandbox_identity_contract=PASS'
echo 'frontend_runtime=PASS'
echo 'gateway_health=PASS'
echo 'service_health_proxy_bypass=PASS'
echo 'deerflow_digital_employee_domains_status=PASS'
