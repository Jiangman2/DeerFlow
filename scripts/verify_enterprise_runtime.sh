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
kubectl_df rollout status deployment/deer-flow-mineru-dispatcher --timeout=180s
kubectl_df rollout status statefulset/postgres --timeout=180s
kubectl_df rollout status statefulset/postgres-dashboard-replica --timeout=600s
kubectl_df rollout status statefulset/weknora-postgres --timeout=180s
kubectl_df rollout status statefulset/weknora-neo4j --timeout=180s
kubectl_df get configmap "$CONFIGMAP" -o name
kubectl_df get secret deer-flow-enterprise-ca -o name
kubectl_df get secret deer-flow-dashboard-postgres -o name

echo "===== DASHBOARD READ-REPLICA ISOLATION ====="
DASHBOARD_REPLICA_NODE="$(
    kubectl_df get pod postgres-dashboard-replica-0 -o jsonpath='{.spec.nodeName}'
)"
echo "dashboard_replica_node=$DASHBOARD_REPLICA_NODE"
test "$DASHBOARD_REPLICA_NODE" = "datax-03"

gateway_python '
import asyncio
import os
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

async def main():
    assert os.environ.get("DEER_FLOW_DASHBOARD_REQUIRE_READ_REPLICA") == "true"
    assert os.environ.get("DEER_FLOW_DASHBOARD_VERIFY_READ_REPLICA") == "true"
    url = os.environ.get("DEER_FLOW_DASHBOARD_DATABASE_URL", "")
    if not url:
        raise SystemExit("FAILED: dashboard replica URL is missing")
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    engine = create_async_engine(
        url,
        pool_size=1,
        max_overflow=0,
        connect_args={"server_settings": {"default_transaction_read_only": "on"}},
    )
    try:
        async with engine.connect() as connection:
            recovery = bool(await connection.scalar(text("SELECT pg_is_in_recovery()")))
            read_only = str(await connection.scalar(text("SHOW transaction_read_only")))
            lag = await connection.scalar(text("SELECT now() - pg_last_xact_replay_timestamp()"))
        print(f"dashboard_replica_recovery={str(recovery).lower()}")
        print(f"dashboard_replica_read_only={read_only}")
        print(f"dashboard_replica_lag={lag}")
        if not recovery or read_only != "on":
            raise SystemExit("FAILED: dashboard target is not a read-only physical standby")
    finally:
        await engine.dispose()

asyncio.run(main())
print("dashboard_replica_isolation=PASS")
'

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

echo "===== SHARED THREAD-DATA UPLOAD CONTRACT ====="
gateway_python '
from pathlib import Path
import yaml

config = yaml.safe_load(Path("/app/backend/config.yaml").read_text()) or {}
sandbox = config.get("sandbox") or {}
assert sandbox.get("shared_thread_data_mounts") is True, sandbox
print("sandbox_shared_thread_data_config=PASS")
'

GATEWAY_USERDATA_CLAIM="$(
    kubectl_df get deployment gateway \
      -o jsonpath='{.spec.template.spec.volumes[?(@.name=="userdata")].persistentVolumeClaim.claimName}'
)"
GATEWAY_USERDATA_MOUNT="$(
    kubectl_df get deployment gateway \
      -o jsonpath='{.spec.template.spec.containers[?(@.name=="gateway")].volumeMounts[?(@.name=="userdata")].mountPath}'
)"
GATEWAY_USERDATA_SUBPATH="$(
    kubectl_df get deployment gateway \
      -o jsonpath='{.spec.template.spec.containers[?(@.name=="gateway")].volumeMounts[?(@.name=="userdata")].subPath}'
)"
PROVISIONER_USERDATA_CLAIM="$(
    kubectl_df get deployment provisioner \
      -o jsonpath='{.spec.template.spec.containers[?(@.name=="provisioner")].env[?(@.name=="USERDATA_PVC_NAME")].value}'
)"
echo "gateway_userdata_claim=$GATEWAY_USERDATA_CLAIM"
echo "gateway_userdata_mount=$GATEWAY_USERDATA_MOUNT"
echo "gateway_userdata_subpath=$GATEWAY_USERDATA_SUBPATH"
echo "provisioner_userdata_claim=$PROVISIONER_USERDATA_CLAIM"
test "$GATEWAY_USERDATA_CLAIM" = "deer-flow-userdata-pvc"
test "$GATEWAY_USERDATA_MOUNT" = "/app/backend/.deer-flow"
test "$GATEWAY_USERDATA_SUBPATH" = "deer-flow"
test "$PROVISIONER_USERDATA_CLAIM" = "$GATEWAY_USERDATA_CLAIM"

kubectl_df get pods -l app=deer-flow-sandbox -o json |
python3 -c '
import json
import sys

payload = json.load(sys.stdin)
items = payload.get("items", [])
for pod in items:
    volumes = {item["name"]: item for item in pod["spec"].get("volumes", [])}
    claim = volumes.get("user-data", {}).get("persistentVolumeClaim", {}).get("claimName")
    assert claim == "deer-flow-userdata-pvc", (pod["metadata"]["name"], claim)
    sandbox = next(item for item in pod["spec"]["containers"] if item["name"] == "sandbox")
    mount = next(item for item in sandbox.get("volumeMounts", []) if item["name"] == "user-data")
    assert mount["mountPath"] == "/mnt/user-data", (pod["metadata"]["name"], mount)
    assert mount.get("subPath", "").startswith("deer-flow/users/"), (pod["metadata"]["name"], mount)
print(f"active_sandbox_shared_mount_probe=PASS pods={len(items)}")
'
echo "shared_thread_data_upload_contract=PASS"

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
  deployment/deer-flow-mineru-adapter \
  deployment/deer-flow-mineru-dispatcher
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

echo "===== MINERU DISPATCHED THREE-NODE CAPACITY ====="
kubectl_df wait \
  --for=condition=Ready \
  pod \
  -l 'app=deer-flow-mineru-adapter,deerflow.cnooc/mineru-role=worker' \
  --timeout=180s
MINERU_POD_COUNT="$(
    kubectl_df get pod \
      -l 'app=deer-flow-mineru-adapter,deerflow.cnooc/mineru-role=worker' \
      --no-headers |
    awk 'END {print NR+0}'
)"
MINERU_NODE_COUNT="$(
    kubectl_df get pod \
      -l 'app=deer-flow-mineru-adapter,deerflow.cnooc/mineru-role=worker' \
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
test "$MINERU_POD_COUNT" -eq 6
test "$MINERU_NODE_COUNT" -eq 3
test "$PDB_MIN_AVAILABLE" = "4"
for NODE in datax-01 datax-02 datax-03
do
    NODE_WORKERS="$(
        kubectl_df get pod \
          -l 'app=deer-flow-mineru-adapter,deerflow.cnooc/mineru-role=worker' \
          --field-selector "spec.nodeName=$NODE" \
          --no-headers |
        awk 'END {print NR+0}'
    )"
    echo "mineru_node=$NODE serialized_workers=$NODE_WORKERS"
    test "$NODE_WORKERS" -eq 2
done
gateway_python '
import json
from urllib.request import urlopen

with urlopen("http://deer-flow-mineru-adapter:50052/health", timeout=5) as response:
    payload = json.load(response)
assert payload["role"] == "dispatcher", payload
assert payload["dispatcher"]["discovered"] == 6, payload
assert payload["dispatcher"]["ready"] is True, payload
print("mineru_dispatcher_health=PASS")
'

echo "===== WEKNORA GRAPH, MODEL, AND SSRF CONTRACT ====="
WEKNORA_ENV="$(
    kubectl_df get deployment weknora-app \
      -o jsonpath='{range .spec.template.spec.containers[?(@.name=="app")].env[*]}{.name}={.value}{"\n"}{end}'
)"
for REQUIRED in \
  'ENABLE_GRAPH_RAG=true' \
  'NEO4J_ENABLE=true' \
  'NEO4J_URI=bolt://weknora-neo4j:7687' \
  'WEKNORA_ASYNQ_CORE_CONCURRENCY=4' \
  'WEKNORA_ASYNQ_SHARED_CONCURRENCY=2' \
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
  deer-flow-mineru-adapter deer-flow-mineru-dispatcher \
  -o wide
kubectl_df get statefulset postgres postgres-dashboard-replica weknora-postgres weknora-neo4j -o wide
echo "enterprise_runtime_baseline=PASS"
