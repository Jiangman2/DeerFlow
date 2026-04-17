#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/../../.." && pwd)"
INSTALL_DIR="${INSTALL_DIR:-/opt/deer-flow}"
ENV_DIR="${ENV_DIR:-/etc/deerflow}"
ENV_FILE="${ENV_FILE:-$ENV_DIR/deerflow.env}"

sudo mkdir -p "$INSTALL_DIR"
sudo mkdir -p "$ENV_DIR"

if [ "$(cd "$ROOT_DIR" && pwd)" != "$(cd "$INSTALL_DIR" && pwd)" ]; then
  if [ "$(cd "$ROOT_DIR" && pwd)" = "$(cd "$INSTALL_DIR" && pwd)/deploy" ] || [ "$(cd "$ROOT_DIR" && pwd)" = "$(cd "$INSTALL_DIR" && pwd)/deploy/non-container" ] || [ "$(cd "$ROOT_DIR" && pwd)" = "$(cd "$INSTALL_DIR" && pwd)/deploy/non-container/server" ]; then
    echo "Refusing to rsync from a subdirectory of INSTALL_DIR. Set INSTALL_DIR to a different path or run from a separate unpack directory."
    exit 2
  fi
  sudo rsync -a --delete "$ROOT_DIR/" "$INSTALL_DIR/"
fi

sudo chmod +x "$INSTALL_DIR/deploy/non-container/server/run.sh"

sudo apt-get update -y
sudo apt-get install -y unzip curl ca-certificates libreoffice-writer antiword

cd "$INSTALL_DIR/backend"
uv sync

if [ -f "$INSTALL_DIR/frontend/.next/standalone/server.js" ]; then
  echo "Detected Next.js standalone output. Skipping pnpm install/build on server."
  if ! command -v node >/dev/null 2>&1; then
    echo "Node.js is required to run the standalone frontend. Please install Node 18.18+ (or 20+) and retry."
    exit 3
  fi
else
  cd "$INSTALL_DIR/frontend"
  pnpm install --frozen-lockfile
  pnpm build
fi

if [ ! -f "$ENV_FILE" ]; then
  sudo cp "$INSTALL_DIR/deploy/non-container/server/deerflow.env.example" "$ENV_FILE"
fi

sudo systemctl daemon-reload || true

echo "$INSTALL_DIR"
echo "$ENV_FILE"
