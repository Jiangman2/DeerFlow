#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
test "$(id -u)" -eq 0
test "$(hostname -s)" = datax-01
cd /home/cnooc
SCRIPT=mineru-bluegreen16-prepare-20261015-r1.py
test -f "$SCRIPT" && test ! -L "$SCRIPT"
CONTROL=$(mktemp -d /run/mineru-bluegreen16-prepare-XXXXXXXX)
install -o root -g root -m 0600 "$SCRIPT" "$CONTROL/prepare.py"
test "$(sha256sum "$CONTROL/prepare.py" | awk '{print $1}')" = \
  1064d935beb33a5e1dc2f741d1f9474fa4cfcc29ac17d6529d5dcca56e8292b4
LOG="$CONTROL/prepare.log"
set +e
python3 -I -B -u "$CONTROL/prepare.py" --prepare-only 2>&1 | tee "$LOG"
RESULT=("${PIPESTATUS[@]}")
set -e
printf 'prepare_exit_code=%s tee_exit_code=%s\n' "${RESULT[0]}" "${RESULT[1]}"
printf 'prepare_log=%s\n' "$LOG"
test "${RESULT[0]}" -eq 0
test "${RESULT[1]}" -eq 0
