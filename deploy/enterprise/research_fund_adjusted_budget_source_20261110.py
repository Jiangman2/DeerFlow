"""Qualify the adjusted-budget candidate before replaying immutable production evidence."""
import hashlib
import json
from pathlib import PurePosixPath

ARCHIVE = 'release/research-fund-adjusted-budget-20261110-r1/'
MANIFEST = ARCHIVE + 'SOURCE.json'
MANIFEST_SHA256 = '7581bd0a68503fcb60e346afbb71171e3f0ecf7b4ce06763948f0270bdd5283f'
SKILL = 'skills/public/research-fund-settlement/'


def require(value, code):
    if not value:
        raise ValueError(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def check(read, path, row):
    p = PurePosixPath(path)
    require(not p.is_absolute() and '..' not in p.parts and '\\' not in path and ':' not in path,
            'ADJUSTED_BUDGET_UNSAFE_PATH')
    raw = read(path)
    require(len(raw) == row['bytes'] and sha(raw) == row['sha256'], 'ADJUSTED_BUDGET_BYTES_CHANGED: ' + path)
    return raw


def qualify(read, paths):
    try:
        raw = read(MANIFEST)
    except (FileNotFoundError, KeyError):
        return None, None
    require(sha(raw) == MANIFEST_SHA256, 'ADJUSTED_BUDGET_SEAL_CHANGED')
    seal = json.loads(raw)
    require(seal['schema'] == 'research-fund-adjusted-budget-candidate.v1'
            and seal['production_commands_executed'] is False
            and seal['production_acceptance_claimed'] is False, 'ADJUSTED_BUDGET_SCOPE_CHANGED')
    require(set(paths) == set(seal['files']), 'CURRENT_FINANCE_FILE_SET_CHANGED: ADJUSTED_BUDGET_CANDIDATE')
    for path, row in {**seal['files'], **seal['support_files']}.items():
        check(read, path, row)
    before = {}
    for path, row in seal['parent_files'].items():
        require(path in seal['files'] or path in seal['support_files'], 'ADJUSTED_BUDGET_PARENT_SCOPE')
        require(row['snapshot'] == ARCHIVE + 'parent/' + path, 'ADJUSTED_BUDGET_PARENT_PATH')
        before[path] = check(read, row['snapshot'], row)
    # The caller verifies every projected byte against the unchanged deployed seal.
    return {'status': 'ADJUSTED_BUDGET_CANDIDATE_VERIFIED_NOT_DEPLOYED',
            'candidate_tree_sha256': seal['candidate_tree_sha256'],
            'production_acceptance_claimed': False, 'production_commands_executed': False}, before
