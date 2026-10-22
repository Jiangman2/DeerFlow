"""Package the one-shot replica expansion; never replace an older delivery."""
import ast
import json
import subprocess
import sys

import package_retire as p
import expand16 as expand
import expand_runtime_probe as probe


def main():
    name = 'mineru-expand16-20261016-r1'
    out = p.ARTIFACTS/name
    assert not out.exists(), 'never_overwrite_delivery'
    tests = subprocess.run([sys.executable, '-B', '-m', 'unittest'] + p.TESTS +
        ['test_reconcile_retirement', 'test_expansion_review', 'test_expand16', '-q'],
        cwd=str(p.HERE), capture_output=True, text=True, timeout=90)
    assert tests.returncode == 0 and 'OK' in tests.stderr, tests.stderr
    review = (p.ARTIFACTS/'mineru-expansion-review-20261016-r1/mineru-expansion-review-20261016-r1.py').read_bytes()
    stage = (p.HERE/'stage_runtime_probe.py').read_bytes()
    assert p.sha(review) == expand.REVIEW_SHA
    assert p.sha(stage) == probe.STAGE_SHA
    live = (p.HERE/'expand_runtime_probe.py').read_text(encoding='utf-8')
    assert live.count("'__EMBED_FROZEN_STAGE_PROBE__'") == 1
    live = live.replace("'__EMBED_FROZEN_STAGE_PROBE__'", repr(stage.decode('utf-8')))
    source = (p.HERE/'expand16.py').read_text(encoding='utf-8')
    for placeholder, body in [('__EMBED_PINNED_EXPANSION_REVIEW__', review.decode('utf-8')),
                              ('__EMBED_EXPANSION_RUNTIME__', live)]:
        assert source.count(repr(placeholder)) == 1
        source = source.replace(repr(placeholder), repr(body))
    for body in (source, live, review, stage):
        ast.parse(body, feature_version=(3, 7))
    ns = dict(__name__='packaged_expansion', __file__=str(out/(name+'.py')))
    exec(compile(source, '<packaged-expansion>', 'exec'), ns)
    assert ns['REVIEW_CODE'].encode() == review and ns['PROBE_CODE'] == live
    assert ns['PLAN'] == expand.PLAN and ns['NODE_RECEIPTS'] == expand.NODE_RECEIPTS
    live_ns = dict(__name__='packaged_runtime_probe')
    exec(compile(live, '<packaged-runtime-probe>', 'exec'), live_ns)
    assert live_ns['STAGE_CODE'].encode() == stage
    assert len(live.encode()) < 65536, 'pod_exec_argument_limit'
    raw = source.encode('utf-8')
    out.mkdir(parents=True, exist_ok=False)
    (out/(name+'.py')).write_bytes(raw)
    (out/'RUNBOOK.txt').write_bytes((p.HERE/'EXPAND16-RUNBOOK.txt').read_bytes())
    record = dict(script_sha256=p.sha(raw), bytes=len(raw),
        embedded_review_sha256=p.sha(review), embedded_runtime_probe_sha256=p.sha(live.encode()),
        embedded_stage_probe_sha256=p.sha(stage), tests=tests.stderr.strip(),
        python37_grammar='PASS', local_platform='Windows Python3.11',
        external_io='MOCKED_IN_LOCAL_TESTS', exact_candidate_dispatcher='DISCOVERY_AND_QUEUE_PRESERVATION_TESTED',
        production_execution='PENDING_OPERATOR_EXECUTION',
        maximum_live_patches=6, live_patch_scope='NEW_WORKER_SPEC_REPLICAS_ONLY',
        target_worker_counts=expand.PLAN, dispatcher_modified=False,
        public_route_modified=False, credentials_included=False, provider_submissions=0,
        automatic_retry=False, automatic_rollback=False, original_artifacts_overwritten=False,
        post_expansion_user_end_to_end='RECOMMENDED')
    (out/'VERIFICATION.json').write_text(json.dumps(record, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(dict(script=str(out/(name+'.py')), **record)))


if __name__ == '__main__':
    main()
