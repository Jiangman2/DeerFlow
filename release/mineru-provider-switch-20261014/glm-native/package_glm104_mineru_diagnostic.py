"""Generate a diagnostic delivery only after offline exact-helper-chain tests."""
import ast
import hashlib
import json
import os
from pathlib import Path
import sys

import diagnose_glm104_mineru as d
import package_glm104_switch as prior

HERE=Path(__file__).resolve().parent
DEST=Path('/downloads/weknora-glm104-mineru-diagnostic-20261017-r1')
NAME='weknora-glm104-mineru-diagnostic-20261017-r1.py'


def main():
    if sys.platform!='linux' or os.geteuid()!=0 or not Path('/.dockerenv').exists():
        raise RuntimeError('disposable_linux_root_container_required')
    if DEST.exists(): raise RuntimeError('delivery_exists')
    source=(HERE/'diagnose_glm104_mineru.py').read_bytes()
    ast.parse(source,feature_version=(3,7))
    previous=prior.previous
    rows=[
      (d.TOOL,Path('/downloads/weknora-glm104-switch-20261017-r1/weknora-glm104-switch-20261017-r1.py'),d.TOOL_SHA),
      (prior.switch.REVIEW_TOOL,Path('/downloads/weknora-glm104-review-20261017-r1/weknora-glm104-review-20261017-r1.py'),prior.switch.REVIEW_TOOL_SHA),
      (previous.o.TOOL,Path('/downloads/weknora-glm88-switch-20261017-r1/weknora-glm88-switch-20261017-r1.py'),previous.o.TOOL_SHA),
      (previous.s.REVIEW_TOOL,Path('/downloads/weknora-glm88-review-20261017-r1/weknora-glm88-review-20261017-r1.py'),previous.s.REVIEW_TOOL_SHA),
      (previous.r.TOOL,Path('/downloads/weknora-glm-activate56-20261017-r1/weknora-glm-activate56-20261017-r1.py'),previous.r.TOOL_SHA),
      (previous.OLD_TOOL,Path('/downloads/weknora-full-enrichment-20261017-r1/weknora-full-enrichment-20261017-r1.py'),previous.OLD_TOOL_SHA),
      (previous.TOOL,Path('/frozen/weknora-tune88-20261016-r1/weknora-tune88-20261016-r1.py'),previous.TOOL_SHA)]
    for target,local,digest in rows:
        raw=local.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=digest or target.exists(): raise RuntimeError('input_changed')
        target.parent.mkdir(parents=True,mode=0o700,exist_ok=False)
        target.write_bytes(raw); target.chmod(0o600)
    code='''import unittest
import diagnose_glm104_mineru as d
obs=d.load_observer()
assert len(obs.WORKERS)==6 and obs.DISPATCHER=='deer-flow-mineru-dedicated-dispatch-r1'
assert callable(obs.health) and callable(obs.resource_snapshot)
suite=unittest.defaultTestLoader.loadTestsFromNames(['test_diagnose_glm104_mineru','test_switch_glm104'])
r=unittest.TextTestRunner(verbosity=2).run(suite)
assert r.wasSuccessful() and not r.skipped and r.testsRun>=70
print('exact_frozen_loader=PASS production_calls=0')
'''
    tests=previous.checked_tests([sys.executable,'-B','-c',code])
    DEST.mkdir()
    (DEST/NAME).write_bytes(source)
    (DEST/'RUNBOOK.txt').write_bytes((HERE/'GLM104-MINERU-DIAGNOSTIC-RUNBOOK.txt').read_bytes())
    (DEST/'LOCAL-TESTS.txt').write_bytes(tests)
    proof=dict(script=NAME,sha256=hashlib.sha256(source).hexdigest(),python37_grammar='PASS',
        offline_tests='PASS_70_ZERO_SKIPS',frozen_helper_loader='PASS',production_execution='NOT_EXECUTED_LOCALLY',
        original_switch_receipt_sha256=d.RECEIPT_SHA,production_writes=0,provider_probe_requests=0,
        automatic_mutation=False,raw_logs_saved=False,read_only_diagnostic=True)
    (DEST/'VERIFICATION.json').write_text(json.dumps(proof,indent=2)+'\n')
    print(tests.decode().split('----------------------------------------------------------------------')[-1].strip())
    print(json.dumps(proof))


if __name__=='__main__': main()
