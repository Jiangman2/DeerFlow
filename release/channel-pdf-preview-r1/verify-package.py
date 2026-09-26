"""Package/source contracts only; this is not a live rollout acceptance test."""
import ast
import hashlib
import json
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
for line in (root / 'PACKAGE.SHA256').read_text().splitlines():
    expected, name = line.split('  ', 1)
    target = (root / name).resolve()
    assert root in target.parents and target.is_file(), name
    assert hashlib.sha256(target.read_bytes()).hexdigest() == expected, name
for component in ('gateway', 'frontend'):
    entries = json.loads((root / (component + '-delta.json')).read_text(encoding='utf-8'))
    assert entries
    assert len({e['path'] for e in entries}) == len(entries)
    for entry in entries:
        assert not Path(entry['path']).is_absolute() and '..' not in Path(entry['path']).parts
        assert entry['edits'] and all(e['before'] and e['after'] for e in entry['edits'])
    dockerfile = (root / ('Dockerfile.' + component)).read_text()
    assert 'RUN --network=none' in dockerfile
    assert not any(x in dockerfile for x in ('pip install', 'apt-get', 'pnpm install', 'npm install', '# syntax='))
for script in root.glob('*.py'):
    ast.parse(script.read_text(encoding='utf-8'))
print('channel_pdf_package_integrity=PASS')
print('channel_pdf_static_contracts=PASS live_validation=NOT_RUN')
print('build_network=NONE new_dependency_install=NONE')
print('rollout_script=NOT_INCLUDED requires_candidate_validation=yes')
