#!/bin/bash
set -Eeuo pipefail
umask 077
test "$(id -u)" = 0
test "$(hostname -s)" = datax-01
test "$(uname -m)" = aarch64
unset PYTHONPATH PYTHONHOME
cd /home/cnooc
PATCH=weknora-glm-sqlite-build-fix-20261016-r1.tar.gz
test -f "$PATCH" && test ! -L "$PATCH"
python3 -I -B - <<'PY'
import os,stat
for p in ('/','/opt'):
 s=os.lstat(p)
 assert stat.S_ISDIR(s.st_mode) and s.st_uid==0 and not s.st_mode & 0o022
assert os.stat('/opt').st_dev != os.stat('/').st_dev
assert not os.path.lexists('/var/lib/weknora-glm-sqlite-fix-20261016-r1'), 'Previous attempt exists: use its status command.'
PY
ROOT=$(mktemp -d /opt/weknora-glm-sqlite-fix-XXXXXXXX)
install -o root -g root -m 0600 "$PATCH" "$ROOT/patch.tar.gz"
test "$(sha256sum "$ROOT/patch.tar.gz" | awk '{print $1}')" = @PATCH_SHA256@
python3 -I -B - "$ROOT" <<'PY'
import re,sys,tarfile
from pathlib import Path
root=Path(sys.argv[1]); target=root/'patch'; target.mkdir(mode=0o700)
seen=set(); total=0
with tarfile.open(str(root/'patch.tar.gz'),'r|gz') as t:
 for m in t:
  assert m.isfile() and not m.issparse() and not m.linkname
  assert re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.+-]+',m.name) and m.name not in seen
  assert len(seen)<20 and 0<m.size<4*1024**2
  seen.add(m.name); total+=m.size; assert total<8*1024**2
  raw=t.extractfile(m).read(); assert len(raw)==m.size
  with (target/m.name).open('xb') as f: f.write(raw)
  (target/m.name).chmod(0o600)
PY
cd "$ROOT/patch"
sha256sum -c PACKAGE.SHA256
echo "repair_status_command=sudo python3 -I -B $ROOT/patch/repair_sqlite_build.py --status"
python3 -I -B -u ./repair_sqlite_build.py --output "$ROOT/rebuild"
cd "$ROOT/rebuild/package"
python3 -E -s -B -u ./build_weknora.py --output "$ROOT/rebuild/build1" --check-only
echo "build_log=$ROOT/build.log"
set +e
python3 -E -s -B -u ./build_weknora.py --output "$ROOT/rebuild/build1" --build-only-ack YES 2>&1 | tee "$ROOT/build.log"
RESULT=("${PIPESTATUS[@]}")
set -e
echo "build_exit_code=${RESULT[0]} tee_exit_code=${RESULT[1]}"
echo "build_log=$ROOT/build.log"
echo "expected_build_result=$ROOT/rebuild/build1/BUILD-RESULT.json"
echo "repair_status_command=sudo python3 -I -B $ROOT/patch/repair_sqlite_build.py --status"
test "${RESULT[0]}" -eq 0
test "${RESULT[1]}" -eq 0
