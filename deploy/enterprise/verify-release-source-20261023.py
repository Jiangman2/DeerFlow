"""Verify committed/source trees against the accepted image-source inventory.

No imports from the application, cluster calls, model calls or database access.
Use --ref HEAD after committing to check Git objects rather than working files.
"""
import argparse
import ast
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[2]
LOCK = 'deploy/enterprise/production-source-lock-20261023.json'
RUNNER = 'release/production-source-20261022/ppt-master-runner/'
FORMAT_BASELINE = 'release/production-source-20261023/gateway-format-baseline/'
CANDIDATE = 'deploy/enterprise/work-source-candidate-20261026.json'


def candidate_overrides(lock, manifest, lock_digest):
    """Validate an explicit undeployed delta without rewriting accepted evidence."""
    assert manifest['schema'] == 'production-source-candidate.v1'
    assert manifest['status'] == 'NOT_DEPLOYED'
    assert manifest['base_lock_sha256'] == lock_digest, 'CANDIDATE_BASE_LOCK_MISMATCH'
    assert len(manifest['base_commit']) == 40 and all(c in '0123456789abcdef' for c in manifest['base_commit'])
    baseline = lock['source_sets']['gateway']
    normalizations = lock.get('format_normalizations', {})
    result = {}
    for name, change in manifest['changes'].items():
        assert name.startswith(('backend/app/', 'backend/packages/harness/')) and name.endswith('.py')
        assert '\\' not in name and PurePosixPath(name).as_posix() == name and '..' not in PurePosixPath(name).parts
        previous = normalizations.get(name, {}).get('formatted_sha256', baseline.get(name))
        assert change['base_sha256'] == previous, (name, 'CANDIDATE_PARENT_MISMATCH')
        digest = change['candidate_sha256']
        assert len(digest) == 64 and all(c in '0123456789abcdef' for c in digest)
        result[name] = digest
    return result


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


def verify(ref=None):
    archived = None
    if ref:
        candidate_exists = subprocess.check_output(['git', 'ls-tree', '-r', '--name-only', ref, CANDIDATE], cwd=ROOT).strip()
        paths = [CANDIDATE] if candidate_exists else []
        raw = subprocess.check_output(['git', 'archive', '--format=tar', ref,
            'backend/app', 'backend/packages/harness', 'frontend', RUNNER, FORMAT_BASELINE, LOCK, *paths], cwd=ROOT)
        with tarfile.open(fileobj=io.BytesIO(raw)) as tar:
            archived = {m.name: tar.extractfile(m).read() for m in tar if m.isfile()}
    read = (lambda name: archived[name]) if archived is not None else (lambda name: (ROOT / name).read_bytes())
    lock_raw = read(LOCK)
    lock = json.loads(lock_raw)
    has_candidate = CANDIDATE in archived if archived is not None else (ROOT / CANDIDATE).is_file()
    manifest = json.loads(read(CANDIDATE)) if has_candidate else None
    overrides = candidate_overrides(lock, manifest, hashlib.sha256(lock_raw.replace(b'\r\n', b'\n')).hexdigest()) if manifest else {}
    normalizations = lock.get('format_normalizations', {})
    results = []
    for component, expected in lock['source_sets'].items():
        expected = {**expected, **overrides} if component == 'gateway' else expected
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
            if component == 'gateway' and name in overrides:
                assert hashlib.sha256(raw.replace(b'\r\n', b'\n')).hexdigest() == digest, (name, 'CANDIDATE_HASH_MISMATCH')
                ast.parse(raw, filename=name)
                continue
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
    return {'status': 'CANDIDATE_SOURCE_VERIFIED' if manifest else 'RELEASE_SOURCE_VERIFIED', 'git_ref': ref, 'results': results,
            'scope': lock['scope'], 'production_modified': False,
            'candidate': {'status': 'NOT_DEPLOYED', 'base_commit': manifest['base_commit'], 'changed_sources': len(overrides)} if manifest else None}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ref', help='Verify a committed Git tree, e.g. HEAD')
    print(json.dumps(verify(parser.parse_args().ref), ensure_ascii=False))
