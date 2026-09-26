#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
MODE="${1:---check-only}"
case "${MODE}" in --check-only|--build-only) ;; *) echo 'usage: build-frontend-datax01.sh --check-only|--build-only' >&2; exit 2;; esac
test "$(id -u)" -eq 0 && test "$(hostname -s)" = datax-01 && test "$(uname -m)" = aarch64
export KUBECONFIG=/etc/kubernetes/admin.conf
unset SSL_CERT_FILE REQUESTS_CA_BUNDLE CURL_CA_BUNDLE

STAGE=/opt/deerflow-build-artifacts/20260924-ppt-preview-auth-r1
ARTIFACT_DIR=/opt/deerflow-build-artifacts/20260924-ppt-preview-auth-r2
# Reuse the completed offline BuildKit cache; the R1 service must be stopped.
BUILDKIT_ROOT=/opt/deerflow-buildkit/ppt-preview-auth-r1
BUILDKIT_ADDR="unix://${ARTIFACT_DIR}/buildkit.sock"
BUILDKIT_UNIT=ppt-preview-auth-r2-buildkit
FRONTEND_BASE=/opt/deerflow-build-artifacts/20260923-channel-pdf-preview-r2/frontend.candidate.oci.tar
FRONTEND_IMAGE=deerflow.local/deer-flow-frontend:20260923-enterprise-arm64-rc15-channel-pdf-preview-r2
TARGET_FRONTEND=deerflow.local/deer-flow-frontend:20260924-ppt-preview-auth-r2
GATEWAY_IMAGE=deerflow.local/deer-flow-gateway:20260924-ppt-relay-r1-adadc3fb4d787ed1
RUNNER_IMAGE=deerflow.local/deer-flow-gateway:20260923-enterprise-arm64-rc15-ppt-master-v660-r15

die() { echo "preview_r2_build=FAIL detail=$*" >&2; exit 1; }
for tool in python3 kubectl buildkitd buildctl systemd-run systemctl sha256sum flock findmnt; do
  command -v "${tool}" >/dev/null || die "missing_tool=${tool}"
done
(cd "${ROOT_DIR}" && sha256sum -c PACKAGE.SHA256) || die package_checksum
python3 -B "${ROOT_DIR}/verify_package.py" "${ROOT_DIR}" || die package_validation
test "$(findmnt -n -o SOURCE -T /)" != "$(findmnt -n -o SOURCE -T /opt)" || die opt_not_separate
test "$(df -Pk / | awk 'NR==2 {print $4}')" -ge $((30 * 1024 * 1024)) || die root_free_below_30GiB
test "$(df -Pk /opt | awk 'NR==2 {print $4}')" -ge $((80 * 1024 * 1024)) || die opt_free_below_80GiB
test "$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)" -ge $((16 * 1024 * 1024)) || die memory_below_16GiB
if ps -eo args | grep -Eq '[c]tr .*images import|[b]uildctl .*build|[n]erdctl .*build'; then die active_image_operation; fi
test -d "${BUILDKIT_ROOT}" || die r1_buildkit_cache_missing
test "$(systemctl is-active ppt-preview-auth-r1-buildkit.service 2>/dev/null || true)" != active || die r1_buildkit_still_active
test "$(systemctl is-active "${BUILDKIT_UNIT}.service" 2>/dev/null || true)" != active || die r2_buildkit_still_active
if ps -eo args | grep -Eq '[b]uildkitd .*ppt-preview-auth-r1'; then die buildkit_root_still_in_use; fi

kubectl --request-timeout=20s get node datax-01 -o json | python3 -c '
import json,sys
d=json.load(sys.stdin); s={x["type"]:x["status"] for x in d["status"]["conditions"]}
assert s.get("Ready")=="True"
for n in ("DiskPressure","MemoryPressure","PIDPressure"): assert s.get(n)=="False",n
' || die node_pressure
kubectl --request-timeout=20s -n deer-flow get pods -l app=gateway -o json | python3 -c '
import json,sys
pods=json.load(sys.stdin)["items"]
assert pods and not any(p["spec"].get("nodeName")=="datax-01" and not p["metadata"].get("deletionTimestamp") for p in pods)
' || die gateway_colocated_with_build
for component in gateway runner frontend; do
  case "${component}" in gateway) deployment=gateway; expected="${GATEWAY_IMAGE}";; runner) deployment=ppt-master-runner; expected="${RUNNER_IMAGE}";; frontend) deployment=frontend; expected="${FRONTEND_IMAGE}";; esac
  kubectl --request-timeout=20s -n deer-flow get deployment "${deployment}" -o json | python3 -c '
import json,sys
d=json.load(sys.stdin); expected=sys.argv[1]; replicas=d["spec"].get("replicas",1)
assert replicas>0 and d["status"].get("observedGeneration",0)>=d["metadata"]["generation"]
assert d["status"].get("readyReplicas",0)==replicas
assert d["status"].get("updatedReplicas",0)==replicas
assert any(c["image"]==expected for c in d["spec"]["template"]["spec"]["containers"])
' "${expected}" || die "live_baseline_changed=${deployment}"
done

for component in gateway runner; do
  archive="${STAGE}/${component}.candidate.oci.tar"
  test -f "${archive}" && test ! -L "${archive}" && test -s "${archive}" || die "r1_candidate_missing=${component}"
  test "$(stat -c %u "${archive}")" -eq 0 || die "r1_candidate_not_root_owned=${component}"
  (cd "${STAGE}" && sha256sum -c "${component}.candidate.oci.tar.sha256") || die "r1_candidate_checksum=${component}"
  echo "r1_candidate_reuse=PASS component=${component} archive=${archive}"
done
test -f "${FRONTEND_BASE}" && test ! -L "${FRONTEND_BASE}" && test -s "${FRONTEND_BASE}" || die frontend_base_missing
test "$(stat -c %u "${FRONTEND_BASE}")" -eq 0 || die frontend_base_not_root_owned
(cd "$(dirname "${FRONTEND_BASE}")" && sha256sum -c "$(basename "${FRONTEND_BASE}").sha256") || die frontend_base_checksum

LAYOUT="${STAGE}/base-frontend"
test -f "${LAYOUT}/index.json" && test -f "${LAYOUT}/manifest.digest" || die r1_frontend_layout_missing
python3 -B - "${LAYOUT}" "${FRONTEND_IMAGE}" <<'PY'
import hashlib,json,re,sys
from pathlib import Path
root=Path(sys.argv[1]); image=sys.argv[2]
digest=(root/'manifest.digest').read_text().strip()
assert re.fullmatch(r'sha256:[a-f0-9]{64}', digest)
rows=json.loads((root/'index.json').read_bytes())['manifests']
matches=[r for r in rows if image in r.get('annotations',{}).values()]
assert len(matches)==1 and matches[0]['digest']==digest
def blob(d):
    value=d['digest']; assert re.fullmatch(r'sha256:[a-f0-9]{64}',value)
    data=(root/'blobs'/'sha256'/value[7:]).read_bytes()
    assert hashlib.sha256(data).hexdigest()==value[7:]
    return json.loads(data)
manifest=blob(matches[0]); config=blob(manifest['config'])
assert config.get('os')=='linux' and config.get('architecture')=='arm64'
assert manifest['layers'] and all((root/'blobs'/'sha256'/r['digest'][7:]).is_file() for r in manifest['layers'])
print('r1_frontend_layout_reuse=PASS image='+image+' manifest='+digest)
PY

if test "${MODE}" = --check-only; then
  echo 'preview_r2_build_check=PASS production_workloads_mutated=no next_step=--build-only'
  exit 0
fi
test "${PREVIEW_R2_BUILD_ACK:-}" = YES || die build_ack_missing
test ! -e "${ARTIFACT_DIR}" || die r2_artifact_dir_exists
install -d -m 0750 "${ARTIFACT_DIR}"
exec 9>"${ARTIFACT_DIR}/build.lock"
flock -n 9 || die concurrent_build
config="${ARTIFACT_DIR}/buildkitd.toml"
printf '[worker.oci]\n  enabled = true\n  rootless = false\n  snapshotter = "overlayfs"\n  max-parallelism = 1\n[worker.containerd]\n  enabled = false\n' >"${config}"
systemd-run --unit "${BUILDKIT_UNIT}" --collect --service-type=exec \
  --property=CPUQuota=150% --property=MemoryMax=12G --property=TasksMax=2048 \
  --property=IOWeight=10 --property=Nice=10 --property=RuntimeMaxSec=4h \
  -- buildkitd --addr "${BUILDKIT_ADDR}" --root "${BUILDKIT_ROOT}" --config "${config}" >/dev/null
cleanup() { systemctl stop "${BUILDKIT_UNIT}.service" >/dev/null 2>&1 || true; }
trap cleanup EXIT
deadline=$(( $(date +%s) + 120 ))
until test -S "${ARTIFACT_DIR}/buildkit.sock" && buildctl --addr "${BUILDKIT_ADDR}" debug workers >/dev/null 2>&1; do
  systemctl is-active --quiet "${BUILDKIT_UNIT}.service" || die buildkit_exited
  test "$(date +%s)" -lt "${deadline}" || die buildkit_timeout
  sleep 2
done
digest="$(<"${LAYOUT}/manifest.digest")"
for stage in source-check candidate; do
  test "$(df -Pk / | awk 'NR==2 {print $4}')" -ge $((30 * 1024 * 1024)) || die root_reserve_breached
  kubectl --request-timeout=20s -n deer-flow get pods -l app=gateway -o json | python3 -c '
import json,sys
assert not any(p["spec"].get("nodeName")=="datax-01" and not p["metadata"].get("deletionTimestamp") for p in json.load(sys.stdin)["items"])
' || die gateway_rescheduled_to_build_node
  args=(
    --progress=plain --frontend dockerfile.v0
    --local "context=${ROOT_DIR}" --local "dockerfile=${ROOT_DIR}"
    --oci-layout "frontend_base=${LAYOUT}"
    --opt filename=Dockerfile.frontend --opt platform=linux/arm64
    --opt "context:preview-frontend-base=oci-layout://frontend_base@${digest}"
  )
  if test "${stage}" = source-check; then
    args+=(--opt target=source-check)
  else
    args+=(--output "type=oci,name=${TARGET_FRONTEND},dest=${ARTIFACT_DIR}/frontend.candidate.oci.tar.partial")
  fi
  buildctl --addr "${BUILDKIT_ADDR}" build "${args[@]}" || die "frontend_${stage}_failed"
done
output="${ARTIFACT_DIR}/frontend.candidate.oci.tar"
test -s "${output}.partial" || die empty_frontend_candidate
mv -- "${output}.partial" "${output}"
(cd "${ARTIFACT_DIR}" && sha256sum frontend.candidate.oci.tar >frontend.candidate.oci.tar.sha256)
echo "preview_r2_frontend_build=PASS image=${TARGET_FRONTEND} archive=${output}"
echo 'preview_r2_build=PASS gateway_and_runner=R1_CANDIDATES_REUSED production_workloads_mutated=no rollout=NOT_PERFORMED'
