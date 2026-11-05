#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
. "${ROOT_DIR}/IMAGE_LOCKS.env"
NS=deer-flow

die() {
  echo "deerflow_media_generation_image_build=FAIL detail=$*" >&2
  exit 1
}

echo '===== DEERFLOW PLATFORM MEDIA GENERATION IMAGE BUILD ====='
date
hostname
uname -m
id

test "$(id -u)" = '0' || die 'must_run_as_root'
test "$(hostname -s)" = 'datax-01' || die 'must_run_on_datax-01'
test "$(uname -m)" = 'aarch64' || die 'requires_aarch64'
command -v buildctl >/dev/null || die 'buildctl_not_found'
command -v ctr >/dev/null || die 'ctr_not_found'
command -v kubectl >/dev/null || die 'kubectl_not_found'
command -v python3 >/dev/null || die 'python3_not_found'
command -v sha256sum >/dev/null || die 'sha256sum_not_found'
command -v tar >/dev/null || die 'tar_not_found'
BUILDCTL_BUILD_HELP="$(buildctl build --help 2>&1)"
grep -q -- '--oci-layout' <<<"${BUILDCTL_BUILD_HELP}" || die 'buildctl_oci_layout_not_supported'

(
  cd "${ROOT_DIR}"
  sha256sum -c PACKAGE.SHA256
)
python3 "${ROOT_DIR}/verify-media-generation-hotfix.py" --root "${ROOT_DIR}"
kubectl -n "${NS}" rollout status deployment/gateway --timeout=300s
BASE_GATEWAY_IMAGE="$(kubectl -n "${NS}" get deployment gateway -o jsonpath='{.spec.template.spec.containers[?(@.name=="gateway")].image}')"
test -n "${BASE_GATEWAY_IMAGE}" || die 'live_gateway_image_missing'
test "${BASE_GATEWAY_IMAGE}" != "${TARGET_GATEWAY_IMAGE}" || die 'target_image_is_already_live'
ctr -n k8s.io images inspect "${BASE_GATEWAY_IMAGE}" >/dev/null || die "missing_live_gateway_image=${BASE_GATEWAY_IMAGE}"

install -d -m 0750 "${ARTIFACT_DIR}"
BASE_RECORD="${ARTIFACT_DIR}/BASE_GATEWAY_IMAGE"
if test -s "${BASE_RECORD}"; then
  test "$(cat "${BASE_RECORD}")" = "${BASE_GATEWAY_IMAGE}" || die 'artifact_cache_base_image_changed'
else
  printf '%s\n' "${BASE_GATEWAY_IMAGE}" > "${BASE_RECORD}"
fi

LAYOUT_DIR="${ARTIFACT_DIR}/base-layouts/gateway"
PARTIAL_DIR="${LAYOUT_DIR}.partial"
EXPORT_TAR="${ARTIFACT_DIR}/gateway.base.oci.tar"
EXPORT_PARTIAL="${EXPORT_TAR}.partial"
if ! test -s "${LAYOUT_DIR}/manifest.digest"; then
  rm -rf -- "${LAYOUT_DIR}" "${PARTIAL_DIR}"
  rm -f -- "${EXPORT_PARTIAL}"
  install -d -m 0750 "${PARTIAL_DIR}"
  ctr -n k8s.io images export --platform linux/arm64 "${EXPORT_PARTIAL}" "${BASE_GATEWAY_IMAGE}"
  test -s "${EXPORT_PARTIAL}" || die 'empty_base_export'
  mv -f -- "${EXPORT_PARTIAL}" "${EXPORT_TAR}"
  tar -xf "${EXPORT_TAR}" -C "${PARTIAL_DIR}"
  python3 - "${PARTIAL_DIR}" "${BASE_GATEWAY_IMAGE}" <<'PY'
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
  mv -- "${PARTIAL_DIR}" "${LAYOUT_DIR}"
fi

LAYOUT_DIGEST="$(cat "${LAYOUT_DIR}/manifest.digest")"
OUTPUT_PATH="${ARTIFACT_DIR}/${GATEWAY_OCI_NAME}"
PARTIAL_PATH="${OUTPUT_PATH}.partial"
if test -s "${OUTPUT_PATH}" && test -s "${OUTPUT_PATH}.sha256" && \
  (cd "${ARTIFACT_DIR}" && sha256sum -c "${GATEWAY_OCI_NAME}.sha256"); then
  echo "image_build_cache=${TARGET_GATEWAY_IMAGE}:PASS"
else
  rm -f -- "${PARTIAL_PATH}"
  buildctl build \
    --progress=plain \
    --frontend dockerfile.v0 \
    --local "context=${ROOT_DIR}" \
    --local "dockerfile=${ROOT_DIR}" \
    --oci-layout "gateway_base=${LAYOUT_DIR}" \
    --opt filename=Dockerfile.gateway \
    --opt platform=linux/arm64 \
    --opt "context:media-generation-gateway-base=oci-layout://gateway_base@${LAYOUT_DIGEST}" \
    --output "type=oci,name=${TARGET_GATEWAY_IMAGE},dest=${PARTIAL_PATH}"
  test -s "${PARTIAL_PATH}" || die 'empty_gateway_oci_output'
  mv -f -- "${PARTIAL_PATH}" "${OUTPUT_PATH}"
  (cd "${ARTIFACT_DIR}" && sha256sum "${GATEWAY_OCI_NAME}" > "${GATEWAY_OCI_NAME}.sha256")
fi
(cd "${ARTIFACT_DIR}" && sha256sum -c "${GATEWAY_OCI_NAME}.sha256")
install -m 0755 "${ROOT_DIR}/import-media-generation-image-node.sh" "${ARTIFACT_DIR}/"
install -m 0640 "${ROOT_DIR}/IMAGE_LOCKS.env" "${ARTIFACT_DIR}/"
"${ROOT_DIR}/import-media-generation-image-node.sh" "${ARTIFACT_DIR}"

echo "base_gateway_image=${BASE_GATEWAY_IMAGE}"
echo "target_gateway_image=${TARGET_GATEWAY_IMAGE}"
echo 'deerflow_media_generation_image_build=PASS local_oci_base=yes registry_access=none'
echo "artifact_dir=${ARTIFACT_DIR}"
echo 'next_step=copy_artifact_directory_and_import_on_datax-02_and_datax-03'
