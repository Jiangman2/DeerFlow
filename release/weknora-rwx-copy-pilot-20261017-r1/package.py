"""Offline Linux tests, exact-artifact qualification, exclusive delivery."""
import ast
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
DEST = Path('/delivery/weknora-rwx-copy-pilot-20261017-r1')
NAME = 'weknora-rwx-copy-pilot-20261017-r1.py'


def launcher(digest):
    return '''sudo bash -se <<'BASH'
set -Eeuo pipefail
umask 077
test "$(hostname -s)" = datax-03
SCRIPT=/home/cnooc/SCRIPT_NAME
test -f "$SCRIPT" && test ! -L "$SCRIPT"
CONTROL=$(mktemp -d /var/lib/weknora-copy-pilot-tool-XXXXXXXX)
install -o root -g root -m 0600 "$SCRIPT" "$CONTROL/pilot.py"
test "$(sha256sum "$CONTROL/pilot.py" | awk '{print $1}')" = SCRIPT_SHA
echo "launcher_log=$CONTROL/start.log"
set +e
python3 -I -B -u "$CONTROL/pilot.py" --start-once 2>&1 | tee "$CONTROL/start.log"
RESULT=("${PIPESTATUS[@]}")
set -e
echo "start_exit_code=${RESULT[0]} tee_exit_code=${RESULT[1]}"
echo 'status_command=sudo python3 -I -B /var/lib/weknora-rwx-copy-pilot-20261017-r1/pilot.py --status'
test "${RESULT[0]}" -eq 0
test "${RESULT[1]}" -eq 0
BASH
'''.replace('SCRIPT_NAME', NAME).replace('SCRIPT_SHA', digest)


def main():
    if sys.platform != 'linux' or not Path('/.dockerenv').exists() or DEST.exists():
        raise RuntimeError('offline_linux_and_fresh_delivery_required')
    source = (HERE/'pilot.py').read_bytes()
    ast.parse(source, feature_version=(3, 7))
    capture = io.StringIO()
    with contextlib.redirect_stdout(capture):
        first = unittest.TextTestRunner(stream=capture, verbosity=2).run(
            unittest.defaultTestLoader.loadTestsFromName('test_pilot'))
    if not first.wasSuccessful() or first.skipped:
        print(capture.getvalue()); raise RuntimeError('source_tests_failed')
    import test_pilot
    with tempfile.TemporaryDirectory(dir='/qualify') as td:
        file = Path(td)/NAME
        file.write_bytes(source)
        spec = importlib.util.spec_from_file_location('delivered_pilot', file)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with patch.object(test_pilot, 'm', module), contextlib.redirect_stdout(capture):
            second = unittest.TextTestRunner(stream=capture, verbosity=2).run(
                unittest.defaultTestLoader.loadTestsFromModule(test_pilot))
        if not second.wasSuccessful() or second.skipped:
            print(capture.getvalue()); raise RuntimeError('delivered_tests_failed')
        help_run = subprocess.run([sys.executable, '-I', '-B', str(file), '--help'],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
        if help_run.returncode or b'--start-once' not in help_run.stdout or b'--status' not in help_run.stdout:
            raise RuntimeError('exact_cli_help_failed')
    digest = hashlib.sha256(source).hexdigest()
    run = launcher(digest).encode()
    syntax = subprocess.run(['bash', '-n'], input=run, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, timeout=5)
    if syntax.returncode: raise RuntimeError('launcher_syntax_failed')
    verification = dict(script=NAME, sha256=digest, source_tests=first.testsRun,
                        exact_delivery_tests=second.testsRun, skipped=0, python37_grammar='PASS',
                        launcher_bash_syntax='PASS', cli_help='PASS',
                        test_network='DISABLED', real_linux_uid1000_fixture_copy='PASS',
                        nfs_systemd_mount_integration='NOT_TESTED', readonly_mount_test='MOCKED',
                        actual_business_copy='NOT_PERFORMED_LOCALLY', production_execution='NOT_PERFORMED',
                        ready_to_migrate=False, ready_to_scale=False)
    files = {NAME: source, 'RUN-DATAX03.txt': run, 'RUNBOOK.txt': (HERE/'README.md').read_bytes(),
             'VERIFICATION.json': (json.dumps(verification, indent=2)+'\n').encode(),
             'LOCAL-TESTS.txt': capture.getvalue().encode()}
    DEST.mkdir()
    for name, data in files.items():
        with (DEST/name).open('xb') as f: f.write(data)
    with (DEST/'SHA256SUMS').open('x') as f:
        for name, data in sorted(files.items()):
            f.write(hashlib.sha256(data).hexdigest()+'  '+name+'\n')
    print(json.dumps(verification))


if __name__ == '__main__':
    main()
