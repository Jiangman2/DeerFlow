#!/usr/bin/env python3
"""Freeze a tested standalone exporter; never overwrite an issued artifact."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    target = parser.parse_args().output.resolve()
    if target.exists(): raise RuntimeError('delivery_already_exists')
    result = subprocess.run([sys.executable, '-B', '-m', 'unittest', '-v', 'test_export_weknora_base'],
                            cwd=str(HERE), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120)
    if result.returncode: raise RuntimeError(result.stdout.decode('utf-8', errors='replace'))
    modules = {name: (HERE/(name+'.py')).read_text(encoding='utf-8')
               for name in ('verify_weknora_base', 'export_weknora_base')}
    for source in modules.values(): ast.parse(source, feature_version=(3, 7))
    # All dependencies below are exact reviewed source bytes, not runtime files
    # imported from an operator-writable cwd. Works with python -I -B.
    bootstrap = '#!/usr/bin/env python3\nimport sys,types\n'
    for name, source in modules.items():
        bootstrap += '_m=types.ModuleType('+repr(name)+')\n'
        bootstrap += '_m.__file__='+repr(name+'.py')+'\nsys.modules['+repr(name)+']=_m\n'
        bootstrap += 'exec(compile('+repr(source)+','+repr(name+'.py')+',"exec"),_m.__dict__)\n'
    bootstrap += 'if __name__ == "__main__":\n    sys.exit(sys.modules["export_weknora_base"].main())\n'
    ast.parse(bootstrap, feature_version=(3, 7))
    payload = bootstrap.encode('utf-8')
    sha = hashlib.sha256(payload).hexdigest()
    target.mkdir(parents=True)
    script = target/'weknora-glm-export-base-20261016-r1.py'
    script.write_bytes(payload)
    help_result = subprocess.run([sys.executable, '-I', '-B', str(script), '--help'],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
    if help_result.returncode: raise RuntimeError('bundled_help_failed')
    (target/'export-tests.txt').write_bytes(result.stdout)
    verification = {'delivery_kind': 'PRIVATE_BASE_EXPORT_ONLY_NOT_BUILD_OR_ROLLOUT',
                    'script_sha256': sha,
                    'source_sha256': {k: hashlib.sha256(v.encode()).hexdigest() for k, v in modules.items()},
                    'offline_tests': 'PASS_SEE_EXPORT_TESTS', 'python37_grammar': 'PASS',
                    'bundled_isolated_python_help': 'PASS', 'linux_filesystem_test': 'NOT_RUN_ON_WINDOWS',
                    'real_containerd_export': 'NOT_RUN_LOCALLY', 'production_workloads_mutated': False,
                    'provider_requests': 0, 'credentials_included': False, 'ready_for_rollout': False}
    (target/'VERIFICATION.json').write_text(json.dumps(verification, indent=2)+'\n', encoding='utf-8')
    (target/'RUNBOOK.txt').write_bytes((HERE/'BASE-EXPORT-RUNBOOK.txt').read_bytes())
    (target/'SHA256SUMS.txt').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.name+'\n'
                                              for p in sorted(target.iterdir()) if p.is_file()), encoding='ascii')
    print(json.dumps({'delivery': str(target), 'script_sha256': sha}, ensure_ascii=False))


if __name__ == '__main__': main()
