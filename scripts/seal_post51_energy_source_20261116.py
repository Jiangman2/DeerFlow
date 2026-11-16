"""Seal this unpublished reconciliation from public bytes; no production access."""
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = 'release/post51-energy-production-sync-20261116/'
ENERGY = 'skills/public/energy-market-daily/'
PARENT = '5c39443075c007d6bf82148759f0b3b797826eee'


def row(raw):
    return {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}


def write(name, value):
    p = ROOT / name
    p.parent.mkdir(exist_ok=True, parents=True)
    p.write_text(json.dumps(value, sort_keys=True, indent=2) + '\n', encoding='utf-8', newline='\n')


def main():
    assembly = json.loads((ROOT / ARCHIVE / 'ASSEMBLY.json').read_bytes())
    paths = set(assembly['current_files'])
    parents = assembly['parent_files']
    base_paths = set(subprocess.check_output(['git', 'ls-tree', '-rz', '--name-only', PARENT], cwd=ROOT).decode().split('\0'))
    changed = set(filter(None, subprocess.check_output(['git', 'diff', PARENT, '--name-only', '-z'], cwd=ROOT).decode().split('\0')))
    changed.update(p for p, r in parents.items() if r is None and p in base_paths)
    changed = sorted(changed & base_paths)
    old = subprocess.check_output(['git', 'cat-file', '--batch'], cwd=ROOT,
                                  input=''.join(PARENT + ':' + p + '\n' for p in changed).encode())
    stream = io.BytesIO(old)
    for name in changed:
        header = stream.readline().split()
        assert len(header) == 3 and header[1] == b'blob', name
        raw = stream.read(int(header[2])); assert stream.read(1) == b'\n'
        if parents.get(name) is None and (ROOT / name).is_file() and (ROOT / name).read_bytes() == raw:
            # Quoted Unicode paths in the preparation inventory were not new
            # files. Unchanged historical fixtures need no parent indirection.
            parents.pop(name, None)
            continue
        snap = ARCHIVE + 'parent/' + name
        target = ROOT / snap; target.parent.mkdir(exist_ok=True, parents=True)
        if target.exists(): assert target.read_bytes() == raw, 'Preserve immutable parent: ' + name
        else: target.write_bytes(raw)
        parents[name] = {'snapshot': snap, **row(raw)}
        if (ROOT / name).is_file(): paths.add(name)

    code = json.loads((ROOT / 'release/energy-publication-fix-20261116-r1/CODE-MANIFEST.json').read_bytes())
    runtime = json.loads((ROOT / 'release/scheduler-concurrency-20261116-r2/SOURCE.json').read_bytes())['expected_sources']
    runtime = {p.removeprefix('/app/'): r for p, r in runtime.items() if p.startswith('/app/')}
    public = {ENERGY + name: r for name, r in code['files'].items()}
    paths.update(public); paths.update(runtime)
    additional = [
        'deploy/enterprise/verify-post51-energy-source-20261116.py',
        'deploy/enterprise/production-energy-source-lock-20261116.json',
        'scripts/seal_post51_energy_source_20261116.py', 'scripts/test_portable_energy_versions.py',
        'tests/test_post51_energy_source_20261116.py',
        'backend/tests/test_upload_memory_released_20261116.py',
        'docs/energy-production-source-sync-20261116.md',
    ]
    paths.update(additional)
    for p in (ROOT / ARCHIVE).rglob('*'):
        if p.is_file() and 'parent' not in p.relative_to(ROOT / ARCHIVE).parts and p.name not in {'SOURCE.json', 'VALIDATION.json', 'COMMITTED-VERIFICATION.json', 'PUBLIC-AUDIT.json'}:
            paths.add(p.relative_to(ROOT).as_posix())
    lock = {
        'schema': 'production-energy-source-lock.v1', 'base_commit': PARENT,
        'scope': 'Observed energy Skill, Gateway source overlay/config delta and actual serving reader; not a full image rebuild lock',
        'gateway': {'generation': 419, 'uid': 'a62fc04c-7a82-4b57-8fbf-a8c7a3839ea1',
                    'spec_sha256': '08a8afffb65eaca6259fe8b01111a5c68a2523fba639f17a4b25611e9ccb98c8',
                    'image': 'deerflow.local/deer-flow-gateway@sha256:0255237caedba428d7ceafd584e171a6d23739f07fc7a566bce016f28d543c5e',
                    'observed_python_sources': runtime, 'rollout_converged': True,
                    'fleet_runtime_acceptance_complete': False, 'oom_diagnostic_pending': True},
        'runtime_configuration': {'max_concurrent_runs': 32, 'multi_instance': True,
                                  'scheduler_recursion_limit': 10000, 'global_max_recursion_limit': 10000,
                                  'private_config_contents_included': False},
        'skill': {'version': '2.1.8-portable', 'public_files': 108, 'installed_files': 110,
                  'installed_tree_sha256': '9ddcf11a813158d00aa119de28955aeeb71c7eca60926b58be5ab67d5579e864',
                  'installed_receipt': 'release/energy-publication-fix-20261116-r1/OPERATOR-INSTALL-REPORT.json',
                  'receipt_sha256': '4155131e5d19b4ddf026f803a3a37bf086123b639fd5401ab83bcb0422761dd0',
                  'browser_binding_and_binary_deployment_managed': True},
        'serving_nginx': {'deployment': 'nginx-security-heai-20261012-r1', 'generation': 3,
                          'spec_sha256': '6061194b0c497f516b8b6c55e7f74f435bca5effc378d7f097c8b2d67d0dcd72',
                          'evidence': 'release/energy-reader-work-repair-20261115-r2/OPERATOR-ACCEPTANCE.json'},
        'personal_notification_candidate_deployed': False, 'production_access': False,
    }
    write(additional[1], lock)
    manifest = {
        'schema': 'post51-energy-production-source.v1', 'base_commit': PARENT,
        'skill_version': '2.1.8-portable', 'gateway_generation': 419,
        'gateway_spec_sha256': lock['gateway']['spec_sha256'],
        'installed_skill_tree_sha256': lock['skill']['installed_tree_sha256'],
        'scheduler_max_concurrent_runs': 32, 'scheduler_multi_instance': True,
        'fleet_runtime_acceptance_complete': False, 'personal_notification_candidate_deployed': False,
        'production_access': False, 'credentials_included': False,
        'runtime_files': runtime, 'energy_files': public,
        'original_energy_files': assembly['original_energy_files'], 'parent_files': parents,
        'files': {name: row((ROOT / name).read_bytes()) for name in sorted(paths) if (ROOT / name).is_file()},
        'source_formatting': 'Exact deployed-overlay bytes retained; root files permit only explicit AST-equivalent CI lint edits',
    }
    write(ARCHIVE + 'SOURCE.json', manifest)
    digest = row((ROOT / ARCHIVE / 'SOURCE.json').read_bytes())['sha256']
    module = ROOT / 'deploy/enterprise/post51_energy_source_20261116.py'
    source = module.read_text(encoding='utf-8')
    source, count = re.subn(r"MANIFEST_SHA256 = '[^']+'", "MANIFEST_SHA256 = '" + digest + "'", source)
    assert count == 1
    module.write_text(source, encoding='utf-8', newline='\n')
    print(json.dumps({'sealed_files': len(manifest['files']), 'parent_paths': len(parents), 'manifest_sha256': digest}))


if __name__ == '__main__':
    main()
