"""Validate exact delivery under Python3.12 and bind the real Python3.7 receipt."""
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

HERE=Path(__file__).resolve().parent
DEST=Path('/delivery/weknora-datax03-nfs-test-stage-20261017-r1')
NAME='weknora-datax03-nfs-test-stage-20261017-r1.py'


def launcher(digest):
    return '''sudo bash -se <<'BASH'
set -Eeuo pipefail
umask 077
test "$(hostname -s)" = datax-03
SCRIPT=/home/cnooc/SCRIPT_NAME
test -f "$SCRIPT" && test ! -L "$SCRIPT"
CONTROL=$(mktemp -d /var/lib/weknora-datax03-nfs-test-tool-XXXXXXXX)
install -o root -g root -m 0600 "$SCRIPT" "$CONTROL/stage.py"
test "$(sha256sum "$CONTROL/stage.py" | awk '{print $1}')" = SCRIPT_SHA
echo "stage_log=$CONTROL/stage.log"
echo "status_command=sudo python3 -I -B $CONTROL/stage.py --status"
set +e
python3 -I -B -u "$CONTROL/stage.py" --stage-once 2>&1 | tee "$CONTROL/stage.log"
RESULT=("${PIPESTATUS[@]}")
set -e
echo "stage_exit_code=${RESULT[0]} tee_exit_code=${RESULT[1]}"
echo "stage_log=$CONTROL/stage.log"
echo "status_command=sudo python3 -I -B $CONTROL/stage.py --status"
test "${RESULT[0]}" -eq 0
test "${RESULT[1]}" -eq 0
BASH
'''.replace('SCRIPT_NAME',NAME).replace('SCRIPT_SHA',digest)


def main():
    if sys.platform!='linux' or not Path('/.dockerenv').exists() or DEST.exists():
        raise RuntimeError('fresh_offline_linux_delivery_required')
    raw=(HERE/'stage.py').read_bytes();digest=hashlib.sha256(raw).hexdigest()
    ast.parse(raw,feature_version=(3,7))
    native_raw=Path('/delivery/weknora-datax03-nfs-test-stage-r1-native37.json').read_bytes()
    native=json.loads(native_raw)
    if not (native.get('python')=='3.7.17' and native.get('result')=='PASS'
            and native.get('script_sha256')==digest and native.get('skipped')==0
            and set(native.get('tests',{}))=={'source','exact_delivery'}
            and min(native['tests'].values())>=34):
        raise RuntimeError('matching_native37_tests_required')
    bind_raw=Path('/delivery/weknora-datax03-nfs-stage-r1-bind-smoke.json').read_bytes()
    bind=json.loads(bind_raw)
    if not (bind.get('script_sha256')==digest and bind.get('bind_mount_syscall')=='PASS'
            and bind.get('source_fd_to_target_inode')=='PASS'
            and bind.get('scope')=='DISPOSABLE_LOCAL_CONTAINER_PRIVATE_NAMESPACE'
            and bind.get('nfs_exports')=='NOT_EXECUTED'):
        raise RuntimeError('matching_real_bind_smoke_required')
    import test_stage
    capture=io.StringIO();counts={}
    with tempfile.TemporaryDirectory(dir='/qualify') as td:
        path=Path(td)/NAME;path.write_bytes(raw);path.chmod(0o600)
        spec=importlib.util.spec_from_file_location('exact_delivery',path)
        delivery=importlib.util.module_from_spec(spec);spec.loader.exec_module(delivery)
        for name,module in (('source',test_stage.m),('exact_delivery',delivery)):
            with patch.object(test_stage,'m',module),contextlib.redirect_stdout(capture):
                result=unittest.TextTestRunner(stream=capture,verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(test_stage))
            if not result.wasSuccessful() or result.skipped:
                print(capture.getvalue());raise RuntimeError('qualification_failed_'+name)
            counts[name]=result.testsRun
        help_result=subprocess.run([sys.executable,'-I','-B',str(path),'--help'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=5)
        if help_result.returncode or b'--stage-once' not in help_result.stdout or b'--status' not in help_result.stdout:
            raise RuntimeError('exact_delivery_help_failed')
    run=launcher(digest).encode()
    syntax=subprocess.run(['bash','-n'],input=run,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=5)
    if syntax.returncode:raise RuntimeError('launcher_syntax_failed')
    report=dict(script=NAME,sha256=digest,python312_tests=counts,python37_tests=native['tests'],
        skipped=0,python37_runtime='PASS',python37_grammar='PASS',cli_help='PASS',launcher_syntax='PASS',
        nonroot_opt_pinned_identity='PASS',new_directory_ownership_and_fd_binding_tests='PASS',
        workflow_mount_exportfs_and_services='MOCKED',real_bind_mount_smoke='PASS_TMPFS_PRIVATE_CONTAINER',
        test_network='DISABLED',actual_server_execution='NOT_PERFORMED',
        actual_crossnode_io='NOT_TESTED',business_directories_exported=False,production_mounts_changed=False,
        ready_for_production_switch=False,ready_to_scale=False,helper_sha256=delivery.AUDIT_SHA)
    files={NAME:raw,'RUN-DATAX03.txt':run,'RUNBOOK.txt':(HERE/'README.md').read_bytes(),
        'VERIFICATION.json':(json.dumps(report,indent=2)+'\n').encode(),
        'LOCAL-TESTS.txt':capture.getvalue().encode(),'PYTHON37-VERIFICATION.json':native_raw,
        'REAL-BIND-SMOKE.json':bind_raw,
        'PYTHON37-TESTS.txt':Path('/delivery/weknora-datax03-nfs-test-stage-r1-native37.txt').read_bytes()}
    DEST.mkdir()
    for name,data in files.items():
        with (DEST/name).open('xb') as f:f.write(data)
    with (DEST/'SHA256SUMS').open('x') as f:
        for name,data in sorted(files.items()):f.write(hashlib.sha256(data).hexdigest()+'  '+name+'\n')
    print(json.dumps(dict(delivery=str(DEST),**report),indent=2))


if __name__=='__main__':main()
