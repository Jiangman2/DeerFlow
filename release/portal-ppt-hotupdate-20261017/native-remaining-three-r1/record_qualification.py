import ast
import base64
import hashlib
import json
from pathlib import Path
import zlib

HERE = Path(__file__).resolve().parent
EXPECTED = 'd608ae5de2fe75ec7ae9d57818fd418713a4cad888ce95ce9b4edd6ebac3e962'


def ref(path):
    raw = path.read_bytes()
    return dict(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())


files = {name: ref(HERE / name) for name in ('resume_three.py', 'resume_three_template.py', 'snapshot_gate_part.py',
         'partial_boundary_part.py', 'test_three.py', 'bind_candidate.py', 'RUNNER-CHAINS.json', 'RUNNER-PARENT-OVERLAP.json')}
assert files['resume_three.py']['sha256'] == EXPECTED
tree = ast.parse((HERE / 'resume_three.py').read_bytes(), feature_version=(3, 7))
constants = {node.targets[0].id: ast.literal_eval(node.value) for node in tree.body
             if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
             and node.targets[0].id in ('SUPERVISOR_SHA', 'SUPERVISOR_Z64', 'SUCCESS_REPORT_SHA', 'PARTIAL_REPORT_SHA')}
supervisor = zlib.decompress(base64.b64decode(constants['SUPERVISOR_Z64']))
assert supervisor == (HERE.parent / 'native-gateway-recovery-r1/recover_gateway.py').read_bytes()
assert hashlib.sha256(supervisor).hexdigest() == constants['SUPERVISOR_SHA']
assert constants['SUCCESS_REPORT_SHA'] == ref(HERE.parent / 'native-gateway-recovery-evidence-r1/REPORT.json')['sha256']
assert constants['PARTIAL_REPORT_SHA'] == ref(HERE.parent / 'native-remaining-preload-evidence-r1/REPORT.json')['sha256']
tests = ast.parse((HERE / 'test_three.py').read_bytes())
names = [node.name for node in ast.walk(tests) if isinstance(node, ast.FunctionDef) and node.name.startswith('test_')]
assert len(names) == 8
value = dict(schema='portal-remaining-three-qualification.v1', status='PASS_LOCAL_PREPARATION_ONLY', files=files,
    python37_syntax=True, supervisor_reused_byte_exact=True,
    embedded_supervisor=ref(HERE.parent / 'native-gateway-recovery-r1/recover_gateway.py'),
    accepted_gateway_success_report=ref(HERE.parent / 'native-gateway-recovery-evidence-r1/REPORT.json'),
    accepted_partial_preload_report=ref(HERE.parent / 'native-remaining-preload-evidence-r1/REPORT.json'),
    fixture_harness_reused=ref(HERE.parent / 'native-remaining-preload-r1/test_remaining.py'),
    linux_fixture_runs=[dict(passed=8, failed=0, seconds=0.991, exitcode=0, completion_chunk='335957', script_sha256=EXPECTED, fixtures=names)],
    unique_fixtures=8, original_supervisor_or_remaining_suites_repeated=False,
    container=dict(context='desktop-linux', image='sha256:1353c9a1723ad9a8f05be830d951311623e153488500b1e22b3bcb059e676630',
                   pull='never', network='none', rootfs_and_source='read-only', cpus=1, memory_bytes=268435456,
                   pids_limit=128, tmpfs_bytes=67108864, tmpfs_mode='0700', automatic_rm=True),
    server_execution=False, image_import=False, image_delete=False, production_mutation=False, ready_for_rollout=False,
    limitations=[
        'Fixtures use real local child processes and recorded or synthetic snapshot rows; no server image unpack was executed.',
        'Snapshot KEY and PARENT are separated using the captured ctr tabwriter header columns; malformed tables fail closed.',
        'Only ordinary 64-hex Active keys whose parent is an exact candidate Committed prefix member are permitted as parent-only references.',
        'Target extract keys, candidate Active/View keys, unclassified relevant keys and incomplete or malformed candidate chains remain blocked.',
        'Only Runner-forward, Runner-rollback-compatible and Host-forward may be imported; the prior three images are verified read-only.',
        'All original content, reference, host, sealed metadata and live storage gates remain; runtime and production rollout acceptance remain pending.',
    ])
with (HERE / 'QUALIFICATION.json').open('xb') as stream:
    stream.write((json.dumps(value, sort_keys=True, indent=2) + '\n').encode())
print(json.dumps(dict(status=value['status'], script=files['resume_three.py'], fixtures_passed=8), sort_keys=True))
