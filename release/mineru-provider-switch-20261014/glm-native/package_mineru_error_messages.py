"""Qualify the exact frozen readers in a disposable offline Linux container."""
import ast
import builtins
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import diagnose_mineru_error_messages as m
import package_glm104_failure_reasons as previous_package

HERE = Path(__file__).resolve().parent
DEST = Path('/delivery/weknora-mineru-error-messages-20261017-r1')
NAME = 'weknora-mineru-error-messages-20261017-r1.py'


def run_tests(names):
    stream = io.StringIO()
    suite = unittest.defaultTestLoader.loadTestsFromNames(names)
    result = unittest.TextTestRunner(stream=stream,verbosity=2).run(suite)
    if not result.wasSuccessful() or result.skipped:
        print(stream.getvalue()); raise RuntimeError('qualification_failed_or_skipped')
    return stream.getvalue().encode(), result.testsRun


def main():
    if sys.platform!='linux' or os.geteuid()!=0 or not Path('/.dockerenv').exists():
        raise RuntimeError('disposable_linux_container_required')
    if DEST.exists(): raise RuntimeError('delivery_exists_no_overwrite')
    raw = (HERE/'diagnose_mineru_error_messages.py').read_bytes()
    ast.parse(raw, feature_version=(3,7))
    logs, count = run_tests(['test_mineru_error_messages','test_diagnose_glm104_failures','test_diagnose_glm104_mineru'])
    f,d,prior = previous_package.f,previous_package.d,previous_package.prior
    old = prior.previous
    rows = [
        (m.HELPER,Path('/inputs/weknora-glm104-failure-reasons-20261017-r1/weknora-glm104-failure-reasons-20261017-r1.py'),m.HELPER_SHA),
        (f.PRIOR,Path('/inputs/weknora-glm104-mineru-diagnostic-20261017-r1/weknora-glm104-mineru-diagnostic-20261017-r1.py'),f.PRIOR_SHA),
        (d.TOOL,Path('/inputs/weknora-glm104-switch-20261017-r1/weknora-glm104-switch-20261017-r1.py'),d.TOOL_SHA),
        (prior.switch.REVIEW_TOOL,Path('/inputs/weknora-glm104-review-20261017-r1/weknora-glm104-review-20261017-r1.py'),prior.switch.REVIEW_TOOL_SHA),
        (old.o.TOOL,Path('/inputs/weknora-glm88-switch-20261017-r1/weknora-glm88-switch-20261017-r1.py'),old.o.TOOL_SHA),
        (old.s.REVIEW_TOOL,Path('/inputs/weknora-glm88-review-20261017-r1/weknora-glm88-review-20261017-r1.py'),old.s.REVIEW_TOOL_SHA),
        (old.r.TOOL,Path('/inputs/weknora-glm-activate56-20261017-r1/weknora-glm-activate56-20261017-r1.py'),old.r.TOOL_SHA),
        (old.OLD_TOOL,Path('/inputs/weknora-full-enrichment-20261017-r1/weknora-full-enrichment-20261017-r1.py'),old.OLD_TOOL_SHA),
        (old.TOOL,Path('/frozen/weknora-tune88-20261016-r1/weknora-tune88-20261016-r1.py'),old.TOOL_SHA)]
    for target,source,digest in rows:
        content=source.read_bytes()
        if hashlib.sha256(content).hexdigest()!=digest or target.exists():
            raise RuntimeError('frozen_input_changed')
        target.parent.mkdir(parents=True,mode=0o700,exist_ok=False)
        target.write_bytes(content); target.chmod(0o600)
    execute = builtins.exec
    def receipt_only_mock(code, namespace):
        execute(code, namespace)
        if namespace.get('__name__')=='frozen_failure_diagnostic':
            namespace['bind_receipt']=lambda:None
    with patch('builtins.exec',side_effect=receipt_only_mock), \
         patch('subprocess.Popen',side_effect=AssertionError('unexpected_process')):
        diagnostic, observer, helper=m.load_helpers()
    assert len(observer.WORKERS)==6 and diagnostic.APP_UID==d.APP_UID
    import test_mineru_error_messages as exact_tests
    exact_tests.d,exact_tests.f=diagnostic,helper
    with patch('subprocess.Popen',side_effect=AssertionError('unexpected_process')):
        frozen, frozen_count=run_tests(['test_mineru_error_messages'])
    digest = hashlib.sha256(raw).hexdigest()
    command = '''sudo bash -se <<'BASH'
set -Eeuo pipefail
umask 077
test "$(hostname -s)" = datax-01
SCRIPT=/home/cnooc/SCRIPT_NAME
test -f "$SCRIPT" && test ! -L "$SCRIPT"
CONTROL=$(mktemp -d /var/lib/mineru-error-message-tool-XXXXXXXX)
install -o root -g root -m 0600 "$SCRIPT" "$CONTROL/check.py"
test "$(sha256sum "$CONTROL/check.py" | awk '{print $1}')" = SCRIPT_SHA
echo "diagnostic_tool=$CONTROL/check.py"
set +e
python3 -I -B -u "$CONTROL/check.py" 2>&1 | tee "$CONTROL/check.log"
RESULT=("${PIPESTATUS[@]}")
set -e
echo "check_exit_code=${RESULT[0]} tee_exit_code=${RESULT[1]}"
echo "check_log=$CONTROL/check.log"
test "${RESULT[0]}" -eq 0
test "${RESULT[1]}" -eq 0
BASH
'''.replace('SCRIPT_NAME',NAME).replace('SCRIPT_SHA',digest)
    # Check the operator command syntax without running it.
    import subprocess
    checked=subprocess.run(['bash','-n'],input=command.encode(),stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=10)
    if checked.returncode: raise RuntimeError('launcher_syntax_failed')
    proof=dict(script=NAME,sha256=digest,python37_grammar='PASS',linux_tests=count,
        frozen_helper_tests=frozen_count,skipped_tests=0,exact_helper_hashes='PASS',
        production_receipt_read='MOCKED_IN_LOCAL_TEST_ONLY',subprocess_during_loader='BLOCKED',
        production_execution='NOT_EXECUTED_LOCALLY',provider_requests=0,production_writes=0,
        launcher_bash_syntax='PASS',raw_messages_exported=False,projection='FIXED_VOCABULARY_LOSSY')
    DEST.mkdir()
    for name,content in ((NAME,raw),('RUNBOOK.txt',(HERE/'MINERU-ERROR-MESSAGES-RUNBOOK.txt').read_bytes()),
                         ('LOCAL-TESTS.txt',logs),('FROZEN-TESTS.txt',frozen),('RUN-datax01.txt',command.encode()),
                         ('VERIFICATION.json',(json.dumps(proof,indent=2)+'\n').encode())):
        (DEST/name).write_bytes(content)
    print(json.dumps(proof))


if __name__=='__main__': main()
