"""Build one frozen Python3.7+ delivery after Linux safety tests; no server access."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
TESTS = ('test_preload_enrichment_images', 'test_transfer_base', 'test_export_enrichment_images',
         'test_export_weknora_base', 'test_native_preload')


def main():
    p = argparse.ArgumentParser(); p.add_argument('--output', type=Path, required=True)
    target = p.parse_args().output.resolve()
    if target.exists(): raise RuntimeError('delivery_exists_do_not_overwrite')
    if sys.platform != 'linux': raise RuntimeError('package_on_linux_so_private_tests_do_not_skip')
    tests = subprocess.run([sys.executable, '-B', '-m', 'unittest', '-v']+list(TESTS), cwd=str(HERE),
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120)
    if tests.returncode or b'skipped=' in tests.stdout or b'... skipped' in tests.stdout:
        raise RuntimeError(tests.stdout.decode('utf-8', 'replace'))
    names = ('verify_weknora_base', 'export_weknora_base', 'export_enrichment_images',
             'preload_native_candidate', 'transfer_base', 'preload_enrichment_images')
    sources = {name: (HERE/(name+'.py')).read_text(encoding='utf-8') for name in names}
    bootstrap = '#!/usr/bin/env python3\nimport sys,types\n'
    for name, source in sources.items():
        ast.parse(source, feature_version=(3, 7))
        bootstrap += '_m=types.ModuleType('+repr(name)+')\n'
        bootstrap += '_m.__file__='+repr(name+'.py')+'\nsys.modules['+repr(name)+']=_m\n'
        bootstrap += 'exec(compile('+repr(source)+','+repr(name+'.py')+',"exec"),_m.__dict__)\n'
    bootstrap += 'if __name__ == "__main__":\n    sys.exit(sys.modules["preload_enrichment_images"].main())\n'
    ast.parse(bootstrap, feature_version=(3, 7))
    payload = bootstrap.encode(); target.mkdir(parents=True)
    filename = 'weknora-enrichment-preload-images-20261017-r1.py'
    output = target/filename; output.write_bytes(payload)
    result = subprocess.run([sys.executable, '-I', '-B', str(output), '--help'],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
    if result.returncode: raise RuntimeError('delivered_isolated_help_failed')
    (target/'LOCAL-TESTS.txt').write_bytes(tests.stdout)
    (target/'RUNBOOK.txt').write_bytes((HERE/'ENRICHMENT-PRELOAD-RUNBOOK.txt').read_bytes())
    import preload_enrichment_images as runtime
    verification = dict(script=filename, script_sha256=hashlib.sha256(payload).hexdigest(),
        sources={k: hashlib.sha256(v.encode()).hexdigest() for k, v in sources.items()},
        linux_tests='PASS_ZERO_SKIPS_SEE_LOCAL_TESTS', python37_grammar='PASS', isolated_python_help='PASS',
        real_enterprise_transfer_and_import='NOT_EXECUTED_LOCALLY', targets=list(runtime.ITEMS),
        no_production_store_connections=True, no_image_build=True, no_rollout=True)
    (target/'VERIFICATION.json').write_text(json.dumps(verification, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(dict(delivery=str(output), sha256=verification['script_sha256'])))


if __name__ == '__main__': main()
