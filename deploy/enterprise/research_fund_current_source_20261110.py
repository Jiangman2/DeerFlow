"""Verify the deployed November 6/10 financial successors without running installers."""
import hashlib
import json
from pathlib import PurePosixPath

import research_fund_late_source_20261105 as prior
import research_fund_adjusted_budget_source_20261110 as adjusted
import post44_source_20261113 as post44

ARCHIVE = 'release/research-fund-current-source-20261110/'
MANIFEST = ARCHIVE + 'SOURCE.json'
MANIFEST_SHA256 = '35fd834b4736d7f37413b5bcb48397bc89364ec4c50d884a19ea6f0a5e37fe26'
SKILL = prior.SKILL
OLD = 'release/research-fund-report-labels-20261106-r1/'
NEW = 'release/research-fund-expense-purposes-20261110-r1/'
BEFORE = prior.AFTER
MIDDLE = '883b98f54f2756c218e98bb535d4d6265fb7bd1fffc2049a54bbac7746ea39dc'
AFTER = '0b417f6540cbab2f35ce9bbde28efe0d9d214273bf4fbf9baf47734f9ea01c85'


def require(value, code):
    if not value:
        raise ValueError(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def check(read, path, row):
    pure = PurePosixPath(path)
    require(not pure.is_absolute() and '..' not in pure.parts and '\\' not in path and ':' not in path,
            'CURRENT_FINANCE_UNSAFE_PATH')
    raw = read(path)
    require(len(raw) == row['bytes'] and sha(raw) == row['sha256'], 'CURRENT_FINANCE_BYTES_CHANGED: '+path)
    return raw


def qualify(read, paths):
    read = post44.financial_historical_view(read)
    try:
        raw = read(MANIFEST)
    except (KeyError, FileNotFoundError):
        return None, None
    require(sha(raw) == MANIFEST_SHA256, 'CURRENT_FINANCE_SEAL_CHANGED')
    seal = json.loads(raw)
    require(seal['schema'] == 'research-fund-current-source.v1'
            and seal['business_acceptance_pending'] is True
            and seal['production_commands_executed'] is False, 'CURRENT_FINANCE_SCOPE_CHANGED')
    require(sha(read(prior.MANIFEST)) == prior.MANIFEST_SHA256, 'CURRENT_FINANCE_PARENT_CHANGED')
    candidate, projection = adjusted.qualify(read, paths)
    if projection is not None:
        candidate_read = read
        read = lambda path: projection[path] if path in projection else candidate_read(path)
    for path, row in seal['evidence_files'].items():
        check(read, path, row)
    for path, row in seal['support_files'].items():
        check(read, path, row)
    manifests = [json.loads(read(directory+'candidate-manifest.json')) for directory in (OLD, NEW)]
    for manifest, digest in zip(manifests, (MIDDLE, AFTER)):
        rows = manifest['files']
        require(len(rows) == 31 and [r['path'] for r in rows] == sorted({r['path'] for r in rows}), 'CURRENT_FINANCE_MANIFEST_SET')
        require(sha(prior.canonical(rows)) == manifest['tree_sha256'] == digest, 'CURRENT_FINANCE_TREE_CHANGED')
    receipts = [json.loads(read(path)) for path in seal['production_receipts']]
    for receipt, before, after, count in zip(receipts, (BEFORE, MIDDLE), (MIDDLE, AFTER), (304, 320)):
        prior.check_receipt(receipt, before, after, count)
    expected = {SKILL+r['path']:r for r in manifests[-1]['files']}
    require(set(paths) == set(expected), 'CURRENT_FINANCE_FILE_SET_CHANGED')
    for path, row in expected.items():
        check(read, path, row)
    # Recover the exact already-sealed November 5 runtime, including its 30-file set.
    parent = prior.archive_files(prior.bundle(read, prior.PROJECT_TYPE+'update-portal-research-fund-project-type-20261105-r1.py'))
    for path, row in seal['parent_support'].items():
        require(row['snapshot'].startswith(ARCHIVE+'parent/'), 'CURRENT_FINANCE_PARENT_SCOPE')
        parent[path] = check(read, row['snapshot'], row)
    result = {'status':'SOURCE_MATCHES_OPERATOR_ACCEPTED_SKILL_FILES','tree_sha256':AFTER,
            'skill_files':31,'report_labels_tree_sha256':MIDDLE,
            'business_acceptance_pending':True,'production_commands_executed':False}
    if candidate:
        result.update(candidate, deployed_tree_sha256=AFTER)
    return result, parent
