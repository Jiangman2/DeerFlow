"""Run offline qualification, inject the probe, test that exact executable, deliver."""
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
DEST = Path('/delivery/weknora-rwx-crossnode-20261017-r1')
NAME = 'weknora-rwx-crossnode-20261017-r1.py'


def main():
    if sys.platform != 'linux' or not Path('/.dockerenv').exists() or DEST.exists():
        raise RuntimeError('offline_linux_and_fresh_delivery_required')
    probe = (HERE/'probe.py').read_text()
    controller = (HERE/'controller.py').read_text()
    marker = 'PROBE_SOURCE = None  # Replaced with reviewed source by the offline packager.'
    if controller.count(marker) != 1:
        raise RuntimeError('injection_marker_changed')
    raw = controller.replace(marker, 'PROBE_SOURCE = ' + repr(probe)).encode()
    for data in (raw, probe.encode()):
        ast.parse(data, feature_version=(3, 7))
    stream = io.StringIO()
    suite = unittest.defaultTestLoader.loadTestsFromNames(['test_controller', 'test_probe'])
    with contextlib.redirect_stdout(stream):
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    if not result.wasSuccessful() or result.skipped:
        print(stream.getvalue())
        raise RuntimeError('qualification_failed')
    # Test the delivered controller, not just the pre-injection source.
    import controller as original
    import test_controller as tests
    with tempfile.TemporaryDirectory(dir='/qualify') as td:
        script = Path(td)/NAME
        script.write_bytes(raw)
        spec = importlib.util.spec_from_file_location('delivered', script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if module.PROBE_SOURCE != probe:
            raise RuntimeError('injected_probe_changed')
        with patch.object(tests, 'm', module), contextlib.redirect_stdout(stream):
            exact = unittest.TextTestRunner(stream=stream, verbosity=2).run(
                unittest.defaultTestLoader.loadTestsFromModule(tests))
        if not exact.wasSuccessful() or exact.skipped:
            print(stream.getvalue())
            raise RuntimeError('delivered_controller_failed')
        checked = subprocess.run([sys.executable, '-I', '-B', str(script), '--help'],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
        if checked.returncode or b'--verify-once' not in checked.stdout or b'--status' not in checked.stdout:
            raise RuntimeError('delivered_cli_help_failed')
    sha = hashlib.sha256(raw).hexdigest()
    launcher = '''sudo bash -se <<'BASH'
set -Eeuo pipefail
umask 077
test "$(hostname -s)" = datax-01
SCRIPT=/home/cnooc/SCRIPT_NAME
test -f "$SCRIPT" && test ! -L "$SCRIPT"
CONTROL=$(mktemp -d /var/lib/weknora-rwx-crossnode-tool-XXXXXXXX)
install -o root -g root -m 0600 "$SCRIPT" "$CONTROL/check.py"
test "$(sha256sum "$CONTROL/check.py" | awk '{print $1}')" = SCRIPT_SHA
echo "status_command=sudo python3 -I -B $CONTROL/check.py --status"
echo "check_log=$CONTROL/check.log"
set +e
python3 -I -B -u "$CONTROL/check.py" --verify-once 2>&1 | tee "$CONTROL/check.log"
RESULT=("${PIPESTATUS[@]}")
set -e
echo "check_exit_code=${RESULT[0]} tee_exit_code=${RESULT[1]}"
echo "check_log=$CONTROL/check.log"
echo "status_command=sudo python3 -I -B $CONTROL/check.py --status"
test "${RESULT[0]}" -eq 0
test "${RESULT[1]}" -eq 0
BASH
'''.replace('SCRIPT_NAME', NAME).replace('SCRIPT_SHA', sha)
    check = subprocess.run(['bash', '-n'], input=launcher.encode(),
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
    if check.returncode:
        raise RuntimeError('launcher_syntax_failed')
    proof = dict(script=NAME, sha256=sha, source_tests=result.testsRun,
                 delivered_controller_tests=exact.testsRun, skipped=0, python37_grammar='PASS',
                 bash_syntax='PASS', exact_cli_help='PASS', nfs_receipt_sha256=original.NFS_SHA,
                 real_linux_uid1000_io='PASS', kubernetes_api='MOCKED', nfs_mountinfo='MOCKED',
                 production_execution='NOT_PERFORMED', actual_cross_node_io='NOT_TESTED',
                 test_network='DISABLED_BY_DOCKER', ready_to_migrate=False, ready_to_scale=False)
    files = {NAME: raw, 'RUN-DATAX01.txt': launcher.encode(), 'RUNBOOK.txt': (HERE/'README.md').read_bytes(),
             'LOCAL-TESTS.txt': stream.getvalue().encode(),
             'VERIFICATION.json': (json.dumps(proof, indent=2) + '\n').encode()}
    DEST.mkdir()
    for name, data in files.items():
        with (DEST/name).open('xb') as f:
            f.write(data)
    with (DEST/'SHA256SUMS').open('x') as f:
        for name, data in sorted(files.items()):
            f.write(hashlib.sha256(data).hexdigest() + '  ' + name + '\n')
    print(json.dumps(proof))


if __name__ == '__main__':
    main()
