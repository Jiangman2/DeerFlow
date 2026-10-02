#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
NS=deer-flow
TARGET_HOST=aiapi.cnooc.com.cn
EXPECTED_GATEWAY_IMAGE='deerflow.local/deer-flow-gateway:20260918-enterprise-arm64-rc15-skillauthoring-model1'
die() { echo "deerflow_llm_direct_transport_status=FAIL detail=$*" >&2; exit 1; }

test "$(id -u)" = 0 || die 'must_run_as_root'
test "$(hostname -s)" = datax-01 || die 'must_run_on_datax-01'
kubectl -n "${NS}" rollout status deployment/gateway --timeout=300s
kubectl -n "${NS}" rollout status deployment/nginx --timeout=300s
image="$(kubectl -n "${NS}" get deployment gateway -o jsonpath='{.spec.template.spec.containers[?(@.name=="gateway")].image}')"
test "${image}" = "${EXPECTED_GATEWAY_IMAGE}" || die "unexpected_gateway_image=${image}"

PODS="$(kubectl -n "${NS}" get pods -l app=gateway -o json | python3 -c '
import json,sys
rows=[]
for pod in json.load(sys.stdin).get("items",[]):
    if (pod.get("metadata") or {}).get("deletionTimestamp"): continue
    states={x.get("name"):x for x in (pod.get("status") or {}).get("containerStatuses") or []}
    if (pod.get("status") or {}).get("phase")=="Running" and (states.get("gateway") or {}).get("ready"):
        rows.append((pod.get("metadata") or {}).get("name",""))
assert len(rows)==2, "ready gateway pods=%d" % len(rows)
print("\n".join(sorted(rows)))
')"
endpoint_count="$(kubectl -n "${NS}" get endpointslice -l kubernetes.io/service-name=gateway -o json | python3 -c '
import json,sys
count=0
for item in json.load(sys.stdin).get("items",[]):
    for endpoint in item.get("endpoints") or []:
        if (endpoint.get("conditions") or {}).get("ready") is not False:
            count+=len(endpoint.get("addresses") or [])
print(count)
')"
test "${endpoint_count}" = 2 || die "gateway_ready_endpoints=${endpoint_count} expected=2"

while IFS= read -r pod; do
  test -n "${pod}" || continue
  for name in NO_PROXY no_proxy; do
    value="$(kubectl -n "${NS}" exec "${pod}" -c gateway -- printenv "${name}" 2>/dev/null || true)"
    python3 - "${TARGET_HOST}" "${value}" <<'PY' || die "${name}_target_missing pod=${pod}"
import sys
tokens=[item.strip() for item in sys.argv[2].split(",") if item.strip()]
raise SystemExit(0 if sys.argv[1] in tokens else 1)
PY
    echo "gateway_${name}_target=PASS pod=${pod}"
  done
  kubectl -n "${NS}" exec -i "${pod}" -c gateway -- \
    sh -ec 'cd /app/backend && PYTHONPATH=packages/harness:. exec .venv/bin/python -' \
    < "${ROOT_DIR}/factory_runtime_probe.py"
  echo "gateway_real_factory_model_transport=PASS pod=${pod}"
done <<< "${PODS}"

NGINX_POD="$(kubectl -n "${NS}" get pods -l app=nginx --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}')"
test -n "${NGINX_POD}" || die 'running_nginx_pod_not_found'
kubectl -n "${NS}" exec "${NGINX_POD}" -c nginx -- \
  wget -qO- -T 10 http://gateway:8001/health
echo
echo 'deerflow_llm_direct_transport_status=PASS'
echo 'gateway_ready_replicas=2'
echo 'gateway_ready_endpoints=2'
echo 'gateway_model_transport=DIRECT_FOR_aiapi.cnooc.com.cn'
echo 'gateway_image=PRESERVED'
echo 'production_database_mutation=NONE'
echo 'weknora_mutation=NONE'
echo 'ppt_runner_mutation=NONE'
