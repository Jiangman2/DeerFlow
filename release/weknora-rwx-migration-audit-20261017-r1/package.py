"""Offline Linux qualification and exclusive operator delivery."""
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
DEST = Path('/delivery/weknora-rwx-migration-audit-20261017-r1')
NAME = 'weknora-rwx-migration-audit-20261017-r1.py'


def launcher(host, sha):
    return '''sudo bash -se <<'BASH'
set -Eeuo pipefail
umask 077
test "$(hostname -s)" = HOST_NAME
SCRIPT=/home/cnooc/SCRIPT_NAME
test -f "$SCRIPT" && test ! -L "$SCRIPT"
CONTROL=$(mktemp -d /var/lib/weknora-migration-audit-tool-XXXXXXXX)
install -o root -g root -m 0600 "$SCRIPT" "$CONTROL/audit.py"
test "$(sha256sum "$CONTROL/audit.py" | awk '{print $1}')" = SCRIPT_SHA
echo "audit_log=$CONTROL/audit.log"
set +e
python3 -I -B -u "$CONTROL/audit.py" --collect 2>&1 | tee "$CONTROL/audit.log"
RESULT=("${PIPESTATUS[@]}")
set -e
echo "audit_exit_code=${RESULT[0]} tee_exit_code=${RESULT[1]}"
echo "audit_log=$CONTROL/audit.log"
test "${RESULT[0]}" -eq 0
test "${RESULT[1]}" -eq 0
BASH
'''.replace('HOST_NAME', host).replace('SCRIPT_NAME', NAME).replace('SCRIPT_SHA', sha)


def main():
    if sys.platform != 'linux' or not Path('/.dockerenv').exists() or DEST.exists():
        raise RuntimeError('offline_linux_and_fresh_delivery_required')
    raw = (HERE/'audit.py').read_bytes()
    ast.parse(raw, feature_version=(3, 7))
    stream = io.StringIO()
    with contextlib.redirect_stdout(stream):
        source = unittest.TextTestRunner(stream=stream, verbosity=2).run(
            unittest.defaultTestLoader.loadTestsFromName('test_audit'))
    if not source.wasSuccessful() or source.skipped:
        print(stream.getvalue())
        raise RuntimeError('source_tests_failed')
    import test_audit
    with tempfile.TemporaryDirectory(dir='/qualify') as td:
        p = Path(td)/NAME
        p.write_bytes(raw)
        spec = importlib.util.spec_from_file_location('delivered_audit', p)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with patch.object(test_audit, 'm', module), contextlib.redirect_stdout(stream):
            delivered = unittest.TextTestRunner(stream=stream, verbosity=2).run(
                unittest.defaultTestLoader.loadTestsFromModule(test_audit))
        if not delivered.wasSuccessful() or delivered.skipped:
            print(stream.getvalue())
            raise RuntimeError('delivered_tests_failed')
        checked = subprocess.run([sys.executable, '-I', '-B', str(p), '--help'],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
        if checked.returncode or b'--collect' not in checked.stdout or b'--status' not in checked.stdout:
            raise RuntimeError('cli_help_failed')
    sha = hashlib.sha256(raw).hexdigest()
    launches = {}
    for host in ('datax-01', 'datax-03'):
        text = launcher(host, sha)
        checked = subprocess.run(['bash', '-n'], input=text.encode(),
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
        if checked.returncode:
            raise RuntimeError('bash_syntax_failed')
        launches['RUN-' + host.replace('-', '').upper() + '.txt'] = text.encode()
    proof = dict(script=NAME, sha256=sha, source_tests=source.testsRun, delivered_tests=delivered.testsRun,
                 skipped=0, python37_grammar='PASS', bash_syntax='PASS', exact_cli_help='PASS',
                 test_network='DISABLED_BY_DOCKER', real_linux_metadata_fixtures='PASS', kubernetes_api='MOCKED',
                 production_execution='NOT_PERFORMED', business_copy_executed=False,
                 ready_to_copy=False, ready_to_migrate=False, ready_to_scale=False)
    files = {NAME: raw, 'RUNBOOK.txt': (HERE/'README.md').read_bytes(),
             'LOCAL-TESTS.txt': stream.getvalue().encode(),
             'VERIFICATION.json': (json.dumps(proof, indent=2) + '\n').encode(), **launches}
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
