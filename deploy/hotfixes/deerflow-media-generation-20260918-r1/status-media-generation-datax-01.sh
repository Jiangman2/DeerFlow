#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
. "${ROOT_DIR}/IMAGE_LOCKS.env"
NS=deer-flow

die() {
  echo "deerflow_media_generation_status=FAIL detail=$*" >&2
  exit 1
}

test "$(id -u)" = '0' || die 'must_run_as_root'
test "$(hostname -s)" = 'datax-01' || die 'must_run_on_datax-01'
kubectl -n "${NS}" rollout status deployment/gateway --timeout=300s
kubectl -n "${NS}" rollout status deployment/nginx --timeout=300s
kubectl -n "${NS}" get deployment gateway nginx -o wide
kubectl -n "${NS}" get pods -l 'app in (gateway,nginx)' -o wide
kubectl -n "${NS}" get endpointslice -l kubernetes.io/service-name=gateway -o wide

current_image="$(kubectl -n "${NS}" get deployment gateway -o jsonpath='{.spec.template.spec.containers[?(@.name=="gateway")].image}')"
test "${current_image}" = "${TARGET_GATEWAY_IMAGE}" || die "unexpected_gateway_image=${current_image}"
GATEWAY_POD="$(kubectl -n "${NS}" get pods -l app=gateway --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}')"
NGINX_POD="$(kubectl -n "${NS}" get pods -l app=nginx --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}')"
test -n "${GATEWAY_POD}" || die 'running_gateway_pod_not_found'
test -n "${NGINX_POD}" || die 'running_nginx_pod_not_found'

kubectl -n "${NS}" exec -i "${GATEWAY_POD}" -c gateway -- \
  sh -ec 'cd /app/backend && PYTHONPATH=packages/harness:. .venv/bin/python -' <<'PY'
import os
import socket

from deerflow.community.media_generation import get_configured_media_generation_tools

expected = {
    "DEERFLOW_IMAGE_GENERATION_API_URL": "http://10.57.2.43:30008/v1/images/generations",
    "DEERFLOW_VIDEO_GENERATION_API_URL": "http://10.57.2.43:30009/v1/videos",
    "DEERFLOW_IMAGE_GENERATION_TIMEOUT_SECONDS": "600",
    "DEERFLOW_VIDEO_GENERATION_POLL_SECONDS": "3",
}
for name, value in expected.items():
    assert os.environ.get(name) == value, (name, os.environ.get(name))
assert [tool.name for tool in get_configured_media_generation_tools()] == ["generate_image", "generate_video"]
for host, port in (("10.57.2.43", 30008), ("10.57.2.43", 30009)):
    connection = socket.create_connection((host, port), timeout=5)
    connection.close()
print("media_generation_live_runtime=PASS")
PY

for skill in image-generation video-generation; do
  expected_sha="$(sha256sum "${ROOT_DIR}/payload/skills/public/${skill}/SKILL.md" | awk '{print $1}')"
  actual_sha="$(kubectl -n "${NS}" exec "${GATEWAY_POD}" -c gateway -- sha256sum "/app/skills/public/${skill}/SKILL.md" | awk '{print $1}')"
  test "${actual_sha}" = "${expected_sha}" || die "skill_digest_mismatch=${skill}"
  echo "media_generation_skill_runtime=PASS skill=${skill} sha256=${actual_sha}"
done

kubectl -n "${NS}" exec "${NGINX_POD}" -c nginx -- wget -qO- -T 10 http://gateway:8001/health
echo
kubectl -n "${NS}" exec "${NGINX_POD}" -c nginx -- wget -qO- -T 10 http://127.0.0.1:2026/api/v1/auth/setup-status
echo
echo 'deerflow_media_generation_status=PASS'
echo 'official_image_generation_skill=PLATFORM_MODEL_ENABLED'
echo 'official_video_generation_skill=PLATFORM_MODEL_ENABLED'
echo 'ordinary_chat_media_tools=ENABLED'
echo 'production_database_mutation=NONE'
echo 'weknora_mutation=NONE'
