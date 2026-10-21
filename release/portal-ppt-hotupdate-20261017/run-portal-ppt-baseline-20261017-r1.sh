#!/usr/bin/env bash
# Read-only Kubernetes collection; only new local control/report files are written.
set -Eeuo pipefail
umask 077
test "$(id -u)" -eq 0
test "$(hostname -s)" = datax-01

SOURCE=/home/cnooc/portal-ppt-baseline-20261017-r1.py
test -f "$SOURCE" && test ! -L "$SOURCE"
OPERATOR_GROUP=$(id -gn cnooc)
CONTROL=$(mktemp -d /var/lib/portal-ppt-baseline-tool-XXXXXXXX)
install -o root -g root -m 0600 -- "$SOURCE" "$CONTROL/refresh.py"
printf '%s  %s\n' \
  9b4854c0505f29885df50086dbcad7cb3fbbe264a78ccdc9741391bc5ac72565 \
  "$CONTROL/refresh.py" | sha256sum --check --status

set +e
python3 -I -B -u "$CONTROL/refresh.py" --collect --out "$CONTROL/report"
COLLECTION_EXIT=$?
set -e

# Export only the reviewed, redacted report into a fresh private download directory.
# Keep the original root-owned result and every failure; never replace old evidence.
if test -f "$CONTROL/report/REPORT.json" && test ! -L "$CONTROL/report/REPORT.json"; then
  EXPORT=$(mktemp -d /home/cnooc/portal-ppt-baseline-20261017-XXXXXXXX)
  install -o cnooc -g "$OPERATOR_GROUP" -m 0600 -- \
    "$CONTROL/report/REPORT.json" "$EXPORT/REPORT.json"
  chown -- "cnooc:$OPERATOR_GROUP" "$EXPORT"
  printf 'download_report=%s\n' "$EXPORT/REPORT.json"
  sha256sum -- "$EXPORT/REPORT.json"
fi
printf 'collection_exit_code=%s\ncontrol_directory=%s\n' "$COLLECTION_EXIT" "$CONTROL"
printf '%s\n' 'Return the redacted REPORT.json and terminal summary. Do not rerun after failure before review.'
exit "$COLLECTION_EXIT"
