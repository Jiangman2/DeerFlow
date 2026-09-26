"""Integrity/static contracts, not ARM64 runtime or production acceptance."""
import ast
import hashlib
import json
import re
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
for line in (root / 'PACKAGE.SHA256').read_text().splitlines():
    expected, name = line.split('  ', 1)
    target = (root / name).resolve()
    assert root in target.parents and target.is_file(), name
    assert hashlib.sha256(target.read_bytes()).hexdigest() == expected, name
    if target.suffix != '.png':
        assert b'\r' not in target.read_bytes(), 'CRLF: ' + name
lock = json.loads((root / 'BASELINE_LOCK.json').read_bytes())
assert lock['schema'] == 1
for component, count in (('gateway', 11), ('frontend', 8)):
    entries = json.loads((root / (component + '-delta.json')).read_bytes())
    assert len(entries) == count and len({e['path'] for e in entries}) == count
    expected = {item['path']: item for item in lock['components'][component]['files']}
    assert set(expected) == {e['path'] for e in entries}
    for entry in entries:
        assert not Path(entry['path']).is_absolute() and '..' not in Path(entry['path']).parts
        for key in ('source_sha256', 'target_sha256'):
            assert re.fullmatch(r'[a-f0-9]{64}', entry[key]) and entry[key] == expected[entry['path']][key]
        assert entry['edits'] or entry['source_sha256'] == entry['target_sha256']
        assert all(e['before'] and e['after'] for e in entry['edits'])
    dockerfile = (root / ('Dockerfile.' + component)).read_text()
    assert 'AS source-check' in dockerfile and '--check-only' in dockerfile
    for line in dockerfile.splitlines():
        if line.startswith('RUN '):
            assert line.startswith('RUN --network=none ')
    assert not any(x in dockerfile for x in ('pip install', 'apt-get', 'pnpm install', 'npm install', '# syntax='))
for script in root.glob('*.py'):
    ast.parse(script.read_text(encoding='utf-8'))
builder = (root / 'build-candidate-datax-01.sh').read_text()
assert builder.index('--opt target=source-check') < builder.index('partial_candidate_needs_inspection')
assert 'verify-oci-baseline.py' in builder and 'io.containerd.image.name' in builder
assert builder.index('trap isolated_buildkit_stop EXIT') < builder.index('isolated_buildkit_start\n')
env = (root / 'IMAGE_LOCKS.env').read_text()
assert 'channel-pdf-preview-r2' in env and 'channel-pdf-preview-r1' not in env
assert not any(root.glob('*rollout*'))
print('channel_pdf_r2_package_integrity=PASS')
print('channel_pdf_r2_static_contracts=PASS audited_source_files=19')
print('candidate_source_preflight=REQUIRED components=gateway,frontend')
print('build_network=NONE new_dependency_install=NONE')
print('production_rollout=NOT_INCLUDED arm64_live_validation=NOT_RUN')
