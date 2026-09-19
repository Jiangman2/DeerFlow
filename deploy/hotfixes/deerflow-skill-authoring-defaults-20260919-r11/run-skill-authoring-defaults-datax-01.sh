#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
. "${ROOT_DIR}/IMAGE_LOCKS.env"
MODE="${1:-}"
NS=deer-flow
MODEL_CONFIG_FILE="${DEERFLOW_PRIMARY_MODEL_CONFIG_FILE:-/home/cnooc/DeepSeek-V4-Flash-stream-thinking-on-off.py}"
TARGET_PPT_UPSTREAM_URL='https://aiapi.cnooc.com.cn/DeepSeek-V4-Flash/FEAT-511-001/v1/chat/completions'
REVISION_ENV='DEERFLOW_UNIFIED_MODEL_CONFIG_REVISION'
REVISION_VALUE='20260919-r11'
EVIDENCE_DIR="/opt/deerflow-hotfix-evidence/deerflow-skill-authoring-defaults-20260919-r11/$(date -u +%Y%m%dT%H%M%SZ)"
TEMP_POD=''
TARGET_APPLIED=no
ROLLBACK_ATTEMPTED=no

die() { echo "deerflow_skill_authoring_defaults=FAIL detail=$*" >&2; return 1; }
cleanup() {
  test -z "${TEMP_POD}" || kubectl -n "${NS}" delete pod "${TEMP_POD}" --ignore-not-found=true --wait=false >/dev/null 2>&1 || true
}
automatic_rollback() {
  local reason="$1"
  test "${ROLLBACK_ATTEMPTED}" = no || return 1
  ROLLBACK_ATTEMPTED=yes
  echo "automatic_skill_authoring_defaults_rollback=START reason=${reason}" >&2
  if "${ROOT_DIR}/rollback-skill-authoring-defaults-datax-01.sh" --state "${EVIDENCE_DIR}/state.env"; then
    TARGET_APPLIED=no
    echo 'automatic_skill_authoring_defaults_rollback=PASS' >&2
    return 0
  fi
  echo "automatic_skill_authoring_defaults_rollback=FAIL state=${EVIDENCE_DIR}/state.env" >&2
  return 1
}
on_error() {
  local exit_code="$?" line="${1:-unknown}"
  trap - ERR
  cleanup
  echo "deerflow_skill_authoring_defaults_failed_line=${line} exit_code=${exit_code}" >&2
  test "${TARGET_APPLIED}" != yes || automatic_rollback "line=${line} exit_code=${exit_code}" || true
  exit "${exit_code}"
}
on_signal() {
  local name="$1" code="$2"
  trap - ERR INT TERM
  cleanup
  echo "deerflow_skill_authoring_defaults_signal=${name} exit_code=${code}" >&2
  test "${TARGET_APPLIED}" != yes || automatic_rollback "signal=${name}" || true
  exit "${code}"
}
trap 'on_error "$LINENO"' ERR
trap cleanup EXIT
trap 'on_signal INT 130' INT
trap 'on_signal TERM 143' TERM

case "${MODE}" in --preflight-only|--rollout) ;; *) echo "usage: $0 --preflight-only|--rollout" >&2; exit 2 ;; esac
test "$(id -u)" = '0' || die 'must_run_as_root'
test "$(hostname -s)" = 'datax-01' || die 'must_run_on_datax-01'
test "$(uname -m)" = 'aarch64' || die 'requires_aarch64'
for command in kubectl python3 sha256sum curl; do command -v "${command}" >/dev/null || die "${command}_not_found"; done
test -s "${MODEL_CONFIG_FILE}" || die "model_config_file_missing=${MODEL_CONFIG_FILE}"
(
  cd "${ROOT_DIR}"
  sha256sum -c PACKAGE.SHA256
)
python3 "${ROOT_DIR}/verify-skill-authoring-defaults-hotfix.py" --root "${ROOT_DIR}"
python3 "${ROOT_DIR}/extract_model_config.py" --input "${MODEL_CONFIG_FILE}" --metadata
for deployment in gateway frontend nginx weknora-app weknora-frontend ppt-master-runner; do
  kubectl -n "${NS}" rollout status "deployment/${deployment}" --timeout=300s
done

current_gateway="$(kubectl -n "${NS}" get deployment gateway -o jsonpath='{.spec.template.spec.containers[?(@.name=="gateway")].image}')"
current_frontend="$(kubectl -n "${NS}" get deployment frontend -o jsonpath='{.spec.template.spec.containers[?(@.name=="frontend")].image}')"
current_weknora_frontend="$(kubectl -n "${NS}" get deployment weknora-frontend -o jsonpath='{.spec.template.spec.containers[?(@.name=="frontend")].image}')"
current_init="$(kubectl -n "${NS}" get deployment gateway -o jsonpath='{.spec.template.spec.initContainers[?(@.name=="seed-extensions-config")].image}')"
test "${current_gateway}" = "${BASE_GATEWAY_IMAGE}" || die "unexpected_gateway_image=${current_gateway} expected=${BASE_GATEWAY_IMAGE}"
test "${current_frontend}" = "${BASE_FRONTEND_IMAGE}" || die "unexpected_frontend_image=${current_frontend} expected=${BASE_FRONTEND_IMAGE}"
test "${current_weknora_frontend}" = "${BASE_WEKNORA_FRONTEND_IMAGE}" || die "unexpected_weknora_frontend_image=${current_weknora_frontend} expected=${BASE_WEKNORA_FRONTEND_IMAGE}"
test -n "${current_init}" || die 'gateway_init_image_missing'
kubectl -n "${NS}" get deployment gateway ppt-master-runner provisioner -o json | python3 -c '
import json,sys
items={x["metadata"]["name"]:x for x in json.load(sys.stdin)["items"]}
gateway=items["gateway"]; runner=items["ppt-master-runner"]; provisioner=items["provisioner"]
assert gateway["spec"].get("replicas")==2, gateway["spec"].get("replicas")
strategy=gateway["spec"].get("strategy") or {}
rolling=strategy.get("rollingUpdate") or {}
assert strategy.get("type")=="RollingUpdate", strategy
assert str(rolling.get("maxUnavailable"))=="0", rolling
assert str(rolling.get("maxSurge")) in ("1","25%"), rolling
pod_spec=gateway["spec"]["template"]["spec"]
assert (pod_spec.get("nodeSelector") or {}).get("workload")=="gateway", pod_spec.get("nodeSelector")
assert any(x.get("key")=="dedicated" and x.get("value")=="gateway" and x.get("effect")=="NoSchedule" for x in pod_spec.get("tolerations") or []), pod_spec.get("tolerations")
def claim_for_mount(deployment, path):
    spec=deployment["spec"]["template"]["spec"]
    volumes={x["name"]:x for x in spec.get("volumes") or []}
    names=[]
    for container in (spec.get("initContainers") or []) + (spec.get("containers") or []):
        for mount in container.get("volumeMounts") or []:
            if mount.get("mountPath")==path: names.append(mount["name"])
    assert names and len(set(names))==1, (deployment["metadata"]["name"],path,names)
    claim=(volumes[names[0]].get("persistentVolumeClaim") or {}).get("claimName")
    assert claim, volumes[names[0]]
    return claim
gateway_home=claim_for_mount(gateway,"/app/backend/.deer-flow")
gateway_skills=claim_for_mount(gateway,"/app/skills")
runner_home=claim_for_mount(runner,"/app/backend/.deer-flow")
runner_skills=claim_for_mount(runner,"/app/skills")
c=next(x for x in provisioner["spec"]["template"]["spec"]["containers"] if x["name"]=="provisioner")
env={x["name"]:x.get("value","") for x in c.get("env") or []}
assert gateway_home==runner_home==env.get("USERDATA_PVC_NAME"), (gateway_home,runner_home,env.get("USERDATA_PVC_NAME"))
assert gateway_skills==runner_skills==env.get("SKILLS_PVC_NAME"), (gateway_skills,runner_skills,env.get("SKILLS_PVC_NAME"))
assert gateway_home.endswith("-rwx-pvc") and gateway_skills.endswith("-rwx-pvc"), (gateway_home,gateway_skills)
print("expanded_gateway_topology=PASS replicas=2 maxUnavailable=0")
print("shared_rwx_storage_contract=PASS userdata=%s skills=%s" % (gateway_home,gateway_skills))
'
kubectl -n "${NS}" exec deployment/ppt-master-runner -c ppt-master-runner -- \
  sh -ec 'cd /app/backend && PYTHONPATH=packages/harness:. .venv/bin/python -c "$1"' sh \
  'import os,tempfile; from deerflow.community.aio_sandbox.aio_sandbox_provider import _open_lock_file; directory=tempfile.TemporaryDirectory(); handle=_open_lock_file(os.path.join(directory.name,"lease")); mode=handle.mode; handle.close(); directory.cleanup(); assert mode=="a+",mode; print("ppt_runner_execution_lease_fd_preserved=PASS mode=a+")'
current_ppt_url="$(kubectl -n "${NS}" get deployment gateway -o json | python3 -c '
import json,sys
d=json.load(sys.stdin)
c=next(x for x in d["spec"]["template"]["spec"]["containers"] if x["name"]=="gateway")
rows=[x.get("value","") for x in c.get("env",[]) if x.get("name")=="PPT_MASTER_DEEPSEEK_UPSTREAM_URL"]
assert len(rows)==1 and rows[0], rows
print(rows[0])
')"
for deployment_container in 'ppt-master-runner ppt-master-runner'; do
  set -- ${deployment_container}
  kubectl -n "${NS}" get deployment "$1" -o json | python3 -c '
import json,sys
d=json.load(sys.stdin); container=sys.argv[1]; marker=sys.argv[2]
c=next(x for x in d["spec"]["template"]["spec"]["containers"] if x["name"]==container)
assert not any(x.get("name")==marker for x in c.get("env",[])), marker+" already exists"
' "$2" "${REVISION_ENV}"
done
kubectl -n "${NS}" get secret deer-flow-env -o json | python3 "${ROOT_DIR}/shared_model_secret.py" --inspect
kubectl -n "${NS}" get configmap deer-flow-config -o jsonpath='{.data.config\.yaml}' | \
  python3 "${ROOT_DIR}/patch_deerflow_model_config.py" --input /dev/stdin --check

running_pod() {
  kubectl -n "${NS}" get pods -l "$1" -o json | python3 -c '
import json, sys
container=sys.argv[1]
for pod in json.load(sys.stdin).get("items", []):
    if (pod.get("metadata") or {}).get("deletionTimestamp"): continue
    states={row.get("name"): row for row in (pod.get("status") or {}).get("containerStatuses") or []}
    if (pod.get("status") or {}).get("phase") == "Running" and (states.get(container) or {}).get("ready"):
        print((pod.get("metadata") or {}).get("name", "")); break
' "$2"
}
GATEWAY_POD="$(running_pod app=gateway gateway)"
PG_POD="$(running_pod app=postgres postgres)"
test -n "${GATEWAY_POD}" || die 'running_gateway_pod_not_found'
test -n "${PG_POD}" || die 'running_postgres_pod_not_found'
python3 "${ROOT_DIR}/extract_model_config.py" --input "${MODEL_CONFIG_FILE}" --runtime-json | \
  kubectl -n "${NS}" exec -i "${GATEWAY_POD}" -c gateway -- \
  sh -ec 'cd /app/backend && PYTHONPATH=packages/harness:. .venv/bin/python -c "$1"' sh \
  "$(cat "${ROOT_DIR}/model_runtime_probe_body.py")"

wait_for_probe() {
  local pod="$1" deadline="$(( $(date +%s) + 240 ))" phase reasons
  while :; do
    phase="$(kubectl -n "${NS}" get pod "${pod}" -o jsonpath='{.status.phase}' 2>/dev/null || true)"
    reasons="$(kubectl -n "${NS}" get pod "${pod}" -o jsonpath='{range .status.containerStatuses[*]}{.state.waiting.reason}{" "}{end}' 2>/dev/null || true)"
    test "${phase}" != Succeeded || return 0
    case "${phase}:${reasons}" in Failed:*|*:ErrImageNeverPull*|*:ImagePullBackOff*|*:CreateContainerConfigError*|*:CreateContainerError*|*:CrashLoopBackOff*) kubectl -n "${NS}" describe pod "${pod}" >&2 || true; return 1 ;; esac
    test "$(date +%s)" -lt "${deadline}" || { kubectl -n "${NS}" describe pod "${pod}" >&2 || true; return 1; }
    sleep 2
  done
}
eligible_nodes() {
  local workload="$1"
  kubectl get nodes -o json | python3 -c '
import json,sys
mode=sys.argv[1]
rows=[]
for node in json.load(sys.stdin).get("items",[]):
    spec=node.get("spec") or {}; status=node.get("status") or {}; meta=node.get("metadata") or {}
    if spec.get("unschedulable"): continue
    ready=any(x.get("type")=="Ready" and x.get("status")=="True" for x in status.get("conditions") or [])
    if not ready: continue
    labels=meta.get("labels") or {}; taints=spec.get("taints") or []
    if mode=="gateway":
        if labels.get("workload")!="gateway": continue
    elif any(x.get("effect") in ("NoSchedule","NoExecute") for x in taints):
        continue
    rows.append(meta.get("name",""))
assert rows, "no eligible nodes for "+mode
print("\n".join(sorted(rows)))
' "${workload}"
}
probe_target_image() {
  local workload="$1" node="$2" image="$3"
  TEMP_POD="skill-authoring-${workload}-${node}"
  kubectl -n "${NS}" delete pod "${TEMP_POD}" --ignore-not-found=true --wait=true --timeout=120s >/dev/null
  case "${workload}" in
    gateway)
  cat <<YAML | kubectl -n "${NS}" apply -f -
apiVersion: v1
kind: Pod
metadata:
  name: ${TEMP_POD}
spec:
  nodeName: ${node}
  restartPolicy: Never
  automountServiceAccountToken: false
  tolerations:
    - operator: Exists
  containers:
    - name: gateway
      image: ${image}
      imagePullPolicy: Never
      command: ["sh", "-ec"]
      args:
        - >-
          cd /app/backend && PYTHONPATH=packages/harness:. .venv/bin/python -c
          'from deerflow.tools.skill_manage_tool import SKILL_AUTHORING_TOOLS;
          from deerflow.community.media_generation import get_configured_media_generation_tools;
          from app.gateway.codex_responses_adapter import _DEFAULT_UPSTREAM_URL;
          from app.gateway.external_agent_analytics import EXTERNAL_PLATFORM_AGENT_NAMES;
          from app.gateway.weknora_identity import _DEFAULT_CNOOC_DEEPSEEK_BASE_URL;
          assert [t.name for t in SKILL_AUTHORING_TOOLS]==["skill_create","skill_edit","skill_patch","skill_delete","skill_write_file","skill_remove_file"];
          assert _DEFAULT_UPSTREAM_URL=="${TARGET_PPT_UPSTREAM_URL}";
          assert _DEFAULT_CNOOC_DEEPSEEK_BASE_URL=="https://aiapi.cnooc.com.cn/DeepSeek-V4-Flash/FEAT-511-001/v1";
          assert "邮件智能整理助手" in EXTERNAL_PLATFORM_AGENT_NAMES;
          print("gateway_skill_authoring_model_mail_agent_and_media_base=PASS")'
YAML
      ;;
    frontend)
  cat <<YAML | kubectl -n "${NS}" apply -f -
apiVersion: v1
kind: Pod
metadata:
  name: ${TEMP_POD}
spec:
  nodeName: ${node}
  restartPolicy: Never
  automountServiceAccountToken: false
  tolerations:
    - operator: Exists
  containers:
    - name: frontend
      image: ${image}
      imagePullPolicy: Never
      command: ["sh", "-ec"]
      args:
        - >-
          grep -q 'mode: "flash"' /app/frontend/src/core/settings/local.ts &&
          test "\$(grep -R -l 'return "flash";' /app/frontend/src/components/workspace/mode-selector.tsx /app/frontend/src/components/workspace/sidecar/sidecar-panel.tsx | wc -l)" = 2 &&
          grep -q '邮件智能整理助手' /app/frontend/src/core/agents/shared-agents.ts &&
          grep -q 'https://cnoocclaw.cnooc.com.cn/yjzs/' /app/frontend/src/core/agents/shared-agents.ts &&
          grep -q '"yjzs"' /app/frontend/src/core/agents/external-launch.ts
YAML
      ;;
    weknora)
  cat <<YAML | kubectl -n "${NS}" apply -f -
apiVersion: v1
kind: Pod
metadata:
  name: ${TEMP_POD}
spec:
  nodeName: ${node}
  restartPolicy: Never
  automountServiceAccountToken: false
  tolerations:
    - operator: Exists
  containers:
    - name: weknora-frontend
      image: ${image}
      imagePullPolicy: Never
      command: ["sh", "-ec"]
      args:
        - >-
          grep -Fq '/assets/deerflow-sidebar-labels-20260918-r1.js' /usr/share/nginx/html/index.html &&
          grep -Fq 'nav-creatChat' /usr/share/nginx/html/assets/deerflow-sidebar-labels-20260918-r1.js &&
          grep -Fq 'nav-agents' /usr/share/nginx/html/assets/deerflow-sidebar-labels-20260918-r1.js &&
          grep -Fq '\\u77e5\\u8bc6\\u95ee\\u7b54' /usr/share/nginx/html/assets/deerflow-sidebar-labels-20260918-r1.js &&
          grep -Fq '\\u81ea\\u5b9a\\u4e49\\u95ee\\u7b54' /usr/share/nginx/html/assets/deerflow-sidebar-labels-20260918-r1.js
YAML
      ;;
    *) die "unknown_probe_workload=${workload}" ;;
  esac
  wait_for_probe "${TEMP_POD}"
  kubectl -n "${NS}" logs "${TEMP_POD}" --all-containers=true || true
  kubectl -n "${NS}" delete pod "${TEMP_POD}" --wait=true --timeout=120s
  TEMP_POD=''
  echo "skill_authoring_defaults_target_image=${workload} node=${node}:PASS"
}
echo '===== SCHEDULER-ELIGIBLE IMMUTABLE TARGET IMAGE PROBES ====='
GATEWAY_IMAGE_NODES="$(eligible_nodes gateway)"
GENERAL_IMAGE_NODES="$(eligible_nodes general)"
echo "gateway_target_image_nodes=$(tr '\n' ',' <<<"${GATEWAY_IMAGE_NODES}" | sed 's/,$//')"
echo "general_target_image_nodes=$(tr '\n' ',' <<<"${GENERAL_IMAGE_NODES}" | sed 's/,$//')"
while IFS= read -r node; do
  probe_target_image gateway "${node}" "${TARGET_GATEWAY_IMAGE}"
done <<<"${GATEWAY_IMAGE_NODES}"
while IFS= read -r node; do
  probe_target_image frontend "${node}" "${TARGET_FRONTEND_IMAGE}"
  probe_target_image weknora "${node}" "${TARGET_WEKNORA_FRONTEND_IMAGE}"
done <<<"${GENERAL_IMAGE_NODES}"
if test "$(wc -l <<<"${GATEWAY_IMAGE_NODES}" | tr -d ' ')" -gt 1; then
  echo 'gateway_multi_instance_residual_risk=DEFERRED_NON_BLOCKING_ZERO_ACTIVITY_ROLLOUT'
else
  die 'gateway_multi_instance_expected_for_expanded_production'
fi

"${ROOT_DIR}/manage-skill-creator-datax-01.sh" --preflight
"${ROOT_DIR}/manage-skillhub-descriptions-datax-01.sh" --preflight
"${ROOT_DIR}/manage-weknora-model-datax-01.sh" --preflight
db_scalar() {
  kubectl -n "${NS}" exec "${PG_POD}" -c postgres -- sh -ec 'exec psql -X -At -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "$1"' sh "$1"
}
activity_snapshot() {
  ACTIVE_RUNS="$(db_scalar "SELECT count(*) FROM runs WHERE status IN ('pending','running');")"
  ACTIVE_OA_JOBS="$(db_scalar "SELECT count(*) FROM oa_policy_flow_jobs WHERE status IN ('queued','launching','running');")"
  echo "active_deerflow_runs=${ACTIVE_RUNS}"
  echo "active_oa_jobs=${ACTIVE_OA_JOBS}"
}
activity_snapshot
if test "${MODE}" = '--preflight-only'; then
  echo 'deerflow_skill_authoring_defaults_preflight=PASS'
  echo 'production_workloads_mutated=no'
  echo 'production_deerflow_database_mutation=NONE'
  echo 'production_skillhub_database_mutation=NONE'
  echo 'production_weknora_database_mutation=NONE'
  echo 'shared_model_secret_mutated=no'
  echo 'production_weknora_frontend_mutated=no'
  if test "${ACTIVE_RUNS}" = 0 && test "${ACTIVE_OA_JOBS}" = 0; then echo 'maintenance_gate_ready=yes'; else echo 'maintenance_gate_ready=no'; fi
  echo 'next_step=wait_for_zero_active_work_then_rerun_with_DEERFLOW_SKILL_AUTHORING_DEFAULTS_ACK_YES_and_--rollout'
  exit 0
fi

test "${DEERFLOW_SKILL_AUTHORING_DEFAULTS_ACK:-}" = YES || die 'set_DEERFLOW_SKILL_AUTHORING_DEFAULTS_ACK=YES'
test "${ACTIVE_RUNS}" = 0 || die "active_deerflow_runs=${ACTIVE_RUNS}"
test "${ACTIVE_OA_JOBS}" = 0 || die "active_oa_jobs=${ACTIVE_OA_JOBS}"
umask 077
install -d -m 0700 "${EVIDENCE_DIR}"
kubectl -n "${NS}" get deployment gateway -o json > "${EVIDENCE_DIR}/gateway.before.json"
kubectl -n "${NS}" get deployment frontend -o json > "${EVIDENCE_DIR}/frontend.before.json"
kubectl -n "${NS}" get deployment weknora-frontend -o json > "${EVIDENCE_DIR}/weknora-frontend.before.json"
kubectl -n "${NS}" get deployment ppt-master-runner -o json > "${EVIDENCE_DIR}/ppt-master-runner.before.json"
kubectl -n "${NS}" get configmap deer-flow-config -o json > "${EVIDENCE_DIR}/deer-flow-config.before.json"
kubectl -n "${NS}" get configmap deer-flow-config -o jsonpath='{.data.config\.yaml}' > "${EVIDENCE_DIR}/config.before.yaml"
kubectl -n "${NS}" get secret deer-flow-env -o json > "${EVIDENCE_DIR}/deer-flow-env.before.json"
python3 "${ROOT_DIR}/shared_model_secret.py" --inspect < "${EVIDENCE_DIR}/deer-flow-env.before.json"
python3 "${ROOT_DIR}/patch_deerflow_model_config.py" --input "${EVIDENCE_DIR}/config.before.yaml" --output "${EVIDENCE_DIR}/config.target.yaml"
printf 'EVIDENCE_DIR=%q\n' "${EVIDENCE_DIR}" > "${EVIDENCE_DIR}/state.env"
printf 'OLD_GATEWAY_IMAGE=%q\n' "${current_gateway}" >> "${EVIDENCE_DIR}/state.env"
printf 'OLD_FRONTEND_IMAGE=%q\n' "${current_frontend}" >> "${EVIDENCE_DIR}/state.env"
printf 'OLD_WEKNORA_FRONTEND_IMAGE=%q\n' "${current_weknora_frontend}" >> "${EVIDENCE_DIR}/state.env"
printf 'OLD_GATEWAY_INIT_IMAGE=%q\n' "${current_init}" >> "${EVIDENCE_DIR}/state.env"
printf 'OLD_PPT_MASTER_DEEPSEEK_UPSTREAM_URL=%q\n' "${current_ppt_url}" >> "${EVIDENCE_DIR}/state.env"
printf 'PATCH_DIRECTIVE=%q\n' '$patch' >> "${EVIDENCE_DIR}/state.env"
"${ROOT_DIR}/manage-skill-creator-datax-01.sh" --backup "${EVIDENCE_DIR}"
"${ROOT_DIR}/manage-skillhub-descriptions-datax-01.sh" --backup "${EVIDENCE_DIR}"
"${ROOT_DIR}/manage-weknora-model-datax-01.sh" --backup "${EVIDENCE_DIR}"
(
  cd "${EVIDENCE_DIR}"
  sha256sum \
    gateway.before.json frontend.before.json weknora-frontend.before.json ppt-master-runner.before.json \
    deer-flow-config.before.json deer-flow-env.before.json config.before.yaml state.env \
    skill-creator.before.tar skillhub-builtins.before.json skillhub-builtins.rollback.sql \
    weknora-deepseek-model.before.json weknora-deepseek-model.rollback.sql > ROLLBACK.SHA256
  sha256sum -c ROLLBACK.SHA256
)
activity_snapshot
test "${ACTIVE_RUNS}" = 0 || die "active_deerflow_runs_after_backup=${ACTIVE_RUNS}"
test "${ACTIVE_OA_JOBS}" = 0 || die "active_oa_jobs_after_backup=${ACTIVE_OA_JOBS}"
echo 'post_backup_activity_race_recheck=PASS'
TARGET_APPLIED=yes

python3 "${ROOT_DIR}/shared_model_secret.py" --patch-from-model-config "${MODEL_CONFIG_FILE}" | \
  kubectl -n "${NS}" patch secret deer-flow-env --type=merge --patch-file=/dev/stdin
python3 - "${EVIDENCE_DIR}/config.target.yaml" <<'PY' | \
  kubectl -n deer-flow patch configmap deer-flow-config --type=merge --patch-file=/dev/stdin
import json,sys
print(json.dumps({"data":{"config.yaml":open(sys.argv[1],encoding="utf-8").read()}},separators=(",",":")))
PY
"${ROOT_DIR}/manage-weknora-model-datax-01.sh" --install "${EVIDENCE_DIR}"
"${ROOT_DIR}/manage-skillhub-descriptions-datax-01.sh" --install "${EVIDENCE_DIR}"
"${ROOT_DIR}/manage-skill-creator-datax-01.sh" --install "${EVIDENCE_DIR}"

activity_snapshot
test "${ACTIVE_RUNS}" = 0 || die "active_deerflow_runs_before_workload_patch=${ACTIVE_RUNS}"
test "${ACTIVE_OA_JOBS}" = 0 || die "active_oa_jobs_before_workload_patch=${ACTIVE_OA_JOBS}"
echo 'pre_workload_activity_race_recheck=PASS'
cat > "${EVIDENCE_DIR}/gateway.target.patch.json" <<JSON
{"spec":{"template":{"spec":{"containers":[{"name":"gateway","image":"${TARGET_GATEWAY_IMAGE}","env":[{"name":"PPT_MASTER_DEEPSEEK_UPSTREAM_URL","value":"${TARGET_PPT_UPSTREAM_URL}"}]}]}}}}
JSON
cat > "${EVIDENCE_DIR}/frontend.target.patch.json" <<JSON
{"spec":{"template":{"spec":{"containers":[{"name":"frontend","image":"${TARGET_FRONTEND_IMAGE}"}]}}}}
JSON
cat > "${EVIDENCE_DIR}/weknora-frontend.target.patch.json" <<JSON
{"spec":{"template":{"spec":{"containers":[{"name":"frontend","image":"${TARGET_WEKNORA_FRONTEND_IMAGE}"}]}}}}
JSON
cat > "${EVIDENCE_DIR}/ppt-master-runner.target.patch.json" <<JSON
{"spec":{"template":{"spec":{"containers":[{"name":"ppt-master-runner","env":[{"name":"${REVISION_ENV}","value":"${REVISION_VALUE}"}]}]}}}}
JSON
kubectl -n "${NS}" patch deployment gateway --type=strategic --patch-file "${EVIDENCE_DIR}/gateway.target.patch.json"
kubectl -n "${NS}" patch deployment frontend --type=strategic --patch-file "${EVIDENCE_DIR}/frontend.target.patch.json"
kubectl -n "${NS}" patch deployment weknora-frontend --type=strategic --patch-file "${EVIDENCE_DIR}/weknora-frontend.target.patch.json"
kubectl -n "${NS}" patch deployment ppt-master-runner --type=strategic --patch-file "${EVIDENCE_DIR}/ppt-master-runner.target.patch.json"
echo 'weknora_app_restart=SKIPPED_DATABASE_CONFIGURATION_IS_LIVE'
for deployment in gateway frontend weknora-frontend ppt-master-runner; do
  kubectl -n "${NS}" rollout status "deployment/${deployment}" --timeout=900s
done
current_init_after="$(kubectl -n "${NS}" get deployment gateway -o jsonpath='{.spec.template.spec.initContainers[?(@.name=="seed-extensions-config")].image}')"
test "${current_init_after}" = "${current_init}" || die "gateway_init_image_changed=${current_init_after}"
"${ROOT_DIR}/status-skill-authoring-defaults-datax-01.sh"

TARGET_APPLIED=no
echo 'deerflow_skill_authoring_defaults_cutover=PASS'
echo 'skill_authoring_required_argument_schema=ENABLED'
echo 'default_chat_mode=FLASH_FOR_UNSET_PREFERENCES'
echo 'skillhub_builtin_descriptions=CHINESE_17'
echo 'deerflow_primary_model=DeepSeek-V4-Flash_NEW_ENDPOINT'
echo 'weknora_primary_model=DeepSeek-V4-Flash_NEW_ENDPOINT'
echo 'ppt_master_relay_model=DeepSeek-V4-Flash_NEW_ENDPOINT'
echo 'shared_deepseek_api_key=UPDATED_WITHOUT_LOGGING_VALUE'
echo 'mail_digital_employee=ENABLED_DIRECT_LINK'
echo 'weknora_sidebar_new_chat=知识问答'
echo 'weknora_sidebar_agents=自定义问答'
echo "rollback_state=${EVIDENCE_DIR}/state.env"
