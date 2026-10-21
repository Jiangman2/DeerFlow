#!/usr/bin/env python3
"""Package a standalone private-transfer tool without credentials."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--output', type=Path, required=True)
    target = parser.parse_args().output.resolve()
    if target.exists(): raise RuntimeError('refuse_existing_delivery')
    tested = subprocess.run([sys.executable, '-B', '-m', 'unittest', '-v',
                             'test_export_weknora_base', 'test_transfer_base'], cwd=str(HERE),
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120)
    if tested.returncode: raise RuntimeError(tested.stdout.decode(errors='replace'))
    source = '#!/usr/bin/env python3\nimport types,sys\n'
    identities = {}
    for name in ('verify_weknora_base', 'export_weknora_base', 'transfer_base'):
        code = (HERE/(name+'.py')).read_text(encoding='utf-8')
        ast.parse(code, feature_version=(3, 7))
        identities[name] = hashlib.sha256(code.encode()).hexdigest()
        source += '_m=types.ModuleType('+repr(name)+');_m.__file__='+repr(name+'.py')+'\n'
        source += 'sys.modules['+repr(name)+']=_m\nexec(compile('+repr(code)+','+repr(name+'.py')+',"exec"),_m.__dict__)\n'
    source += 'if __name__=="__main__":\n    sys.exit(sys.modules["transfer_base"].main())\n'
    ast.parse(source, feature_version=(3, 7))
    target.mkdir(parents=True)
    script = target/'weknora-glm-transfer-base-20261016-r1.py'; script.write_bytes(source.encode())
    result = subprocess.run([sys.executable, '-I', '-B', str(script), '--help'],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
    if result.returncode: raise RuntimeError('bundled_cli_failed')
    sha = hashlib.sha256(script.read_bytes()).hexdigest()
    (target/'transfer-tests.txt').write_bytes(tested.stdout)
    verification = {'script_sha256': sha, 'module_sha256': identities,
                    'offline_tests': 'PASS_SEE_TRANSFER_TESTS', 'python37_grammar': 'PASS',
                    'bundled_isolated_help': 'PASS', 'real_ssh_and_sudo': 'NOT_TESTED_LOCALLY',
                    'linux_private_filesystem_test': 'NOT_RUN_ON_WINDOWS',
                    'production_workloads_mutated': False, 'credentials_included': False,
                    'candidate_build_started': False, 'ready_for_rollout': False}
    (target/'VERIFICATION.json').write_text(json.dumps(verification, indent=2)+'\n', encoding='utf-8')
    for name in ('BUILD-INPUT-LOCK.json', 'TRANSFER-RUNBOOK.txt'):
        (target/name).write_bytes((HERE/name).read_bytes())
    (target/'SHA256SUMS.txt').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.name+'\n'
                                              for p in sorted(target.iterdir()) if p.is_file()), encoding='ascii')
    print(json.dumps({'delivery': str(target), 'script_sha256': sha}))


if __name__ == '__main__': main()
