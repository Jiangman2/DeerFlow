"""Generate the immutable stopped-consumer recovery delivery after qualification."""
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
DEST = Path('C:/Users/15285/.codex/visualizations/2026/09/24/'
            '01a0d2d7-0015-7a73-93c3-791b713a47f6/weknora-resume56-20261016-r1')
NAME = 'weknora-resume56-20261016-r1.py'
PINS = {'tune_weknora88.py':'680a61d26f6aa3cf6ce13d28a645022760caeb0ed138c938841bcd183456bfb5',
        'tune_weknora56.py':'782b8c72d3cce7638525e1ea40f9f49fb3068fd46273cb099cab930879d55e6c',
        'observe_pipeline.py':'dd30806b5225824650d9fa9326a76878dad8af497e33d823732f892c267d5551'}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def bundle():
    parts = ['#!/usr/bin/env python3\nimport base64,hashlib,types,sys\n']; hashes = {}
    for name in ('diagnose_mineru_quarantine','recover_weknora56_consumers'):
        raw = (ROOT/(name+'.py')).read_bytes(); digest = sha(raw); hashes[name+'.py'] = digest
        ast.parse(raw.decode(),feature_version=(3,7))
        parts.append("""raw=base64.b85decode(%r)
if hashlib.sha256(raw).hexdigest()!=%r: raise SystemExit('embedded_source_digest_mismatch')
mod=types.ModuleType(%r); mod.__file__=%r; sys.modules[%r]=mod
exec(compile(raw,mod.__file__,'exec'),mod.__dict__)
del raw
""" % (base64.b85encode(raw),digest,name,'<embedded_'+name+'>',name))
    parts.append("if __name__=='__main__':\n    sys.exit(sys.modules['recover_weknora56_consumers'].main())\n")
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
CONTROL=$(mktemp -d /var/lib/weknora-resume56-tool-XXXXXXXX)
install -o root -g root -m 0600 "$SCRIPT" "$CONTROL/recover.py"
test "$(sha256sum "$CONTROL/recover.py" | awk '{print $1}')" = SCRIPT_SHA
echo "recovery_tool=$CONTROL/recover.py"
echo "status_command=sudo python3 -I -B $CONTROL/recover.py --status"
set +e
python3 -I -B -u "$CONTROL/recover.py" --recover 2>&1 | tee "$CONTROL/recover.log"
RESULT=("${PIPESTATUS[@]}")
set -e
echo "recovery_exit_code=${RESULT[0]} tee_exit_code=${RESULT[1]}"
echo "recovery_log=$CONTROL/recover.log"
echo "status_command=sudo python3 -I -B $CONTROL/recover.py --status"
test "${RESULT[0]}" -eq 0
test "${RESULT[1]}" -eq 0
'''.replace('SCRIPT_NAME',NAME).replace('SCRIPT_SHA',digest)).encode()


def main():
    if DEST.exists():
        raise RuntimeError('delivery_exists_do_not_overwrite')
    for name,digest in PINS.items():
        if sha((ROOT/name).read_bytes()) != digest:
            raise RuntimeError('immutable_helper_changed:'+name)
    tests = ('test_recover_weknora56_consumers','test_diagnose_mineru_quarantine',
             'test_tune_weknora88','test_tune88_contract','test_observe_tune88',
             'test_tune_weknora56','test_weknora_tune_placement','test_audit_weknora_scheduling',
             'test_audit_weknora_process','test_tuning_review','test_observe_pipeline','test_package_tune88')
    result = unittest.TextTestRunner(verbosity=1).run(unittest.defaultTestLoader.loadTestsFromNames(tests))
    if not result.wasSuccessful():
        raise RuntimeError('qualification_failed_no_delivery')
    raw, hashes = bundle(); sh = launcher(sha(raw))
    ast.parse(raw.decode(),feature_version=(3,7))
    with tempfile.TemporaryDirectory(prefix='resume56-check-') as temp:
        path = Path(temp); script = path/NAME; script.write_bytes(raw)
        shell = path/'START-resume56-datax01.sh'; shell.write_bytes(sh)
        subprocess.run(['C:/Program Files/Git/bin/bash.exe','-n',str(shell)],check=True,capture_output=True)
        for mode in ('--recover','--status','--reconcile'):
            check = subprocess.run([sys.executable,'-I','-B',str(script),mode],timeout=15,capture_output=True)
            receipt = json.loads(check.stdout)
            if check.returncode != 1 or check.stderr or receipt.get('failure',{}).get('check') != 'run_on_datax01_as_root':
                raise RuntimeError('wrong_host_smoke_failed')
    verification = {'unit_mock_tests_passed':result.testsRun,'platform':sys.platform,
                    'python37_grammar':'PASS','bash_syntax':'PASS','wrong_host_all_modes':'PASS',
                    'embedded_source_hashes':hashes,'immutable_helpers_preserved':PINS,
                    'script_sha256':sha(raw),'production_executed':False,
                    'restores_slots':56,'model_startup_limit':64,'ready_for88':False,
                    'mineru_quarantine_fixed':False,'production_mutation_scope':'ONE_APP_TEMPLATE_ANNOTATION_CAS'}
    files = {NAME:raw,'START-resume56-datax01.sh':sh,'RUNBOOK.txt':(ROOT/'RESUME56-RUNBOOK.txt').read_bytes(),
             'VERIFICATION.json':(json.dumps(verification,indent=2)+'\n').encode()}
    DEST.mkdir()
    for name,data in files.items():
        with (DEST/name).open('xb') as f:
            f.write(data)
        if (DEST/name).read_bytes() != data:
            raise RuntimeError('artifact_readback_failed')
    with (DEST/'PACKAGE.SHA256').open('x',encoding='ascii',newline='\n') as f:
        f.write(''.join(sha(data)+'  '+name+'\n' for name,data in files.items()))
    print(json.dumps({'delivery':str(DEST),'script':NAME,'script_sha256':sha(raw),
                      'tests_passed':result.testsRun,'production_executed':False}))


if __name__ == '__main__':
    main()
