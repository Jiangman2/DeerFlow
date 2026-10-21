"""Freeze the narrow Redis-only recovery; qualify against the actual prior delivery."""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
TESTS = ('test_resume_enrichment_preload', 'test_enrichment_preload_diagnostic',
         'test_frozen_enrichment_recovery', 'test_preload_enrichment_images', 'test_transfer_base',
         'test_export_enrichment_images', 'test_export_weknora_base', 'test_native_preload')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--previous-tool', type=Path, required=True)
    args = parser.parse_args(); target = args.output.resolve()
    if target.exists(): raise RuntimeError('delivery_exists_do_not_overwrite')
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise RuntimeError('package_on_root_linux_for_private_path_tests')
    import diagnose_enrichment_preload as diagnostic
    if hashlib.sha256(args.previous_tool.read_bytes()).hexdigest() != diagnostic.TOOL_SHA:
        raise RuntimeError('previous_delivery_changed')
    env = dict(os.environ, FROZEN_ENRICHMENT_PRELOAD=str(args.previous_tool.resolve()), PYTHONDONTWRITEBYTECODE='1')
    tests = subprocess.run([sys.executable, '-B', '-m', 'unittest', '-v']+list(TESTS), cwd=str(HERE), env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120)
    if tests.returncode or b'skipped=' in tests.stdout or b'... skipped' in tests.stdout:
        raise RuntimeError(tests.stdout.decode('utf-8', 'replace'))
    names = ('diagnose_enrichment_preload', 'resume_enrichment_preload')
    sources = {name: (HERE/(name+'.py')).read_text(encoding='utf-8') for name in names}
    bootstrap = '#!/usr/bin/env python3\nimport sys,types\n'
    for name, source in sources.items():
        ast.parse(source, feature_version=(3, 7))
        bootstrap += '_m=types.ModuleType('+repr(name)+')\n'
        bootstrap += '_m.__file__='+repr(name+'.py')+'\nsys.modules['+repr(name)+']=_m\n'
        bootstrap += 'exec(compile('+repr(source)+','+repr(name+'.py')+',"exec"),_m.__dict__)\n'
    bootstrap += 'if __name__ == "__main__":\n    sys.exit(sys.modules["resume_enrichment_preload"].main())\n'
    ast.parse(bootstrap, feature_version=(3, 7))
    payload = bootstrap.encode(); target.mkdir(parents=True)
    filename = 'weknora-enrichment-preload-resume-20261017-r1.py'
    output = target/filename; output.write_bytes(payload)
    result = subprocess.run([sys.executable, '-I', '-B', str(output), '--help'],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
    if result.returncode: raise RuntimeError('isolated_delivered_help_failed')
    (target/'LOCAL-TESTS.txt').write_bytes(tests.stdout)
    (target/'RUNBOOK.txt').write_bytes((HERE/'ENRICHMENT-PRELOAD-RECOVERY-RUNBOOK.txt').read_bytes())
    import resume_enrichment_preload as recovery
    report = dict(script=filename, script_sha256=hashlib.sha256(payload).hexdigest(),
        sources={k: hashlib.sha256(v.encode()).hexdigest() for k, v in sources.items()},
        linux_tests='PASS_ZERO_SKIPS_SEE_LOCAL_TESTS', python37_grammar='PASS',
        frozen_previous_delivery_compatibility='PASS', isolated_python_help='PASS',
        real_enterprise_import='NOT_EXECUTED_LOCALLY', previous_tool=str(diagnostic.TOOL),
        previous_tool_sha256=diagnostic.TOOL_SHA, previous_receipts=recovery.RECEIPTS,
        state=str(recovery.STATE), original_state=str(recovery.OLD),
        postgres_reimport=False, source_transfer=False, provider_requests=0, production_writes=0)
    (target/'VERIFICATION.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(dict(delivery=str(output), sha256=report['script_sha256'])))


if __name__ == '__main__': main()
