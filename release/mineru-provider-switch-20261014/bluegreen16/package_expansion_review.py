"""Build a single three-node read-only review tool; never replace prior artifacts."""
import ast
import json
import subprocess
import sys
from pathlib import Path

import package_retire as p
import expansion_review as review


def main():
    name = 'mineru-expansion-review-20261016-r1'
    out = p.ARTIFACTS/name
    assert not out.exists(), 'never_overwrite_delivery'
    tests = subprocess.run([sys.executable, '-B', '-m', 'unittest']+
        p.TESTS+['test_reconcile_retirement', 'test_expansion_review', '-q'],
        cwd=str(p.HERE), capture_output=True, text=True, timeout=90)
    assert tests.returncode == 0 and 'OK' in tests.stderr, tests.stderr
    code = (p.ARTIFACTS/'mineru-retirement-reconcile-20261016-r1-fix1/mineru-retirement-reconcile-20261016-r1-fix1.py').read_bytes()
    assert p.sha(code) == review.RECONCILE_SHA
    source = (p.HERE/'expansion_review.py').read_text(encoding='utf-8')
    assert source.count("'__EMBED_PINNED_RECONCILE__'") == 1
    source = source.replace("'__EMBED_PINNED_RECONCILE__'", repr(code.decode()))
    ast.parse(source, feature_version=(3, 7))
    ns = dict(__name__='packaged_review', __file__=str(out/(name+'.py')))
    exec(compile(source, '<packaged-review>', 'exec'), ns)
    assert ns['RECONCILE_CODE'].encode() == code
    raw = source.encode()
    out.mkdir(parents=True)
    (out/(name+'.py')).write_bytes(raw)
    (out/'RUNBOOK.txt').write_bytes((p.HERE/'EXPANSION-REVIEW-RUNBOOK.txt').read_bytes())
    record = dict(script_sha256=p.sha(raw), bytes=len(raw), tests=tests.stderr.strip(),
        python37_grammar='PASS', local_platform='Windows Python3.11',
        runtime_parsers_and_frozen_capacity_getters='TESTED_WITH_EXTERNAL_IO_MOCKED',
        production_execution='PENDING_OPERATOR_EXECUTION',
        production_writes=0, provider_requests=0, credentials_included=False,
        original_artifacts_overwritten=False, ready_to_expand_16=False)
    (out/'VERIFICATION.json').write_text(json.dumps(record, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(dict(script=str(out/(name+'.py')), **record)))


if __name__ == '__main__':
    main()
