"""Offline Linux qualification and an exclusive new operator delivery."""
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
DEST = Path('/delivery/weknora-existing-nfs-test-20261017-r1')
NAME = 'weknora-existing-nfs-stage-20261017-r1.py'


def main():
    if sys.platform != 'linux' or not Path('/.dockerenv').exists() or DEST.exists():
        raise RuntimeError('offline_linux_container_and_fresh_delivery_required')
    raw = (HERE / 'stage_nfs.py').read_bytes()
    ast.parse(raw, feature_version=(3, 7))
    stream = io.StringIO()
    suite = unittest.defaultTestLoader.loadTestsFromName('test_stage_nfs')
    with contextlib.redirect_stdout(stream):
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    if not result.wasSuccessful() or result.skipped:
        print(stream.getvalue())
        raise RuntimeError('qualification_failed')
    sha = hashlib.sha256(raw).hexdigest()
    launcher = '''sudo bash -se <<'BASH'
set -Eeuo pipefail
umask 077
test "$(hostname -s)" = ecs-zdz-pro-0000
SCRIPT=/home/cnooc/SCRIPT_NAME
test -f "$SCRIPT" && test ! -L "$SCRIPT"
CONTROL=$(mktemp -d /var/lib/weknora-existing-nfs-tool-XXXXXXXX)
install -o root -g root -m 0600 "$SCRIPT" "$CONTROL/stage.py"
test "$(sha256sum "$CONTROL/stage.py" | awk '{print $1}')" = SCRIPT_SHA
echo "status_command=sudo python3 -I -B $CONTROL/stage.py --status"
echo "stage_log=$CONTROL/stage.log"
set +e
python3 -I -B -u "$CONTROL/stage.py" --create-once 2>&1 | tee "$CONTROL/stage.log"
RESULT=("${PIPESTATUS[@]}")
set -e
echo "stage_exit_code=${RESULT[0]} tee_exit_code=${RESULT[1]}"
echo "stage_log=$CONTROL/stage.log"
echo "status_command=sudo python3 -I -B $CONTROL/stage.py --status"
test "${RESULT[0]}" -eq 0
test "${RESULT[1]}" -eq 0
BASH
'''.replace('SCRIPT_NAME', NAME).replace('SCRIPT_SHA', sha)
    checked = subprocess.run(['bash', '-n'], input=launcher.encode(),
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
    if checked.returncode:
        raise RuntimeError('launcher_syntax_failed')
    proof = dict(script=NAME, sha256=sha, linux_synthetic_tests=result.testsRun,
                 skipped=0, python37_grammar='PASS', bash_syntax='PASS',
                 real_exportfs_invocations=0, production_execution='NOT_PERFORMED',
                 actual_nfs_and_cross_node_io='NOT_TESTED', test_network='DISABLED_BY_DOCKER',
                 scope='NEW_EMPTY_DIRECTORIES_AND_FOUR_EXACT_EXPORTS_ONLY',
                 ready_to_migrate=False, ready_to_scale=False)
    files = {NAME: raw, 'RUN-NFS.txt': launcher.encode(),
             'RUNBOOK.txt': (HERE/'README.md').read_bytes(),
             'LOCAL-TESTS.txt': stream.getvalue().encode(),
             'VERIFICATION.json': (json.dumps(proof, indent=2)+'\n').encode()}
    DEST.mkdir()
    for name, data in files.items():
        with (DEST/name).open('xb') as stream:
            stream.write(data)
    with (DEST/'SHA256SUMS').open('x') as stream:
        for name, data in sorted(files.items()):
            stream.write(hashlib.sha256(data).hexdigest()+'  '+name+'\n')
    print(json.dumps(proof))


if __name__ == '__main__':
    main()
