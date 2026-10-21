"""Record the observed local fixture qualification without executing any server action."""
import ast
import base64
import hashlib
import json
from pathlib import Path
import zlib

HERE = Path(__file__).resolve().parent
EXPECTED = '438d6485e32d40c120f9ca81d69e21aec91a76d0a6a79a70873219b31c75a798'


def ref(path):
    raw = path.read_bytes()
    return dict(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())


files = {name: ref(HERE / name) for name in ('resume_remaining.py', 'continue_remaining_template.py', 'test_remaining.py', 'bind_candidate.py')}
assert files['resume_remaining.py']['sha256'] == EXPECTED
source = (HERE / 'resume_remaining.py').read_bytes()
tree = ast.parse(source, feature_version=(3, 7))
constants = {node.targets[0].id: ast.literal_eval(node.value) for node in tree.body
             if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
             and node.targets[0].id in ('SUPERVISOR_SHA', 'SUPERVISOR_Z64', 'SUCCESS_REPORT_SHA')}
supervisor = zlib.decompress(base64.b64decode(constants['SUPERVISOR_Z64']))
assert supervisor == (HERE.parent / 'native-gateway-recovery-r1/recover_gateway.py').read_bytes()
assert hashlib.sha256(supervisor).hexdigest() == constants['SUPERVISOR_SHA']
assert constants['SUCCESS_REPORT_SHA'] == ref(HERE.parent / 'native-gateway-recovery-evidence-r1/REPORT.json')['sha256']
tests = ast.parse((HERE / 'test_remaining.py').read_bytes())
names = [node.name for node in ast.walk(tests) if isinstance(node, ast.FunctionDef) and node.name.startswith('test_')]
assert len(names) == 10
value = dict(
    schema='portal-remaining-preload-qualification.v1', status='PASS_LOCAL_PREPARATION_ONLY', files=files,
    python37_syntax=True, supervisor_reused_byte_exact=True,
    embedded_supervisor=ref(HERE.parent / 'native-gateway-recovery-r1/recover_gateway.py'),
    accepted_gateway_success_report=ref(HERE.parent / 'native-gateway-recovery-evidence-r1/REPORT.json'),
    linux_fixture_runs=[dict(passed=10, failed=0, seconds=1.686, exitcode=0, completion_chunk='0682c0',
                            script_sha256=EXPECTED, fixtures=names)],
    unique_fixtures=10, original_supervisor_suites_repeated=False,
    container=dict(context='desktop-linux', image='sha256:1353c9a1723ad9a8f05be830d951311623e153488500b1e22b3bcb059e676630',
                   pull='never', network='none', rootfs_and_source='read-only', cpus=1, memory_bytes=268435456,
                   pids_limit=128, tmpfs_bytes=67108864, tmpfs_mode='0700', automatic_rm=True),
    server_execution=False, image_import=False, image_delete=False, production_mutation=False, ready_for_rollout=False,
    limitations=[
        'The new fixtures exercise real local child processes and mocked containerd/storage responses; no server or image unpack was executed.',
        'All original content, reference, host, sealed metadata and live storage gates must pass freshly on datax-02.',
        'The script only imports the remaining four roles, preserves prior frontend/Gateway-forward results and requires final six-image verification.',
        'Preload completion does not establish native runtime acceptance, Kubernetes resource capacity or production rollout readiness.',
        'The unchanged supervisor retains its historical GATEWAY-IMPORT-* internal receipt names in separately owned per-role evidence directories.',
    ])
with (HERE / 'QUALIFICATION.json').open('xb') as stream:
    stream.write((json.dumps(value, sort_keys=True, indent=2) + '\n').encode())
print(json.dumps(dict(status=value['status'], script=files['resume_remaining.py'], fixtures_passed=10), sort_keys=True))
