"""Offline Linux-container qualification against exact deployed helper bytes."""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import switch_glm88 as s
import review_glm88 as r
from package_enrichment_gateway_recovery import checked_tests
from recover_enrichment_gateway import OLD_TOOL,OLD_TOOL_SHA
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from diagnose_mineru_quarantine import TOOL,TOOL_SHA

HERE=Path(__file__).resolve().parent
NAME='weknora-glm88-switch-20261017-r1.py'


def sha(raw): return hashlib.sha256(raw).hexdigest()


def package(args):
    if sys.platform!='linux' or os.geteuid()!=0 or not Path('/.dockerenv').exists():
        raise RuntimeError('isolated_linux_root_container_required')
    if args.output.exists(): raise RuntimeError('delivery_exists')
    source=(HERE/'switch_glm88.py').read_bytes(); ast.parse(source,feature_version=(3,7))
    rows=[(s.REVIEW_TOOL,args.review.read_bytes(),s.REVIEW_TOOL_SHA),
          (r.TOOL,args.activation.read_bytes(),r.TOOL_SHA),
          (OLD_TOOL,args.original_full.read_bytes(),OLD_TOOL_SHA),(TOOL,args.original_tune.read_bytes(),TOOL_SHA)]
    for target,raw,digest in rows:
        if sha(raw)!=digest or target.exists(): raise RuntimeError('frozen_helper_or_test_path_drift')
    tests=checked_tests([sys.executable,'-B','-m','unittest','-v','test_switch_glm88','test_review_glm88',
        'test_glm_activation','test_switch_image56','test_activation_review','test_production_release',
        'test_native_preload','test_export_weknora_base'])
    # These server-shaped paths are ONLY inside the disposable --network=none
    # container. No /var/lib host mount is permitted in the packaging command.
    for target,raw,digest in rows:
        target.parent.mkdir(mode=0o700,parents=True,exist_ok=False)
        target.write_bytes(raw); target.chmod(0o600)
    with tempfile.TemporaryDirectory(dir='/root') as tmp:
        delivered=Path(tmp)/NAME; delivered.write_bytes(source); delivered.chmod(0o600)
        checked_tests([sys.executable,'-I','-B',str(delivered),'--help'])
        code='''import importlib.util,sys,unittest
spec=importlib.util.spec_from_file_location('switch_glm88',sys.argv[1])
s=importlib.util.module_from_spec(spec); sys.modules['switch_glm88']=s; spec.loader.exec_module(s)
s.r=s.load_review()
sys.modules['review_glm88']=s.r
assert s.r.e.TARGET_MODEL==128 and sum(s.r.TARGET.values())==88
assert s.r.sw.a.native is not None
suite=unittest.defaultTestLoader.loadTestsFromNames(['test_switch_glm88','test_review_glm88'])
result=unittest.TextTestRunner(verbosity=2).run(suite)
assert result.wasSuccessful() and not result.skipped and result.testsRun>=70
'''
        frozen=checked_tests([sys.executable,'-B','-c',code,str(delivered)])
    args.output.mkdir(parents=True)
    for name,raw in ((NAME,source),('LOCAL-TESTS.txt',tests),('PACKAGED-FROZEN-TESTS.txt',frozen),
                     ('RUNBOOK.txt',(HERE/'GLM88-SWITCH-RUNBOOK.txt').read_bytes())):
        (args.output/name).write_bytes(raw)
    proof=dict(script=NAME,sha256=sha(source),bytes=len(source),python37_grammar='PASS',
        linux_tests='PASS_ZERO_SKIPS',packaged_exact_frozen_tests='PASS_ZERO_SKIPS',
        real_frozen_loader_chain='PASS_IN_DISPOSABLE_CONTAINER',review_sha256=s.REVIEW_SHA,proposal_sha256=s.PROPOSAL_SHA,
        production_execution='NOT_EXECUTED_LOCALLY',production_writes=0,provider_requests=0,
        controlled_mutation='ONE_SIGTSTP_AND_ONE_ENV_ONLY_RECREATE_CAS',
        target_pools=r.TARGET,target_model_default=128,mineru_slots=16,
        performance_acceptance='PENDING_PRODUCTION_OBSERVATION')
    (args.output/'VERIFICATION.json').write_text(json.dumps(proof,indent=2)+'\n')
    print(tests.decode().split('----------------------------------------------------------------------')[-1].strip())
    print(frozen.decode().split('----------------------------------------------------------------------')[-1].strip())
    print(json.dumps(proof))


if __name__=='__main__':
    p=argparse.ArgumentParser()
    for name in ('output','review','activation','original-full','original-tune'): p.add_argument('--'+name,type=Path,required=True)
    package(p.parse_args())
