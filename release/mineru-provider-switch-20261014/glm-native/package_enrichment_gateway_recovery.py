"""Offline-qualify and package the receipt-bound Gateway replica recovery."""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from package_full_enrichment import TESTS
from recover_enrichment_gateway import OLD_TOOL_SHA, OLD_RESULT_SHA

HERE = Path(__file__).resolve().parent
NAME = 'weknora-enrichment-gateway-recovery-20261017-r1.py'


def checked_tests(argv):
    value = subprocess.run(argv, cwd=str(HERE), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=240)
    if value.returncode or b'skipped=' in value.stdout or b'... skipped' in value.stdout:
        sys.stdout.buffer.write(value.stdout); raise RuntimeError('qualification_failed_or_skipped')
    return value.stdout


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--original', type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != 'linux' or os.geteuid() != 0: raise RuntimeError('linux_root_qualification_required')
    if args.output.exists(): raise RuntimeError('delivery_already_exists')
    if hashlib.sha256(args.original.read_bytes()).hexdigest() != OLD_TOOL_SHA:
        raise RuntimeError('historical_delivery_changed')
    log = checked_tests([sys.executable, '-B', '-m', 'unittest', '-v', 'test_gateway_enrichment_recovery']+TESTS)
    boot = '#!/usr/bin/env python3\nimport sys,types\n'
    hashes = {}
    for name in ('gateway_enrichment_baseline', 'recover_enrichment_gateway'):
        source = (HERE/(name+'.py')).read_text(encoding='utf-8')
        ast.parse(source, feature_version=(3, 7)); hashes[name] = hashlib.sha256(source.encode()).hexdigest()
        boot += '_m=types.ModuleType('+repr(name)+'); _m.__file__='+repr(name+'.py')+'\n'
        boot += 'sys.modules['+repr(name)+']=_m\nexec(compile('+repr(source)+','+repr(name+'.py')+',"exec"),_m.__dict__)\n'
    boot += 'if __name__=="__main__":\n    sys.exit(sys.modules["recover_enrichment_gateway"].main())\n'
    ast.parse(boot, feature_version=(3, 7))
    with tempfile.TemporaryDirectory() as temp:
        target = Path(temp)/NAME; target.write_text(boot, encoding='utf-8')
        checked_tests([sys.executable, '-I', '-B', str(target), '--help'])
        # Exercise the PACKAGED recovery and exact delivered original orchestrator,
        # not just current worktree copies. All cluster/model operations are mocked.
        code = '''import runpy,sys,hashlib,unittest
from pathlib import Path
runpy.run_path(sys.argv[1],run_name='offline_packaged')
r=sys.modules['recover_enrichment_gateway']; r.OLD_TOOL=Path(sys.argv[2])
a=r.load_original()
assert hashlib.sha256(a.RUNTIME_SOURCE.encode()).hexdigest()=='840459bb2292c638239c19f18beb8018f0f910d9ffa949c2612b0b094b5f6f81'
suite=unittest.defaultTestLoader.loadTestsFromNames(['test_gateway_enrichment_recovery','test_full_enrichment'])
result=unittest.TextTestRunner(verbosity=2).run(suite)
assert result.wasSuccessful() and not result.skipped and result.testsRun==52
'''
        packaged_log = checked_tests([sys.executable, '-B', '-c', code, str(target), str(args.original.resolve())])
    args.output.mkdir(parents=True)
    (args.output/NAME).write_bytes(boot.encode())
    sha = hashlib.sha256(boot.encode()).hexdigest()
    (args.output/'RUNBOOK.txt').write_bytes((HERE/'ENRICHMENT-GATEWAY-RECOVERY-RUNBOOK.txt').read_bytes())
    (args.output/'LOCAL-TESTS.txt').write_bytes(log)
    (args.output/'PACKAGED-FROZEN-TESTS.txt').write_bytes(packaged_log)
    report = dict(script=NAME, sha256=sha, original_script_sha256=OLD_TOOL_SHA,
        original_failure_sha256=OLD_RESULT_SHA, sources=hashes, linux_offline_tests='PASS_ZERO_SKIPS',
        packaged_frozen_original_tests='52_PASS_ZERO_SKIPS', python37_grammar='PASS',
        server_execution='NOT_EXECUTED_LOCALLY', production_writes=0, provider_requests=0,
        ready_for_rollout=False)
    (args.output/'VERIFICATION.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print(log.decode('utf-8', 'replace').split('----------------------------------------------------------------------')[-1].strip())
    print(json.dumps(dict(delivery=str(args.output/NAME), sha256=sha, bytes=len(boot.encode()),
                         packaged_frozen_tests='52_PASS')))


if __name__ == '__main__': main()
