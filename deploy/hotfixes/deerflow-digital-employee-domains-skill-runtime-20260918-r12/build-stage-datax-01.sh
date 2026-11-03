#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
. "${ROOT_DIR}/IMAGE_LOCKS.env"

die() {
  echo "digital_employee_domains_build=FAIL detail=$*" >&2
  exit 1
}

echo '===== DEERFLOW DIGITAL EMPLOYEE BUSINESS-DOMAIN IMAGE BUILD ====='
date
hostname
uname -m
id

test "$(id -u)" = '0' || die 'must_run_as_root'
test "$(hostname -s)" = 'datax-01' || die 'must_run_on_datax-01'
test "$(uname -m)" = 'aarch64' || die 'requires_aarch64'
command -v buildctl >/dev/null || die 'buildctl_not_found'
command -v ctr >/dev/null || die 'ctr_not_found'
command -v python3 >/dev/null || die 'python3_not_found'
command -v sha256sum >/dev/null || die 'sha256sum_not_found'
command -v tar >/dev/null || die 'tar_not_found'
BUILDCTL_BUILD_HELP="$(buildctl build --help 2>&1)"
grep -q -- '--oci-layout' <<<"${BUILDCTL_BUILD_HELP}" || die 'buildctl_oci_layout_not_supported'

(
  cd "${ROOT_DIR}"
  sha256sum -c PACKAGE.SHA256
)
python3 "${ROOT_DIR}/verify-digital-employee-domains.py" --root "${ROOT_DIR}"

ctr -n k8s.io images inspect "${BASE_GATEWAY_IMAGE}" >/dev/null || die "missing_base_gateway_image=${BASE_GATEWAY_IMAGE}"
ctr -n k8s.io images inspect "${BASE_FRONTEND_IMAGE}" >/dev/null || die "missing_base_frontend_image=${BASE_FRONTEND_IMAGE}"
ctr -n k8s.io images inspect "${BASE_PROVISIONER_IMAGE}" >/dev/null || die "missing_base_provisioner_image=${BASE_PROVISIONER_IMAGE}"
install -d -m 0750 "${ARTIFACT_DIR}"

prepare_oci_layout() {
  local source_image="$1"
  local layout_name="$2"
  local layout_dir="${ARTIFACT_DIR}/base-layouts/${layout_name}"
  local partial_dir="${layout_dir}.partial"
  local export_tar="${ARTIFACT_DIR}/${layout_name}.base.oci.tar"
  local export_partial="${export_tar}.partial"

  if test -s "${layout_dir}/manifest.digest"; then
    local cached_digest
    cached_digest="$(cat "${layout_dir}/manifest.digest")"
    case "${cached_digest}" in sha256:*) ;; *) die "invalid_cached_digest=${cached_digest}" ;; esac
    test -s "${layout_dir}/blobs/sha256/${cached_digest#sha256:}" || die "cached_manifest_blob_missing=${cached_digest}"
    echo "base_oci_layout_cache=${source_image}:PASS"
    return
  fi

  rm -rf -- "${layout_dir}" "${partial_dir}"
  rm -f -- "${export_partial}"
  install -d -m 0750 "${partial_dir}"
  ctr -n k8s.io images export --platform linux/arm64 "${export_partial}" "${source_image}"
  test -s "${export_partial}" || die "empty_base_export=${source_image}"
  mv -f -- "${export_partial}" "${export_tar}"
  tar -xf "${export_tar}" -C "${partial_dir}"
  python3 - "${partial_dir}" "${source_image}" <<'PY'
from __future__ import print_function

import json
import sys
from pathlib import Path

layout = Path(sys.argv[1])
source_image = sys.argv[2]
index = json.loads((layout / "index.json").read_text(encoding="utf-8"))
descriptors = index.get("manifests") or []
matches = [
    item
    for item in descriptors
    if (item.get("annotations") or {}).get("org.opencontainers.image.ref.name") == source_image
]
descriptor = matches[0] if len(matches) == 1 else descriptors[0] if len(descriptors) == 1 else None
if descriptor is None:
    raise SystemExit("could not select one OCI descriptor for " + source_image)
digest = descriptor.get("digest") or ""
if not digest.startswith("sha256:"):
    raise SystemExit("unexpected OCI descriptor digest: " + digest)
blob = layout / "blobs" / "sha256" / digest.split(":", 1)[1]
if not blob.is_file():
    raise SystemExit("OCI descriptor blob is missing: " + str(blob))
(layout / "manifest.digest").write_text(digest + "\n", encoding="ascii")
print("base_oci_layout_digest=" + digest)
PY
  mv -- "${partial_dir}" "${layout_dir}"
  echo "base_oci_layout=${source_image}:PASS"
}

prepare_oci_layout "${BASE_GATEWAY_IMAGE}" gateway
prepare_oci_layout "${BASE_FRONTEND_IMAGE}" frontend
prepare_oci_layout "${BASE_PROVISIONER_IMAGE}" provisioner
GATEWAY_BASE_LAYOUT="${ARTIFACT_DIR}/base-layouts/gateway"
FRONTEND_BASE_LAYOUT="${ARTIFACT_DIR}/base-layouts/frontend"
PROVISIONER_BASE_LAYOUT="${ARTIFACT_DIR}/base-layouts/provisioner"
GATEWAY_BASE_DIGEST="$(cat "${GATEWAY_BASE_LAYOUT}/manifest.digest")"
FRONTEND_BASE_DIGEST="$(cat "${FRONTEND_BASE_LAYOUT}/manifest.digest")"
PROVISIONER_BASE_DIGEST="$(cat "${PROVISIONER_BASE_LAYOUT}/manifest.digest")"

build_oci() {
  local dockerfile="$1"
  local base_alias="$2"
  local layout_name="$3"
  local layout_dir="$4"
  local layout_digest="$5"
  local target_image="$6"
  local output_name="$7"
  local output_path="${ARTIFACT_DIR}/${output_name}"
  local partial_path="${output_path}.partial"

  if test -s "${output_path}" && test -s "${output_path}.sha256" &&
    (cd "${ARTIFACT_DIR}" && sha256sum -c "${output_name}.sha256"); then
    echo "image_build_cache=${target_image}:PASS"
    return
  fi

  rm -f -- "${partial_path}"
  buildctl build \
    --progress=plain \
    --frontend dockerfile.v0 \
    --local "context=${ROOT_DIR}" \
    --local "dockerfile=${ROOT_DIR}" \
    --oci-layout "${layout_name}=${layout_dir}" \
    --opt "filename=${dockerfile}" \
    --opt platform=linux/arm64 \
    --opt "context:${base_alias}=oci-layout://${layout_name}@${layout_digest}" \
    --output "type=oci,name=${target_image},dest=${partial_path}"
  test -s "${partial_path}" || die "empty_oci_output=${partial_path}"
  mv -f -- "${partial_path}" "${output_path}"
  (
    cd "${ARTIFACT_DIR}"
    sha256sum "${output_name}" > "${output_name}.sha256"
    sha256sum -c "${output_name}.sha256"
  )
  echo "image_build=${target_image}:PASS"
}

build_oci Dockerfile.gateway digital-domains-gateway-base gateway_base "${GATEWAY_BASE_LAYOUT}" "${GATEWAY_BASE_DIGEST}" "${TARGET_GATEWAY_IMAGE}" "${GATEWAY_OCI_NAME}"
build_oci Dockerfile.frontend digital-domains-frontend-base frontend_base "${FRONTEND_BASE_LAYOUT}" "${FRONTEND_BASE_DIGEST}" "${TARGET_FRONTEND_IMAGE}" "${FRONTEND_OCI_NAME}"
build_oci Dockerfile.provisioner digital-domains-provisioner-base provisioner_base "${PROVISIONER_BASE_LAYOUT}" "${PROVISIONER_BASE_DIGEST}" "${TARGET_PROVISIONER_IMAGE}" "${PROVISIONER_OCI_NAME}"

install -m 0755 "${ROOT_DIR}/import-digital-employee-domains-images-node.sh" "${ARTIFACT_DIR}/"
install -m 0640 "${ROOT_DIR}/IMAGE_LOCKS.env" "${ARTIFACT_DIR}/"
"${ROOT_DIR}/import-digital-employee-domains-images-node.sh" "${ARTIFACT_DIR}"

echo 'digital_employee_domains_skill_runtime_image_build=PASS images=gateway,frontend,provisioner local_oci_base=yes registry_access=none'
echo 'research_fund_settlement_skill_payload=PASS deployment=shared_pvc_atomic_swap'
echo "artifact_dir=${ARTIFACT_DIR}"
echo 'next_step=copy_artifact_directory_and_import_on_datax-02_and_datax-03'
