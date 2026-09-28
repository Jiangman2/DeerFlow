#!/usr/bin/env bash
# Build the 2026-09-13 WeKnora UI base from the exact R5 source and overlay.
# This creates only a local OCI archive; it does not import or deploy an image.
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="${1:?usage: build_oci.sh /absolute/path/to/new.oci.tar}"
BUILDKIT_ADDR="${BUILDKIT_ADDR:-unix:///run/buildkit/buildkitd.sock}"
ENTERPRISE_CA_FILE="${ENTERPRISE_CA_FILE:?set ENTERPRISE_CA_FILE to the approved CA bundle path}"

case "${OUT}" in /*) ;; *) echo "output must be an absolute path" >&2; exit 2 ;; esac
test "$(uname -m)" = aarch64
test -s "${ENTERPRISE_CA_FILE}"
test ! -e "${OUT}"
command -v buildctl >/dev/null
command -v sha256sum >/dev/null

# shellcheck disable=SC1091
source "${ROOT}/IMAGE_LOCKS.env"
(
  cd "${ROOT}"
  sha256sum -c --status FRONTEND.SHA256
  sha256sum -c --status UPSTREAM_UI_FILES.SHA256
)

STAGE="$(mktemp -d)"
trap 'rm -rf -- "${STAGE}"' EXIT
mkdir -p "${STAGE}/frontend" "${STAGE}/.deerflow-enterprise/overlay" \
  "${STAGE}/.deerflow-enterprise/tests"
cp -a "${ROOT}/frontend/." "${STAGE}/frontend/"
cp -a "${ROOT}/overlay/." "${STAGE}/.deerflow-enterprise/overlay/"
cp -a "${ROOT}/tests/." "${STAGE}/.deerflow-enterprise/tests/"

buildctl --addr "${BUILDKIT_ADDR}" build \
  --progress=plain \
  --frontend dockerfile.v0 \
  --local context="${STAGE}" \
  --local dockerfile="${ROOT}/overlay" \
  --opt filename=Dockerfile.ui \
  --opt platform=linux/arm64 \
  --opt image-resolve-mode=local \
  --opt "build-arg:NODE_IMAGE=${NODE_IMAGE}" \
  --opt "build-arg:NGINX_IMAGE=${NGINX_IMAGE}" \
  --opt "build-arg:NPM_REGISTRY=https://registry.npmmirror.com" \
  --opt "build-arg:HTTP_PROXY=${HTTP_PROXY:-}" \
  --opt "build-arg:HTTPS_PROXY=${HTTPS_PROXY:-}" \
  --opt "build-arg:NO_PROXY=${NO_PROXY:-}" \
  --secret id=enterprise_ca,src="${ENTERPRISE_CA_FILE}" \
  --output "type=oci,name=${NEW_UI_IMAGE},dest=${OUT}"

test -s "${OUT}"
sha256sum "${OUT}"
