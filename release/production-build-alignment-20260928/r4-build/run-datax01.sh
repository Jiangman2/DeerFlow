#!/usr/bin/env bash
set -Eeuo pipefail
test "$(hostname -s)" = datax-01
test "$(id -u)" -eq 0
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
sha256sum -c PACKAGE.SHA256
exec python3 -B ./build_candidates.py "$@"
