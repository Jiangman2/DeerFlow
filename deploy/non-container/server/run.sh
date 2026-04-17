#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/../../.." && pwd)"

if [ -f "$ROOT_DIR/.env" ]; then
  set -a
  . "$ROOT_DIR/.env"
  set +a
fi

export DEER_FLOW_HOME="${DEER_FLOW_HOME:-$ROOT_DIR/backend/.deer-flow}"
export DEER_FLOW_CONFIG_PATH="${DEER_FLOW_CONFIG_PATH:-$ROOT_DIR/config.yaml}"
export DEER_FLOW_EXTENSIONS_CONFIG_PATH="${DEER_FLOW_EXTENSIONS_CONFIG_PATH:-$ROOT_DIR/extensions_config.json}"

export GATEWAY_PORT="${GATEWAY_PORT:-8001}"
export LANGGRAPH_PORT="${LANGGRAPH_PORT:-2025}"
export FRONTEND_PORT="${FRONTEND_PORT:-3000}"

cd "$ROOT_DIR/backend"
PYTHONPATH=. uv run uvicorn app.gateway.app:app --host 0.0.0.0 --port "$GATEWAY_PORT" --workers 2 &

PYTHONIOENCODING=utf-8 uv run langgraph dev --no-browser --allow-blocking --no-reload --host 0.0.0.0 --port "$LANGGRAPH_PORT" &

cd "$ROOT_DIR/frontend"
if [ -f "$ROOT_DIR/frontend/.next/standalone/server.js" ]; then
  HOSTNAME=0.0.0.0 PORT="$FRONTEND_PORT" node "$ROOT_DIR/frontend/.next/standalone/server.js" &
else
  pnpm start -- --hostname 0.0.0.0 --port "$FRONTEND_PORT" &
fi

wait
