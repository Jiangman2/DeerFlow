"""Package an admin-only membership completion, reusing the qualified host transport."""
import argparse
import ast
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def extract(filename, names):
    source = (HERE / filename).read_text(encoding='utf-8')
    nodes = [n for n in ast.parse(source).body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.name in names]
    assert {n.name for n in nodes} == set(names), 'HELPER_SHAPE_CHANGED'
    return '\n\n'.join(ast.get_source_segment(source, n) for n in nodes) + '\n'


def replace(source, before, after):
    assert source.count(before) == 1, 'TEMPLATE_SHAPE_CHANGED'
    return source.replace(before, after, 1)


def read(name):
    return json.loads((HERE / name).read_text(encoding='utf-8'))


def build():
    receipt = read('weknora-company-activation-field-20261024-r1.json')['observation']
    package = read('weknora-company-activation-package-20261024-r1.json')
    assert receipt['runtime_accepted'] and receipt['auto_enrollment_deployed'] and receipt['accepted_pods'] == 70
    assert receipt['planned_gateway']['generation'] == 387
    shared = "import asyncio,json,os,sys,time\nORG='b9cfbf7f-7281-4f62-a72b-6a22379efe60'\nKB='bd83e659-9d9c-429a-acf1-ead01037cfa1'\nOWNER='10000'\nEMAIL='jiangman2@cnooc.com.cn'\n"
    common = shared + extract('enroll-weknora-company-members-child.py', ('Refused', 'require', 'API', 'Database', 'role_map', 'with_heartbeat'))
    bootstrap = shared + extract('bootstrap-weknora-company-knowledge-child.py', ('Refused', 'require', 'platform_admin', 'tenant_for_owner'))
    child = (HERE / 'enroll-weknora-company-admins-child.py').read_text(encoding='utf-8')
    sources = {k.removeprefix('app/backend/'): v['sha256'] for k, v in read('weknora-company-preload-build-20261024-r1.json')['qualification']['source_files'].items()}
    for key, value in {'COMMON': common, 'BOOTSTRAP': bootstrap, 'SOURCES': sources}.items():
        child = replace(child, key + ' = None', key + ' = ' + repr(value))

    pins = {'gateway': [receipt['planned_gateway'][k] for k in ('uid', 'generation', 'spec_sha256')],
            'weknora-app': ['44aca5b9-8fc1-44d6-af61-37c6d62723d5', 82, 'e7f481fc2b552098b8a8249beb2eb064212218cc48d5bbade2807446dc7ee83c']}
    reports = [{k: receipt['report_' + k] for k in ('path', 'bytes', 'sha256')}]
    base = ['kubectl', '--kubeconfig=/etc/kubernetes/admin.conf', '--request-timeout=15s', '-n', 'deer-flow']
    guard = 'import hashlib,json,os,stat,subprocess\nfrom pathlib import Path\n'
    for key, value in {'PINS': pins, 'RECEIPTS': reports, 'IMAGE': package['candidate_image'], 'BASE': base}.items():
        guard += key + ' = ' + repr(value) + '\n'
    guard += extract('inspect-weknora-company-members-host.py', ('Refused', 'require', 'digest', 'query', 'check_deployment', 'eligible'))
    guard += '''
def check_receipts():
    for pin in RECEIPTS:
        path = pin['path']
        require(os.path.realpath(path) == path, 'RECEIPT_SYMLINK')
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1
                    and not info.st_mode & 0o022 and info.st_size == pin['bytes'] and info.st_size <= 1048576, 'RECEIPT_METADATA_CHANGED')
            raw = stream.read(pin['bytes'] + 1)
        require(len(raw) == pin['bytes'] and hashlib.sha256(raw).hexdigest() == pin['sha256'], 'RECEIPT_HASH_CHANGED')
        row = json.loads(raw)
        require(row.get('status') == 'WEKNORA_COMPANY_AUTO_ENROLLMENT_RUNTIME_ACCEPTED_BROWSER_PENDING'
                and row.get('runtime_accepted') is True and row.get('auto_enrollment_deployed') is True, 'ACTIVATION_NOT_ACCEPTED')
    return RECEIPTS
'''
    host = (HERE / 'enroll-weknora-company-members-host.py').read_text(encoding='utf-8')
    changes = [
        ('Add existing eligible workspaces as native viewers.', 'Complete missing existing administrator workspace memberships.'),
        ('CHILD = None', 'CHILD = ' + repr(child)), ('GUARD = None', 'GUARD = ' + repr(guard)),
        ('/var/lib/portal-weknora-company-bulk-enrollment-20261024-r1', '/var/lib/portal-weknora-company-admin-enrollment-20261025-r1'),
        ('BULK_VIEWER_ENROLLMENT_ACCEPTED_AUTO_ENROLLMENT_NOT_DEPLOYED', 'EXISTING_ADMIN_MEMBERSHIPS_ACCEPTED_ROLES_PRESERVED'),
        ('< 7240', '< 640'), ('portal-weknora-company-bulk-enrollment.v1', 'portal-weknora-company-admin-enrollment.v1'),
        ('allowed = {"phase", "pass"} if phase == "FRESH_PAGED_INVENTORY" else {"phase", "batches", "posts", "confirmed_viewers"}', 'allowed = {"phase", "checked", "posts", "preserved"}'),
        ('phase in {"FRESH_PAGED_INVENTORY", "BULK_VIEWER_BATCH_VERIFIED"}', 'phase == "ADMIN_MEMBERSHIP_PROGRESS"'),
        ('0 <= v <= 10000', '0 <= v <= 32'), ('"maximum_membership_posts": 10000', '"maximum_membership_posts": 32'),
        ('--enroll-existing-company-viewers-once', '--enroll-company-admins-once'),
        ('portal-weknora-company-bulk-enrollment-host.v1', 'portal-weknora-company-admin-enrollment-host.v1'),
        ('"auto_enrollment_deployed": False', '"auto_enrollment_deployed": True'),
        ('BULK_INCOMPLETE_SEE_OBSERVATION', 'ADMIN_COMPLETION_INCOMPLETE_SEE_OBSERVATION'),
        ('BULK_OPERATION_STOPPED_INSPECT_REPORT', 'ADMIN_OPERATION_STOPPED_INSPECT_REPORT'),
    ]
    for before, after in changes:
        host = replace(host, before, after)
    # Reject a substituted control parent before opening the durable once marker.
    host = replace(host, '    DIRECTORY.mkdir(mode=0o700)', '''    parent = DIRECTORY.parent
    require(str(parent.resolve()) == str(parent) and parent.stat().st_uid == 0 and not parent.stat().st_mode & 0o022, "UNSAFE_CONTROL_PARENT")
    DIRECTORY.mkdir(mode=0o700)''')
    # Only metadata gets and the one stdin-controlled child are ever used.
    for source in (common, bootstrap, guard, child, host):
        ast.parse(source, feature_version=(3, 7))
    return host.encode('utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    raw = build()
    with args.output.open('xb') as stream:
        stream.write(raw)
    print(json.dumps({'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}))
