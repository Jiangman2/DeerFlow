"""Frozen small production-preload delivery; local tests are not rollout approval."""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from package_enrichment_gateway_recovery import checked_tests
import production_node_preload as node

HERE=Path(__file__).resolve().parent
NAME='weknora-glm-release-prepare-20261017-r1.py'
MODULES=('verify_weknora_base','export_weknora_base','preload_native_candidate','transfer_base',
    'gateway_enrichment_baseline','recover_enrichment_gateway','production_release_review','prepare_production_release')
REMOTE=('verify_weknora_base','export_weknora_base','preload_native_candidate','production_node_preload')
TESTS=['test_production_release','test_transfer_base','test_export_weknora_base','test_native_preload']


def package(output,original,sql_proof):
    if sys.platform!='linux' or os.geteuid()!=0: raise RuntimeError('linux_root_qualification_required')
    if output.exists(): raise RuntimeError('delivery_exists')
    proof=json.loads(sql_proof.read_bytes())
    if proof.get('local_postgres16_sql')!='PASS' or proof.get('production_connections')!=0: raise RuntimeError('sql_qualification_required')
    sources={name:(HERE/(name+'.py')).read_text(encoding='utf-8') for name in set(MODULES+REMOTE)}
    for source in sources.values(): ast.parse(source,feature_version=(3,7))
    remote_sources={name:sources[name] for name in REMOTE}
    remote='import sys,types\n_sources='+repr(remote_sources)+'\n'
    remote+='for _name,_source in _sources.items():\n _m=types.ModuleType(_name); _m.__file__=_name+".py"; sys.modules[_name]=_m\n exec(compile(_source,_m.__file__,"exec"),_m.__dict__)\n'
    remote+='sys.modules["production_node_preload"].MODULE_SOURCES=_sources\nsys.exit(sys.modules["production_node_preload"].main(["--receive-once"]))\n'
    boot=node.boot({name:sources[name] for name in MODULES})
    boot+='sys.modules["prepare_production_release"].REMOTE_CODE='+repr(remote)+'\n'
    boot+='if __name__=="__main__":\n try: sys.exit(sys.modules["prepare_production_release"].main())\n except Exception as e:\n  _p=sys.modules["prepare_production_release"]; _p.emit({"weknora_release_prepare":"UNCONFIRMED","failed_check":_p.safe_error(e),"production_writes":0,"ready_for_rollout":False}); sys.exit(1)\n'
    ast.parse(boot,feature_version=(3,7)); ast.parse(remote,feature_version=(3,7))
    tests=checked_tests([sys.executable,'-B','-m','unittest','-v']+TESTS)
    with tempfile.TemporaryDirectory() as t:
        f=Path(t)/NAME; f.write_text(boot,encoding='utf-8')
        checked_tests([sys.executable,'-I','-B',str(f),'--help'])
        code='''import runpy,sys,unittest
from pathlib import Path
runpy.run_path(sys.argv[1],run_name='packaged_test')
p=sys.modules['prepare_production_release']; assert len(p.remote_command()[-1].encode())<120000
r=sys.modules['recover_enrichment_gateway']; r.OLD_TOOL=Path(sys.argv[2]); a=r.load_original()
assert sys.modules['production_release_review'].accepted.__module__=='production_release_review'
suite=unittest.defaultTestLoader.loadTestsFromName('test_production_release')
result=unittest.TextTestRunner(verbosity=2).run(suite)
assert result.wasSuccessful() and not result.skipped and result.testsRun==24
'''
        frozen=checked_tests([sys.executable,'-B','-c',code,str(f),str(original.resolve())])
    output.mkdir(parents=True)
    (output/NAME).write_bytes(boot.encode()); (output/'LOCAL-TESTS.txt').write_bytes(tests)
    (output/'PACKAGED-FROZEN-TESTS.txt').write_bytes(frozen)
    (output/'SQL-QUALIFICATION.json').write_bytes(sql_proof.read_bytes())
    (output/'RUNBOOK.txt').write_bytes((HERE/'PRODUCTION-RELEASE-PREPARE-RUNBOOK.txt').read_bytes())
    report=dict(script=NAME,sha256=hashlib.sha256(boot.encode()).hexdigest(),bytes=len(boot.encode()),
        remote_receiver_sha256=hashlib.sha256(remote.encode()).hexdigest(),
        source_sha256={k:hashlib.sha256(v.encode()).hexdigest() for k,v in sources.items()},
        python37_grammar='PASS',linux_tests='PASS_ZERO_SKIPS',packaged_frozen_tests='24_PASS',
        local_postgres16_sql='PASS',server_preload='NOT_EXECUTED_LOCALLY',production_writes=0,ready_for_rollout=False)
    (output/'VERIFICATION.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(tests.decode().split('----------------------------------------------------------------------')[-1].strip())
    print(json.dumps({k:report[k] for k in ('script','sha256','bytes','packaged_frozen_tests')}))


if __name__=='__main__':
    p=argparse.ArgumentParser()
    for name in ('output','original','sql-proof'): p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args(); package(a.output,a.original,a.sql_proof)
