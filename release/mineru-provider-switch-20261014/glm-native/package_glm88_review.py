"""Qualify the read-only review against the actual shipped GLM activator."""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import review_glm88 as r
from package_enrichment_gateway_recovery import checked_tests
from recover_enrichment_gateway import OLD_TOOL_SHA
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from diagnose_mineru_quarantine import TOOL_SHA

HERE=Path(__file__).resolve().parent
NAME='weknora-glm88-review-20261017-r1.py'


def sha(data): return hashlib.sha256(data).hexdigest()


def package(args):
    if sys.platform!='linux' or os.geteuid()!=0: raise RuntimeError('linux_root_qualification_required')
    if args.output.exists(): raise RuntimeError('delivery_exists')
    source=(HERE/'review_glm88.py').read_bytes(); ast.parse(source,feature_version=(3,7))
    activation,full,tune=args.activation.read_bytes(),args.original_full.read_bytes(),args.original_tune.read_bytes()
    if (sha(activation),sha(full),sha(tune))!=(r.TOOL_SHA,OLD_TOOL_SHA,TOOL_SHA): raise RuntimeError('frozen_dependency_drift')
    tests=checked_tests([sys.executable,'-B','-m','unittest','-v','test_review_glm88','test_glm_activation',
        'test_switch_image56','test_activation_review','test_production_release','test_native_preload','test_export_weknora_base'])
    with tempfile.TemporaryDirectory(dir='/root') as tmp:
        root=Path(tmp)
        for name,raw in ((NAME,source),('activate.py',activation),('full.py',full),('tune.py',tune)):
            p=root/name; p.write_bytes(raw); p.chmod(0o600)
        checked_tests([sys.executable,'-I','-B',str(root/NAME),'--help'])
        smoke='''import importlib.util,sys,unittest
from pathlib import Path
spec=importlib.util.spec_from_file_location('review_glm88',sys.argv[1])
r=importlib.util.module_from_spec(spec); sys.modules['review_glm88']=r; spec.loader.exec_module(r)
r.TOOL=Path(sys.argv[2]); r.a=r.load_activation(); r.sw=r.a.sw
sys.modules['recover_enrichment_gateway'].OLD_TOOL=Path(sys.argv[3])
sys.modules['diagnose_mineru_quarantine'].TOOL=Path(sys.argv[4])
r.sw.initialize(); r.obs=r.sw.obs; r.e=r.sw.e
assert r.e.CURRENT==r.CURRENT and r.e.TARGET==r.TARGET and r.e.TARGET_MODEL==128
suite=unittest.defaultTestLoader.loadTestsFromName('test_review_glm88')
result=unittest.TextTestRunner(verbosity=2).run(suite)
assert result.wasSuccessful() and not result.skipped and result.testsRun>=30
'''
        frozen=checked_tests([sys.executable,'-B','-c',smoke,str(root/NAME),str(root/'activate.py'),str(root/'full.py'),str(root/'tune.py')])
    args.output.mkdir(parents=True)
    for name,raw in ((NAME,source),('LOCAL-TESTS.txt',tests),('PACKAGED-FROZEN-TESTS.txt',frozen),
                     ('RUNBOOK.txt',(HERE/'GLM88-REVIEW-RUNBOOK.txt').read_bytes())):
        (args.output/name).write_bytes(raw)
    proof=dict(script=NAME,sha256=sha(source),bytes=len(source),python37_grammar='PASS',
        linux_tests='PASS_ZERO_SKIPS',packaged_exact_frozen_tests='PASS_ZERO_SKIPS',
        frozen_activation_sha256=r.TOOL_SHA,activation_pass_receipt_sha256=r.RECEIPT_SHA,
        operator_reported_production_business_acceptance=True,production_writes=0,
        server_execution='NOT_EXECUTED_LOCALLY',ready_to_apply=False)
    (args.output/'VERIFICATION.json').write_text(json.dumps(proof,indent=2)+'\n')
    print(tests.decode().split('----------------------------------------------------------------------')[-1].strip())
    print(frozen.decode().split('----------------------------------------------------------------------')[-1].strip())
    print(json.dumps(proof))


if __name__=='__main__':
    p=argparse.ArgumentParser()
    for name in ('output','activation','original-full','original-tune'): p.add_argument('--'+name,type=Path,required=True)
    package(p.parse_args())
