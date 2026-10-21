"""Immutable native helper + production staging tool after actual offline qualification."""
import argparse
import ast
import base64
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import zlib
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from package_image56_switch import MODULES as SWITCH_MODULES
from package_enrichment_gateway_recovery import checked_tests
from production_node_preload import boot
from recover_enrichment_gateway import OLD_TOOL_SHA
from diagnose_mineru_quarantine import TOOL_SHA
import stage_glm_model as stage
import native_credential_transport as crypto

HERE=Path(__file__).resolve().parent
NAME='weknora-glm-model-stage-20261017-r1.py'
MODULES=SWITCH_MODULES+('native_credential_transport','stage_glm_model')


def sha(b): return hashlib.sha256(b).hexdigest()


def package(args):
    if sys.platform!='linux' or os.geteuid()!=0: raise RuntimeError('linux_qualification_required')
    if args.output.exists(): raise RuntimeError('delivery_exists')
    build=json.loads((args.helper.parent/'BUILD.json').read_bytes())
    native=json.loads(args.native_proof.read_bytes()); sql=json.loads(args.sql_proof.read_bytes())
    binary=args.helper.read_bytes()
    if build.get('helper_sha256')!=sha(binary) or native.get('helper_sha256')!=sha(binary): raise RuntimeError('helper_proof_mismatch')
    if native.get('native_credential_qualification')!='PASS' or native.get('platform')!='linux/arm64' or native.get('uid')!=1000:
        raise RuntimeError('native_execution_not_qualified')
    if native.get('launcher_sha256')!=sha(crypto.LAUNCHER.encode()) or native.get('temp_helper_cleanup')!='PASS':
        raise RuntimeError('exact_launcher_not_qualified')
    if build.get('crypto_source_sha256')!=sha((HERE.parents[2]/'vendor/weknora-app-20260912/internal/utils/crypto.go').read_bytes()):
        raise RuntimeError('native_crypto_source_drift')
    if build.get('main_sha256')!=sha((HERE/'credential_helper_main.go').read_bytes()): raise RuntimeError('helper_main_drift')
    if sql.get('model_stage_postgres16')!='PASS' or sql.get('sql_sha256')!=sha(stage.insert_sql('enc:v1:'+'A'*39).encode()) or sql.get('read_sql_sha256')!=sha(stage.READ_SQL.encode()):
        raise RuntimeError('exact_sql_not_qualified')
    sources={}
    for name in MODULES:
        path=(HERE.parent if name=='diagnose_mineru_quarantine' else HERE)/(name+'.py')
        sources[name]=path.read_text(encoding='utf-8'); ast.parse(sources[name],feature_version=(3,7))
    payload=base64.b85encode(zlib.compress(binary,9)).decode()
    code=boot(sources)+"sys.modules['native_credential_transport'].HELPER=__import__('zlib').decompress(__import__('base64').b85decode("+repr(payload)+"))\n"
    code+="sys.modules['native_credential_transport'].HELPER_SHA="+repr(sha(binary))+"\n"
    code+='''if __name__=='__main__':
 try: sys.exit(sys.modules['stage_glm_model'].main())
 except Exception as e:
  r=sys.modules['stage_glm_model']
  print(__import__('json').dumps(dict(glm_model_stage='UNCONFIRMED',error_type=type(e).__name__,failed_check=r.sw.recovery.safe_error(e),raw_error_printed=False,instruction='USE_STATUS_NO_REPEAT_STAGE_NO_DELETE_STATE')),flush=True)
  sys.exit(1)
'''
    ast.parse(code,feature_version=(3,7))
    tests=checked_tests([sys.executable,'-B','-m','unittest','-v','test_stage_glm_model','test_switch_image56',
        'test_activation_review','test_production_release','test_native_preload','test_export_weknora_base'])
    full,tune=args.original_full.read_bytes(),args.original_tune.read_bytes()
    if sha(full)!=OLD_TOOL_SHA or sha(tune)!=TOOL_SHA: raise RuntimeError('frozen_dependency_drift')
    with tempfile.TemporaryDirectory(dir='/root') as tmp:
        p=Path(tmp)/NAME; p.write_text(code,encoding='utf-8')
        for name,raw in (('full.py',full),('tune.py',tune)):
            f=Path(tmp)/name; f.write_bytes(raw); f.chmod(0o600)
        checked_tests([sys.executable,'-I','-B',str(p),'--help'])
        smoke='''import runpy,sys,unittest,hashlib
from pathlib import Path
runpy.run_path(sys.argv[1],run_name='packaged_definitions_only')
sys.modules['recover_enrichment_gateway'].OLD_TOOL=Path(sys.argv[2])
sys.modules['diagnose_mineru_quarantine'].TOOL=Path(sys.argv[3])
r=sys.modules['stage_glm_model']; r.sw.initialize()
c=sys.modules['native_credential_transport']; assert hashlib.sha256(c.HELPER).hexdigest()==c.HELPER_SHA
assert r.sw.e.CURRENT==r.sw.CAPS and r.sw.e.CURRENT_MODEL==64
suite=unittest.defaultTestLoader.loadTestsFromName('test_stage_glm_model')
result=unittest.TextTestRunner(verbosity=2).run(suite)
assert result.wasSuccessful() and not result.skipped and result.testsRun>=21
'''
        frozen=checked_tests([sys.executable,'-B','-c',smoke,str(p),str(Path(tmp)/'full.py'),str(Path(tmp)/'tune.py')])
    args.output.mkdir(parents=True)
    (args.output/NAME).write_bytes(code.encode())
    for name,raw in (('LOCAL-TESTS.txt',tests),('PACKAGED-TESTS.txt',frozen),('SQL-QUALIFICATION.json',args.sql_proof.read_bytes()),
        ('CRYPTO-QUALIFICATION.json',args.native_proof.read_bytes()),('HELPER-BUILD.json',(args.helper.parent/'BUILD.json').read_bytes()),
        ('RUNBOOK.txt',(HERE/'MODEL-STAGE-RUNBOOK.txt').read_bytes())): (args.output/name).write_bytes(raw)
    report=dict(script=NAME,sha256=sha(code.encode()),bytes=len(code.encode()),native_helper_sha256=sha(binary),
        unit_and_frozen_tests='PASS_ZERO_SKIPS',real_postgres16='PASS',arm64_crypto_and_native_format='PASS',
        production_stage_performed=False,production_activation_performed=False,ready_for_rollout=False,
        source_sha256={name:sha(value.encode()) for name,value in sources.items()})
    (args.output/'VERIFICATION.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(tests.decode().split('----------------------------------------------------------------------')[-1].strip())
    print(frozen.decode().split('----------------------------------------------------------------------')[-1].strip())
    print(json.dumps({k:report[k] for k in ('script','sha256','bytes')}))


if __name__=='__main__':
    p=argparse.ArgumentParser()
    for name in ('output','helper','sql-proof','native-proof','original-full','original-tune'):
        p.add_argument('--'+name,type=Path,required=True)
    package(p.parse_args())
