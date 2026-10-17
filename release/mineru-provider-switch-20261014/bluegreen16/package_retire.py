"""Package a single bounded retirement script; preserve every prior artifact."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).absolute().parent
ARTIFACTS = Path('C:/Users/15285/.codex/visualizations/2026/09/24/01a0d2d7-0015-7a73-93c3-791b713a47f6')
NAME = 'mineru-old-pool-retire-20261016-r1'
TESTS = ['test_retire_old_pool', 'test_old_pool_audit', 'test_switch_bluegreen16',
         'test_recover_stage_permissions', 'test_stage_bluegreen16', 'test_prepare_bluegreen16']


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    out = Path(args.output).absolute()
    assert not out.exists(), 'never_overwrite_delivery'
    tests = subprocess.run([sys.executable, '-B', '-m', 'unittest']+TESTS+['-q'],
        cwd=str(HERE), capture_output=True, text=True, timeout=60)
    assert tests.returncode == 0 and 'OK' in tests.stderr, tests.stderr
    audit = (ARTIFACTS/'mineru-old-pool-audit-20261016-r1-fix1/mineru-old-pool-audit-20261016-r1-fix1.py').read_bytes()
    stage = (HERE/'stage_runtime_probe.py').read_bytes()
    assert sha(audit) == '2d43207e9f9e091c3dd49305d573d77bac1407dcb346ff881f2d9dd70a390b40'
    assert sha(stage) == 'a5e23fa8b381c6443c86ceabba0e19db14d5a22072a04962d86da1f651e6f503'
    live = (HERE/'retire_runtime_probe.py').read_bytes().decode('utf-8')
    assert live.count("'__EMBED_FROZEN_STAGE_PROBE__'") == 1
    live = live.replace("'__EMBED_FROZEN_STAGE_PROBE__'", repr(stage.decode('utf-8')))
    source = (HERE/'retire_old_pool.py').read_bytes().decode('utf-8')
    for placeholder, body in [('__EMBED_REVIEWED_AUDIT__', audit.decode('utf-8')), ('__EMBED_RETIRE_RUNTIME__', live)]:
        assert source.count(repr(placeholder)) == 1
        source = source.replace(repr(placeholder), repr(body))
    for body in (source, live, stage, audit):
        ast.parse(body, feature_version=(3, 7))
    ns = {'__name__': 'packaged_retirement', '__file__': str(out/(NAME+'.py'))}
    exec(compile(source, '<packaged-retirement>', 'exec'), ns)
    assert ns['AUDIT_CODE'].encode() == audit and ns['RUNTIME_CODE'] == live
    live_ns = {'__name__': 'packaged_live_check'}
    exec(compile(live, '<packaged-live-check>', 'exec'), live_ns)
    assert live_ns['STAGE_CODE'].encode() == stage
    raw = source.encode('utf-8')
    out.mkdir(parents=True, exist_ok=False)
    (out/(NAME+'.py')).write_bytes(raw)
    (out/'RUNBOOK.txt').write_bytes((HERE/'RETIRE-RUNBOOK.txt').read_bytes())
    verification = dict(script_sha256=sha(raw), embedded_audit_sha256=sha(audit),
        embedded_live_probe_sha256=sha(live.encode()), embedded_stage_probe_sha256=sha(stage),
        tests=tests.stderr.strip(), python37_grammar='PASS', local_platform='Windows Python3.11',
        production_linux_execution='PENDING_OPERATOR_EXECUTION', live_mutation_limit=2,
        live_mutation_scope='OLD_DISPATCHER_AND_WORKER_SPEC_REPLICAS_TO_ZERO_ONLY',
        automatic_retry=False, automatic_rollback=False, final16_expansion=False,
        credentials_included=False, provider_requests=0, original_artifacts_overwritten=False)
    (out/'VERIFICATION.json').write_text(json.dumps(verification, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(dict(script=str(out/(NAME+'.py')), sha256=sha(raw), bytes=len(raw), verification=verification)))


if __name__ == '__main__':
    main()
