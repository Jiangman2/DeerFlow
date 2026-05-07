#!/usr/bin/env bash
set -euo pipefail

cat >&2 <<'MSG'
[DEPRECATED] deploy/zero-build/build_bundle.sh has been retired.
This script used legacy `:prebuilt` image tags and is no longer aligned with docker/docker-compose.yaml.

Use the standard compose-based packaging flow instead:
  1) docker compose -f docker/docker-compose.yaml --env-file .env build gateway langgraph frontend
  2) docker save -o <output.tar> docker-gateway docker-langgraph docker-frontend nginx:alpine minio/minio:latest minio/mc:latest

Then deploy on server with:
  docker load -i <output.tar>
  docker compose -f docker/docker-compose.yaml --env-file .env up -d
MSG

exit 1
