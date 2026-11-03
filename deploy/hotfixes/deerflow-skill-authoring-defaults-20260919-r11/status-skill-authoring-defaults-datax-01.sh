#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
. "${ROOT_DIR}/IMAGE_LOCKS.env"
NS=deer-flow
MODEL_CONFIG_FILE="${DEERFLOW_PRIMARY_MODEL_CONFIG_FILE:-/home/cnooc/DeepSeek-V4-Flash-stream-thinking-on-off.py}"
TARGET_PPT_UPSTREAM_URL='https://aiapi.cnooc.com.cn/DeepSeek-V4-Flash/FEAT-511-001/v1/chat/completions'
REVISION_ENV='DEERFLOW_UNIFIED_MODEL_CONFIG_REVISION'
REVISION_VALUE='20260919-r11'
die() { echo "deerflow_skill_authoring_defaults_status=FAIL detail=$*" >&2; exit 1; }
phase() { echo "===== STATUS PHASE: $* ====="; }
pod_file_check() {
  local label="$1" pod="$2" container="$3" needle="$4" path="$5"
  if kubectl -n "${NS}" exec "${pod}" -c "${container}" -- grep -Fq -- "${needle}" "${path}"; then
    echo "${label}=PASS"
    return 0
  fi
  kubectl -n "${NS}" exec "${pod}" -c "${container}" -- ls -l -- "${path}" >&2 || true
  die "${label}=FAIL pod=${pod} path=${path}"
}
nginx_fetch() {
  local label="$1" url="$2" destination="$3" attempt
  for attempt in $(seq 1 24); do
    if kubectl -n "${NS}" exec "${NGINX_POD}" -c nginx -- \
      wget -qO "${destination}" -T 10 "${url}"; then
      echo "${label}=PASS attempt=${attempt}"
      return 0
    fi
    sleep 5
  done
  die "${label}=FAIL url=${url} attempts=24"
}
nginx_expect_http_status() {
  local label="$1" url="$2" destination="$3" allowed="$4" attempt output status rc
  for attempt in $(seq 1 24); do
    set +e
    output="$(kubectl -n "${NS}" exec "${NGINX_POD}" -c nginx -- \
      wget -S -O "${destination}" -T 10 "${url}" 2>&1)"
    rc=$?
    set -e
    status="$(printf '%s\n' "${output}" | awk '
      {
        for (field_no = 1; field_no <= NF; field_no++) {
          if ($field_no ~ /^HTTP\/[0-9.]+$/ && $(field_no + 1) ~ /^[0-9][0-9][0-9]$/) {
            value = $(field_no + 1)
          }
        }
      }
      END { print value }
    ')"
    case ",${allowed}," in
      *",${status},"*)
        echo "${label}=PASS attempt=${attempt} http_status=${status} wget_exit_code=${rc}"
        return 0
        ;;
    esac
    printf '%s\n' "${output}" >&2
    sleep 5
  done
  die "${label}=FAIL url=${url} expected_http_status=${allowed} observed_http_status=${status:-NONE} attempts=24"
}
test "$(id -u)" = 0 || die 'must_run_as_root'
test "$(hostname -s)" = datax-01 || die 'must_run_on_datax-01'
test -s "${MODEL_CONFIG_FILE}" || die "model_config_file_missing=${MODEL_CONFIG_FILE}"
phase 'DEPLOYMENT AVAILABILITY'
for deployment in gateway frontend nginx weknora-app weknora-frontend ppt-master-runner; do
  kubectl -n "${NS}" rollout status "deployment/${deployment}" --timeout=300s
done
gateway_image="$(kubectl -n "${NS}" get deployment gateway -o jsonpath='{.spec.template.spec.containers[?(@.name=="gateway")].image}')"
frontend_image="$(kubectl -n "${NS}" get deployment frontend -o jsonpath='{.spec.template.spec.containers[?(@.name=="frontend")].image}')"
weknora_frontend_image="$(kubectl -n "${NS}" get deployment weknora-frontend -o jsonpath='{.spec.template.spec.containers[?(@.name=="frontend")].image}')"
test "${gateway_image}" = "${TARGET_GATEWAY_IMAGE}" || die "unexpected_gateway_image=${gateway_image}"
test "${frontend_image}" = "${TARGET_FRONTEND_IMAGE}" || die "unexpected_frontend_image=${frontend_image}"
test "${weknora_frontend_image}" = "${TARGET_WEKNORA_FRONTEND_IMAGE}" || die "unexpected_weknora_frontend_image=${weknora_frontend_image}"
gateway_ready_count="$(kubectl -n "${NS}" get pods -l app=gateway -o json | python3 -c '
import json,sys
count=0
for pod in json.load(sys.stdin).get("items",[]):
    if (pod.get("metadata") or {}).get("deletionTimestamp"): continue
    states={x.get("name"):x for x in (pod.get("status") or {}).get("containerStatuses") or []}
    if (pod.get("status") or {}).get("phase")=="Running" and (states.get("gateway") or {}).get("ready"): count+=1
print(count)
')"
test "${gateway_ready_count}" = 2 || die "gateway_ready_replicas=${gateway_ready_count} expected=2"
gateway_endpoint_count="$(kubectl -n "${NS}" get endpointslice -l kubernetes.io/service-name=gateway -o json | python3 -c '
import json,sys
count=0
for item in json.load(sys.stdin).get("items",[]):
    for endpoint in item.get("endpoints") or []:
        if (endpoint.get("conditions") or {}).get("ready") is not False: count+=len(endpoint.get("addresses") or [])
print(count)
')"
test "${gateway_endpoint_count}" = 2 || die "gateway_ready_endpoints=${gateway_endpoint_count} expected=2"
echo 'expanded_gateway_status=PASS replicas=2 endpoints=2'

running_pod() {
  kubectl -n "${NS}" get pods -l "$1" -o json | python3 -c '
import json,sys
container=sys.argv[1]
for pod in json.load(sys.stdin).get("items",[]):
    if (pod.get("metadata") or {}).get("deletionTimestamp"): continue
    states={x.get("name"):x for x in (pod.get("status") or {}).get("containerStatuses") or []}
    if (pod.get("status") or {}).get("phase")=="Running" and (states.get(container) or {}).get("ready"):
        print((pod.get("metadata") or {}).get("name","")); break
' "$2"
}
GATEWAY_POD="$(running_pod app=gateway gateway)"
FRONTEND_POD="$(running_pod app=frontend frontend)"
NGINX_POD="$(running_pod app=nginx nginx)"
WEKNORA_POD="$(running_pod app=weknora-app app)"
WEKNORA_FRONTEND_POD="$(running_pod app=weknora-frontend frontend)"
PPT_RUNNER_POD="$(running_pod app=ppt-master-runner ppt-master-runner)"
test -n "${GATEWAY_POD}" && test -n "${FRONTEND_POD}" && test -n "${NGINX_POD}" && test -n "${WEKNORA_POD}" && test -n "${WEKNORA_FRONTEND_POD}" && test -n "${PPT_RUNNER_POD}" || die 'running_pod_missing'
phase 'PPT RUNNER EXECUTION LEASE'
kubectl -n "${NS}" exec "${PPT_RUNNER_POD}" -c ppt-master-runner -- \
  sh -ec 'cd /app/backend && PYTHONPATH=packages/harness:. .venv/bin/python -c "$1"' sh \
  'import os,tempfile; from deerflow.community.aio_sandbox.aio_sandbox_provider import _open_lock_file; directory=tempfile.TemporaryDirectory(); handle=_open_lock_file(os.path.join(directory.name,"lease")); mode=handle.mode; handle.close(); directory.cleanup(); assert mode=="a+",mode; print("ppt_runner_execution_lease_fd_preserved=PASS mode=a+")'
if kubectl -n "${NS}" logs "${PPT_RUNNER_POD}" -c ppt-master-runner --since=30m 2>&1 | grep -Fq 'Bad file descriptor'; then
  die 'recent_ppt_runner_bad_file_descriptor_detected'
fi
echo 'recent_ppt_runner_bad_file_descriptor=NONE'

phase 'MODEL AND GATEWAY RUNTIME'
kubectl -n "${NS}" get secret deer-flow-env -o json | \
  python3 "${ROOT_DIR}/shared_model_secret.py" --verify --model-config "${MODEL_CONFIG_FILE}"
kubectl -n "${NS}" exec -i "${GATEWAY_POD}" -c gateway -- sh -ec 'cd /app/backend && PYTHONPATH=packages/harness:. .venv/bin/python -' <<'PY'
import os
from app.gateway.codex_responses_adapter import _DEFAULT_UPSTREAM_URL
from app.gateway.external_agent_analytics import EXTERNAL_PLATFORM_AGENT_NAMES
from app.gateway.weknora_identity import _DEFAULT_CNOOC_DEEPSEEK_BASE_URL
from deerflow.config import get_app_config
from deerflow.tools.skill_manage_tool import SKILL_AUTHORING_TOOLS

names=[tool.name for tool in SKILL_AUTHORING_TOOLS]
assert names == ["skill_create","skill_edit","skill_patch","skill_delete","skill_write_file","skill_remove_file"], names
model=next(item for item in get_app_config().models if item.name == "deepseek-v4-flash")
dump=model.model_dump()
assert dump["model"] == "DeepSeek-V4-Flash"
assert dump["base_url"] == "https://aiapi.cnooc.com.cn/DeepSeek-V4-Flash/FEAT-511-001/v1"
assert dump["temperature"] == 0.0
assert dump["when_thinking_enabled"]["extra_body"]["enable_thinking"] is True
assert dump["when_thinking_disabled"]["extra_body"]["enable_thinking"] is False
assert _DEFAULT_CNOOC_DEEPSEEK_BASE_URL == dump["base_url"]
assert _DEFAULT_UPSTREAM_URL == dump["base_url"] + "/chat/completions"
assert os.environ["PPT_MASTER_DEEPSEEK_UPSTREAM_URL"] == _DEFAULT_UPSTREAM_URL
assert "邮件智能整理助手" in EXTERNAL_PLATFORM_AGENT_NAMES
print("skill_authoring_model_and_mail_agent_runtime=PASS")
PY
cat "${ROOT_DIR}/live_model_runtime_probe.py" | kubectl -n "${NS}" exec -i "${GATEWAY_POD}" -c gateway -- sh -ec 'cd /app/backend && PYTHONPATH=packages/harness:. .venv/bin/python -'

phase 'FRONTEND RUNTIME CONTENT'
pod_file_check frontend_default_flash "${FRONTEND_POD}" frontend 'mode: "flash"' /app/frontend/src/core/settings/local.ts
pod_file_check frontend_mail_agent_name "${FRONTEND_POD}" frontend '邮件智能整理助手' /app/frontend/src/core/agents/shared-agents.ts
pod_file_check frontend_mail_agent_url "${FRONTEND_POD}" frontend 'https://cnoocclaw.cnooc.com.cn/yjzs/' /app/frontend/src/core/agents/shared-agents.ts
pod_file_check frontend_mail_agent_key "${FRONTEND_POD}" frontend '"yjzs"' /app/frontend/src/core/agents/external-launch.ts

phase 'WEKNORA FRONTEND RUNTIME CONTENT'
pod_file_check weknora_index_script "${WEKNORA_FRONTEND_POD}" frontend '/assets/deerflow-sidebar-labels-20260918-r1.js' /usr/share/nginx/html/index.html
pod_file_check weknora_nav_new_chat "${WEKNORA_FRONTEND_POD}" frontend 'nav-creatChat' /usr/share/nginx/html/assets/deerflow-sidebar-labels-20260918-r1.js
pod_file_check weknora_nav_agents "${WEKNORA_FRONTEND_POD}" frontend 'nav-agents' /usr/share/nginx/html/assets/deerflow-sidebar-labels-20260918-r1.js
pod_file_check weknora_label_knowledge_qa "${WEKNORA_FRONTEND_POD}" frontend '\u77e5\u8bc6\u95ee\u7b54' /usr/share/nginx/html/assets/deerflow-sidebar-labels-20260918-r1.js
pod_file_check weknora_label_custom_qa "${WEKNORA_FRONTEND_POD}" frontend '\u81ea\u5b9a\u4e49\u95ee\u7b54' /usr/share/nginx/html/assets/deerflow-sidebar-labels-20260918-r1.js

phase 'WEKNORA PROXY AND BACKEND RUNTIME'
nginx_fetch weknora_frontend_proxy http://127.0.0.1:2026/platform/knowledge-bases /tmp/r11-weknora-index.html
nginx_fetch weknora_sidebar_asset_proxy http://127.0.0.1:2026/assets/deerflow-sidebar-labels-20260918-r1.js /tmp/r11-weknora-labels.js
# This WeKnora route is authentication-protected in production. HTTP 401 is the
# expected anonymous response and proves that Service routing and the app HTTP
# stack are live. Some compatible builds expose it anonymously and return 200.
nginx_expect_http_status weknora_backend_runtime http://weknora-app:8080/api/v1/system/info /tmp/r11-weknora-system-info.json 200,401
kubectl -n "${NS}" exec "${NGINX_POD}" -c nginx -- grep -Fq '/assets/deerflow-sidebar-labels-20260918-r1.js' /tmp/r11-weknora-index.html || die 'weknora_http_index_script=FAIL'
kubectl -n "${NS}" exec "${NGINX_POD}" -c nginx -- grep -Fq 'nav-creatChat' /tmp/r11-weknora-labels.js || die 'weknora_http_nav_new_chat=FAIL'
kubectl -n "${NS}" exec "${NGINX_POD}" -c nginx -- grep -Fq '\u77e5\u8bc6\u95ee\u7b54' /tmp/r11-weknora-labels.js || die 'weknora_http_label_knowledge_qa=FAIL'
kubectl -n "${NS}" exec "${NGINX_POD}" -c nginx -- grep -Fq '\u81ea\u5b9a\u4e49\u95ee\u7b54' /tmp/r11-weknora-labels.js || die 'weknora_http_label_custom_qa=FAIL'
echo 'weknora_sidebar_asset_runtime=PASS'
phase 'PPT RUNNER CONFIG REVISION'
for deployment_container in 'ppt-master-runner ppt-master-runner'; do
  set -- ${deployment_container}
  value="$(kubectl -n "${NS}" get deployment "$1" -o json | python3 -c '
import json,sys
d=json.load(sys.stdin); container=sys.argv[1]; marker=sys.argv[2]
c=next(x for x in d["spec"]["template"]["spec"]["containers"] if x["name"]==container)
rows=[x.get("value","") for x in c.get("env",[]) if x.get("name")==marker]
assert len(rows)==1, rows
print(rows[0])
' "$2" "${REVISION_ENV}")"
  test "${value}" = "${REVISION_VALUE}" || die "model_revision_marker_missing_deployment=$1"
done
echo 'weknora_app_restart=SKIPPED_DATABASE_CONFIGURATION_IS_LIVE'
phase 'PERSISTENT CONTENT STATUS'
"${ROOT_DIR}/manage-skill-creator-datax-01.sh" --status
"${ROOT_DIR}/manage-skillhub-descriptions-datax-01.sh" --status
"${ROOT_DIR}/manage-weknora-model-datax-01.sh" --status
kubectl -n "${NS}" exec "${NGINX_POD}" -c nginx -- wget -qO- -T 10 http://gateway:8001/health
echo
kubectl -n "${NS}" exec "${NGINX_POD}" -c nginx -- wget -qO- -T 10 http://127.0.0.1:2026/api/v1/auth/setup-status
echo
echo 'deerflow_skill_authoring_defaults_status=PASS'
echo 'skill_authoring_required_argument_schema=ENABLED'
echo 'default_chat_mode=FLASH_FOR_UNSET_PREFERENCES'
echo 'skillhub_builtin_descriptions=CHINESE_17'
echo 'deerflow_primary_model=DeepSeek-V4-Flash_NEW_ENDPOINT_LIVE'
echo 'weknora_primary_model=DeepSeek-V4-Flash_NEW_ENDPOINT_CONFIGURED'
echo 'ppt_master_relay_model=DeepSeek-V4-Flash_NEW_ENDPOINT_CONFIGURED'
echo 'mail_digital_employee=ENABLED_DIRECT_LINK'
echo 'weknora_sidebar_labels=知识问答,自定义问答'
