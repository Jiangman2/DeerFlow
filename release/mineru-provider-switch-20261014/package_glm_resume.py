"""Qualify a separate GLM-only recovery delivery without modifying prior tools."""
import ast
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import resume_glm_review as recovery
import package_after_resume56 as previous

ROOT = Path(__file__).parent
DEST = previous.DEST.parent/'weknora-glm-wire-resume-20261016-r1'
NAME = 'weknora-glm-wire-resume-20261016-r1.py'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def launcher(digest):
    return ('''#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
test "$(id -u)" -eq 0
test "$(hostname -s)" = datax-01
cd /home/cnooc
SCRIPT=SCRIPT_NAME
test -f "$SCRIPT" && test ! -L "$SCRIPT"
CONTROL=$(mktemp -d /var/lib/weknora-glm-resume-tool-XXXXXXXX)
install -o root -g root -m 0600 "$SCRIPT" "$CONTROL/resume.py"
test "$(sha256sum "$CONTROL/resume.py" | awk '{print $1}')" = SCRIPT_SHA
echo "glm_resume_tool=$CONTROL/resume.py"
echo "status_command=sudo python3 -I -B $CONTROL/resume.py --status"
set +e
python3 -I -B -u "$CONTROL/resume.py" --probe-once 2>&1 | tee "$CONTROL/resume.log"
RESULT=("${PIPESTATUS[@]}")
set -e
echo "resume_exit_code=${RESULT[0]} tee_exit_code=${RESULT[1]}"
echo "resume_log=$CONTROL/resume.log"
echo "status_command=sudo python3 -I -B $CONTROL/resume.py --status"
test "${RESULT[0]}" -eq 0
test "${RESULT[1]}" -eq 0
'''.replace('SCRIPT_NAME', NAME).replace('SCRIPT_SHA', digest)).encode()


def main():
    if DEST.exists():
        raise RuntimeError('delivery_exists_do_not_overwrite')
    suites = ('test_resume_glm_review', 'test_after_resume56', 'test_recover_weknora56_consumers',
              'test_diagnose_mineru_quarantine', 'test_tune_weknora88', 'test_tune88_contract',
              'test_observe_tune88', 'test_tune_weknora56', 'test_weknora_tune_placement',
              'test_audit_weknora_scheduling', 'test_audit_weknora_process', 'test_tuning_review',
              'test_observe_pipeline', 'test_package_tune88')
    result = unittest.TextTestRunner(verbosity=1).run(unittest.defaultTestLoader.loadTestsFromNames(suites))
    if not result.wasSuccessful():
        raise RuntimeError('tests_failed_no_delivery')
    old = (previous.DEST/previous.NAME).read_bytes()
    if sha(old) != recovery.OLD_TOOL_SHA:
        raise RuntimeError('previous_delivery_changed')
    raw = (ROOT/'resume_glm_review.py').read_bytes()
    ast.parse(raw.decode(), feature_version=(3, 7))
    shell = launcher(sha(raw))
    with tempfile.TemporaryDirectory(prefix='glm-resume-qualification-') as temp:
        folder = Path(temp)
        script = folder/NAME; script.write_bytes(raw)
        start = folder/'START-glm-resume-datax01.sh'; start.write_bytes(shell)
        subprocess.run(['C:/Program Files/Git/bin/bash.exe', '-n', str(start)], check=True, capture_output=True)
        for mode in ('--status', '--probe-once'):
            res = subprocess.run([sys.executable, '-I', '-B', str(script), mode], timeout=15, capture_output=True)
            if res.returncode != 1 or res.stderr or json.loads(res.stdout).get('failure', {}).get('check') != 'run_on_datax01_as_root':
                raise RuntimeError('wrong_host_refusal_failed')
        # Import the exact old bundle as the new tool does, without its main.
        # Mock only the production read-helper initialization. No cluster access.
        import_test = '''import sys,types
from pathlib import Path
from unittest.mock import patch
p=Path(sys.argv[1]); ns={'__name__':'isolated_import','__file__':str(p)}
exec(compile(p.read_bytes(),str(p),'exec'),ns)
original=ns['exec'] if 'exec' in ns else exec
def importing(code,scope):
 original(code,scope)
 mod=sys.modules['recover_weknora56_consumers']
 mod.initialize=lambda: None
 mod.e='test-helper'; mod.obs='test-observer'
with patch.dict(ns,{'safe_read':lambda *a:Path(sys.argv[2]).read_bytes(),'exec':importing}):
 ns['initialize']()
 r=ns['review']
assert r.e=='test-helper' and r.obs=='test-observer'
assert r.REMOTE_SOURCE and 'def run(token):' in r.REMOTE_SOURCE
assert r.APP_UID=='d64b460f-b27e-4a6f-afde-c5583b7d0455'
print('immutable_bundle_import=PASS')
'''
        res = subprocess.run([sys.executable, '-I', '-B', '-c', import_test, str(script), str(previous.DEST/previous.NAME)],
                             timeout=15, capture_output=True)
        if res.returncode or res.stderr or res.stdout.strip() != b'immutable_bundle_import=PASS':
            raise RuntimeError('installed_bundle_import_test_failed: ' + res.stderr.decode(errors='replace')[:2048])
    verification = {'tests_passed': result.testsRun, 'python37_grammar': 'PASS', 'bash_syntax': 'PASS',
                    'wrong_host_modes': 'PASS', 'immutable_bundle_import': 'PASS',
                    'script_sha256': sha(raw), 'prior_installed_tool_sha256': sha(old),
                    'prior_result_sha256': recovery.OLD_RESULT_SHA,
                    'production_executed': False, 'production_writes': 0, 'live_glm_tested': False,
                    'native_go_client_tested': False, 'maximum_synthetic_requests': 2}
    files = {NAME: raw, 'START-glm-resume-datax01.sh': shell,
             'RUNBOOK.txt': (ROOT/'GLM-RESUME-RUNBOOK.txt').read_bytes(),
             'VERIFICATION.json': (json.dumps(verification, indent=2)+'\n').encode()}
    DEST.mkdir()
    for name, data in files.items():
        with (DEST/name).open('xb') as f:
            f.write(data)
        if (DEST/name).read_bytes() != data:
            raise RuntimeError('delivery_readback_failed')
    with (DEST/'PACKAGE.SHA256').open('x', encoding='ascii', newline='\n') as f:
        f.write(''.join(sha(data)+'  '+name+'\n' for name, data in files.items()))
    print(json.dumps({'delivery': str(DEST), 'script_sha256': sha(raw), 'launcher_sha256': sha(shell),
                      'tests_passed': result.testsRun, 'production_writes': 0}))


if __name__ == '__main__':
    main()
