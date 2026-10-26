#!/bin/bash
set -Eeuo pipefail
umask 077
test "$(id -u)" = 0
test "$(hostname -s)" = datax-01
test "$(uname -m)" = aarch64
unset PYTHONPATH PYTHONHOME
cd /home/cnooc
PACKAGE=weknora-glm-offline-build-20261016-r1.tar.gz
test -f "$PACKAGE" && test ! -L "$PACKAGE"
# Validate the build parent before creating or copying anything beneath it.
python3 -I -B - <<'PY'
import os,stat
for p in ('/','/opt'):
 s=os.lstat(p)
 assert stat.S_ISDIR(s.st_mode) and s.st_uid==0 and not s.st_mode & 0o022
assert os.stat('/opt').st_dev != os.stat('/').st_dev
v=os.statvfs('/opt')
assert v.f_bavail*v.f_frsize > max(20*1024**3,v.f_blocks*v.f_frsize//5)+65*1024**3
PY
ROOT=$(mktemp -d /opt/weknora-glm-build-XXXXXXXX)
install -o root -g root -m 0600 "$PACKAGE" "$ROOT/input.tar.gz"
test "$(sha256sum "$ROOT/input.tar.gz" | awk '{print $1}')" = @PACKAGE_SHA256@
python3 -I -B - "$ROOT" <<'PY'
import os,re,shutil,sys,tarfile
from pathlib import Path
root=Path(sys.argv[1]); target=root/'package'; target.mkdir(mode=0o700)
seen=set(); total=0
with tarfile.open(str(root/'input.tar.gz'),'r|gz') as t:
 for m in t:
  assert m.isfile() and not m.issparse() and not m.linkname
  assert re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]+',m.name) and m.name not in seen
  assert len(seen)<80 and 0<m.size<4*1024**3
  seen.add(m.name); total+=m.size; assert total<6*1024**3
  with t.extractfile(m) as inp,(target/m.name).open('xb') as out:
   shutil.copyfileobj(inp,out,1024**2)
  (target/m.name).chmod(0o600)
assert {'PACKAGE.SHA256','BUILD-PLAN.json','build_weknora.py','context.tar','builder.oci.tar'} <= seen
PY
cd "$ROOT/package"
sha256sum -c PACKAGE.SHA256
echo "build_directory=$ROOT"
echo "build_log=$ROOT/build.log"
set +e
python3 -E -s -B -u ./build_weknora.py --output "$ROOT/build1" --build-only-ack YES 2>&1 | tee "$ROOT/build.log"
RESULT=("${PIPESTATUS[@]}")
set -e
echo "build_exit_code=${RESULT[0]} tee_exit_code=${RESULT[1]}"
echo "build_log=$ROOT/build.log"
echo "expected_build_result=$ROOT/build1/BUILD-RESULT.json"
test "${RESULT[0]}" -eq 0
test "${RESULT[1]}" -eq 0
