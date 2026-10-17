import ast
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def pin(path):
    raw = path.read_bytes()
    return {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}


for name in ('resume_phase_b.py', 'test_phase_b.py'):
    ast.parse((ROOT / name).read_text(encoding='utf-8'), feature_version=(3, 7))
data = {
    'schema': 'portal-native-phase-b-preparation-qualification.v1',
    'status': 'PREPARATION_NOT_DELIVERED',
    'files': {name: pin(ROOT / name) for name in ('resume_phase_b.py', 'test_phase_b.py', 'PREPARATION.txt')},
    'observed_fixture_run': {
        'passed': 10, 'failed': 0, 'seconds': 0.212, 'exitcode': 0,
        'platform': 'Linux', 'python37_syntax': True,
        'container': 'portal-native-phase-b-fixture-20261017-r1',
        'image': 'sha256:1353c9a1723ad9a8f05be830d951311623e153488500b1e22b3bcb059e676630',
        'limits': {'network': 'none', 'pull': 'never', 'cpus': 1, 'memory_bytes': 268435456,
                   'memory_swap_bytes': 268435456, 'pids': 128, 'rootfs': 'read-only', 'source': 'read-only',
                   'tmpfs_bytes': 67108864, 'tmpfs_mode': '0700'},
        'actual_image_import': False, 'server_access': False, 'old_r3_suite_repeated': False,
        'covered': [
            'Frozen deployed module and actual failure exact byte pins; unreleased startup defaults',
            'Exactly 24 original checkpoint files; modified receipt, extra entry and dangling Phase B link rejection',
            'Proven sealed directory inode held across user-parent rename; symlink substitution rejected',
            'Only four metadata members, frozen metadata hashes/platform/layers checked without full OCI/layer reads',
            'Unexpected metadata member and changed config rejected',
            'Metadata symlink and hardlink rejected; held FD read-only at offset zero',
            'Insufficient original storage gate does not request Phase B or import',
            'Six Phase B imports only; seven fresh closure/reference/collision/budget gates and twelve completion checks',
            'First failed unpack ends attempt without replay or advancing next role',
            'Real common flock held during continuation; new evidence/once preserve old failure; replay rejected'
        ]
    },
    'pending': [
        'Exact 24 checkpoint hashes and sizes from read-only target inventory',
        'Separate space recovery and unchanged fresh original storage gate',
        'Independent review of finalized executable and pins before delivery',
        'Native ARM64 runtime qualification and later production rollout gates'
    ],
    'ready_for_rollout': False,
    'native_runtime_passed': False,
    'production_mutation': False,
    'limitations': [
        'Checkpoint structural test uses synthetic receipt pins except the actual exact FAIL receipt.',
        'Success-path import fixture is mocked; real frozen subprocess import is not executed.',
        'Only fixed local host public-key identity is checked; no fresh Kubernetes Node UID query.',
        'Current code intentionally refuses execution until reviewed checkpoint pins and release state are finalized.'
    ]
}
(ROOT / 'QUALIFICATION.json').write_text(json.dumps(data, indent=2, sort_keys=True) + '\n', encoding='utf-8', newline='\n')
print(json.dumps({'status': data['status'], 'files': data['files'], 'qualification': pin(ROOT / 'QUALIFICATION.json')}, sort_keys=True))
