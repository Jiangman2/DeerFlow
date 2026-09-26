#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
MODE="${1:---check-only}"
case "${MODE}" in --check-only|--build-only) ;; *) echo 'usage: build-datax01.sh --check-only|--build-only' >&2; exit 2;; esac
test "$(id -u)" -eq 0 && test "$(hostname -s)" = datax-01 && test "$(uname -m)" = aarch64
export KUBECONFIG=/etc/kubernetes/admin.conf
unset SSL_CERT_FILE REQUESTS_CA_BUNDLE CURL_CA_BUNDLE

ARTIFACT_DIR=/opt/deerflow-build-artifacts/20260924-ppt-preview-auth-r1
BUILDKIT_ROOT=/opt/deerflow-buildkit/ppt-preview-auth-r1
BUILDKIT_ADDR="unix://${ARTIFACT_DIR}/buildkit.sock"
BUILDKIT_UNIT="ppt-preview-auth-r1-buildkit"
BASE_gateway=/opt/deerflow-build-artifacts/ppt-relay-r1-build2/gateway.candidate.oci.tar
BASE_runner=/opt/deerflow-build-artifacts/20260923-ppt-master-v660-r15/deer-flow-gateway-20260923-enterprise-arm64-rc15-ppt-master-v660-r15.oci.tar
BASE_frontend=/opt/deerflow-build-artifacts/20260923-channel-pdf-preview-r2/frontend.candidate.oci.tar
IMAGE_gateway=deerflow.local/deer-flow-gateway:20260924-ppt-relay-r1-adadc3fb4d787ed1
IMAGE_runner=deerflow.local/deer-flow-gateway:20260923-enterprise-arm64-rc15-ppt-master-v660-r15
IMAGE_frontend=deerflow.local/deer-flow-frontend:20260923-enterprise-arm64-rc15-channel-pdf-preview-r2
TARGET_gateway=deerflow.local/deer-flow-gateway:20260924-ppt-preview-auth-r1
TARGET_runner=deerflow.local/deer-flow-gateway:20260924-ppt-preview-auth-r1-runner
TARGET_frontend=deerflow.local/deer-flow-frontend:20260924-ppt-preview-auth-r1

die() { echo "preview_build=FAIL detail=$*" >&2; exit 1; }
for tool in python3 kubectl ctr buildkitd buildctl systemd-run systemctl sha256sum flock findmnt stat; do
  command -v "${tool}" >/dev/null || die "missing_tool=${tool}"
done
(cd "${ROOT_DIR}" && sha256sum -c PACKAGE.SHA256) || die package_checksum
python3 -B "${ROOT_DIR}/verify_package.py" "${ROOT_DIR}" || die package_validation
test "$(findmnt -n -o SOURCE -T /)" != "$(findmnt -n -o SOURCE -T /opt)" || die opt_not_separate
root_free="$(df -Pk / | awk 'NR==2 {print $4}')"
opt_free="$(df -Pk /opt | awk 'NR==2 {print $4}')"
test "${root_free}" -ge $((30 * 1024 * 1024)) || die root_free_below_30GiB
test "${opt_free}" -ge $((80 * 1024 * 1024)) || die opt_free_below_80GiB
mem_free="$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)"
test "${mem_free}" -ge $((16 * 1024 * 1024)) || die memory_below_16GiB
if ps -eo args | grep -Eq '[c]tr .*images import|[b]uildctl .*build|[n]erdctl .*build'; then
  die active_image_operation
fi
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
  if test "${component}" = runner; then deployment=ppt-master-runner; else deployment="${component}"; fi
  expected_var="IMAGE_${component}"; expected="${!expected_var}"
  kubectl --request-timeout=20s -n deer-flow get deployment "${deployment}" -o json | python3 -c '
import json,sys
d=json.load(sys.stdin); expected=sys.argv[1]; replicas=d["spec"].get("replicas",1)
assert replicas>0 and d["status"].get("observedGeneration",0)>=d["metadata"]["generation"]
assert d["status"].get("readyReplicas",0)==replicas
assert d["status"].get("updatedReplicas",0)==replicas
assert any(c["image"]==expected for c in d["spec"]["template"]["spec"]["containers"])
' "${expected}" || die "live_baseline_changed=${deployment}"
  archive_var="BASE_${component}"; archive="${!archive_var}"
  test -f "${archive}" && test ! -L "${archive}" && test -s "${archive}" || die "base_archive_missing=${archive}"
  test -f "${archive}.sha256" && test ! -L "${archive}.sha256" || die "base_checksum_missing=${archive}.sha256"
  test "$(stat -c %u "${archive}")" -eq 0 || die "base_not_root_owned=${archive}"
  test "$(stat -c %a "${archive}")" -le 644 || die "base_permissions_too_open=${archive}"
  (cd "$(dirname "${archive}")" && sha256sum -c "$(basename "${archive}").sha256") || die "base_checksum_failed=${archive}"
  ctr -n k8s.io images inspect "${expected}" >/dev/null || die "base_image_missing=${expected}"
  echo "preview_base=PASS component=${component} image=${expected} archive=${archive}"
done

if test "${MODE}" = --check-only; then
  echo 'preview_build_check=PASS production_workloads_mutated=no next_step=--build-only'
  exit 0
fi
test "${PREVIEW_BUILD_ACK:-}" = YES || die build_ack_missing
test ! -e "${ARTIFACT_DIR}" || die artifact_dir_exists_choose_new_release
test ! -e "${BUILDKIT_ROOT}" || die buildkit_root_exists_choose_new_release
test "$(systemctl is-active "${BUILDKIT_UNIT}.service" 2>/dev/null || true)" != active || die buildkit_already_active
install -d -m 0750 "${ARTIFACT_DIR}" "${BUILDKIT_ROOT}"
exec 9>"${ARTIFACT_DIR}/build.lock"
flock -n 9 || die concurrent_build

for component in gateway runner frontend; do
  archive_var="BASE_${component}"; archive="${!archive_var}"
  image_var="IMAGE_${component}"; image="${!image_var}"
  python3 -B "${ROOT_DIR}/prepare_base.py" "${archive}" "${ARTIFACT_DIR}/base-${component}" "${image}" || die "base_prepare_failed=${component}"
done

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

for component in gateway runner frontend; do
  test "$(df -Pk / | awk 'NR==2 {print $4}')" -ge $((30 * 1024 * 1024)) || die root_reserve_breached
  kubectl --request-timeout=20s -n deer-flow get pods -l app=gateway -o json | python3 -c '
import json,sys
assert not any(p["spec"].get("nodeName")=="datax-01" and not p["metadata"].get("deletionTimestamp") for p in json.load(sys.stdin)["items"])
' || die gateway_rescheduled_to_build_node
  layout="${ARTIFACT_DIR}/base-${component}"
  digest="$(<"${layout}/manifest.digest")"
  target_var="TARGET_${component}"; target="${!target_var}"
  echo "preview_build_component=START component=${component}"
  buildctl --addr "${BUILDKIT_ADDR}" build --progress=plain --frontend dockerfile.v0 \
    --local "context=${ROOT_DIR}" --local "dockerfile=${ROOT_DIR}" \
    --oci-layout "${component}_base=${layout}" \
    --opt "filename=Dockerfile.${component}" --opt platform=linux/arm64 \
    --opt target=source-check \
    --opt "context:preview-${component}-base=oci-layout://${component}_base@${digest}" || die "source_check_failed=${component}"
  output="${ARTIFACT_DIR}/${component}.candidate.oci.tar"
  buildctl --addr "${BUILDKIT_ADDR}" build --progress=plain --frontend dockerfile.v0 \
    --local "context=${ROOT_DIR}" --local "dockerfile=${ROOT_DIR}" \
    --oci-layout "${component}_base=${layout}" \
    --opt "filename=Dockerfile.${component}" --opt platform=linux/arm64 \
    --opt "context:preview-${component}-base=oci-layout://${component}_base@${digest}" \
    --output "type=oci,name=${target},dest=${output}.partial" || die "candidate_build_failed=${component}"
  test -s "${output}.partial" || die "empty_candidate=${component}"
  mv -- "${output}.partial" "${output}"
  (cd "${ARTIFACT_DIR}" && sha256sum "$(basename "${output}")" >"$(basename "${output}").sha256")
  echo "preview_build_component=PASS component=${component} image=${target}"
done
echo "preview_build=PASS artifact_dir=${ARTIFACT_DIR} production_workloads_mutated=no rollout=NOT_PERFORMED"
