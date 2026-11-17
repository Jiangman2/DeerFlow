"""Seal this unpublished GitHub sync; never mutate a historical release seal."""
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = 'release/personal-notification-source-20261117/'
MODULE = 'deploy/enterprise/personal_notification_source_20261117.py'
MANIFEST = ARCHIVE + 'SOURCE.json'
PARENT = 'a2148ed200b211a4e57832990390d9d4bc48b406'
RELEASE = 'release/energy-personal-notification-ready-20261116-r1/'


def row(raw):
    return {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}


def immutable(path, raw):
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        assert target.read_bytes() == raw, 'Existing immutable evidence differs: ' + path
    else:
        target.write_bytes(raw)


def main():
    assert subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT).decode().strip() == PARENT
    for name in ('OPERATOR-ACTIVATION-REPORT.json', 'OPERATOR-RUNTIME-ACCEPTANCE-20261117.json',
                 'OPERATOR-BUSINESS-ACCEPTANCE-20261117.json'):
        immutable(ARCHIVE + name, (ROOT / RELEASE / name).read_bytes())
    immutable(ARCHIVE + 'RELEASE-SOURCE.json', (ROOT / RELEASE / 'SOURCE.json').read_bytes())
    immutable(ARCHIVE + 'PRODUCTION-LOCK.json', (ROOT / 'deploy/enterprise/production-personal-notification-source-lock-20261117.json').read_bytes())
    changed = list(filter(None, subprocess.check_output(['git', 'diff', '--name-only', '-z'], cwd=ROOT).decode().split('\0')))
    added = list(filter(None, subprocess.check_output(['git', 'ls-files', '--others', '--exclude-standard', '-z'], cwd=ROOT).decode().split('\0')))
    reports = {ARCHIVE + p for p in ('VALIDATION.json', 'PUBLIC-AUDIT.json', 'COMMITTED-VERIFICATION.json')}
    names = sorted(set(changed + added) - {MANIFEST, MODULE} - reports)
    names = [p for p in names if not p.startswith(ARCHIVE + 'parent/')
             and not any(x in p.split('/') for x in ('__pycache__', '.pytest_cache'))]
    parents = {}
    for path in changed:
        raw = subprocess.check_output(['git', 'show', PARENT + ':' + path], cwd=ROOT)
        snapshot = ARCHIVE + 'parent/' + path
        immutable(snapshot, raw)
        parents[path] = {'snapshot': snapshot, **row(raw)}
    seal = {
        'schema': 'personal-notification-source.v1', 'parent_commit': PARENT,
        'gateway_generation': 420, 'gateway_spec_sha256': 'd4b61d0bc09cda8550f3d1564a899cb8d44e17033ae9bf58afb1147268c238a9',
        'operation': '55f53add31c741b5b859ba0dba3c61ec', 'personal_work_business_accepted': True,
        'scheduler_concurrency': 32, 'scheduler_budget': 10000,
        'credentials_included': False, 'production_actions_by_pr': 0,
        'files': {p: row((ROOT / p).read_bytes()) for p in names},
        'parent_files': parents,
        'added_files': sorted(set(added) - {MANIFEST, MODULE} - reports),
        'core_files': sorted(p for p in names if p.startswith(ARCHIVE)),
    }
    raw = (json.dumps(seal, sort_keys=True, indent=2) + '\n').encode()
    # Only an unpublished working tree at the exact merged parent can refresh
    # this new seal. After the first commit the HEAD guard above refuses it.
    (ROOT / MANIFEST).write_bytes(raw)
    module = ROOT / MODULE
    source = module.read_text(encoding='utf-8')
    source, count = re.subn(r"MANIFEST_SHA256 = '[A-Za-z0-9]+'", "MANIFEST_SHA256 = '" + row(raw)['sha256'] + "'", source)
    assert count == 1
    module.write_text(source, encoding='utf-8', newline='\n')
    print(json.dumps({'sealed_files': len(names), 'retained_parent_files': len(parents),
                      'manifest_sha256': row(raw)['sha256'], 'production_actions': 0}))


if __name__ == '__main__':
    main()
