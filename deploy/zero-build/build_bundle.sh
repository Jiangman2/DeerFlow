#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
OUT_DIR="$REPO_ROOT/deploy/zero-build/out"
IMG_DIR="$OUT_DIR/images"
STAGE_DIR="$OUT_DIR/stage"
mkdir -p "$IMG_DIR"

cd "$REPO_ROOT"

docker build -f backend/Dockerfile -t deer-flow-backend:prebuilt .
docker build -f frontend/Dockerfile --target prod -t deer-flow-frontend:prebuilt .
docker build -f docker/provisioner/Dockerfile -t deer-flow-provisioner:prebuilt docker/provisioner
docker pull nginx:alpine

docker save -o "$IMG_DIR/deerflow-images.tar" \
  deer-flow-backend:prebuilt \
  deer-flow-frontend:prebuilt \
  deer-flow-provisioner:prebuilt \
  nginx:alpine

rm -rf "$STAGE_DIR"
mkdir -p "$STAGE_DIR/deer-flow"

rsync -a \
  --exclude "backend/.venv" \
  --exclude "backend/.deer-flow" \
  --exclude "frontend/node_modules" \
  --exclude "frontend/.next" \
  --exclude "node_modules" \
  --exclude ".venv" \
  --exclude "_upstream_tmp" \
  --exclude "deploy/zero-build/out" \
  "$REPO_ROOT/" \
  "$STAGE_DIR/deer-flow/"

mkdir -p "$STAGE_DIR/deer-flow/deploy/zero-build/out/images"
cp "$IMG_DIR/deerflow-images.tar" "$STAGE_DIR/deer-flow/deploy/zero-build/out/images/deerflow-images.tar"

tar -czf "$OUT_DIR/deerflow-bundle.tgz" -C "$STAGE_DIR" deer-flow

echo "$OUT_DIR/deerflow-bundle.tgz"
