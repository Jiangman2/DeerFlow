#!/usr/bin/env bash
# Fixed read-only runtime collection, then local /opt/DeerFlow source evidence.
# Both collector checksums are frozen and verified before any collection.
set -Eeuo pipefail
umask 077

test "$(id -u)" -eq 0
test "$(hostname -s)" = datax-01
OPERATOR_GROUP=$(id -gn cnooc)

BASELINE_SOURCE=/home/cnooc/portal-ppt-baseline-20261017-r2.py
DIRECTORY_SOURCE=/home/cnooc/portal-source-directory-20261017-r2.py
test -f "$BASELINE_SOURCE" && test ! -L "$BASELINE_SOURCE"
test -f "$DIRECTORY_SOURCE" && test ! -L "$DIRECTORY_SOURCE"
CONTROL=$(mktemp -d /var/lib/portal-ppt-refresh-tool-XXXXXXXX)
install -o root -g root -m 0600 -- "$BASELINE_SOURCE" "$CONTROL/baseline.py"
install -o root -g root -m 0600 -- "$DIRECTORY_SOURCE" "$CONTROL/source.py"
printf '%s  %s\n' \
  ac6d909f579bddef02675eb248b662eba44c5b5eba1cdd65a24bf7889100a92b \
  "$CONTROL/baseline.py" | sha256sum --check --status
printf '%s  %s\n' \
  f8208e0add5eeaf91b219851279ad4d75809d4826289066f20c0e0fa47011ba5 \
  "$CONTROL/source.py" | sha256sum --check --status

printf '%s\n' 'refresh_phase=current_runtime_baseline'
set +e
python3 -I -B -u "$CONTROL/baseline.py" --collect --out "$CONTROL/baseline"
BASELINE_EXIT=$?
set -e
SOURCE_EXIT=NOT_RUN
FINAL_EXIT=$BASELINE_EXIT
if test "$BASELINE_EXIT" -eq 0; then
  printf '%s\n' 'refresh_phase=local_source_directory'
  set +e
  python3 -I -B -u "$CONTROL/source.py" --collect \
    --baseline "$CONTROL/baseline/REPORT.json" --out "$CONTROL/source"
  SOURCE_EXIT=$?
  set -e
  FINAL_EXIT=$SOURCE_EXIT
fi

printf 'baseline_exit_code=%s\nsource_exit_code=%s\nready_for_build=false\nready_for_rollout=false\n' \
  "$BASELINE_EXIT" "$SOURCE_EXIT" > "$CONTROL/STATUS.txt"
EVIDENCE_FILES=(STATUS.txt)
for relative in baseline/REPORT.json source/REPORT.json source/SOURCE-EVIDENCE.tar.gz; do
  if test -f "$CONTROL/$relative" && test ! -L "$CONTROL/$relative"; then
    EVIDENCE_FILES+=("$relative")
  fi
done
# All archived names are fixed above; no logs, configuration, or arbitrary paths.
tar -C "$CONTROL" -czf "$CONTROL/EVIDENCE.tar.gz" -- "${EVIDENCE_FILES[@]}"
chmod 0600 "$CONTROL/EVIDENCE.tar.gz"
EXPORT=$(mktemp -d /home/cnooc/portal-ppt-refresh-20261017-r2-XXXXXXXX)
install -o cnooc -g "$OPERATOR_GROUP" -m 0600 -- \
  "$CONTROL/EVIDENCE.tar.gz" "$EXPORT/EVIDENCE.tar.gz"
chown -- "cnooc:$OPERATOR_GROUP" "$EXPORT"
printf 'download_evidence=%s\n' "$EXPORT/EVIDENCE.tar.gz"
sha256sum -- "$EXPORT/EVIDENCE.tar.gz"
printf 'baseline_exit_code=%s\nsource_exit_code=%s\ncontrol_directory=%s\n' \
  "$BASELINE_EXIT" "$SOURCE_EXIT" "$CONTROL"
printf '%s\n' 'Return EVIDENCE.tar.gz and the terminal summary. Preserve failures for review; do not rerun automatically.'
exit "$FINAL_EXIT"
