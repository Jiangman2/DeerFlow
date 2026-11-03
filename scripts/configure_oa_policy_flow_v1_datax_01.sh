#!/usr/bin/env bash
set -Eeuo pipefail

NAMESPACE="${NAMESPACE:-deer-flow}"
SECRET_NAME="${SECRET_NAME:-deer-flow-env}"
EXPECTED_HOST="datax-01"
OWNER_USER_ID="d1792c77-c297-439d-843f-81ea66095d1a"
AGENT_NAME="制度文生图智能体"
HANDOFF_FILE="/home/cnooc/oa-policy-flow-v1-secure-handoff.env"
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="/opt/deerflow-build-artifacts/oa-policy-flow-v1-config-${STAMP}"
PATCH_FILE="$(mktemp /tmp/oa-policy-flow-v1-secret.XXXXXX.json)"
HANDOFF_TMP="$(mktemp /tmp/oa-policy-flow-v1-handoff.XXXXXX.env)"

cleanup() {
  rm -f -- "${PATCH_FILE}" "${HANDOFF_TMP}"
}
trap cleanup EXIT

fail() {
  echo "oa_policy_flow_v1_config=FAIL detail=$*" >&2
  exit 1
}

secret_value() {
  local key="$1"
  local encoded
  encoded="$(
    kubectl -n "${NAMESPACE}" get secret "${SECRET_NAME}" \
      -o "jsonpath={.data.${key}}" 2>/dev/null || true
  )"
  if test -n "${encoded}"; then
    printf '%s' "${encoded}" | base64 -d
  fi
}

echo "===== OA POLICY FLOW V1 OUR-SIDE CONFIG GUARD ====="
date
hostname -s
uname -m
id

for command in kubectl python3 base64 install sha256sum; do
  command -v "${command}" >/dev/null || fail "missing_command=${command}"
done

test "$(hostname -s)" = "${EXPECTED_HOST}" || \
  fail "expected_host=${EXPECTED_HOST} actual_host=$(hostname -s)"
test "$(id -u)" = "0" || fail "must_run_as_root"
test "${KUBECONFIG:-}" = "/etc/kubernetes/admin.conf" || \
  fail "export_KUBECONFIG=/etc/kubernetes/admin.conf"

kubectl get --raw=/readyz >/dev/null
kubectl -n "${NAMESPACE}" get secret "${SECRET_NAME}" >/dev/null

CURRENT_ENABLED="$(secret_value OA_POLICY_FLOW_ENABLED | tr '[:upper:]' '[:lower:]')"
case "${CURRENT_ENABLED}" in
  ""|0|false|no|off)
    ;;
  *)
    fail "integration_is_already_enabled; this_script_is_preconfiguration_only"
    ;;
esac

INGEST_KEY="$(secret_value OA_POLICY_FLOW_INGEST_KEY)"
if test "${#INGEST_KEY}" -lt 32; then
  INGEST_KEY="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
fi

SIGNING_SECRET="$(secret_value OA_POLICY_FLOW_CALLBACK_SIGNING_SECRET)"
if test "${#SIGNING_SECRET}" -lt 32; then
  SIGNING_SECRET="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
fi

test "${#INGEST_KEY}" -ge 32 || fail "ingest_key_generation_failed"
test "${#SIGNING_SECRET}" -ge 32 || fail "signing_secret_generation_failed"

mkdir -p "${BACKUP_DIR}"
chmod 0700 "${BACKUP_DIR}"
kubectl -n "${NAMESPACE}" get secret "${SECRET_NAME}" -o yaml \
  > "${BACKUP_DIR}/${SECRET_NAME}.before.yaml"
chmod 0600 "${BACKUP_DIR}/${SECRET_NAME}.before.yaml"

export INGEST_KEY SIGNING_SECRET OWNER_USER_ID AGENT_NAME PATCH_FILE
python3 - <<'PY'
import json
import os
from pathlib import Path

patch = {
    "stringData": {
        "OA_POLICY_FLOW_ENABLED": "false",
        "OA_POLICY_FLOW_INGEST_KEY": os.environ["INGEST_KEY"],
        "OA_POLICY_FLOW_OWNER_USER_ID": os.environ["OWNER_USER_ID"],
        "OA_POLICY_FLOW_AGENT_NAME": os.environ["AGENT_NAME"],
        "OA_POLICY_FLOW_CALLBACK_SIGNING_SECRET": os.environ["SIGNING_SECRET"],
        "OA_POLICY_FLOW_CALLBACK_ALLOW_HTTP": "false",
        "OA_POLICY_FLOW_POLL_SECONDS": "2",
        "OA_POLICY_FLOW_RUN_POLL_SECONDS": "5",
        "OA_POLICY_FLOW_LEASE_SECONDS": "90",
        "OA_POLICY_FLOW_MAX_CONCURRENT_JOBS": "2",
        "OA_POLICY_FLOW_MAX_PROCESS_ATTEMPTS": "5",
        "OA_POLICY_FLOW_MAX_CALLBACK_ATTEMPTS": "8",
        "OA_POLICY_FLOW_CALLBACK_TIMEOUT_SECONDS": "30",
        "OA_POLICY_FLOW_RUN_TIMEOUT_SECONDS": "1800",
        "OA_POLICY_FLOW_MAX_DOCUMENT_BYTES": "10485760",
        "OA_POLICY_FLOW_MAX_RESULT_BYTES": "10485760",
    }
}
Path(os.environ["PATCH_FILE"]).write_text(
    json.dumps(patch, ensure_ascii=False, separators=(",", ":")),
    encoding="utf-8",
)
PY
chmod 0600 "${PATCH_FILE}"

kubectl -n "${NAMESPACE}" patch secret "${SECRET_NAME}" \
  --type=merge --patch-file="${PATCH_FILE}" >/dev/null

umask 077
{
  echo '# OA policy-flow V1 confidential handoff material'
  echo '# Transfer only through the approved enterprise secret channel.'
  echo 'OA_POLICY_FLOW_API_BASE=https://cnoocclaw.cnooc.com.cn/api/v1/integrations/oa/policy-flow/jobs'
  printf 'OA_POLICY_FLOW_INGEST_KEY=%s\n' "${INGEST_KEY}"
  printf 'OA_POLICY_FLOW_CALLBACK_SIGNING_SECRET=%s\n' "${SIGNING_SECRET}"
} > "${HANDOFF_TMP}"
install -o cnooc -g cnooc -m 0600 "${HANDOFF_TMP}" "${HANDOFF_FILE}"

for key in \
  OA_POLICY_FLOW_ENABLED \
  OA_POLICY_FLOW_INGEST_KEY \
  OA_POLICY_FLOW_OWNER_USER_ID \
  OA_POLICY_FLOW_AGENT_NAME \
  OA_POLICY_FLOW_CALLBACK_SIGNING_SECRET \
  OA_POLICY_FLOW_CALLBACK_ALLOW_HTTP \
  OA_POLICY_FLOW_MAX_CONCURRENT_JOBS \
  OA_POLICY_FLOW_RUN_TIMEOUT_SECONDS \
  OA_POLICY_FLOW_MAX_DOCUMENT_BYTES \
  OA_POLICY_FLOW_MAX_RESULT_BYTES
do
  value="$(secret_value "${key}")"
  test -n "${value}" || fail "missing_after_patch=${key}"
  echo "${key}=PRESENT length=${#value}"
done

test "$(secret_value OA_POLICY_FLOW_ENABLED)" = "false" || \
  fail "feature_flag_must_remain_false"
test "$(secret_value OA_POLICY_FLOW_OWNER_USER_ID)" = "${OWNER_USER_ID}" || \
  fail "owner_user_id_mismatch"
test "$(secret_value OA_POLICY_FLOW_AGENT_NAME)" = "${AGENT_NAME}" || \
  fail "agent_name_mismatch"

CALLBACK_URL="$(secret_value OA_POLICY_FLOW_CALLBACK_URL)"
if test -n "${CALLBACK_URL}"; then
  echo "oa_callback_url=PRESENT_REQUIRES_REVIEW"
else
  echo "oa_callback_url=AWAITING_OA"
fi

echo "oa_policy_flow_v1_our_parameters=PASS"
echo "feature_enabled=false"
echo "gateway_restarted=no"
echo "secure_handoff_file=${HANDOFF_FILE}"
echo "secret_backup=${BACKUP_DIR}/${SECRET_NAME}.before.yaml"
echo "next_step=obtain_OA_callback_URL_network_CA_and_test_window"
