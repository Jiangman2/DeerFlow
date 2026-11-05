#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
. "${ROOT_DIR}/IMAGE_LOCKS.env"
INPUT_DIR="${1:-${ARTIFACT_DIR}}"

die() {
  echo "digital_employee_domains_image_import=FAIL detail=$*" >&2
  exit 1
}

test "$(id -u)" = '0' || die 'must_run_as_root'
test "$(uname -m)" = 'aarch64' || die 'requires_aarch64'
case "$(hostname -s)" in
  datax-01|datax-02|datax-03) ;;
  *) die 'must_run_on_datax_node' ;;
esac
command -v ctr >/dev/null || die 'ctr_not_found'
command -v sha256sum >/dev/null || die 'sha256sum_not_found'

for name in "${GATEWAY_OCI_NAME}" "${FRONTEND_OCI_NAME}" "${PROVISIONER_OCI_NAME}"; do
  test -s "${INPUT_DIR}/${name}" || die "missing=${INPUT_DIR}/${name}"
  test -s "${INPUT_DIR}/${name}.sha256" || die "missing=${INPUT_DIR}/${name}.sha256"
  (cd "${INPUT_DIR}" && sha256sum -c "${name}.sha256")
  ctr -n k8s.io images import "${INPUT_DIR}/${name}"
done

ctr -n k8s.io images inspect "${TARGET_GATEWAY_IMAGE}" >/dev/null
ctr -n k8s.io images inspect "${TARGET_FRONTEND_IMAGE}" >/dev/null
ctr -n k8s.io images inspect "${TARGET_PROVISIONER_IMAGE}" >/dev/null
echo "digital_employee_domains_skill_runtime_image_import=PASS node=$(hostname -s)"
