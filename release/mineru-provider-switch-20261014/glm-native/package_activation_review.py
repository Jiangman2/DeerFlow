"""Linux qualification and immutable dual-node delivery; no server connection."""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from package_enrichment_gateway_recovery import checked_tests
from production_node_preload import boot
from review_activation import POLICY_SQL
from recover_enrichment_gateway import OLD_TOOL_SHA

HERE=Path(__file__).resolve().parent
NAME='weknora-glm-activation-review-20261017-r1.py'
MODULES=('verify_weknora_base','export_weknora_base','preload_native_candidate','production_node_preload',
    'preload_transfer_transport','recover_production_preload','gateway_enrichment_baseline',
    'recover_enrichment_gateway','production_release_review','review_activation')


def package(output,original_full,sql_proof):
    if sys.platform!='linux' or os.geteuid()!=0: raise RuntimeError('linux_root_qualification_required')
    if output.exists(): raise RuntimeError('delivery_exists')
    proof=json.loads(sql_proof.read_bytes())
    if proof.get('activation_postgres16_qualification')!='PASS' or proof.get('exact_sql_sha256')!=hashlib.sha256(POLICY_SQL.encode()).hexdigest():
        raise RuntimeError('exact_sql_qualification_required')
    sources={name:(HERE/(name+'.py')).read_text(encoding='utf-8') for name in MODULES}
    for code in sources.values(): ast.parse(code,feature_version=(3,7))
    code=boot(sources)+'''if __name__=='__main__':
 try: sys.exit(sys.modules['review_activation'].main())
 except Exception as e:
  r=sys.modules['review_activation']
  print(__import__('json').dumps(dict(r.common(),preparation='UNCONFIRMED',failed_check=r.recovery.safe_error(e),error_type=type(e).__name__,instruction='USE_STATUS_NO_REPEAT_PREPARE')),flush=True)
  sys.exit(1)
'''
    ast.parse(code,feature_version=(3,7))
    tests=checked_tests([sys.executable,'-B','-m','unittest','-v','test_activation_review',
                        'test_production_release','test_native_preload','test_export_weknora_base'])
    original=original_full.read_bytes()
    if hashlib.sha256(original).hexdigest()!=OLD_TOOL_SHA: raise RuntimeError('original_full_tool_hash')
    with tempfile.TemporaryDirectory(dir='/root') as tmp:
        script=Path(tmp)/NAME; script.write_text(code,encoding='utf-8')
        frozen_original=Path(tmp)/'original-full.py'; frozen_original.write_bytes(original); frozen_original.chmod(0o600)
        checked_tests([sys.executable,'-I','-B',str(script),'--help'])
        smoke='''import runpy,sys,unittest
from pathlib import Path
runpy.run_path(sys.argv[1],run_name='packaged_definitions_only')
f=sys.modules['recover_enrichment_gateway']; f.OLD_TOOL=Path(sys.argv[2]); f.load_original()
suite=unittest.defaultTestLoader.loadTestsFromName('test_activation_review')
result=unittest.TextTestRunner(verbosity=2).run(suite)
assert result.wasSuccessful() and not result.skipped and result.testsRun>=22
'''
        frozen=checked_tests([sys.executable,'-B','-c',smoke,str(script),str(frozen_original)])
    output.mkdir(parents=True)
    (output/NAME).write_bytes(code.encode())
    (output/'LOCAL-TESTS.txt').write_bytes(tests)
    (output/'PACKAGED-TESTS.txt').write_bytes(frozen)
    (output/'SQL-QUALIFICATION.json').write_bytes(sql_proof.read_bytes())
    (output/'RUNBOOK.txt').write_bytes((HERE/'ACTIVATION-REVIEW-RUNBOOK.txt').read_bytes())
    report=dict(script=NAME,sha256=hashlib.sha256(code.encode()).hexdigest(),bytes=len(code.encode()),
        linux_tests='PASS_ZERO_SKIPS',frozen_packaged_tests='PASS_ZERO_SKIPS',python37_grammar='PASS',
        production_operations_executed_locally=False,ready_for_rollout=False,
        source_sha256={k:hashlib.sha256(v.encode()).hexdigest() for k,v in sources.items()})
    (output/'VERIFICATION.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(tests.decode().split('----------------------------------------------------------------------')[-1].strip())
    print(frozen.decode().split('----------------------------------------------------------------------')[-1].strip())
    print(json.dumps({k:report[k] for k in ('script','sha256','bytes')}))


if __name__=='__main__':
    p=argparse.ArgumentParser()
    for name in ('output','original-full','sql-proof'): p.add_argument('--'+name,required=True,type=Path)
    a=p.parse_args(); package(a.output,a.original_full,a.sql_proof)
