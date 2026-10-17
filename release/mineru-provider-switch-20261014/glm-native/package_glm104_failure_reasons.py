"""Package immutable follow-up after qualifying the exact frozen read helpers."""
import ast
import hashlib
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import diagnose_glm104_failures as f
import diagnose_glm104_mineru as d
import package_glm104_switch as prior

HERE=Path(__file__).resolve().parent
DEST=Path('/downloads/weknora-glm104-failure-reasons-20261017-r1')
NAME='weknora-glm104-failure-reasons-20261017-r1.py'


def main():
    if sys.platform!='linux' or os.geteuid()!=0 or not Path('/.dockerenv').exists():
        raise RuntimeError('disposable_linux_container_required')
    if DEST.exists(): raise RuntimeError('delivery_exists')
    source=(HERE/'diagnose_glm104_failures.py').read_bytes(); ast.parse(source,feature_version=(3,7))
    previous=prior.previous
    rows=[
      (f.PRIOR,Path('/downloads/weknora-glm104-mineru-diagnostic-20261017-r1/weknora-glm104-mineru-diagnostic-20261017-r1.py'),f.PRIOR_SHA),
      (d.TOOL,Path('/downloads/weknora-glm104-switch-20261017-r1/weknora-glm104-switch-20261017-r1.py'),d.TOOL_SHA),
      (prior.switch.REVIEW_TOOL,Path('/downloads/weknora-glm104-review-20261017-r1/weknora-glm104-review-20261017-r1.py'),prior.switch.REVIEW_TOOL_SHA),
      (previous.o.TOOL,Path('/downloads/weknora-glm88-switch-20261017-r1/weknora-glm88-switch-20261017-r1.py'),previous.o.TOOL_SHA),
      (previous.s.REVIEW_TOOL,Path('/downloads/weknora-glm88-review-20261017-r1/weknora-glm88-review-20261017-r1.py'),previous.s.REVIEW_TOOL_SHA),
      (previous.r.TOOL,Path('/downloads/weknora-glm-activate56-20261017-r1/weknora-glm-activate56-20261017-r1.py'),previous.r.TOOL_SHA),
      (previous.OLD_TOOL,Path('/downloads/weknora-full-enrichment-20261017-r1/weknora-full-enrichment-20261017-r1.py'),previous.OLD_TOOL_SHA),
      (previous.TOOL,Path('/frozen/weknora-tune88-20261016-r1/weknora-tune88-20261016-r1.py'),previous.TOOL_SHA)]
    for target,local,digest in rows:
        raw=local.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=digest or target.exists(): raise RuntimeError('frozen_input_changed')
        target.parent.mkdir(parents=True,mode=0o700,exist_ok=False)
        target.write_bytes(raw); target.chmod(0o600)
    # Substitute only the unavailable production RECEIPT in this local test.
    # All installed executable helper bytes still go through exact SHA checks.
    import types
    orig_exec=__builtins__['exec'] if isinstance(__builtins__,dict) else __builtins__.exec
    def guarded_exec(code, namespace):
        orig_exec(code, namespace)
        if namespace.get('__name__')=='frozen_failure_diagnostic':
            namespace['bind_receipt']=lambda:None
    with patch('builtins.exec',side_effect=guarded_exec):
        # Prevent accidental process/network access during initialization.
        with patch('subprocess.Popen',side_effect=AssertionError('unexpected process call')):
            frozen,obs,review=f.load_helpers()
    assert callable(review.pg_binding) and callable(review.DB_SHELL.split)
    assert len(obs.WORKERS)==6 and callable(obs.call) and frozen.APP_UID==d.APP_UID
    code='''import unittest
suite=unittest.defaultTestLoader.loadTestsFromNames(['test_diagnose_glm104_failures','test_diagnose_glm104_mineru'])
r=unittest.TextTestRunner(verbosity=2).run(suite)
assert r.wasSuccessful() and not r.skipped and r.testsRun==38
'''
    tests=previous.checked_tests([sys.executable,'-B','-c',code])
    DEST.mkdir()
    (DEST/NAME).write_bytes(source)
    (DEST/'RUNBOOK.txt').write_bytes((HERE/'GLM104-FAILURE-REASONS-RUNBOOK.txt').read_bytes())
    (DEST/'LOCAL-TESTS.txt').write_bytes(tests)
    proof=dict(script=NAME,sha256=hashlib.sha256(source).hexdigest(),python37_grammar='PASS',
        offline_tests='PASS_38_ZERO_SKIPS',exact_frozen_read_helpers='PASS',
        production_receipt_binding_test='MOCKED_LOCAL_ONLY_NOT_PRODUCTION_EXECUTION',
        production_execution='NOT_EXECUTED_LOCALLY',read_only=True,provider_requests=0,
        database_sample_limit=f.SAMPLE,raw_errors_exported=False)
    (DEST/'VERIFICATION.json').write_text(json.dumps(proof,indent=2)+'\n')
    print(json.dumps(proof))


if __name__=='__main__': main()
