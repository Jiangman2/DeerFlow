"""Generate standalone read-only reconciliation without replacing prior files."""
import ast
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import package_retire as p


def main():
    name = 'mineru-retirement-reconcile-20261016-r1-fix1'
    out = p.ARTIFACTS/name
    assert not out.exists(), 'never_overwrite_delivery'
    tests = subprocess.run([sys.executable, '-B', '-m', 'unittest']+p.TESTS+['test_reconcile_retirement', '-q'],
        cwd=str(p.HERE), capture_output=True, text=True, timeout=60)
    assert tests.returncode == 0 and 'OK' in tests.stderr, tests.stderr
    audit = (p.ARTIFACTS/'mineru-old-pool-audit-20261016-r1-fix1/mineru-old-pool-audit-20261016-r1-fix1.py').read_bytes()
    stage = (p.HERE/'stage_runtime_probe.py').read_bytes()
    previous = (p.ARTIFACTS/'mineru-old-pool-retire-20261016-r1/mineru-old-pool-retire-20261016-r1.py').read_bytes()
    previous_reconcile = (p.ARTIFACTS/'mineru-retirement-reconcile-20261016-r1/mineru-retirement-reconcile-20261016-r1.py').read_bytes()
    assert p.sha(audit) == '2d43207e9f9e091c3dd49305d573d77bac1407dcb346ff881f2d9dd70a390b40'
    assert p.sha(stage) == 'a5e23fa8b381c6443c86ceabba0e19db14d5a22072a04962d86da1f651e6f503'
    assert p.sha(previous) == '3789f99a2f4064b3cdb76a06497ae997a8efe3c4977c1a36c12bcb2e0e98b2d9'
    assert p.sha(previous_reconcile) == 'a734db8526e046bdbd98300a926878c645e9b217b96372e58986d83ad60dfb49'
    live = (p.HERE/'retire_runtime_probe.py').read_bytes().decode()
    live = live.replace("'__EMBED_FROZEN_STAGE_PROBE__'", repr(stage.decode()))
    retire = (p.HERE/'retire_old_pool.py').read_bytes().decode()
    retire = retire.replace("'__EMBED_REVIEWED_AUDIT__'", repr(audit.decode()))
    retire = retire.replace("'__EMBED_RETIRE_RUNTIME__'", repr(live))
    source = (p.HERE/'reconcile_retirement.py').read_bytes().decode()
    assert source.count("'__EMBED_CORRECTED_RETIRE__'") == 1
    source = source.replace("'__EMBED_CORRECTED_RETIRE__'", repr(retire))
    for body in (source, retire, live, audit, stage):
        ast.parse(body, feature_version=(3, 7))
    ns = {'__name__': 'packaged_reconcile'}
    exec(compile(source, '<packaged-reconcile>', 'exec'), ns)
    assert ns['RETIRE_CODE'] == retire
    child = {'__name__': 'packaged_retire'}
    exec(compile(retire, '<packaged-retire>', 'exec'), child)
    assert child['AUDIT_CODE'].encode() == audit and child['RUNTIME_CODE'] == live
    raw = source.encode()
    out.mkdir(parents=True)
    (out/(name+'.py')).write_bytes(raw)
    (out/'RUNBOOK.txt').write_bytes((p.HERE/'RECONCILE-RUNBOOK.txt').read_bytes())
    record = dict(script_sha256=p.sha(raw), bytes=len(raw), tests=tests.stderr.strip(),
        python37_grammar='PASS', local_platform='Windows Python3.11',
        production_execution='PENDING_OPERATOR_EXECUTION', production_writes=0,
        provider_requests=0, credentials_included=False, original_artifact_sha256=p.sha(previous),
        previous_reconcile_sha256=p.sha(previous_reconcile),
        real_terminal_audit_guard_integration='PASS_WITH_EXTERNAL_IO_MOCKED',
        original_artifacts_overwritten=False, ready_to_expand_16=False)
    (out/'VERIFICATION.json').write_text(json.dumps(record, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(dict(script=str(out/(name+'.py')), **record)))


if __name__ == '__main__':
    main()
