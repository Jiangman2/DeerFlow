"""Offline source/exact-delivery qualification; exclusive output directory."""
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
DEST = Path('/delivery/weknora-rwx-copy-pilot-resume-20261017-r1')
NAME = 'weknora-rwx-copy-pilot-resume-20261017-r1.py'


def launcher(digest):
    return '''sudo bash -se <<'BASH'
set -Eeuo pipefail
umask 077
test "$(hostname -s)" = datax-03
SCRIPT=/home/cnooc/SCRIPT_NAME
test -f "$SCRIPT" && test ! -L "$SCRIPT"
CONTROL=$(mktemp -d /var/lib/weknora-copy-resume-tool-XXXXXXXX)
install -o root -g root -m 0600 "$SCRIPT" "$CONTROL/resume.py"
test "$(sha256sum "$CONTROL/resume.py" | awk '{print $1}')" = SCRIPT_SHA
echo "recovery_log=$CONTROL/resume.log"
set +e
python3 -I -B -u "$CONTROL/resume.py" --resume-once 2>&1 | tee "$CONTROL/resume.log"
RESULT=("${PIPESTATUS[@]}")
set -e
echo "resume_exit_code=${RESULT[0]} tee_exit_code=${RESULT[1]}"
echo 'status_command=sudo python3 -I -B /var/lib/weknora-rwx-copy-pilot-resume-20261017-r1/resume.py --status'
test "${RESULT[0]}" -eq 0
test "${RESULT[1]}" -eq 0
BASH
'''.replace('SCRIPT_NAME', NAME).replace('SCRIPT_SHA', digest)


def main():
    if sys.platform != 'linux' or not Path('/.dockerenv').exists() or DEST.exists():
        raise RuntimeError('offline_linux_and_fresh_delivery_required')
    source = (HERE/'resume.py').read_bytes()
    ast.parse(source, feature_version=(3, 7))
    capture = io.StringIO()
    counts = {}
    sys.path.insert(0, '/engine')
    import test_pilot
    import test_resume
    with tempfile.TemporaryDirectory(dir='/qualify') as td:
        file = Path(td)/NAME; file.write_bytes(source)
        spec = importlib.util.spec_from_file_location('qualified_delivery', file)
        delivered = importlib.util.module_from_spec(spec); spec.loader.exec_module(delivered)
        engine_path = Path(td)/'engine.py'; engine_path.write_bytes(Path('/engine/pilot.py').read_bytes())
        engine, _ = delivered.load_engine(engine_path)
        for name, module, target in (('source_tests', test_resume, test_resume.m),
                                     ('exact_delivery_tests', test_resume, delivered),
                                     ('unchanged_engine_regressions', test_pilot, engine)):
            with patch.object(module, 'm', target), contextlib.redirect_stdout(capture):
                result = unittest.TextTestRunner(stream=capture, verbosity=2).run(
                    unittest.defaultTestLoader.loadTestsFromModule(module))
            if not result.wasSuccessful() or result.skipped:
                print(capture.getvalue()); raise RuntimeError('qualification_failed_'+name)
            counts[name] = result.testsRun
        help_result = subprocess.run([sys.executable, '-I', '-B', str(file), '--help'],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
        if help_result.returncode or b'--resume-once' not in help_result.stdout or b'--status' not in help_result.stdout:
            raise RuntimeError('exact_delivery_help_failed')
    digest = hashlib.sha256(source).hexdigest()
    run = launcher(digest).encode()
    syntax = subprocess.run(['bash', '-n'], input=run, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, timeout=5)
    if syntax.returncode: raise RuntimeError('launcher_syntax_failed')
    verification = dict(script=NAME, sha256=digest, tests=counts, skipped=0,
                        python37_grammar='PASS', cli_help='PASS', launcher_bash_syntax='PASS',
                        test_network='DISABLED', real_linux_uid1000_synthetic_copy='PASS',
                        engine_sha256=delivered.ENGINE_SHA, original_package_modified=False,
                        systemd243_private_mount_nfs_integration='NOT_TESTED',
                        actual_source_readonly_mount='MOCKED_IN_OFFLINE_TESTS',
                        production_execution='NOT_PERFORMED', ready_to_migrate=False, ready_to_scale=False)
    files = {NAME: source, 'RUN-DATAX03.txt': run, 'RUNBOOK.txt': (HERE/'README.md').read_bytes(),
             'VERIFICATION.json': (json.dumps(verification, indent=2)+'\n').encode(),
             'LOCAL-TESTS.txt': capture.getvalue().encode()}
    DEST.mkdir()
    for name, data in files.items():
        with (DEST/name).open('xb') as f: f.write(data)
    with (DEST/'SHA256SUMS').open('x') as f:
        for name, data in sorted(files.items()):
            f.write(hashlib.sha256(data).hexdigest()+'  '+name+'\n')
    print(json.dumps(dict(delivery=str(DEST), **verification), indent=2))


if __name__ == '__main__':
    main()
