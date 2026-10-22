"""Package an offline-tested, read-only, exact-receipt runtime byte comparator."""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

HERE=Path(__file__).resolve().parent


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(); target=args.output.resolve()
    if target.exists(): raise RuntimeError('delivery_exists')
    if sys.platform!='linux' or os.geteuid()!=0: raise RuntimeError('root_linux_tests_required')
    tests=subprocess.run([sys.executable,'-B','-m','unittest','-v','test_classify_enrichment_assets',
        'test_audit_enrichment_runtime'],cwd=str(HERE),stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=120)
    if tests.returncode or b'skipped=' in tests.stdout or b'... skipped' in tests.stdout:
        raise RuntimeError(tests.stdout.decode('utf-8','replace'))
    names=('diagnose_enrichment_preload','audit_enrichment_runtime','classify_enrichment_assets')
    sources={n:(HERE/(n+'.py')).read_text(encoding='utf-8') for n in names}
    boot='#!/usr/bin/env python3\nimport sys,types\n'
    for name,source in sources.items():
        ast.parse(source,feature_version=(3,7))
        boot+='_m=types.ModuleType('+repr(name)+')\n_m.__file__='+repr(name+'.py')+'\n'
        boot+='sys.modules['+repr(name)+']=_m\nexec(compile('+repr(source)+','+repr(name+'.py')+',"exec"),_m.__dict__)\n'
    boot+='if __name__=="__main__":\n    sys.exit(sys.modules["classify_enrichment_assets"].main())\n'
    ast.parse(boot,feature_version=(3,7))
    target.mkdir(parents=True); script=target/'weknora-runtime-byte-compare-20261017-r1.py'
    script.write_bytes(boot.encode())
    result=subprocess.run([sys.executable,'-I','-B',str(script),'--help'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=15)
    if result.returncode: raise RuntimeError('packaged_help_failed')
    # Exercise the real packaged functions, not just the source-module tests.
    verify='''import hashlib,runpy,sys
runpy.run_path(sys.argv[1],run_name='frozen_test')
c=sys.modules['classify_enrichment_assets']; a=sys.modules['audit_enrichment_runtime']
def sha(x): return hashlib.sha256(x).hexdigest()
name='app/config/example.yaml'; raw=b'k: v\\n'
row=dict(kind='file',sha256=sha(raw),content_classification=c.newline_hashes(raw))
assert c.classify({name:sha(b'k: v\\r\\n')},{name:row})['byte_differences_explained']
assert not c.classify({name:sha(b'k: wrong\\r\\n')},{name:row})['byte_differences_explained']
'''
    result=subprocess.run([sys.executable,'-I','-B','-c',verify,str(script)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=15)
    if result.returncode: raise RuntimeError('packaged_function_test_failed')
    (target/'LOCAL-TESTS.txt').write_bytes(tests.stdout)
    (target/'RUNBOOK.txt').write_bytes((HERE/'ENRICHMENT-BYTE-COMPARE-RUNBOOK.txt').read_bytes())
    report=dict(script=script.name,sha256=hashlib.sha256(script.read_bytes()).hexdigest(),
        linux_offline_tests='PASS_ZERO_SKIPS',packaged_function_test='PASS',python37_grammar='PASS',
        server_execution='NOT_EXECUTED_LOCALLY',production_writes=0,provider_requests=0)
    (target/'VERIFICATION.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(delivery=str(script),sha256=report['sha256'])))


if __name__=='__main__': main()
