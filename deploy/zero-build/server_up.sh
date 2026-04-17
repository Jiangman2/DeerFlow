#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

if ! command -v docker >/dev/null 2>&1; then
  echo "docker not found"
  exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "docker compose not found"
  exit 1
fi

IMG_TAR="$REPO_ROOT/deploy/zero-build/out/images/deerflow-images.tar"
if [ ! -f "$IMG_TAR" ]; then
  echo "missing images tar: $IMG_TAR"
  exit 1
fi

docker load -i "$IMG_TAR" >/dev/null

export PORT="${PORT:-2026}"
export NGINX_CONF="${NGINX_CONF:-nginx.conf}"
export DEER_FLOW_REPO_ROOT="$REPO_ROOT"
export DEER_FLOW_HOME="${DEER_FLOW_HOME:-$REPO_ROOT/backend/.deer-flow}"
export DEER_FLOW_CONFIG_PATH="${DEER_FLOW_CONFIG_PATH:-$REPO_ROOT/config.yaml}"
export DEER_FLOW_EXTENSIONS_CONFIG_PATH="${DEER_FLOW_EXTENSIONS_CONFIG_PATH:-$REPO_ROOT/extensions_config.json}"
export DEER_FLOW_DOCKER_SOCKET="${DEER_FLOW_DOCKER_SOCKET:-/var/run/docker.sock}"

if [ -z "${BETTER_AUTH_SECRET:-}" ]; then
  if command -v openssl >/dev/null 2>&1; then
    export BETTER_AUTH_SECRET="$(openssl rand -hex 32)"
  else
    export BETTER_AUTH_SECRET="$(python3 - <<'PY'\nimport secrets\nprint(secrets.token_hex(32))\nPY)"
  fi
fi

cd "$REPO_ROOT/docker"
docker compose -f docker-compose.prebuilt.yaml up -d

echo "OK http://localhost:${PORT}"

