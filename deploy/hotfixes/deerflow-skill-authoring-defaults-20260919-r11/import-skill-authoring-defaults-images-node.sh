#!/usr/bin/env bash
set -Eeuo pipefail

REQUESTED_ARTIFACT_DIR="${1:-}"
MODE="${2:---all}"
test -n "${REQUESTED_ARTIFACT_DIR}" || { echo 'usage: import-skill-authoring-defaults-images-node.sh ARTIFACT_DIR [--all|--gateway-only]' >&2; exit 2; }
case "${MODE}" in --all|--gateway-only) ;; *) echo 'invalid import mode' >&2; exit 2 ;; esac
test "$(id -u)" = '0' || { echo 'must run as root' >&2; exit 1; }
test "$(uname -m)" = 'aarch64' || { echo 'requires aarch64' >&2; exit 1; }
. "${REQUESTED_ARTIFACT_DIR}/IMAGE_LOCKS.env"
ARTIFACT_DIR="${REQUESTED_ARTIFACT_DIR}"
cd "${ARTIFACT_DIR}"
archives=("${GATEWAY_OCI_NAME}")
if test "${MODE}" = '--all'; then
  archives+=("${FRONTEND_OCI_NAME}" "${WEKNORA_FRONTEND_OCI_NAME}")
fi
for archive in "${archives[@]}"; do
  sha256sum -c "${archive}.sha256"
  ctr -n k8s.io images import "${archive}"
done
ctr -n k8s.io images inspect "${TARGET_GATEWAY_IMAGE}" >/dev/null
if test "${MODE}" = '--all'; then
  ctr -n k8s.io images inspect "${TARGET_FRONTEND_IMAGE}" >/dev/null
  ctr -n k8s.io images inspect "${TARGET_WEKNORA_FRONTEND_IMAGE}" >/dev/null
fi
echo "skill_authoring_defaults_image_import=PASS node=$(hostname -s) mode=${MODE}"
