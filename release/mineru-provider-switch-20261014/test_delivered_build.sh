#!/bin/sh
# Disposable Linux container ONLY. /opt is a fresh tmpfs, /delivery is read-only.
set -eu
umask 077
test "$(id -u)" = 0
root=$(mktemp -d /opt/mineru-build-test-XXXXXXXX)
mkdir -m 700 "$root/package"
tar --no-same-owner --no-same-permissions -xzf /delivery/deerflow-mineru-dedicated-async-build-20261015-r1-fix1.tar.gz -C "$root/package"
cd "$root/package"
sha256sum -c PACKAGE.SHA256
export MINERU_BUILD_BUNDLE="$root/package"
/app/backend/.venv/bin/python -B /tests/test_build.py
/app/backend/.venv/bin/python -B ./candidate_probe.py ./payload-adapter.py
/app/backend/.venv/bin/python -B ./build_mineru.py --help
printf 'delivered_build_package=PASS production_build_started=no provider_requests=0\n'
