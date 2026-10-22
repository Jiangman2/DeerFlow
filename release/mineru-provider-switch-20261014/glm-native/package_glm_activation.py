"""Build a standalone activator only after real SQL and Linux qualification."""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from package_model_stage import MODULES as STAGE_MODULES
from package_enrichment_gateway_recovery import checked_tests
from production_node_preload import boot
from recover_enrichment_gateway import OLD_TOOL_SHA
from diagnose_mineru_quarantine import TOOL_SHA

HERE=Path(__file__).resolve().parent
NAME='weknora-glm-activate56-20261017-r1.py'
MODULES=STAGE_MODULES+('glm_document_migration','glm_activation_plan','activate_glm56')


def sha(value): return hashlib.sha256(value).hexdigest()


def package(args):
    if sys.platform!='linux' or os.geteuid()!=0: raise RuntimeError('linux_root_qualification_required')
    if args.output.exists(): raise RuntimeError('delivery_exists')
    proof=json.loads(args.sql_proof.read_bytes())
    if proof.get('glm_activation_postgres16')!='PASS' or proof.get('document_migration',{}).get('rows')!=23964:
        raise RuntimeError('real_document_batch_sql_qualification_required')
    for name in ('glm_document_migration','glm_activation_plan','activate_glm56'):
        if proof.get('source_sha256',{}).get(name+'.py')!=sha((HERE/(name+'.py')).read_bytes()):
            raise RuntimeError('source_changed_since_sql_qualification:'+name)
    sources={}
    for name in MODULES:
        path=(HERE.parent if name=='diagnose_mineru_quarantine' else HERE)/(name+'.py')
        sources[name]=path.read_text(encoding='utf-8'); ast.parse(sources[name],feature_version=(3,7))
    code=boot(sources)+'''if __name__=='__main__':
 try: sys.exit(sys.modules['activate_glm56'].main())
 except Exception as e:
  r=sys.modules['activate_glm56']
  print(__import__('json').dumps(dict(glm_activation='UNCONFIRMED',failed_check=r.sw.recovery.safe_error(e),error_type=type(e).__name__,raw_error_printed=False,instruction='USE_STATUS_NO_REPEAT_APPLY_NO_FORCE_TERMINATION_NO_IMAGE_ONLY_ROLLBACK')),flush=True)
  sys.exit(1)
'''
    ast.parse(code,feature_version=(3,7))
    tests=checked_tests([sys.executable,'-B','-m','unittest','-v','test_glm_activation',
        'test_switch_image56','test_activation_review','test_production_release','test_native_preload','test_export_weknora_base'])
    full,tune=args.original_full.read_bytes(),args.original_tune.read_bytes()
    if sha(full)!=OLD_TOOL_SHA or sha(tune)!=TOOL_SHA: raise RuntimeError('frozen_helpers_changed')
    with tempfile.TemporaryDirectory(dir='/root') as tmp:
        root=Path(tmp); script=root/NAME; script.write_text(code,encoding='utf-8')
        for name,data in (('full.py',full),('tune.py',tune)):
            dest=root/name; dest.write_bytes(data); dest.chmod(0o600)
        checked_tests([sys.executable,'-I','-B',str(script),'--help'])
        smoke='''import runpy,sys,unittest
from pathlib import Path
runpy.run_path(sys.argv[1],run_name='packaged_definitions_only')
sys.modules['recover_enrichment_gateway'].OLD_TOOL=Path(sys.argv[2])
sys.modules['diagnose_mineru_quarantine'].TOOL=Path(sys.argv[3])
r=sys.modules['activate_glm56']; r.sw.initialize()
assert r.sw.e.CURRENT==r.sw.CAPS and r.sw.e.CURRENT_MODEL==64
suite=unittest.defaultTestLoader.loadTestsFromName('test_glm_activation')
result=unittest.TextTestRunner(verbosity=2).run(suite)
assert result.wasSuccessful() and not result.skipped and result.testsRun>=25
'''
        frozen=checked_tests([sys.executable,'-B','-c',smoke,str(script),str(root/'full.py'),str(root/'tune.py')])
    args.output.mkdir(parents=True)
    (args.output/NAME).write_bytes(code.encode())
    (args.output/'LOCAL-TESTS.txt').write_bytes(tests)
    (args.output/'PACKAGED-TESTS.txt').write_bytes(frozen)
    (args.output/'SQL-QUALIFICATION.json').write_bytes(args.sql_proof.read_bytes())
    (args.output/'RUNBOOK.txt').write_bytes((HERE/'GLM-ACTIVATE56-RUNBOOK.txt').read_bytes())
    report=dict(script=NAME,sha256=sha(code.encode()),bytes=len(code.encode()),
        linux_tests='PASS_ZERO_SKIPS',packaged_frozen_tests='PASS_ZERO_SKIPS',python37_grammar='PASS',
        postgres16_document_migration_rows=23964,production_operations_executed_locally=False,
        production_model_activation='NOT_PERFORMED',native_glm_key_requested=False,
        source_sha256={k:sha(v.encode()) for k,v in sources.items()})
    (args.output/'VERIFICATION.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(tests.decode().split('----------------------------------------------------------------------')[-1].strip())
    print(frozen.decode().split('----------------------------------------------------------------------')[-1].strip())
    print(json.dumps({k:report[k] for k in ('script','sha256','bytes')}))


if __name__=='__main__':
    p=argparse.ArgumentParser()
    for name in ('output','original-full','original-tune','sql-proof'): p.add_argument('--'+name,required=True,type=Path)
    package(p.parse_args())
