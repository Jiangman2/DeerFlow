"""Freeze reviewed exporter/helpers into one Python3.7-compatible operator file."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent


def main():
    p = argparse.ArgumentParser(); p.add_argument('--output', type=Path, required=True)
    target = p.parse_args().output.resolve()
    if target.exists(): raise RuntimeError('delivery_exists_do_not_overwrite')
    tests = subprocess.run([sys.executable, '-B', '-m', 'unittest', '-v',
                            'test_export_enrichment_images', 'test_export_weknora_base'],
                           cwd=str(HERE), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120)
    if tests.returncode: raise RuntimeError(tests.stdout.decode('utf-8', 'replace'))
    names = ('verify_weknora_base', 'export_weknora_base', 'export_enrichment_images')
    sources = {n: (HERE/(n+'.py')).read_text(encoding='utf-8') for n in names}
    bootstrap = '#!/usr/bin/env python3\nimport sys,types\n'
    for name, source in sources.items():
        ast.parse(source, feature_version=(3, 7))
        bootstrap += '_m=types.ModuleType('+repr(name)+')\n'
        bootstrap += '_m.__file__='+repr(name+'.py')+'\nsys.modules['+repr(name)+']=_m\n'
        bootstrap += 'exec(compile('+repr(source)+','+repr(name+'.py')+',"exec"),_m.__dict__)\n'
    bootstrap += 'if __name__ == "__main__":\n    sys.exit(sys.modules["export_enrichment_images"].main())\n'
    ast.parse(bootstrap, feature_version=(3, 7))
    payload = bootstrap.encode('utf-8'); sha = hashlib.sha256(payload).hexdigest()
    target.mkdir(parents=True)
    output = target/'weknora-enrichment-export-images-20261017-r1.py'
    output.write_bytes(payload)
    result = subprocess.run([sys.executable, '-I', '-B', str(output), '--help'],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
    if result.returncode: raise RuntimeError('delivered_isolated_help_failed')
    (target/'LOCAL-TESTS.txt').write_bytes(tests.stdout)
    (target/'RUNBOOK.txt').write_bytes((HERE/'ENRICHMENT-EXPORT-RUNBOOK.txt').read_bytes())
    verification = {'script_sha256': sha, 'sources': {k: hashlib.sha256(v.encode()).hexdigest() for k, v in sources.items()},
        'local_tests': 'PASS_SEE_LOCAL_TESTS', 'python37_grammar': 'PASS', 'isolated_python_help': 'PASS',
        'real_enterprise_export': 'NOT_EXECUTED_LOCALLY', 'private_image_archive_only': True,
        'production_data_clone': False, 'production_writes': 0, 'provider_requests': 0,
        'ready_for_rollout': False}
    (target/'VERIFICATION.json').write_text(json.dumps(verification, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'delivery': str(output), 'sha256': sha}, ensure_ascii=False))


if __name__ == '__main__': main()
