"""Immutable standalone delivery after Linux and frozen-module qualification."""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from package_enrichment_gateway_recovery import checked_tests
from package_activation_review import MODULES as REVIEW_MODULES
from production_node_preload import boot
from recover_enrichment_gateway import OLD_TOOL_SHA
from diagnose_mineru_quarantine import TOOL_SHA
from switch_image56 import MODEL_SQL

HERE=Path(__file__).resolve().parent
NAME='weknora-glm-image56-switch-20261017-r1.py'
MODULES=REVIEW_MODULES+('diagnose_mineru_quarantine','switch_image56')


def package(output,original_full,original_tune,sql_proof):
    if sys.platform!='linux' or os.geteuid()!=0: raise RuntimeError('linux_root_qualification_required')
    if output.exists(): raise RuntimeError('delivery_exists')
    proof=json.loads(sql_proof.read_bytes())
    if proof.get('image56_postgres16_qualification')!='PASS' or proof.get('exact_sql_sha256')!=hashlib.sha256(MODEL_SQL.encode()).hexdigest():
        raise RuntimeError('exact_sql_qualification_required')
    sources={}
    for name in MODULES:
        directory=HERE.parent if name=='diagnose_mineru_quarantine' else HERE
        sources[name]=(directory/(name+'.py')).read_text(encoding='utf-8')
    for code in sources.values(): ast.parse(code,feature_version=(3,7))
    code=boot(sources)+'''if __name__=='__main__':
 try: sys.exit(sys.modules['switch_image56'].main())
 except Exception as e:
  r=sys.modules['switch_image56']
  print(__import__('json').dumps(dict(image_switch='UNCONFIRMED',failed_check=r.recovery.safe_error(e),error_type=type(e).__name__,instruction='USE_STATUS_NO_REPEAT_APPLY_NO_FORCE_TERMINATION',raw_error_printed=False)),flush=True)
  sys.exit(1)
'''
    ast.parse(code,feature_version=(3,7))
    tests=checked_tests([sys.executable,'-B','-m','unittest','-v','test_switch_image56',
                        'test_activation_review','test_production_release','test_native_preload','test_export_weknora_base'])
    full,tune=original_full.read_bytes(),original_tune.read_bytes()
    if hashlib.sha256(full).hexdigest()!=OLD_TOOL_SHA or hashlib.sha256(tune).hexdigest()!=TOOL_SHA:
        raise RuntimeError('frozen_original_hash')
    with tempfile.TemporaryDirectory(dir='/root') as tmp:
        script=Path(tmp)/NAME; script.write_text(code,encoding='utf-8')
        for name,data in (('full.py',full),('tune.py',tune)):
            dest=Path(tmp)/name; dest.write_bytes(data); dest.chmod(0o600)
        checked_tests([sys.executable,'-I','-B',str(script),'--help'])
        smoke='''import runpy,sys,unittest
from pathlib import Path
runpy.run_path(sys.argv[1],run_name='packaged_definitions_only')
sys.modules['recover_enrichment_gateway'].OLD_TOOL=Path(sys.argv[2])
sys.modules['diagnose_mineru_quarantine'].TOOL=Path(sys.argv[3])
r=sys.modules['switch_image56']; r.initialize()
assert r.e.obs is r.obs and r.e.CURRENT==r.CAPS and r.e.CURRENT_MODEL==64
suite=unittest.defaultTestLoader.loadTestsFromName('test_switch_image56')
result=unittest.TextTestRunner(verbosity=2).run(suite)
assert result.wasSuccessful() and not result.skipped and result.testsRun>=31
'''
        frozen=checked_tests([sys.executable,'-B','-c',smoke,str(script),str(Path(tmp)/'full.py'),str(Path(tmp)/'tune.py')])
    output.mkdir(parents=True)
    (output/NAME).write_bytes(code.encode())
    (output/'LOCAL-TESTS.txt').write_bytes(tests); (output/'PACKAGED-TESTS.txt').write_bytes(frozen)
    (output/'SQL-QUALIFICATION.json').write_bytes(sql_proof.read_bytes())
    (output/'RUNBOOK.txt').write_bytes((HERE/'IMAGE56-SWITCH-RUNBOOK.txt').read_bytes())
    report=dict(script=NAME,sha256=hashlib.sha256(code.encode()).hexdigest(),bytes=len(code.encode()),
        linux_tests='PASS_ZERO_SKIPS',frozen_packaged_tests='PASS_ZERO_SKIPS',python37_grammar='PASS',
        production_operations_executed_locally=False,production_rollout='NOT_PERFORMED',
        source_sha256={k:hashlib.sha256(v.encode()).hexdigest() for k,v in sources.items()})
    (output/'VERIFICATION.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(tests.decode().split('----------------------------------------------------------------------')[-1].strip())
    print(frozen.decode().split('----------------------------------------------------------------------')[-1].strip())
    print(json.dumps({k:report[k] for k in ('script','sha256','bytes')}))


if __name__=='__main__':
    p=argparse.ArgumentParser()
    for name in ('output','original-full','original-tune','sql-proof'): p.add_argument('--'+name,required=True,type=Path)
    a=p.parse_args(); package(a.output,a.original_full,a.original_tune,a.sql_proof)
