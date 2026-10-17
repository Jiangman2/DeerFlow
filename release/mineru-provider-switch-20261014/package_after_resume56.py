"""Qualify and create a new immutable diagnostic delivery, never production apply."""
import ast
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).parent
DEST = Path('C:/Users/15285/.codex/visualizations/2026/09/24/01a0d2d7-0015-7a73-93c3-791b713a47f6/weknora-after-resume56-20261016-r1')
NAME = 'weknora-after-resume56-20261016-r1.py'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def bundle():
    parts = ['#!/usr/bin/env python3\nimport base64,hashlib,types,sys\n']; hashes = {}
    for name in ('diagnose_mineru_quarantine', 'recover_weknora56_consumers', 'review_after_resume56'):
        raw = (ROOT/(name+'.py')).read_bytes(); digest = sha(raw); hashes[name+'.py'] = digest
        ast.parse(raw.decode(), feature_version=(3, 7))
        parts.append("raw=base64.b85decode(%r)\nassert hashlib.sha256(raw).hexdigest()==%r\nmod=types.ModuleType(%r); mod.__file__=%r; sys.modules[%r]=mod\nexec(compile(raw,mod.__file__,'exec'),mod.__dict__)\ndel raw\n" %
                     (base64.b85encode(raw), digest, name, '<embedded_'+name+'>', name))
    raw = (ROOT/'glm_vlm_wire_probe.py').read_bytes(); hashes['glm_vlm_wire_probe.py'] = sha(raw)
    ast.parse(raw.decode(), feature_version=(3, 7))
    parts.append("raw=base64.b85decode(%r)\nassert hashlib.sha256(raw).hexdigest()==%r\nsys.modules['review_after_resume56'].REMOTE_SOURCE=raw.decode()\ndel raw\n" % (base64.b85encode(raw), sha(raw)))
    parts.append("if __name__=='__main__':\n    sys.exit(sys.modules['review_after_resume56'].main())\n")
    return ''.join(parts).encode(), hashes


def launcher(digest):
    return ('''#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
test "$(id -u)" -eq 0
test "$(hostname -s)" = datax-01
cd /home/cnooc
SCRIPT=SCRIPT_NAME
test -f "$SCRIPT" && test ! -L "$SCRIPT"
CONTROL=$(mktemp -d /var/lib/weknora-fresh-review-tool-XXXXXXXX)
install -o root -g root -m 0600 "$SCRIPT" "$CONTROL/review.py"
test "$(sha256sum "$CONTROL/review.py" | awk '{print $1}')" = SCRIPT_SHA
echo "review_tool=$CONTROL/review.py"
echo "status_command=sudo python3 -I -B $CONTROL/review.py --status"
set +e
python3 -I -B -u "$CONTROL/review.py" --audit-and-glm-once 2>&1 | tee "$CONTROL/review.log"
RESULT=("${PIPESTATUS[@]}")
set -e
echo "review_exit_code=${RESULT[0]} tee_exit_code=${RESULT[1]}"
echo "review_log=$CONTROL/review.log"
echo "status_command=sudo python3 -I -B $CONTROL/review.py --status"
test "${RESULT[0]}" -eq 0
test "${RESULT[1]}" -eq 0
'''.replace('SCRIPT_NAME', NAME).replace('SCRIPT_SHA', digest)).encode()


def main():
    if DEST.exists():
        raise RuntimeError('delivery_exists_do_not_overwrite')
    suites = ('test_after_resume56', 'test_recover_weknora56_consumers', 'test_diagnose_mineru_quarantine',
              'test_tune_weknora88', 'test_tune88_contract', 'test_observe_tune88', 'test_tune_weknora56',
              'test_weknora_tune_placement', 'test_audit_weknora_scheduling', 'test_audit_weknora_process',
              'test_tuning_review', 'test_observe_pipeline', 'test_package_tune88')
    result = unittest.TextTestRunner(verbosity=1).run(unittest.defaultTestLoader.loadTestsFromNames(suites))
    if not result.wasSuccessful(): raise RuntimeError('tests_failed_no_delivery')
    raw, hashes = bundle(); sh = launcher(sha(raw))
    ast.parse(raw.decode(), feature_version=(3, 7))
    with tempfile.TemporaryDirectory(prefix='review56-test-') as temp:
        p = Path(temp); script = p/NAME; script.write_bytes(raw)
        shell = p/'START-review-datax01.sh'; shell.write_bytes(sh)
        subprocess.run(['C:/Program Files/Git/bin/bash.exe', '-n', str(shell)], check=True, capture_output=True)
        for mode in ('--status', '--audit', '--audit-and-glm-once'):
            r = subprocess.run([sys.executable, '-I', '-B', str(script), mode], timeout=15, capture_output=True)
            if r.returncode != 1 or r.stderr or json.loads(r.stdout).get('failure', {}).get('check') != 'run_on_datax01_as_root':
                raise RuntimeError('wrong_host_delivery_test_failed')
    files = {NAME: raw, 'START-review-datax01.sh': sh, 'RUNBOOK.txt': (ROOT/'AFTER-RESUME56-RUNBOOK.txt').read_bytes(),
             'VERIFICATION.json': (json.dumps({'tests_passed': result.testsRun, 'python37_grammar': 'PASS',
               'bash_syntax': 'PASS', 'wrong_host_modes': 'PASS', 'embedded_source_hashes': hashes,
               'script_sha256': sha(raw), 'production_executed': False, 'native_go_client_tested': False,
               'live_glm_tested': False, 'production_writes': 0, 'maximum_synthetic_requests': 2}, indent=2)+'\n').encode()}
    DEST.mkdir()
    for name, data in files.items():
        with (DEST/name).open('xb') as f: f.write(data)
        if (DEST/name).read_bytes() != data: raise RuntimeError('artifact_readback_failed')
    with (DEST/'PACKAGE.SHA256').open('x', encoding='ascii', newline='\n') as f:
        f.write(''.join(sha(data)+'  '+name+'\n' for name, data in files.items()))
    print(json.dumps({'delivery': str(DEST), 'script_sha256': sha(raw), 'launcher_sha256': sha(sh), 'tests_passed': result.testsRun}))


if __name__ == '__main__':
    main()
