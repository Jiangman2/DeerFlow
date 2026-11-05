#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
test "$(hostname -s)" = datax-01
test "$(id -u)" -eq 0
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
sha256sum -c PACKAGE.SHA256
EVIDENCE=$(mktemp -d /run/portal-acceptance-evidence-XXXXXXXX)
echo "acceptance_evidence=$EVIDENCE"
set +e
python3 -B -u ./run-acceptance.py --output "$EVIDENCE/RESULT.json" "$@" 2>&1 | tee "$EVIDENCE/acceptance.log"
RESULT=("${PIPESTATUS[@]}")
set -e
echo "collection_exit_code=${RESULT[0]} tee_exit_code=${RESULT[1]}"
# Reports contain diagnostics only. Explicitly export copies, never permissions
# on the private directory or production configuration.
if test -f "$EVIDENCE/RESULT.json" && test ! -L "$EVIDENCE/RESULT.json"; then
  SAFE=$(mktemp /home/cnooc/portal-acceptance-result-XXXXXXXX.json)
  install -o cnooc -g cnooc -m 0640 "$EVIDENCE/RESULT.json" "$SAFE"
  echo "safe_result=$SAFE"
fi
SAFELOG=$(mktemp /home/cnooc/portal-acceptance-log-XXXXXXXX.log)
install -o cnooc -g cnooc -m 0640 "$EVIDENCE/acceptance.log" "$SAFELOG"
echo "safe_log=$SAFELOG"
test "${RESULT[0]}" -eq 0
test "${RESULT[1]}" -eq 0
