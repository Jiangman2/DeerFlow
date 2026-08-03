#!/usr/bin/env bash
set -Eeuo pipefail

KUBECONFIG_FILE="${KUBECONFIG_FILE:-/etc/kubernetes/admin.conf}"
NAMESPACE="${NAMESPACE:-deer-flow}"
CONFIGMAP="${CONFIGMAP:-deer-flow-config}"

kubectl_df() {
    kubectl --kubeconfig="$KUBECONFIG_FILE" -n "$NAMESPACE" "$@"
}

kubectl_cluster() {
    kubectl --kubeconfig="$KUBECONFIG_FILE" "$@"
}

gateway_python() {
    local program="$1"
    kubectl_df exec deployment/gateway -c gateway -- \
      sh -c 'cd /app/backend && exec uv run --no-sync python -c "$1"' \
      sh "$program"
}

echo "===== ENTERPRISE RUNTIME BASELINE ====="
date
hostname
uname -m

test -s "$KUBECONFIG_FILE"

echo "===== THREE-NODE ARM64 CLUSTER ====="
for NODE in datax-01 datax-02 datax-03
do
    ARCH="$(
        kubectl_cluster get node "$NODE" \
          -o jsonpath='{.metadata.labels.kubernetes\.io/arch}'
    )"
    READY="$(
        kubectl_cluster get node "$NODE" \
          -o jsonpath='{range .status.conditions[?(@.type=="Ready")]}{.status}{end}'
    )"
    echo "node=$NODE arch=$ARCH ready=$READY"
    test "$ARCH" = "arm64"
    test "$READY" = "True"
done

echo "===== CORE WORKLOAD READINESS ====="
kubectl_df rollout status deployment/gateway --timeout=180s
kubectl_df rollout status deployment/provisioner --timeout=180s
kubectl_df rollout status deployment/frontend --timeout=180s
kubectl_df rollout status deployment/weknora-app --timeout=180s
kubectl_df rollout status deployment/weknora-frontend --timeout=180s
kubectl_df rollout status deployment/deer-flow-mineru-adapter --timeout=180s
kubectl_df rollout status statefulset/postgres --timeout=180s
kubectl_df rollout status statefulset/weknora-postgres --timeout=180s
kubectl_df rollout status statefulset/weknora-neo4j --timeout=180s
kubectl_df get configmap "$CONFIGMAP" -o name
kubectl_df get secret deer-flow-enterprise-ca -o name

echo "===== CONFIG REFERENCES AND RESOLUTION ====="
gateway_python '
from pathlib import Path
import os
import yaml

config = yaml.safe_load(Path("/app/backend/config.yaml").read_text()) or {}
environment = ((config.get("sandbox") or {}).get("environment") or {})
expected = {
    "HTTP_PROXY": "HTTP_PROXY",
    "HTTPS_PROXY": "HTTPS_PROXY",
    "NO_PROXY": "NO_PROXY",
    "http_proxy": "HTTP_PROXY",
    "https_proxy": "HTTPS_PROXY",
    "no_proxy": "NO_PROXY",
}

for target, source in expected.items():
    reference_ok = environment.get(target) == f"${source}"
    resolved_ok = bool(os.environ.get(source, ""))
    print(f"{target}_reference_ok={str(reference_ok).lower()}")
    print(f"{target}_resolved_nonempty={str(resolved_ok).lower()}")
    if not reference_ok or not resolved_ok:
        raise SystemExit(f"FAILED: {target} does not resolve from {source}")

no_proxy = os.environ["NO_PROXY"]
for forbidden in ("dashscope.aliyuncs.com", "aliyuncs.com", "tavily.com"):
    if forbidden in {item.strip() for item in no_proxy.split(",")}:
        raise SystemExit(f"FAILED: {forbidden} incorrectly bypasses the proxy")

print("sandbox_command_proxy_resolution=PASS")
'

echo "===== PROVISIONER CONTRACT ====="
PROVISIONER_ENV="$(
    kubectl_df get deployment provisioner \
      -o jsonpath='{range .spec.template.spec.containers[?(@.name=="provisioner")].env[*]}{.name}={.value}{"\n"}{end}'
)"

for REQUIRED in \
  'IMAGE_GENERATION_PROVIDER=dashscope' \
  'DASHSCOPE_TLS_COMPAT=true' \
  'SANDBOX_CA_SECRET_NAME=deer-flow-enterprise-ca' \
  'SANDBOX_CA_SECRET_KEY=ca-bundle.crt' \
  'SANDBOX_CA_MOUNT_PATH=/etc/deerflow-ca' \
  'SANDBOX_OPENSSL_CONFIGMAP_NAME=deer-flow-gateway-openssl-compat'
do
    grep -Fxq "$REQUIRED" <<<"$PROVISIONER_ENV"
    echo "present=$REQUIRED"
done

SECRET_KEYS="$(
    sed -n 's/^SANDBOX_SECRET_ENV_KEYS=//p' <<<"$PROVISIONER_ENV"
)"
for KEY in DASHSCOPE_API_KEY SANDBOX_API_KEY HTTP_PROXY HTTPS_PROXY http_proxy https_proxy
do
    tr ',' '\n' <<<"$SECRET_KEYS" | grep -Fxq "$KEY"
    echo "sandbox_secret_key_declared=$KEY"
done

if tr ',' '\n' <<<"$SECRET_KEYS" | grep -Fxq GEMINI_API_KEY; then
    echo "FAILED: GEMINI_API_KEY remains in SANDBOX_SECRET_ENV_KEYS"
    exit 1
fi

echo "===== GATEWAY CA CONTRACT ====="
gateway_python '
from pathlib import Path
import os

expected = {
    "SSL_CERT_FILE": "/etc/deerflow-ca/ca-bundle.crt",
    "REQUESTS_CA_BUNDLE": "/etc/deerflow-ca/ca-bundle.crt",
    "CURL_CA_BUNDLE": "/etc/deerflow-ca/ca-bundle.crt",
    "NODE_EXTRA_CA_CERTS": "/etc/deerflow-ca/ca-bundle.crt",
    "OPENSSL_CONF": "/etc/deerflow-openssl/openssl.cnf",
    "DASHSCOPE_TLS_COMPAT": "true",
    "TAVILY_TLS_COMPAT": "true",
    "WEKNORA_BAILIAN_TLS_COMPAT": "true",
}
for key, value in expected.items():
    ok = os.environ.get(key) == value
    print(f"{key}_ok={str(ok).lower()}")
    if not ok:
        raise SystemExit(f"FAILED: {key} runtime value is incorrect")

for path in (
    "/etc/deerflow-ca/ca-bundle.crt",
    "/etc/deerflow-openssl/openssl.cnf",
):
    present = Path(path).is_file() and Path(path).stat().st_size > 0
    print(f"{path}_present={str(present).lower()}")
    if not present:
        raise SystemExit(f"FAILED: {path} is missing or empty")

print("gateway_ca_contract=PASS")
'

echo "===== IMMUTABLE LIVE IMAGE POLICY ====="
for WORKLOAD in \
  deployment/gateway \
  deployment/frontend \
  deployment/provisioner \
  deployment/deer-flow-mineru-adapter
do
    IMAGE="$(
        kubectl_df get "$WORKLOAD" \
          -o jsonpath='{.spec.template.spec.containers[0].image}'
    )"
    POLICY="$(
        kubectl_df get "$WORKLOAD" \
          -o jsonpath='{.spec.template.spec.containers[0].imagePullPolicy}'
    )"
    echo "workload=$WORKLOAD image=$IMAGE pull_policy=$POLICY"
    [[ "$IMAGE" == deerflow.local/* ]]
    [[ "$IMAGE" != *:latest ]]
    test "$POLICY" = "Never"
done

WEKNORA_UI_IMAGE="$(
    kubectl_df get deployment/weknora-frontend \
      -o jsonpath='{.spec.template.spec.containers[0].image}'
)"
WEKNORA_POSTGRES_IMAGE="$(
    kubectl_df get statefulset/weknora-postgres \
      -o jsonpath='{.spec.template.spec.containers[0].image}'
)"
echo "weknora_ui_image=$WEKNORA_UI_IMAGE"
echo "weknora_postgres_image=$WEKNORA_POSTGRES_IMAGE"
[[ "$WEKNORA_UI_IMAGE" == *@sha256:401604e79a5717dd966a13383d775f15d3125481d3e080eba329e4a0fbcf9523 ]]
[[ "$WEKNORA_POSTGRES_IMAGE" == deerflow.local/weknora-postgres:pg16-search-arm64-20260722-1 ]]

echo "===== STATIC STORAGE CONTRACT ====="
for PVC in \
  deer-flow-userdata-pvc \
  deer-flow-skills-pvc \
  deer-flow-postgres-backups-pvc \
  weknora-postgres-data \
  weknora-data-files \
  weknora-docreader-tmp \
  weknora-neo4j-data
do
    PHASE="$(kubectl_df get pvc "$PVC" -o jsonpath='{.status.phase}')"
    VOLUME="$(kubectl_df get pvc "$PVC" -o jsonpath='{.spec.volumeName}')"
    echo "pvc=$PVC phase=$PHASE volume=$VOLUME"
    test "$PHASE" = "Bound"
    test -n "$VOLUME"
done

POSTGRES_ACCESS="$(
    kubectl_df get statefulset/postgres \
      -o jsonpath='{.spec.volumeClaimTemplates[0].spec.accessModes[0]}'
)"
echo "postgres_access_mode=$POSTGRES_ACCESS"
test "$POSTGRES_ACCESS" = "ReadWriteOnce"

echo "===== MINERU THREE-NODE CAPACITY ====="
kubectl_df wait \
  --for=condition=Ready \
  pod \
  -l app=deer-flow-mineru-adapter \
  --timeout=180s
MINERU_POD_COUNT="$(
    kubectl_df get pod \
      -l app=deer-flow-mineru-adapter \
      --no-headers |
    awk 'END {print NR+0}'
)"
MINERU_NODE_COUNT="$(
    kubectl_df get pod \
      -l app=deer-flow-mineru-adapter \
      -o jsonpath='{range .items[*]}{.spec.nodeName}{"\n"}{end}' |
    sort -u |
    awk 'END {print NR+0}'
)"
PDB_MIN_AVAILABLE="$(
    kubectl_df get pdb deer-flow-mineru-adapter \
      -o jsonpath='{.spec.minAvailable}'
)"
echo "mineru_pod_count=$MINERU_POD_COUNT"
echo "mineru_distinct_node_count=$MINERU_NODE_COUNT"
echo "mineru_pdb_min_available=$PDB_MIN_AVAILABLE"
test "$MINERU_POD_COUNT" -eq 3
test "$MINERU_NODE_COUNT" -eq 3
test "$PDB_MIN_AVAILABLE" = "2"

echo "===== WEKNORA GRAPH, MODEL, AND SSRF CONTRACT ====="
WEKNORA_ENV="$(
    kubectl_df get deployment weknora-app \
      -o jsonpath='{range .spec.template.spec.containers[?(@.name=="app")].env[*]}{.name}={.value}{"\n"}{end}'
)"
for REQUIRED in \
  'ENABLE_GRAPH_RAG=true' \
  'NEO4J_ENABLE=true' \
  'NEO4J_URI=bolt://weknora-neo4j:7687' \
  'WEKNORA_ASYNQ_CORE_CONCURRENCY=2' \
  'WEKNORA_ASYNQ_SHARED_CONCURRENCY=1' \
  'SSRF_WHITELIST_EXTRA=deer-flow-mineru-adapter,model.ai.cnooc,gateway'
do
    grep -Fxq "$REQUIRED" <<<"$WEKNORA_ENV"
    echo "present=$REQUIRED"
done

if grep -E '^SSRF_WHITELIST_EXTRA=.*(10\.0\.0\.0/8|192\.168\.0\.0/16)' \
  <<<"$WEKNORA_ENV"
then
    echo "FAILED: broad private CIDR found in WeKnora SSRF allowlist"
    exit 1
fi

echo "===== SINGLE PUBLIC ENTRY ====="
NODEPORT="$(
    kubectl_df get service nginx-nodeport \
      -o jsonpath='{.spec.ports[0].nodePort}'
)"
echo "nginx_nodeport=$NODEPORT"
test "$NODEPORT" = "32026"

echo "===== FINAL STATUS ====="
kubectl_df get deployment \
  gateway frontend provisioner weknora-app weknora-frontend \
  deer-flow-mineru-adapter \
  -o wide
kubectl_df get statefulset postgres weknora-postgres weknora-neo4j -o wide
echo "enterprise_runtime_baseline=PASS"
