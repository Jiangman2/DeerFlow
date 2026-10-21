"""Package the fixture-config repair; reuse exact candidate and original real gates."""
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
from diagnose_enrichment_startup import RUNTIME_SHA
from recover_enrichment_config import DIAGNOSTIC_RESULT_SHA, FIXTURE_RESULT_SHA
import startup_safe_diagnostics

HERE=Path(__file__).resolve().parent
NAME='weknora-enrichment-config-recovery-20261017-r1.py'


def main():
    p=argparse.ArgumentParser()
    for name in ('output','original','config-proof'): p.add_argument('--'+name,type=Path,required=True)
    args=p.parse_args()
    if sys.platform!='linux' or os.geteuid()!=0: raise RuntimeError('linux_root_required')
    if args.output.exists(): raise RuntimeError('delivery_already_exists')
    if hashlib.sha256(args.original.read_bytes()).hexdigest()!=OLD_TOOL_SHA: raise RuntimeError('original_delivery_changed')
    proof=json.loads(args.config_proof.read_bytes())
    if not (proof.get('fixture_config_reproduction')=='PASS' and proof.get('original_exact_dereference_panics') is True and
            proof.get('fixed_extract_pointers_present') is True and proof.get('fixed_summary_pointer_present') is True and
            proof.get('provider_requests')==0 and proof.get('proxy')=='LOCAL_FILE_ONLY' and
            proof.get('probe_sha256')==hashlib.sha256((HERE/'fixture_config_probe.go').read_bytes()).hexdigest()):
        raise RuntimeError('config_reproduction_receipt_required')
    log=checked_tests([sys.executable,'-B','-m','unittest','-v','test_enrichment_config_recovery',
        'test_enrichment_startup_diagnostic','test_gateway_enrichment_recovery']+TESTS)
    boot='#!/usr/bin/env python3\nimport sys,types\n'; hashes={}
    for name in ('gateway_enrichment_baseline','recover_enrichment_gateway','startup_safe_diagnostics',
                 'diagnose_enrichment_startup','fixture_config_fix','recover_enrichment_config'):
        source=(HERE/(name+'.py')).read_text(encoding='utf-8')
        ast.parse(source,feature_version=(3,7)); hashes[name]=hashlib.sha256(source.encode()).hexdigest()
        boot+='_m=types.ModuleType('+repr(name)+'); _m.__file__='+repr(name+'.py')+'\n'
        boot+='sys.modules['+repr(name)+']=_m\nexec(compile('+repr(source)+','+repr(name+'.py')+',"exec"),_m.__dict__)\n'
        if name=='startup_safe_diagnostics':
            boot+='_m.SAFE_FUNCTION_SOURCE='+repr(inspect.getsource(startup_safe_diagnostics.safe_startup))+'\n'
    boot+='if __name__=="__main__":\n    sys.exit(sys.modules["recover_enrichment_config"].main())\n'
    ast.parse(boot,feature_version=(3,7))
    with tempfile.TemporaryDirectory() as temp:
        target=Path(temp)/NAME; target.write_text(boot,encoding='utf-8')
        checked_tests([sys.executable,'-I','-B',str(target),'--help'])
        code='''import runpy,sys,hashlib,json,unittest
from pathlib import Path
runpy.run_path(sys.argv[1],run_name='offline_packaged')
r=sys.modules['recover_enrichment_gateway']; r.OLD_TOOL=Path(sys.argv[2]); a=r.load_original()
assert hashlib.sha256(a.RUNTIME_SOURCE.encode()).hexdigest()==sys.argv[3]
proof=json.loads(Path(sys.argv[4]).read_bytes())
assert a.spec_hash(a.clean_config())==proof['original_config_sha256']
assert a.spec_hash(sys.modules['fixture_config_fix'].corrected_config(a.clean_config()))==proof['corrected_config_sha256']
suite=unittest.defaultTestLoader.loadTestsFromNames(['test_enrichment_config_recovery','test_enrichment_startup_diagnostic','test_gateway_enrichment_recovery','test_full_enrichment'])
result=unittest.TextTestRunner(verbosity=2).run(suite)
assert result.wasSuccessful() and not result.skipped and result.testsRun==90
'''
        packaged=checked_tests([sys.executable,'-B','-c',code,str(target),str(args.original.resolve()),RUNTIME_SHA,str(args.config_proof.resolve())])
    args.output.mkdir(parents=True)
    (args.output/NAME).write_bytes(boot.encode()); sha=hashlib.sha256(boot.encode()).hexdigest()
    (args.output/'RUNBOOK.txt').write_bytes((HERE/'ENRICHMENT-CONFIG-RECOVERY-RUNBOOK.txt').read_bytes())
    (args.output/'LOCAL-TESTS.txt').write_bytes(log)
    (args.output/'PACKAGED-FROZEN-TESTS.txt').write_bytes(packaged)
    (args.output/'LOCAL-GO-CONFIG-PROBE.json').write_bytes(args.config_proof.read_bytes())
    report=dict(script=NAME,sha256=sha,sources=hashes,original_tool_sha256=OLD_TOOL_SHA,
        diagnostic_result_sha256=DIAGNOSTIC_RESULT_SHA,diagnostic_fixture_result_sha256=FIXTURE_RESULT_SHA,
        linux_offline_tests='374_PASS_ZERO_SKIPS',packaged_frozen_original_tests='90_PASS_ZERO_SKIPS',
        go_viper_pointer_reproduction='PASS_MINIMAL_WINDOWS_AMD64_NOT_FULL_APP',
        config_proof_sha256=hashlib.sha256(args.config_proof.read_bytes()).hexdigest(),python37_grammar='PASS',
        server_execution='NOT_EXECUTED_LOCALLY',production_writes=0,provider_requests=0,ready_for_rollout=False)
    (args.output/'VERIFICATION.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(log.decode('utf-8','replace').split('----------------------------------------------------------------------')[-1].strip())
    print(json.dumps(dict(delivery=str(args.output/NAME),sha256=sha,bytes=len(boot.encode()),packaged_frozen_tests='90_PASS')))


if __name__=='__main__': main()
