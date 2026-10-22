"""Qualify the startup-only diagnostic against the exact delivered full-App tool."""
import argparse
import ast
import hashlib
import inspect
import json
import os
from pathlib import Path
import sys
import tempfile

from package_full_enrichment import TESTS
from package_enrichment_gateway_recovery import checked_tests
from recover_enrichment_gateway import OLD_TOOL_SHA
from diagnose_enrichment_startup import OLD_RESULT_SHA, RECOVERY_TOOL_SHA, RUNTIME_SHA
import startup_safe_diagnostics

HERE = Path(__file__).resolve().parent
NAME = 'weknora-enrichment-startup-diagnostic-20261017-r1.py'


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--original',type=Path,required=True)
    args=parser.parse_args()
    if sys.platform!='linux' or os.geteuid()!=0: raise RuntimeError('linux_root_required')
    if args.output.exists(): raise RuntimeError('delivery_already_exists')
    if hashlib.sha256(args.original.read_bytes()).hexdigest()!=OLD_TOOL_SHA:
        raise RuntimeError('original_delivery_changed')
    log=checked_tests([sys.executable,'-B','-m','unittest','-v',
                      'test_enrichment_startup_diagnostic','test_gateway_enrichment_recovery']+TESTS)
    boot='#!/usr/bin/env python3\nimport sys,types\n'; hashes={}
    for name in ('gateway_enrichment_baseline','recover_enrichment_gateway',
                 'startup_safe_diagnostics','diagnose_enrichment_startup'):
        source=(HERE/(name+'.py')).read_text(encoding='utf-8')
        ast.parse(source,feature_version=(3,7)); hashes[name]=hashlib.sha256(source.encode()).hexdigest()
        boot+='_m=types.ModuleType('+repr(name)+'); _m.__file__='+repr(name+'.py')+'\n'
        boot+='sys.modules['+repr(name)+']=_m\nexec(compile('+repr(source)+','+repr(name+'.py')+',"exec"),_m.__dict__)\n'
        if name=='startup_safe_diagnostics':
            boot+='_m.SAFE_FUNCTION_SOURCE='+repr(inspect.getsource(startup_safe_diagnostics.safe_startup))+'\n'
    boot+='if __name__=="__main__":\n    sys.exit(sys.modules["diagnose_enrichment_startup"].main())\n'
    ast.parse(boot,feature_version=(3,7))
    with tempfile.TemporaryDirectory() as temp:
        target=Path(temp)/NAME; target.write_text(boot,encoding='utf-8')
        checked_tests([sys.executable,'-I','-B',str(target),'--help'])
        code='''import runpy,sys,hashlib,unittest
from pathlib import Path
runpy.run_path(sys.argv[1],run_name='offline_packaged')
r=sys.modules['recover_enrichment_gateway']; r.OLD_TOOL=Path(sys.argv[2])
a=r.load_original()
assert hashlib.sha256(a.RUNTIME_SOURCE.encode()).hexdigest()==sys.argv[3]
suite=unittest.defaultTestLoader.loadTestsFromNames(['test_enrichment_startup_diagnostic','test_gateway_enrichment_recovery','test_full_enrichment'])
result=unittest.TextTestRunner(verbosity=2).run(suite)
assert result.wasSuccessful() and not result.skipped and result.testsRun==76
'''
        packaged_log=checked_tests([sys.executable,'-B','-c',code,str(target),str(args.original.resolve()),RUNTIME_SHA])
    args.output.mkdir(parents=True)
    (args.output/NAME).write_bytes(boot.encode())
    sha=hashlib.sha256(boot.encode()).hexdigest()
    (args.output/'RUNBOOK.txt').write_bytes((HERE/'ENRICHMENT-STARTUP-DIAGNOSTIC-RUNBOOK.txt').read_bytes())
    (args.output/'LOCAL-TESTS.txt').write_bytes(log)
    (args.output/'PACKAGED-FROZEN-TESTS.txt').write_bytes(packaged_log)
    report=dict(script=NAME,sha256=sha,sources=hashes,original_tool_sha256=OLD_TOOL_SHA,
        previous_recovery_tool_sha256=RECOVERY_TOOL_SHA,previous_failure_sha256=OLD_RESULT_SHA,
        frozen_runtime_sha256=RUNTIME_SHA,linux_offline_tests='360_PASS_ZERO_SKIPS',
        packaged_frozen_original_tests='76_PASS_ZERO_SKIPS',python37_grammar='PASS',
        production_writes=0,provider_requests=0,server_execution='NOT_EXECUTED_LOCALLY',
        native_enrichment_acceptance='NOT_TESTED',ready_for_rollout=False)
    (args.output/'VERIFICATION.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(log.decode('utf-8','replace').split('----------------------------------------------------------------------')[-1].strip())
    print(json.dumps(dict(delivery=str(args.output/NAME),sha256=sha,bytes=len(boot.encode()),
                         packaged_frozen_tests='76_PASS')))


if __name__=='__main__': main()
