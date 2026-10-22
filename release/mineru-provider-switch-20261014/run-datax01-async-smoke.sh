#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
test "$(id -u)" -eq 0
test "$(hostname -s)" = datax-01
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
sha256sum -c PACKAGE.SHA256
test -d /home/cnooc && test ! -L /home/cnooc
id cnooc >/dev/null
LOG=$(mktemp "$PWD/async-smoke-XXXXXXXX.log")
printf 'async_smoke_tool=%s/probe_async.py\n' "$PWD"
printf 'async_smoke_log=%s\n' "$LOG"
set +e
python3 -B -u ./probe_async.py --probe-once 2>&1 | tee "$LOG"
RESULT=("${PIPESTATUS[@]}")
set -e
printf 'probe_exit_code=%s tee_exit_code=%s\n' "${RESULT[0]}" "${RESULT[1]}"
DOWNLOAD=$(mktemp -d /home/cnooc/mineru-async-smoke-download-XXXXXXXX)
install -o cnooc -g cnooc -m 0640 "$LOG" "$DOWNLOAD/async-smoke.log"
REPORT=$(python3 -B - "$LOG" <<'PY'
import hashlib, json, stat, sys
from pathlib import Path
records = []
for line in Path(sys.argv[1]).read_text(encoding='utf-8').splitlines():
    try:
        row = json.loads(line)
    except ValueError:
        continue
    if isinstance(row, dict) and 'report' in row and 'report_sha256' in row:
        records.append(row)
if not records:
    print('')
    sys.exit(0)
assert len(records) == 1, 'unique_report_required'
row = records[0]
p = Path(row['report'])
assert p.name == 'RESULT.json' and p.parent.parent == Path('/var/lib')
assert p.parent.name.startswith('mineru-async-smoke-') and p.resolve() == p
for d in (Path('/'), Path('/var'), Path('/var/lib'), p.parent):
    s = d.lstat()
    assert stat.S_ISDIR(s.st_mode) and s.st_uid == 0 and not s.st_mode & 0o022
s = p.lstat()
assert stat.S_ISREG(s.st_mode) and s.st_uid == 0 and s.st_nlink == 1
assert not s.st_mode & 0o022 and s.st_size <= 1048576
raw = p.read_bytes()
assert hashlib.sha256(raw).hexdigest() == row['report_sha256']
data = json.loads(raw)
assert data['production_workloads_mutated'] is False and data['credentials_saved'] is False
print(str(p))
PY
)
if test -n "$REPORT"; then
  install -o cnooc -g cnooc -m 0640 "$REPORT" "$DOWNLOAD/RESULT.json"
fi
chown cnooc:cnooc "$DOWNLOAD"
chmod 0700 "$DOWNLOAD"
printf 'download_directory=%s\n' "$DOWNLOAD"
printf 'uncertain_outcome_action=USE_STATUS_DO_NOT_REPEAT_PROBE\n'
test "${RESULT[0]}" -eq 0
test "${RESULT[1]}" -eq 0
