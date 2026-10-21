"""Offline qualification/package for the enrichment-only104 preflight."""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import package_glm88_load as previous

HERE=Path(__file__).resolve().parent
NAME='weknora-glm104-review-20261017-r1.py'
OBSERVER_SHA='04bddd80d533bb5af3e1f5e0594cb59dc54fbcb19dbd971c413d6882614cb23d'


def sha(raw): return hashlib.sha256(raw).hexdigest()


def package(args):
    if sys.platform!='linux' or os.geteuid()!=0 or not Path('/.dockerenv').exists():
        raise RuntimeError('disposable_linux_root_container_required')
    if args.output.exists(): raise RuntimeError('delivery_exists')
    observer=(HERE/'observe_glm88_load.py').read_bytes()
    if sha(observer)!=OBSERVER_SHA: raise RuntimeError('accepted_observer_source_changed')
    entry=(HERE/'review_glm104.py').read_bytes()
    for raw in (observer,entry): ast.parse(raw,feature_version=(3,7))
    boot='#!/usr/bin/env python3\nimport sys,types\n'
    boot+='_o=types.ModuleType("observe_glm88_load"); _o.__file__="observe_glm88_load.py"\n'
    boot+='sys.modules["observe_glm88_load"]=_o\n'
    boot+='exec(compile('+repr(observer.decode())+',_o.__file__,"exec"),_o.__dict__)\n'
    source=(boot+entry.decode()).encode(); ast.parse(source,feature_version=(3,7))
    rows=[(previous.o.TOOL,args.switch.read_bytes(),previous.o.TOOL_SHA),
          (previous.s.REVIEW_TOOL,args.review.read_bytes(),previous.s.REVIEW_TOOL_SHA),
          (previous.r.TOOL,args.activation.read_bytes(),previous.r.TOOL_SHA),
          (previous.OLD_TOOL,args.original_full.read_bytes(),previous.OLD_TOOL_SHA),
          (previous.TOOL,args.original_tune.read_bytes(),previous.TOOL_SHA)]
    for target,raw,digest in rows:
        if sha(raw)!=digest or target.exists(): raise RuntimeError('frozen_helper_or_test_path_drift')
    names=['test_review_glm104','test_observe_glm88_load','test_switch_glm88','test_review_glm88',
           'test_glm_activation','test_switch_image56','test_activation_review','test_production_release',
           'test_native_preload','test_export_weknora_base']
    code='''import unittest,sys
suite=unittest.defaultTestLoader.loadTestsFromNames(sys.argv[1:])
result=unittest.TextTestRunner(verbosity=2).run(suite)
assert result.wasSuccessful() and not result.skipped and result.testsRun>=268
'''
    tests=previous.checked_tests([sys.executable,'-B','-c',code]+names)
    # Server-shaped trust paths are only inside this disposable Linux container.
    for target,raw,digest in rows:
        target.parent.mkdir(mode=0o700,parents=True,exist_ok=False)
        target.write_bytes(raw); target.chmod(0o600)
    with tempfile.TemporaryDirectory(dir='/root') as tmp:
        delivered=Path(tmp)/NAME; delivered.write_bytes(source); delivered.chmod(0o600)
        previous.checked_tests([sys.executable,'-I','-B',str(delivered),'--help'])
        (Path(tmp)/'observe_glm88_load.py').write_bytes(observer)
        code='''import importlib.util,sys,unittest
from pathlib import Path
spec=importlib.util.spec_from_file_location('review_glm104',sys.argv[1])
m=importlib.util.module_from_spec(spec); sys.modules['review_glm104']=m; spec.loader.exec_module(m)
m.initialize()
sys.modules['switch_glm88']=m.o.s; sys.modules['review_glm88']=m.o.r
m.o.__file__=str(Path(sys.argv[1]).parent/'observe_glm88_load.py')
assert m.CURRENT==m.o.r.TARGET and sum(m.TARGET.values())==104 and m.o.r.e.TARGET_MODEL==128
suite=unittest.defaultTestLoader.loadTestsFromNames(['test_review_glm104','test_observe_glm88_load',
 'test_switch_glm88','test_review_glm88'])
result=unittest.TextTestRunner(verbosity=2).run(suite)
assert result.wasSuccessful() and not result.skipped and result.testsRun>=121
'''
        frozen=previous.checked_tests([sys.executable,'-B','-c',code,str(delivered)])
    args.output.mkdir(parents=True)
    for name,raw in ((NAME,source),('LOCAL-TESTS.txt',tests),('PACKAGED-FROZEN-TESTS.txt',frozen),
                     ('RUNBOOK.txt',(HERE/'GLM104-REVIEW-RUNBOOK.txt').read_bytes())):
        (args.output/name).write_bytes(raw)
    proof=dict(script=NAME,sha256=sha(source),bytes=len(source),python37_grammar='PASS',
        sources=dict(observer=sha(observer),entry=sha(entry)),source_tests='PASS_ZERO_SKIPS',
        packaged_frozen_tests='PASS_ZERO_SKIPS',installed_helper_chain='VERIFIED_IN_DISPOSABLE_CONTAINER',
        production_writes=0,signals_sent=0,provider_probe_requests=0,only_proposed_change='ENRICHMENT_48_TO_64',
        server_execution='NOT_EXECUTED_LOCALLY',ready_to_apply=False)
    (args.output/'VERIFICATION.json').write_text(json.dumps(proof,indent=2)+'\n')
    for result in (tests,frozen): print(result.decode().split('----------------------------------------------------------------------')[-1].strip())
    print(json.dumps(proof))


if __name__=='__main__':
    p=argparse.ArgumentParser()
    for name in ('output','switch','review','activation','original-full','original-tune'):
        p.add_argument('--'+name,type=Path,required=True)
    package(p.parse_args())
