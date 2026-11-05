"""Offline chain for the accepted Round3 and project-type Skill successors.

Validate current files before projecting the prior source for its immutable verifier.
No deployment tool is imported or executed.
"""
import ast
import base64
import hashlib
import io
import json
from pathlib import PurePosixPath
import tarfile

ARCHIVE = 'release/research-fund-late-source-20261105/'
MANIFEST = ARCHIVE + 'SOURCE.json'
MANIFEST_SHA256 = 'e2908d9a3c0dc475d7888b4b8469ebc8399c44bbc42aa9cd3674a19b1bf7afdb'
SKILL = 'skills/public/research-fund-settlement/'
PARENT_SOURCE = 'release/research-fund-source-20261105/SOURCE.json'
PARENT_SEAL = 'ba3eb7fe8a8c02e6c2b92c62021c33649bae76a2ef7b742744ea81835edee8a7'
NOV05 = 'release/research-fund-feedback-20261105/'
ROUND3 = 'release/research-fund-feedback-round3-20261105/'
PROJECT_TYPE = 'release/research-fund-project-type-20261105/'
INSPECTOR = 'deploy/enterprise/inspect-portal-research-fund-skill-20261105-r3.py'
BEFORE = '6c393b11a0023a50de29a1958088322324f759e85c6f3080816aed023c61e951'
MIDDLE = '0d935a5e5c45e8bf916b6b52ee310826d68acb22fffb0cbc70ab5d3304840a4c'
AFTER = '28944ec28d4644acb09cd10eb8e73dc546a4250d0c21172d325b23cb713d7d43'
PARENT_SUPPORT = frozenset(('scripts/review_research_fund_templates.py', 'tests/skills/test_research_fund_nov05_model.py'))
SUPPORT_PATHS = PARENT_SUPPORT | frozenset(
    'tests/skills/test_research_fund_' + name + '.py'
    for name in ('round3_acceptance', 'round3_computed', 'round3_integration', 'round3_reasons', 'round3_render', 'project_type_text')
)
NODES = ['ecs-zdz-pro-00' + str(n) for n in range(18, 23)]


def require(ok, code):
    if not ok:
        raise ValueError(code)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def safe(path):
    value = PurePosixPath(path)
    require(isinstance(path, str) and not value.is_absolute() and '..' not in value.parts
            and '\\' not in path and ':' not in path and value.as_posix() == path, 'LATE_SOURCE_UNSAFE_PATH')


def check_file(read, path, row):
    safe(path)
    raw = read(path)
    require(len(raw) == row['bytes'] and sha(raw) == row['sha256'], 'LATE_SOURCE_FILE_CHANGED: ' + path)
    return raw


def inventory(value):
    rows = value['files']
    paths = [r['path'] for r in rows]
    for path in paths:
        safe(path)
    require(paths == sorted(set(paths)) and len(rows) == 30, 'LATE_SOURCE_FILE_SET_CHANGED')
    require(sha(canonical(rows)) == value['tree_sha256'] and len(rows) == value['file_count']
            and sum(r['bytes'] for r in rows) == value['bytes'], 'LATE_SOURCE_MANIFEST_CHANGED')
    return rows


def bundle(read, path):
    tree = ast.parse(read(path).decode('utf-8'))
    values = [node.value for node in tree.body if isinstance(node, ast.Assign)
              and any(isinstance(target, ast.Name) and target.id == 'BUNDLE' for target in node.targets)]
    require(len(values) == 1, 'LATE_SOURCE_BUNDLE_REQUIRED')
    return ast.literal_eval(values[0])


def archive_files(value):
    expected = value['after']
    rows = inventory(expected)
    data = base64.b64decode(value['archive_b64'], validate=True)
    require(len(data) <= 1048576 and sha(data) == value['archive_sha256'], 'LATE_SOURCE_ARCHIVE_CHANGED')
    files = {}
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        for member in archive:
            safe(member.name)
            prefix = 'research-fund-settlement/'
            require(member.isfile() and member.name.startswith(prefix) and member.size <= 2097152,
                    'LATE_SOURCE_ARCHIVE_MEMBER')
            path = SKILL + member.name[len(prefix):]
            require(path not in files and len(files) < 30, 'LATE_SOURCE_ARCHIVE_DUPLICATE')
            files[path] = archive.extractfile(member).read()
    require(set(files) == {SKILL + row['path'] for row in rows}, 'LATE_SOURCE_ARCHIVE_SET')
    for row in rows:
        check_file(files.__getitem__, SKILL + row['path'], row)
    return files


def check_receipt(receipt, before, after, regressions):
    require(receipt['status'] == 'RESEARCH_FUND_SKILL_UPDATED_RUNTIME_ACCEPTED' and receipt['exitcode'] == 0
            and receipt['skill_updated'] and receipt['runtime_node_views_accepted']
            and receipt['maintenance_pod_absent'], 'LATE_SOURCE_RUNTIME_NOT_ACCEPTED')
    require(receipt['before_tree_sha256'] == before and receipt['after_tree_sha256'] == after,
            'LATE_SOURCE_RECEIPT_TREE_CHANGED')
    require(receipt['browser_business_acceptance_pending'] is True
            and receipt['production_database_access'] is False and receipt['production_workload_mutation'] is False
            and receipt['workload_patch_requests'] == 0 and receipt['provider_requests'] == 0,
            'LATE_SOURCE_ACCEPTANCE_OVERCLAIM')
    require(receipt['checked_deployments_unchanged'] == ['gateway', 'ppt-master-runner', 'provisioner'],
            'LATE_SOURCE_DEPLOYMENT_SCOPE')
    for field, expected in (('before_consumers', before), ('after_consumers', after)):
        rows = receipt[field]
        require(sorted(r['node'] for r in rows) == NODES and all(r['status'] == 'PASS' and r['tree_sha256'] == expected for r in rows), 'LATE_SOURCE_NODE_COVERAGE')
    for key, status in (('staging', 'STAGED_SYNTHETIC_ACCEPTED'), ('publication', 'SKILL_PACKAGE_UPDATED')):
        part = receipt[key]
        require(part['status'] == status and part['backup_verified'] and part['after_tree_sha256'] == after,
                'LATE_SOURCE_PUBLICATION_CHANGED')
        checks = part['synthetic_checks']
        actual = [r for r in checks if r['action'] == 'regressions']
        require(len(checks) == 10 and all(r['status'] == 'PASS' for r in checks)
                and len(actual) == 1 and actual[0]['passed'] == regressions, 'LATE_SOURCE_TEST_RECEIPT_CHANGED')


def qualify(read, skill_paths):
    try:
        raw = read(MANIFEST)
    except (KeyError, FileNotFoundError):
        return None, None
    require(sha(raw) == MANIFEST_SHA256, 'LATE_SOURCE_SEAL_CHANGED')
    sealed = json.loads(raw)
    require(sealed['schema'] == 'research-fund-late-source-successor.v1'
            and sealed['before_tree'] == BEFORE and sealed['middle_tree'] == MIDDLE and sealed['after_tree'] == AFTER,
            'LATE_SOURCE_SCHEMA_CHANGED')
    require(sealed['business_acceptance_pending'] is True and sealed['production_commands_executed_by_verifier'] is False,
            'LATE_SOURCE_ACCEPTANCE_OVERCLAIM')
    require(sha(read(PARENT_SOURCE)) == sealed['parent_source_sha256'] == PARENT_SEAL, 'LATE_SOURCE_PARENT_CHANGED')
    require(set(sealed['support_files']) == SUPPORT_PATHS and set(sealed['parent_snapshots']) == PARENT_SUPPORT,
            'LATE_SOURCE_SUPPORT_SET_CHANGED')
    for path, row in sealed['evidence_files'].items():
        require(path.startswith((ROUND3, PROJECT_TYPE)) or path == INSPECTOR, 'LATE_SOURCE_EVIDENCE_SCOPE')
        check_file(read, path, row)
    for path, row in sealed['support_files'].items():
        check_file(read, path, row)
    old = bundle(read, NOV05 + 'update-portal-research-fund-skill-20261105-r1.py')
    first = bundle(read, ROUND3 + 'update-portal-research-fund-skill-round3-20261105-r1.py')
    last = bundle(read, PROJECT_TYPE + 'update-portal-research-fund-project-type-20261105-r1.py')
    require(first['before'] == old['after'] and last['before'] == first['after'], 'LATE_SOURCE_CHAIN_CHANGED')
    require((old['after']['tree_sha256'], first['after']['tree_sha256'], last['after']['tree_sha256']) == (BEFORE, MIDDLE, AFTER), 'LATE_SOURCE_CHAIN_CHANGED')
    for directory, bound, before, after, count in ((ROUND3, first, BEFORE, MIDDLE, 263), (PROJECT_TYPE, last, MIDDLE, AFTER, 278)):
        required = {directory + 'candidate-manifest.json', directory + 'operator-update-20261105.json'}
        require(required <= sealed['evidence_files'].keys(), 'LATE_SOURCE_RECEIPT_REGISTRATION_MISSING')
        require(json.loads(read(directory + 'candidate-manifest.json')) == bound['after'], 'LATE_SOURCE_CANDIDATE_CHANGED')
        archive_files(bound)
        check_receipt(json.loads(read(directory + 'operator-update-20261105.json')), before, after, count)
    expected = archive_files(last)
    require(set(skill_paths) == set(expected), 'LATE_SOURCE_FILE_SET_CHANGED')
    for path, data in expected.items():
        require(read(path) == data, 'LATE_SOURCE_RUNTIME_CHANGED: ' + path)
    parent = archive_files(old)
    for path, row in sealed['parent_snapshots'].items():
        require(row['snapshot'].startswith(ARCHIVE + 'parent/'), 'LATE_SOURCE_SNAPSHOT_SCOPE')
        parent[path] = check_file(read, row['snapshot'], row)
    return {'status': 'SOURCE_MATCHES_OPERATOR_ACCEPTED_SKILL_FILES', 'skill_files': len(expected),
            'tree_sha256': AFTER, 'intermediate_tree_sha256': MIDDLE, 'prior_successor_tree_sha256': BEFORE,
            'late_evidence_files_verified': len(sealed['evidence_files']),
            'business_acceptance_pending': True, 'production_commands_executed': False}, parent
