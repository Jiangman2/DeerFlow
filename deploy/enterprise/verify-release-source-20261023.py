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
LOCK = 'deploy/enterprise/production-source-lock-20261023.json'
RUNNER = 'release/production-source-20261022/ppt-master-runner/'


def verify(ref=None):
    archived = None
    if ref:
        raw = subprocess.check_output(['git', 'archive', '--format=tar', ref,
            'backend/app', 'backend/packages/harness', 'frontend', RUNNER, LOCK], cwd=ROOT)
        with tarfile.open(fileobj=io.BytesIO(raw)) as tar:
            archived = {m.name: tar.extractfile(m).read() for m in tar if m.isfile()}
    read = (lambda name: archived[name]) if archived is not None else (lambda name: (ROOT / name).read_bytes())
    lock = json.loads(read(LOCK))
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
        results.append({'component': component, 'files': len(expected), 'hashes': 'PASS'})
    return {'status': 'RELEASE_SOURCE_VERIFIED', 'git_ref': ref, 'results': results,
            'scope': lock['scope'], 'production_modified': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ref', help='Verify a committed Git tree, e.g. HEAD')
    print(json.dumps(verify(parser.parse_args().ref), ensure_ascii=False))
