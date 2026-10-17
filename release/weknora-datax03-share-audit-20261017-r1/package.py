"""Qualify in an offline Linux container and create an exclusive delivery."""
import ast
import contextlib
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import unittest

HERE = Path(__file__).resolve().parent
DEST = Path('/delivery/weknora-datax03-share-audit-20261017-r1')
NAME = 'weknora-datax03-share-audit-20261017-r1.py'


def main():
    if sys.platform != 'linux' or not Path('/.dockerenv').exists() or DEST.exists():
        raise RuntimeError('linux_container_and_fresh_delivery_required')
    raw = (HERE / 'audit.py').read_bytes()
    ast.parse(raw, feature_version=(3, 7))
    stream = io.StringIO()
    suite = unittest.defaultTestLoader.loadTestsFromName('test_audit')
    with contextlib.redirect_stdout(stream):
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    if not result.wasSuccessful() or result.skipped:
        print(stream.getvalue())
        raise RuntimeError('local_qualification_failed')
    sha = hashlib.sha256(raw).hexdigest()
    launcher = '''sudo bash -se <<'BASH'
set -Eeuo pipefail
umask 077
test "$(hostname -s)" = datax-03
SCRIPT=/home/cnooc/SCRIPT_NAME
test -f "$SCRIPT" && test ! -L "$SCRIPT"
CONTROL=$(mktemp -d /var/lib/weknora-datax03-share-audit-XXXXXXXX)
install -o root -g root -m 0600 "$SCRIPT" "$CONTROL/audit.py"
test "$(sha256sum "$CONTROL/audit.py" | awk '{print $1}')" = SCRIPT_SHA
echo "audit_log=$CONTROL/audit.log"
set +e
python3 -I -B -u "$CONTROL/audit.py" 2>&1 | tee "$CONTROL/audit.log"
RESULT=("${PIPESTATUS[@]}")
set -e
echo "audit_exit_code=${RESULT[0]} tee_exit_code=${RESULT[1]}"
echo "audit_log=$CONTROL/audit.log"
test "${RESULT[0]}" -eq 0
test "${RESULT[1]}" -eq 0
BASH
'''.replace('SCRIPT_NAME', NAME).replace('SCRIPT_SHA', sha)
    check = subprocess.run(['bash', '-n'], input=launcher.encode(), stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=5)
    if check.returncode:
        raise RuntimeError('launcher_syntax_failed')
    proof = dict(script=NAME, sha256=sha, linux_synthetic_tests=result.testsRun,
                 skipped=0, python37_grammar='PASS', bash_syntax='PASS',
                 production_execution='NOT_PERFORMED', real_nfs_access='NOT_TESTED',
                 test_network='DISABLED_BY_CONTAINER_LAUNCH', ready_to_scale=False)
    files = {NAME: raw, 'RUN-datax03.txt': launcher.encode(),
             'RUNBOOK.txt': (HERE / 'README.md').read_bytes(),
             'LOCAL-TESTS.txt': stream.getvalue().encode(),
             'VERIFICATION.json': (json.dumps(proof, indent=2) + '\n').encode()}
    DEST.mkdir()
    for name, data in files.items():
        with (DEST / name).open('xb') as out:
            out.write(data)
    with (DEST / 'SHA256SUMS').open('x') as out:
        for name, data in sorted(files.items()):
            out.write(hashlib.sha256(data).hexdigest() + '  ' + name + '\n')
    print(json.dumps(proof))


if __name__ == '__main__':
    main()
