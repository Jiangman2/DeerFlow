"""Record the observed final local fixture run; never executes a server action."""
import ast
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXPECTED = '13b18107c4be2018d74e6b5d4fea00ab36b46ff091a4a601958da593dfbb5cec'
PRIOR = 'd16a3bbae87dc5a2b8ec56802b2c223e84e55da1c027f65691380782420d48af'


def ref(path):
    raw = path.read_bytes()
    return {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}


files = {name: ref(HERE / name) for name in
         ('recover_gateway.py', 'recover_gateway_template.py', 'test_recovery.py')}
assert files['recover_gateway.py']['sha256'] == EXPECTED
final_source = (HERE / 'recover_gateway.py').read_bytes()
prior_source = final_source.replace(
    b"            completion_status = 'GATEWAY_ALREADY_COMPLETE_REVIEW_REQUIRED'\n            result['gateway_completion'] = completion",
    b"            result.update(status='GATEWAY_ALREADY_COMPLETE_REVIEW_REQUIRED', gateway_completion=completion)")
prior_source = prior_source.replace(
    b"            completion_status = 'GATEWAY_COMPLETE_REVIEW_REQUIRED'\n            result['gateway_completion'] = completion",
    b"            result.update(status='GATEWAY_COMPLETE_REVIEW_REQUIRED', gateway_completion=completion, image_import_may_be_partial=False)")
prior_source = prior_source.replace(b"        result.update(status=completion_status, image_import_may_be_partial=False)\n", b'')
assert len(prior_source) == 25254 and hashlib.sha256(prior_source).hexdigest() == PRIOR
ast.parse((HERE / 'recover_gateway.py').read_bytes(), feature_version=(3, 7))
tests = ast.parse((HERE / 'test_recovery.py').read_bytes())
names = [node.name for node in ast.walk(tests)
         if isinstance(node, ast.FunctionDef) and node.name.startswith('test_')]
assert len(names) == 10
value = {
    'schema': 'portal-gateway-single-recovery-qualification.v1',
    'status': 'PASS_LOCAL_PREPARATION_ONLY',
    'files': files,
    'python37_syntax': True,
    'linux_fixture_runs': [{
        'passed': 9, 'failed': 0, 'seconds': 0.979, 'exitcode': 0,
        'script_sha256': PRIOR,
        'fixtures': [name for name in names if name != 'test_final_snapshot_failure_keeps_incomplete_status'],
        'execution_session': 64987,
        'completion_chunk': 'aef6af',
    }, {
        'passed': 2, 'failed': 0, 'seconds': 0.228, 'exitcode': 0,
        'script_sha256': EXPECTED, 'completion_chunk': '703160',
        'fixtures': ['test_final_snapshot_failure_keeps_incomplete_status',
                     'test_main_imports_only_gateway_holds_lock_and_stops_after_success'],
        'scope': 'Final status delta: one new failure fixture and the affected success/one-shot fixture rerun',
    }],
    'unique_fixtures': 10,
    'all_ten_single_run': False,
    'final_delta_verified_against_prior_sha': True,
    'container': {
        'context': 'desktop-linux',
        'image': 'sha256:1353c9a1723ad9a8f05be830d951311623e153488500b1e22b3bcb059e676630',
        'pull': 'never', 'network': 'none', 'rootfs_and_source': 'read-only',
        'cpus': 1, 'memory_bytes': 268435456, 'pids_limit': 128,
        'tmpfs_bytes': 67108864, 'tmpfs_mode': '0700', 'automatic_rm': True,
    },
    'diagnostic_evidence': ref(HERE.parent / 'native-phase-b-diagnostic-evidence-r1/REPORT.json'),
    'embedded_guard': ref(HERE.parent / 'native-phase-b-resume-r1/resume_phase_b_bound.py'),
    'original_suites_repeated': False,
    'server_execution': False, 'image_import': False, 'image_delete': False,
    'production_mutation': False, 'ready_for_rollout': False,
    'limitations': [
        'Local Linux fixture subprocesses and mocked containerd responses only; no server or real image unpack was exercised.',
        'The Gateway failure cause remains undetermined until direct subprocess diagnostics are obtained.',
        'Storage, content, image identity and snapshot gates must pass freshly on datax-02.',
        'This delivery handles Gateway only; remaining image preloads and native runtime acceptance remain pending.',
    ],
}
with (HERE / 'QUALIFICATION.json').open('xb') as stream:
    stream.write((json.dumps(value, sort_keys=True, indent=2) + '\n').encode())
print(json.dumps({'status': value['status'], 'script': files['recover_gateway.py'],
                  'unique_fixtures': 10, 'executions_passed': 11, 'failed': 0}, sort_keys=True))
