#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
. "${ROOT_DIR}/IMAGE_LOCKS.env"
NS=deer-flow

die() {
  echo "deerflow_skill_authoring_defaults_image_build=FAIL detail=$*" >&2
  exit 1
}

test "$(id -u)" = '0' || die 'must_run_as_root'
test "$(hostname -s)" = 'datax-01' || die 'must_run_on_datax-01'
test "$(uname -m)" = 'aarch64' || die 'requires_aarch64'
for command in buildctl ctr kubectl python3 sha256sum tar; do
  command -v "${command}" >/dev/null || die "${command}_not_found"
done
grep -q -- '--oci-layout' <<<"$(buildctl build --help 2>&1)" || die 'buildctl_oci_layout_not_supported'
(
  cd "${ROOT_DIR}"
  sha256sum -c PACKAGE.SHA256
)
python3 "${ROOT_DIR}/verify-skill-authoring-defaults-hotfix.py" --root "${ROOT_DIR}"
kubectl -n "${NS}" rollout status deployment/gateway --timeout=300s
kubectl -n "${NS}" rollout status deployment/frontend --timeout=300s
kubectl -n "${NS}" rollout status deployment/weknora-frontend --timeout=300s

live_gateway="$(kubectl -n "${NS}" get deployment gateway -o jsonpath='{.spec.template.spec.containers[?(@.name=="gateway")].image}')"
live_frontend="$(kubectl -n "${NS}" get deployment frontend -o jsonpath='{.spec.template.spec.containers[?(@.name=="frontend")].image}')"
live_weknora_frontend="$(kubectl -n "${NS}" get deployment weknora-frontend -o jsonpath='{.spec.template.spec.containers[?(@.name=="frontend")].image}')"
test "${live_gateway}" = "${BASE_GATEWAY_IMAGE}" || die "unexpected_live_gateway=${live_gateway} expected=${BASE_GATEWAY_IMAGE}"
test "${live_frontend}" = "${BASE_FRONTEND_IMAGE}" || die "unexpected_live_frontend=${live_frontend} expected=${BASE_FRONTEND_IMAGE}"
test "${live_weknora_frontend}" = "${BASE_WEKNORA_FRONTEND_IMAGE}" || die "unexpected_live_weknora_frontend=${live_weknora_frontend} expected=${BASE_WEKNORA_FRONTEND_IMAGE}"
ctr -n k8s.io images inspect "${BASE_GATEWAY_IMAGE}" >/dev/null || die "missing_base_image=${BASE_GATEWAY_IMAGE}"
ctr -n k8s.io images inspect "${BASE_FRONTEND_IMAGE}" >/dev/null || die "missing_base_image=${BASE_FRONTEND_IMAGE}"
ctr -n k8s.io images inspect "${BASE_WEKNORA_FRONTEND_IMAGE}" >/dev/null || die "missing_base_image=${BASE_WEKNORA_FRONTEND_IMAGE}"
install -d -m 0750 "${ARTIFACT_DIR}"

prepare_layout() {
  local key="$1"
  local image="$2"
  local layout="${ARTIFACT_DIR}/base-layouts/${key}"
  local partial="${layout}.partial"
  local archive="${ARTIFACT_DIR}/${key}.base.oci.tar"
  if test -s "${layout}/manifest.digest"; then
    cat "${layout}/manifest.digest"
    return 0
  fi
  rm -rf -- "${layout}" "${partial}"
  rm -f -- "${archive}.partial"
  install -d -m 0750 "${partial}"
  ctr -n k8s.io images export --platform linux/arm64 "${archive}.partial" "${image}"
  test -s "${archive}.partial" || die "empty_base_export=${image}"
  mv -f -- "${archive}.partial" "${archive}"
  tar -xf "${archive}" -C "${partial}"
  python3 - "${partial}" "${image}" <<'PY'
from __future__ import print_function
import json, sys
from pathlib import Path
layout = Path(sys.argv[1])
source = sys.argv[2]
index = json.loads((layout / "index.json").read_text(encoding="utf-8"))
descriptors = index.get("manifests") or []
matches = [item for item in descriptors if (item.get("annotations") or {}).get("org.opencontainers.image.ref.name") == source]
descriptor = matches[0] if len(matches) == 1 else descriptors[0] if len(descriptors) == 1 else None
if descriptor is None:
    raise SystemExit("could not select OCI descriptor for " + source)
digest = descriptor.get("digest") or ""
if not digest.startswith("sha256:"):
    raise SystemExit("unexpected OCI digest " + digest)
blob = layout / "blobs" / "sha256" / digest.split(":", 1)[1]
if not blob.is_file():
    raise SystemExit("missing OCI manifest blob")
(layout / "manifest.digest").write_text(digest + "\n", encoding="ascii")
print(digest)
PY
  mv -- "${partial}" "${layout}"
  cat "${layout}/manifest.digest"
}

build_image() {
  local key="$1"
  local context_name="$2"
  local dockerfile="$3"
  local target_image="$4"
  local archive_name="$5"
  local layout="${ARTIFACT_DIR}/base-layouts/${key}"
  local digest
  local output="${ARTIFACT_DIR}/${archive_name}"
  digest="$(cat "${layout}/manifest.digest")"
  if test -s "${output}" && test -s "${output}.sha256" && (
    cd "${ARTIFACT_DIR}" && sha256sum -c "${archive_name}.sha256"
  ); then
    echo "image_build_cache=${target_image}:PASS"
    return 0
  fi
  rm -f -- "${output}.partial"
  buildctl build \
    --progress=plain \
    --frontend dockerfile.v0 \
    --local "context=${ROOT_DIR}" \
    --local "dockerfile=${ROOT_DIR}" \
    --oci-layout "${key}_base=${layout}" \
    --opt "filename=${dockerfile}" \
    --opt platform=linux/arm64 \
    --opt "context:${context_name}=oci-layout://${key}_base@${digest}" \
    --output "type=oci,name=${target_image},dest=${output}.partial"
  test -s "${output}.partial" || die "empty_image_output=${target_image}"
  mv -f -- "${output}.partial" "${output}"
  (cd "${ARTIFACT_DIR}" && sha256sum "${archive_name}" > "${archive_name}.sha256")
}

prepare_layout gateway "${BASE_GATEWAY_IMAGE}" >/dev/null
prepare_layout frontend "${BASE_FRONTEND_IMAGE}" >/dev/null
prepare_layout weknora-frontend "${BASE_WEKNORA_FRONTEND_IMAGE}" >/dev/null
build_image gateway skill-authoring-gateway-base Dockerfile.gateway "${TARGET_GATEWAY_IMAGE}" "${GATEWAY_OCI_NAME}"
build_image frontend default-flash-frontend-base Dockerfile.frontend "${TARGET_FRONTEND_IMAGE}" "${FRONTEND_OCI_NAME}"
build_image weknora-frontend weknora-frontend-base Dockerfile.weknora-frontend "${TARGET_WEKNORA_FRONTEND_IMAGE}" "${WEKNORA_FRONTEND_OCI_NAME}"
(
  cd "${ARTIFACT_DIR}"
  sha256sum -c "${GATEWAY_OCI_NAME}.sha256"
  sha256sum -c "${FRONTEND_OCI_NAME}.sha256"
  sha256sum -c "${WEKNORA_FRONTEND_OCI_NAME}.sha256"
)
install -m 0755 "${ROOT_DIR}/import-skill-authoring-defaults-images-node.sh" "${ARTIFACT_DIR}/"
install -m 0640 "${ROOT_DIR}/IMAGE_LOCKS.env" "${ARTIFACT_DIR}/"
"${ROOT_DIR}/import-skill-authoring-defaults-images-node.sh" "${ARTIFACT_DIR}"

printf '%s\n' "${BASE_GATEWAY_IMAGE}" > "${ARTIFACT_DIR}/BASE_GATEWAY_IMAGE"
printf '%s\n' "${BASE_FRONTEND_IMAGE}" > "${ARTIFACT_DIR}/BASE_FRONTEND_IMAGE"
printf '%s\n' "${BASE_WEKNORA_FRONTEND_IMAGE}" > "${ARTIFACT_DIR}/BASE_WEKNORA_FRONTEND_IMAGE"
echo 'deerflow_skill_authoring_defaults_image_build=PASS local_oci_base=yes registry_access=none'
echo "artifact_dir=${ARTIFACT_DIR}"
echo 'next_step=import_all_images_on_every_ready_untainted_general_node_and_gateway_image_on_every_workload_gateway_node_including_ecs-zdz-pro-0022'
