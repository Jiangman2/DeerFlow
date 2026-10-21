"""Create the Linux-tested read-only runtime audit delivery."""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

HERE=Path(__file__).resolve().parent
TESTS=('test_audit_enrichment_runtime','test_enrichment_preload_diagnostic','test_frozen_enrichment_recovery',
       'test_resume_enrichment_preload','test_preload_enrichment_images','test_runtime_reverify')


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--previous-tool',type=Path,required=True); args=parser.parse_args()
    target=args.output.resolve()
    if target.exists(): raise RuntimeError('delivery_exists')
    if sys.platform!='linux' or os.geteuid()!=0: raise RuntimeError('root_linux_tests_required')
    env=dict(os.environ,FROZEN_ENRICHMENT_PRELOAD=str(args.previous_tool.resolve()),PYTHONDONTWRITEBYTECODE='1')
    tests=subprocess.run([sys.executable,'-B','-m','unittest','-v']+list(TESTS),cwd=str(HERE),env=env,
                         stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=120)
    if tests.returncode or b'skipped=' in tests.stdout or b'... skipped' in tests.stdout:
        raise RuntimeError(tests.stdout.decode('utf-8','replace'))
    sources={n:(HERE/(n+'.py')).read_text(encoding='utf-8') for n in ('diagnose_enrichment_preload','audit_enrichment_runtime')}
    boot='#!/usr/bin/env python3\nimport sys,types\n'
    for name,source in sources.items():
        ast.parse(source,feature_version=(3,7))
        boot+='_m=types.ModuleType('+repr(name)+')\n_m.__file__='+repr(name+'.py')+'\n'
        boot+='sys.modules['+repr(name)+']=_m\nexec(compile('+repr(source)+','+repr(name+'.py')+',"exec"),_m.__dict__)\n'
    boot+='if __name__=="__main__":\n    sys.exit(sys.modules["audit_enrichment_runtime"].main())\n'
    ast.parse(boot,feature_version=(3,7))
    target.mkdir(parents=True); output=target/'weknora-enrichment-runtime-audit-20261017-r1.py'
    output.write_bytes(boot.encode())
    help_result=subprocess.run([sys.executable,'-I','-B',str(output),'--help'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=15)
    if help_result.returncode: raise RuntimeError('frozen_help_failed')
    (target/'LOCAL-TESTS.txt').write_bytes(tests.stdout)
    (target/'RUNBOOK.txt').write_bytes((HERE/'ENRICHMENT-RUNTIME-AUDIT-RUNBOOK.txt').read_bytes())
    report=dict(script=output.name,sha256=hashlib.sha256(output.read_bytes()).hexdigest(),
        sources={n:hashlib.sha256(s.encode()).hexdigest() for n,s in sources.items()},
        linux_offline_tests='PASS_ZERO_SKIPS',python37_grammar='PASS',isolated_help='PASS',
        real_enterprise_audit='NOT_EXECUTED_LOCALLY',production_writes=0,provider_requests=0)
    (target/'VERIFICATION.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(delivery=str(output),sha256=report['sha256'])))


if __name__=='__main__': main()
