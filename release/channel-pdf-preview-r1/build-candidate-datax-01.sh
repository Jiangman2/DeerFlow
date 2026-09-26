#!/usr/bin/env bash
set -Eeuo pipefail
ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
. "${ROOT_DIR}/IMAGE_LOCKS.env"
. "${ROOT_DIR}/isolated-buildkit.sh"
export KUBECONFIG=/etc/kubernetes/admin.conf
unset SSL_CERT_FILE REQUESTS_CA_BUNDLE CURL_CA_BUNDLE
die() { echo "channel_pdf_build=FAIL detail=$*" >&2; exit 1; }
test "$(id -u)" = 0 || die requires_root
test "$(hostname -s)" = datax-01 || die requires_datax_01
test "$(uname -m)" = aarch64 || die requires_arm64
for tool in ctr kubectl python3 sha256sum tar buildctl buildkitd flock; do
  command -v "${tool}" >/dev/null || die "missing=${tool}"
done
(cd "${ROOT_DIR}" && sha256sum -c PACKAGE.SHA256)
python3 "${ROOT_DIR}/verify-package.py" "${ROOT_DIR}"
install -d -m 0750 "${ARTIFACT_DIR}"
exec 9>"${ARTIFACT_DIR}/build.lock"
flock -n 9 || die candidate_build_already_running
isolated_buildkit_preflight
mem_available_kib="$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)"
test "${mem_available_kib}" -ge $((16 * 1024 * 1024)) || die insufficient_available_memory

for component in gateway frontend; do
  if test "${component}" = gateway; then expected="${BASE_GATEWAY_IMAGE}"; else expected="${BASE_FRONTEND_IMAGE}"; fi
  kubectl --request-timeout=30s -n deer-flow get deployment "${component}" -o json |
    python3 -c '
import json,sys
d=json.load(sys.stdin); name,expected=sys.argv[1:]
c=next(x for x in d["spec"]["template"]["spec"]["containers"] if x["name"]==name)
assert c["image"]==expected, "live_base_image_changed: "+name
s=d.get("status",{}); replicas=d["spec"].get("replicas",1)
assert replicas>0 and s.get("observedGeneration",0)>=d["metadata"]["generation"]
assert s.get("readyReplicas",0)==replicas and s.get("updatedReplicas",0)==replicas
assert s.get("replicas",0)==replicas, "deployment_is_still_rolling"
print("live_baseline=PASS component="+name+" image="+expected)
' "${component}" "${expected}"
done

# Export only the two actual live bases. This never pulls from a registry.
for component in gateway frontend; do
  if test "${component}" = gateway; then source_image="${BASE_GATEWAY_IMAGE}"; else source_image="${BASE_FRONTEND_IMAGE}"; fi
  archive="${ARTIFACT_DIR}/${component}.base.oci.tar"
  layout="${ARTIFACT_DIR}/base-layout-${component}"
  ctr -n k8s.io images inspect "${source_image}" >/dev/null || die "base_image_missing=${source_image}"
  if test -e "${archive}"; then
    (cd "${ARTIFACT_DIR}" && sha256sum -c "${component}.base.oci.tar.sha256")
  else
    test ! -e "${archive}.partial" || die "partial_archive_needs_inspection=${archive}.partial"
    ctr -n k8s.io images export --platform linux/arm64 "${archive}.partial" "${source_image}"
    mv -- "${archive}.partial" "${archive}"
    (cd "${ARTIFACT_DIR}" && sha256sum "${component}.base.oci.tar" >"${component}.base.oci.tar.sha256")
  fi
  if ! test -s "${layout}/index.json"; then
    install -d -m 0750 "${layout}"
    tar -xf "${archive}" -C "${layout}"
  fi
  python3 - "${layout}" "${source_image}" <<'PY'
import hashlib,json,sys
from pathlib import Path
root=Path(sys.argv[1]); image=sys.argv[2]
rows=json.loads((root/'index.json').read_text())['manifests']
matches=[r for r in rows if r.get('annotations',{}).get(
    'io.containerd.image.name', r.get('annotations',{}).get('org.opencontainers.image.ref.name')
)==image]
if len(matches)!=1: raise SystemExit('base_manifest_match_count='+str(len(matches))+' image='+image)
digest=matches[0]['digest']
if not digest.startswith('sha256:'): raise SystemExit('invalid_base_digest')
blob=root/'blobs'/'sha256'/digest.split(':')[1]
if hashlib.sha256(blob.read_bytes()).hexdigest()!=digest.split(':')[1]: raise SystemExit('base_manifest_checksum_failed')
(root/'manifest.digest').write_text(digest+'\n')
print('resolved_base_manifest='+digest+' image='+image)
PY
done

fingerprint="$(
  sha256sum "${ROOT_DIR}/PACKAGE.SHA256" \
    "${ARTIFACT_DIR}/base-layout-gateway/manifest.digest" \
    "${ARTIFACT_DIR}/base-layout-frontend/manifest.digest" |
    awk '{print $1}' | sha256sum | awk '{print $1}'
)"
if test -s "${ARTIFACT_DIR}/BUILD_INPUT.sha256"; then
  test "$(cat "${ARTIFACT_DIR}/BUILD_INPUT.sha256")" = "${fingerprint}" || die build_inputs_changed
else
  printf '%s\n' "${fingerprint}" >"${ARTIFACT_DIR}/BUILD_INPUT.sha256"
fi

isolated_buildkit_start
trap isolated_buildkit_stop EXIT
for component in gateway frontend; do
  # Recheck colocation immediately before each build; never reschedule Gateway.
  kubectl --request-timeout=30s -n deer-flow get pods -l app=gateway -o json |
    python3 -c 'import json,sys; assert not any(p.get("spec",{}).get("nodeName")=="datax-01" and p.get("status",{}).get("phase") not in ("Succeeded","Failed") for p in json.load(sys.stdin)["items"]), "Gateway now colocated with build node"'
  if test "${component}" = gateway; then target="${TARGET_GATEWAY_IMAGE}"; else target="${TARGET_FRONTEND_IMAGE}"; fi
  layout="${ARTIFACT_DIR}/base-layout-${component}"
  digest="$(cat "${layout}/manifest.digest")"
  output="${ARTIFACT_DIR}/${component}.candidate.oci.tar"
  if test -e "${output}"; then
    (cd "${ARTIFACT_DIR}" && sha256sum -c "${component}.candidate.oci.tar.sha256")
    echo "candidate_build_cache=PASS component=${component}"
    continue
  fi
  test ! -e "${output}.partial" || die "partial_candidate_needs_inspection=${output}.partial"
  isolated_buildctl build --progress=plain --frontend dockerfile.v0 \
    --local "context=${ROOT_DIR}" --local "dockerfile=${ROOT_DIR}" \
    --oci-layout "${component}_base=${layout}" \
    --opt "filename=Dockerfile.${component}" --opt platform=linux/arm64 \
    --opt "context:channel-pdf-${component}-base=oci-layout://${component}_base@${digest}" \
    --output "type=oci,name=${target},dest=${output}.partial"
  test -s "${output}.partial" || die empty_candidate
  mv -- "${output}.partial" "${output}"
  (cd "${ARTIFACT_DIR}" && sha256sum "${component}.candidate.oci.tar" >"${component}.candidate.oci.tar.sha256")
done
echo 'channel_pdf_candidate_build=PASS components=gateway,frontend'
echo 'production_workloads_mutated=no production_database_mutation=NONE'
echo 'image_import=NOT_PERFORMED rollout=NOT_PERFORMED'
echo "artifact_dir=${ARTIFACT_DIR}"
echo 'next_step=review_build_log_then_preload_and_validate_candidates_before_rollout'
