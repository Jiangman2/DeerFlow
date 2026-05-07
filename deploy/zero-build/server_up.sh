#!/usr/bin/env bash
set -euo pipefail

cat >&2 <<'MSG'
[DEPRECATED] deploy/zero-build/server_up.sh has been retired.
This script starts legacy prebuilt-tag services and is no longer aligned with docker/docker-compose.yaml.

Use:
  docker load -i <your_images.tar>
  docker compose -f docker/docker-compose.yaml --env-file .env up -d
MSG

exit 1
