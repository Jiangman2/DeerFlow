"""Generate a qualified, new delivery; run in an offline Linux test container."""
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
DEST = Path('/delivery/weknora-replicas-preflight-20261017-r1')
NAME = 'weknora-replicas-preflight-20261017-r1.py'


def main():
    if sys.platform != 'linux' or not Path('/.dockerenv').exists():
        raise RuntimeError('offline_linux_test_container_required')
    if DEST.exists():
        raise RuntimeError('delivery_exists_no_overwrite')
    raw = (HERE / 'inventory.py').read_bytes()
    ast.parse(raw, feature_version=(3, 7))
    stream = io.StringIO()
    suite = unittest.defaultTestLoader.loadTestsFromName('test_inventory')
    with contextlib.redirect_stdout(stream):
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    if not result.wasSuccessful() or result.skipped:
        print(stream.getvalue())
        raise RuntimeError('qualification_failed_or_skipped')
    sha = hashlib.sha256(raw).hexdigest()
    command = '''sudo bash -se <<'BASH'
set -Eeuo pipefail
umask 077
test "$(hostname -s)" = datax-01
SCRIPT=/home/cnooc/SCRIPT_NAME
test -f "$SCRIPT" && test ! -L "$SCRIPT"
CONTROL=$(mktemp -d /var/lib/weknora-replicas-preflight-XXXXXXXX)
install -o root -g root -m 0600 "$SCRIPT" "$CONTROL/check.py"
test "$(sha256sum "$CONTROL/check.py" | awk '{print $1}')" = SCRIPT_SHA
echo "check_log=$CONTROL/check.log"
set +e
python3 -I -B -u "$CONTROL/check.py" 2>&1 | tee "$CONTROL/check.log"
RESULT=("${PIPESTATUS[@]}")
set -e
echo "check_exit_code=${RESULT[0]} tee_exit_code=${RESULT[1]}"
echo "check_log=$CONTROL/check.log"
test "${RESULT[0]}" -eq 0
test "${RESULT[1]}" -eq 0
BASH
'''.replace('SCRIPT_NAME', NAME).replace('SCRIPT_SHA', sha)
    syntax = subprocess.run(['bash', '-n'], input=command.encode(), stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, timeout=5)
    if syntax.returncode:
        raise RuntimeError('launcher_syntax_failed')
    proof = dict(script=NAME, sha256=sha, python37_grammar='PASS', linux_tests=result.testsRun,
                 skipped_tests=0, launcher_bash_syntax='PASS', network='DISABLED_BY_DOCKER',
                 production_execution='NOT_PERFORMED', database_connections=0,
                 production_writes=0, provider_requests=0, ready_to_scale=False)
    contents = {NAME: raw, 'RUN-datax01.txt': command.encode(),
                'RUNBOOK.txt': (HERE / 'README.md').read_bytes(),
                'LOCAL-TESTS.txt': stream.getvalue().encode(),
                'VERIFICATION.json': (json.dumps(proof, indent=2) + '\n').encode()}
    DEST.mkdir(mode=0o700)
    for name, data in contents.items():
        with (DEST / name).open('xb') as out:
            out.write(data)
    with (DEST / 'SHA256SUMS').open('x') as out:
        for name, data in sorted(contents.items()):
            out.write(hashlib.sha256(data).hexdigest() + '  ' + name + '\n')
    print(json.dumps(proof))


if __name__ == '__main__':
    main()
