"""Offline qualification of the104 switch against the exact deployed readers."""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import package_glm88_load as previous
import switch_glm104 as switch

HERE=Path(__file__).resolve().parent
NAME='weknora-glm104-switch-20261017-r1.py'


def sha(raw): return hashlib.sha256(raw).hexdigest()


def package(args):
    if sys.platform!='linux' or os.geteuid()!=0 or not Path('/.dockerenv').exists():
        raise RuntimeError('disposable_linux_root_container_required')
    if args.output.exists(): raise RuntimeError('delivery_exists')
    source=(HERE/'switch_glm104.py').read_bytes(); ast.parse(source,feature_version=(3,7))
    rows=[(switch.REVIEW_TOOL,args.review104.read_bytes(),switch.REVIEW_TOOL_SHA),
          (previous.o.TOOL,args.switch88.read_bytes(),previous.o.TOOL_SHA),
          (previous.s.REVIEW_TOOL,args.review88.read_bytes(),previous.s.REVIEW_TOOL_SHA),
          (previous.r.TOOL,args.activation.read_bytes(),previous.r.TOOL_SHA),
          (previous.OLD_TOOL,args.original_full.read_bytes(),previous.OLD_TOOL_SHA),
          (previous.TOOL,args.original_tune.read_bytes(),previous.TOOL_SHA)]
    for target,raw,digest in rows:
        if sha(raw)!=digest or target.exists(): raise RuntimeError('frozen_helper_or_test_path_drift')
    names=['test_switch_glm104','test_review_glm104','test_observe_glm88_load','test_switch_glm88',
           'test_review_glm88','test_glm_activation','test_switch_image56','test_activation_review',
           'test_production_release','test_native_preload','test_export_weknora_base']
    code='''import unittest,sys
suite=unittest.defaultTestLoader.loadTestsFromNames(sys.argv[1:])
result=unittest.TextTestRunner(verbosity=2).run(suite)
assert result.wasSuccessful() and not result.skipped and result.testsRun>=319
'''
    tests=previous.checked_tests([sys.executable,'-B','-c',code]+names)
    # Exact server-shaped files are inside this disposable container only.
    for target,raw,digest in rows:
        target.parent.mkdir(mode=0o700,parents=True,exist_ok=False)
        target.write_bytes(raw); target.chmod(0o600)
    with tempfile.TemporaryDirectory(dir='/root') as tmp:
        delivered=Path(tmp)/NAME; delivered.write_bytes(source); delivered.chmod(0o600)
        previous.checked_tests([sys.executable,'-I','-B',str(delivered),'--help'])
        (Path(tmp)/'observe_glm88_load.py').write_bytes((HERE/'observe_glm88_load.py').read_bytes())
        code='''import importlib.util,sys,unittest
from pathlib import Path
spec=importlib.util.spec_from_file_location('switch_glm104',sys.argv[1])
s=importlib.util.module_from_spec(spec); sys.modules['switch_glm104']=s; spec.loader.exec_module(s)
s.u=s.load_review(); s.r=s.u.o.r
sys.modules['review_glm104']=s.u; sys.modules['switch_glm88']=s.u.o.s; sys.modules['review_glm88']=s.r
s.u.o.__file__=str(Path(sys.argv[1]).parent/'observe_glm88_load.py')
assert sum(s.u.CURRENT.values())==88 and sum(s.u.TARGET.values())==104
assert s.r.e.TARGET_MODEL==128 and sum(s.r.TARGET.values())==88
assert s.target_env()[s.r.e.review.SETTINGS['enrichment'][1]]=='64'
assert s.r.e.TARGET_ENV[s.r.e.review.SETTINGS['enrichment'][1]]=='48'
suite=unittest.defaultTestLoader.loadTestsFromNames(['test_switch_glm104','test_review_glm104',
 'test_observe_glm88_load','test_switch_glm88','test_review_glm88'])
result=unittest.TextTestRunner(verbosity=2).run(suite)
assert result.wasSuccessful() and not result.skipped and result.testsRun>=172
'''
        frozen=previous.checked_tests([sys.executable,'-B','-c',code,str(delivered)])
    args.output.mkdir(parents=True)
    for name,raw in ((NAME,source),('LOCAL-TESTS.txt',tests),('PACKAGED-FROZEN-TESTS.txt',frozen),
                     ('RUNBOOK.txt',(HERE/'GLM104-SWITCH-RUNBOOK.txt').read_bytes())):
        (args.output/name).write_bytes(raw)
    proof=dict(script=NAME,sha256=sha(source),bytes=len(source),python37_grammar='PASS',
        source_tests='PASS_ZERO_SKIPS',packaged_frozen_tests='PASS_ZERO_SKIPS',
        installed_helper_chain='VERIFIED_IN_DISPOSABLE_CONTAINER',review_sha256=switch.REVIEW_SHA,
        proposal_sha256=switch.PROPOSAL_SHA,production_execution='NOT_EXECUTED_LOCALLY',
        production_writes=0,provider_probe_requests=0,only_proposed_change='ENRICHMENT_48_TO_64',
        scope='ONE_IDENTITY_BOUND_SIGTSTP_AND_ONE_ENV_CAS',target_slots=104,mineru_slots=16,
        model_default=128,automatic_retry=False,automatic_rollback=False,
        throughput_acceptance='PENDING_REAL_BACKLOG_AND_RESOURCE_OBSERVATION')
    (args.output/'VERIFICATION.json').write_text(json.dumps(proof,indent=2)+'\n')
    for result in (tests,frozen): print(result.decode().split('----------------------------------------------------------------------')[-1].strip())
    print(json.dumps(proof))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    for name in ('output','review104','switch88','review88','activation','original-full','original-tune'):
        parser.add_argument('--'+name,type=Path,required=True)
    package(parser.parse_args())
