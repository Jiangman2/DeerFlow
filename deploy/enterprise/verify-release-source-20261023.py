"""Verify committed/source trees against the accepted image-source inventory.

No imports from the application, cluster calls, model calls or database access.
Use --ref HEAD after committing to check Git objects rather than working files.
"""
import argparse
import ast
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[2]
LOCK = 'deploy/enterprise/production-source-lock-20261026.json'
RUNNER = 'release/production-source-20261022/ppt-master-runner/'
FORMAT_BASELINE = 'release/production-source-20261023/gateway-format-baseline/'


def syntax_fingerprint(raw, *, import_order=False):
    """Ignore layout; optionally sort only consecutive module-level imports."""
    tree = ast.parse(raw)
    if import_order:
        start = 0
        while start < len(tree.body):
            if not isinstance(tree.body[start], (ast.Import, ast.ImportFrom)):
                start += 1
                continue
            end = start + 1
            while end < len(tree.body) and isinstance(tree.body[end], (ast.Import, ast.ImportFrom)):
                end += 1
            tree.body[start:end] = sorted(tree.body[start:end], key=ast.dump)
            start = end
    return ast.dump(tree, include_attributes=False)


def verify_runtime_patch(read, lock):
    """Bind archived runtime bytes to the independently collected ConfigMap hash."""
    patch = lock.get('runtime_patch')
    if not patch:
        return None
    for path, expected in patch['files'].items():
        assert hashlib.sha256(read(path).replace(b'\r\n', b'\n')).hexdigest() == expected, (path, 'RUNTIME_ARCHIVE_CHANGED')
    data = {name: read(patch['archive_prefix'] + name).decode('utf-8').replace('\r\n', '\n')
            for name in patch['payload_files']}
    digest = lambda value: hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()
    assert digest(data) == patch['payload_sha256'], 'RUNTIME_PAYLOAD_CHANGED'
    assert digest({'data': data, 'binaryData': {}}) == patch['configmap']['content_sha256'], 'RUNTIME_CONFIGMAP_MISMATCH'
    for path, expected in patch['qualified_source_files'].items():
        assert hashlib.sha256(read(path).replace(b'\r\n', b'\n')).hexdigest() == expected, (path, 'MEMBER_UI_SOURCE_CHANGED')
    record = lock['current_observation']
    raw = read(record['path']).replace(b'\r\n', b'\n')
    assert hashlib.sha256(raw).hexdigest() == record['sha256'], 'CURRENT_OBSERVATION_CHANGED'
    observation = json.loads(raw)['observation']
    assert observation['status'] == 'CURRENT_METADATA_COLLECTED' and observation['workload_specs_stable'], 'UNSTABLE_OBSERVATION'
    observed_cm = next(c for c in observation['configmaps'] if c['identity']['name'] == patch['configmap']['identity']['name'])
    assert observed_cm == patch['configmap'], 'OBSERVED_CONFIGMAP_CHANGED'
    workloads = {w['identity']['name']: w for w in observation['workloads']}
    for name, expected in record['bindings'].items():
        actual = workloads[name]
        assert all(actual['identity'][key] == expected[key] for key in ('uid', 'generation')), (name, 'WORKLOAD_IDENTITY_MISMATCH')
        assert actual['spec_sha256'] == expected['spec_sha256'], (name, 'WORKLOAD_SPEC_MISMATCH')
        images = [c['image'] for c in actual['containers']]
        assert images == expected['images'], (name, 'WORKLOAD_IMAGE_MISMATCH')
        if name in lock['images']:
            assert lock['images'][name] in images, (name, 'RELEASE_IMAGE_MISMATCH')
    return {'component': 'weknora-member-ui-overlay', 'files': len(patch['files']),
            'configmap_hash': 'PASS', 'source_files': len(patch['qualified_source_files']),
            'observation_bindings': 'PASS', 'full_base_image_source_mapping': 'NOT_PROVEN'}


def verify(ref=None, lock_path=LOCK):
    archived = None
    if ref:
        selected_lock = json.loads(subprocess.check_output(['git', 'show', ref + ':' + lock_path], cwd=ROOT))
        extra = []
        if selected_lock.get('runtime_patch'):
            extra = [selected_lock['runtime_patch']['archive_prefix'],
                     *selected_lock['runtime_patch']['qualified_source_files'], selected_lock['current_observation']['path']]
        raw = subprocess.check_output(['git', 'archive', '--format=tar', ref,
            'backend/app', 'backend/packages/harness', 'frontend', RUNNER, FORMAT_BASELINE, lock_path, *extra], cwd=ROOT)
        with tarfile.open(fileobj=io.BytesIO(raw)) as tar:
            archived = {m.name: tar.extractfile(m).read() for m in tar if m.isfile()}
    read = (lambda name: archived[name]) if archived is not None else (lambda name: (ROOT / name).read_bytes())
    lock = json.loads(read(lock_path))
    normalizations = lock.get('format_normalizations', {})
    results = []
    for component, expected in lock['source_sets'].items():
        prefix = RUNNER if component == 'ppt-master-runner' else ''
        if component != 'frontend':
            roots = [prefix + 'backend/app/', prefix + 'backend/packages/harness/']
            if archived is not None:
                actual = {n.removeprefix(prefix) for n in archived
                          if n.endswith('.py') and any(n.startswith(r) for r in roots)}
            else:
                actual = {p.relative_to(ROOT / prefix).as_posix() for r in roots
                          for p in (ROOT / r).rglob('*.py') if '__pycache__' not in p.parts}
            if actual != set(expected):
                raise AssertionError((component, 'SOURCE_SET_MISMATCH', sorted(actual ^ set(expected))))
        for name, digest in expected.items():
            raw = read(prefix + name)
            normalization = normalizations.get(name) if component == 'gateway' else None
            if normalization:
                original = read(FORMAT_BASELINE + name).replace(b'\r\n', b'\n')
                raw = raw.replace(b'\r\n', b'\n')
                assert hashlib.sha256(original).hexdigest() == digest, (name, 'PRODUCTION_BASELINE_MISMATCH')
                assert hashlib.sha256(raw).hexdigest() == normalization['formatted_sha256'], (name, 'FORMATTED_HASH_MISMATCH')
                import_order = normalization['kind'] == 'layout_and_import_order'
                assert syntax_fingerprint(original, import_order=import_order) == syntax_fingerprint(raw, import_order=import_order), (name, 'LOGIC_CHANGED')
                continue
            if hashlib.sha256(raw).hexdigest() != digest:
                # Git enforces LF for text; binary assets are always compared raw.
                try:
                    raw.decode('utf-8')
                except UnicodeDecodeError:
                    raise AssertionError((component, name, 'BINARY_HASH_MISMATCH')) from None
                raw = raw.replace(b'\r\n', b'\n')
                if hashlib.sha256(raw).hexdigest() != digest:
                    raise AssertionError((component, name, 'SOURCE_HASH_MISMATCH'))
            if name.endswith('.py'):
                ast.parse(raw, filename=name)
        results.append({'component': component, 'files': len(expected), 'hashes': 'PASS',
                        'format_normalized': len(normalizations) if component == 'gateway' else 0})
    patch_result = verify_runtime_patch(read, lock)
    if patch_result:
        results.append(patch_result)
    return {'status': 'RELEASE_SOURCE_VERIFIED', 'git_ref': ref, 'lock_file': lock_path, 'results': results,
            'scope': lock['scope'], 'production_modified': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ref', help='Verify a committed Git tree, e.g. HEAD')
    parser.add_argument('--lock', default=LOCK, help='Repository-relative source lock; use the matching historical lock with an older ref')
    args = parser.parse_args()
    print(json.dumps(verify(args.ref, args.lock), ensure_ascii=False))
