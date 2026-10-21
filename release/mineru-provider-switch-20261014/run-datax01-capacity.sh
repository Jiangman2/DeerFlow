#!/usr/bin/env bash
# Read-only cluster inventory; only new local evidence/export files are written.
set -Eeuo pipefail
umask 077
test "$(id -u)" -eq 0
test "$(hostname -s)" = datax-01
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
test -f PACKAGE.SHA256 && test ! -L PACKAGE.SHA256
sha256sum -c PACKAGE.SHA256
test -d /home/cnooc && test ! -L /home/cnooc
id cnooc >/dev/null

LOG=$(mktemp "$PWD/capacity-XXXXXXXX.log")
printf 'capacity_log=%s\n' "$LOG"
set +e
python3 -B -u ./audit_capacity.py 2>&1 | tee "$LOG"
RESULT=("${PIPESTATUS[@]}")
set -e
printf 'capacity_exit_code=%s tee_exit_code=%s\n' "${RESULT[0]}" "${RESULT[1]}"

# Export only the sanitized log and (when present) sanitized report. Do not
# loosen permissions on any existing production/evidence directory.
DOWNLOAD=$(mktemp -d /home/cnooc/mineru-capacity-download-XXXXXXXX)
install -o cnooc -g cnooc -m 0640 "$LOG" "$DOWNLOAD/capacity.log"
if test "${RESULT[0]}" -eq 0 && test "${RESULT[1]}" -eq 0; then
  REPORT=$(python3 -B - "$LOG" <<'PY'
import hashlib, json, stat, sys
from pathlib import Path
receipts = []
for line in Path(sys.argv[1]).read_text(encoding='utf-8').splitlines():
    try:
        row = json.loads(line)
    except ValueError:
        continue
    if row.get('inventory') == 'COMPLETE' and 'report' in row:
        receipts.append(row)
assert len(receipts) == 1, 'unique_report_required'
row = receipts[0]
p = Path(row['report'])
assert p.name == 'RESULT.json' and p.parent.parent == Path('/var/lib')
assert p.parent.name.startswith('mineru-capacity-') and p.resolve() == p
for d in (Path('/'), Path('/var'), Path('/var/lib'), p.parent):
    s = d.lstat()
    assert stat.S_ISDIR(s.st_mode) and s.st_uid == 0 and not s.st_mode & 0o022
s = p.lstat()
assert stat.S_ISREG(s.st_mode) and s.st_uid == 0 and s.st_nlink == 1
assert not s.st_mode & 0o022 and s.st_size <= 4194304
raw = p.read_bytes()
assert hashlib.sha256(raw).hexdigest() == row['report_sha256']
data = json.loads(raw)
assert data['provider_requests'] == 0 and data['production_workloads_mutated'] is False
print(str(p))
PY
  )
  install -o cnooc -g cnooc -m 0640 "$REPORT" "$DOWNLOAD/RESULT.json"
fi
chown cnooc:cnooc "$DOWNLOAD"
chmod 0700 "$DOWNLOAD"
printf 'download_directory=%s\n' "$DOWNLOAD"
printf 'diagnostic_files_only=yes production_configuration_modified=no provider_requests=0\n'
test "${RESULT[0]}" -eq 0
test "${RESULT[1]}" -eq 0
