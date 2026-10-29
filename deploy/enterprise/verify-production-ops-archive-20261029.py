"""Verify archived operational evidence without importing or executing its tools."""
import argparse
import ast
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import subprocess

ROOT = Path(__file__).resolve().parents[2]
PREFIX = 'release/production-ops-20261028/'
MANIFEST_SHA256 = '0b753336a76f37f20359e3737581c85eef942c80bc4be7f12eb54e27159ed8a3'


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def verify_payload(read, names):
    raw = read('MANIFEST.json')
    require(digest(raw) == MANIFEST_SHA256, 'ARCHIVE_MANIFEST_CHANGED')
    manifest = json.loads(raw)
    require(not manifest['work_update_included'] and not manifest['weknora_provenance_work_included'], 'UNEXPECTED_SCOPE')
    expected = {row['path'] for row in manifest['files']}
    require(len(expected) == len(manifest['files']), 'DUPLICATE_PATH')
    for path in expected:
        parts = PurePosixPath(path)
        require(not parts.is_absolute() and '..' not in parts.parts and '\\' not in path, 'UNSAFE_PATH')
    require(set(names) == expected | {'MANIFEST.json', 'README.md', '.gitattributes'}, 'ARCHIVE_FILE_SET_CHANGED')
    payload = {}
    python_count = 0
    for row in manifest['files']:
        data = read(row['path'])
        require(len(data) == row['bytes'] and digest(data) == row['sha256'], 'ARCHIVE_BYTES_CHANGED: ' + row['path'])
        payload[row['path']] = data
        if row['path'].endswith('.py'):
            ast.parse(data.decode('utf-8'), feature_version=(3, 7))
            python_count += 1

    def record(name):
        return json.loads(payload['historical/' + name])

    base = payload['historical/rotate-relocate-portal-0015-auth-log-20261028-r1.py']
    require(digest(base) == 'c4bb6ad4c5b39bb646f207c4c00e7d4ccd7e03067311f4c49f51d23cbd14e546', 'TRANSFER_SOURCE_CHANGED')
    source = payload['historical/portal-auth-log-policy-0015-20261028-r1.py'].decode().replace('\r\n', '\n')
    require(source.count('BASE_SOURCE=None') == 1, 'INSTALLER_ANCHOR_CHANGED')
    packaged = source.replace('BASE_SOURCE=None', 'BASE_SOURCE=' + repr(base.decode()), 1).encode()
    delivered = payload['delivered/install-portal-0015-auth-log-policy-20261028-r1.py']
    require(packaged == delivered, 'DELIVERY_NOT_REPRODUCED_FROM_ARCHIVED_SOURCE')
    qualification = record('0015-auth-log-policy-tool-checks-20261028-r1.json')
    require(digest(delivered) == qualification['sha256'] and len(delivered) == qualification['bytes'], 'DELIVERY_QUALIFICATION_MISMATCH')
    require(qualification['tests_failed'] == 0 and qualification['tests_passed'] == 12, 'HISTORICAL_QUALIFICATION_CHANGED')

    transfer = record('0015-auth-log-relocation-field-20261028-r1.json')['observation']
    # The tool adds report metadata only after saving the original report.
    original = (json.dumps({k: v for k, v in transfer.items() if not k.startswith('report_')}, sort_keys=True) + '\n').encode()
    require(len(original) == transfer['report_bytes'] and digest(original) == transfer['report_sha256'], 'TRANSFER_RECEIPT_DIGEST_MISMATCH')
    installation = record('0015-auth-log-policy-install-field-20261028-r1.json')
    observed = installation['observation']
    require(installation['installation_accepted'] and not installation['first_scheduled_execution_confirmed'], 'INSTALLATION_SCOPE_CHANGED')
    require(observed['accepted_transfer_report_sha256'] == digest(original), 'INSTALLATION_TRANSFER_BINDING')
    require(observed['status'] == 'AUTH_LOG_PERMANENT_POLICY_INSTALLED_CHECKED_NO_ROTATION', 'INSTALLATION_NOT_ACCEPTED')
    require(observed['threshold_bytes'] == 104857600 and observed['hourly_minute'] == 17, 'POLICY_PARAMETERS_CHANGED')
    require(all(observed[k] == 0 for k in ('rotation_requests', 'service_restart_requests', 'service_signal_requests', 'workload_patch_requests', 'container_runtime_commands')), 'INSTALLATION_SIDE_EFFECT_SCOPE_CHANGED')

    for name, status in (
        ('dispatcher', 'DISPATCHER_REFERENCE_RESTORED_RUNTIME_READY_BROWSER_PENDING'),
        ('skillhub', 'SKILLHUB_REFERENCES_RESTORED_RUNTIME_READY_BROWSER_PENDING'),
    ):
        recovery = json.loads(payload['evidence/' + name + '-recovery-field.json'])['observation']
        require(recovery['status'] == status, 'RECOVERY_NOT_ACCEPTED')
        require(all(recovery[k] == 0 for k in ('deployment_patch_requests', 'image_pull_or_import_requests', 'pod_delete_requests', 'nginx_reload_requests', 'business_requests_by_tool')), 'RECOVERY_SCOPE_CHANGED')
    alloy = record('alloy-recovery-field-20261028-r1.json')
    require(alloy['runtime_accepted'] and alloy['image_import_requests'] == 1, 'ALLOY_RECOVERY_NOT_ACCEPTED')
    require(alloy['workload_patch_requests'] == alloy['pod_delete_requests'] == 0, 'ALLOY_RECOVERY_SCOPE_CHANGED')
    require(alloy['daemonset_spec_preserved'] and alloy['protected_deployment_specs_preserved'], 'ALLOY_WORKLOADS_CHANGED')

    frontend = record('frontend-polish-activation-field-20261028-r2.json')['observation']
    old_lock = record('frontend-production-source-lock-20261028.json')
    require(frontend['stage_passed'] and frontend['status'] == 'FRONTEND_POLISH_RUNTIME_ACCEPTED_BROWSER_PENDING'
            and frontend['after']['frontend']['generation'] == 230, 'FRONTEND_230_NOT_ACCEPTED')
    require(old_lock['image'].endswith(frontend['cache_manifest']), 'FRONTEND_230_IMAGE_MISMATCH')
    require(len(frontend['accepted_pods']) == 2 and not frontend['other_workload_mutation'], 'FRONTEND_230_SCOPE_CHANGED')
    require(all(p['observation']['status'] == 'PASS' and p['observation']['sources_verified']
                for p in frontend['accepted_pods']), 'FRONTEND_230_POD_NOT_ACCEPTED')
    require(frontend['services_preserved'] and frontend['unrelated_specs_preserved'], 'FRONTEND_230_OTHER_SERVICES_CHANGED')
    return dict(status='OPERATIONS_ARCHIVE_VERIFIED', files=len(payload), python37_syntax_files=python_count,
                delivered_installer_sha256=digest(delivered), policy_installation_accepted=True,
                first_scheduled_execution_confirmed=False, frontend_230_historical_only=True,
                application_code_modified=False, production_access=False, archived_tools_executed=False)


def verify(ref=None):
    if ref:
        revision = subprocess.check_output(['git', 'rev-parse', '--verify', ref + '^{commit}'], cwd=ROOT).decode().strip()
        names = subprocess.check_output(['git', 'ls-tree', '-r', '--name-only', revision, '--', PREFIX], cwd=ROOT).decode().splitlines()
        raw = subprocess.check_output(['git', 'cat-file', '--batch'], cwd=ROOT,
                                      input=''.join(revision + ':' + n + '\n' for n in names).encode())
        stream, payload = io.BytesIO(raw), {}
        for path in names:
            header = stream.readline().decode().split()
            require(len(header) == 3 and header[1] == 'blob', 'INVALID_GIT_OBJECT')
            payload[path[len(PREFIX):]] = stream.read(int(header[2]))
            require(stream.read(1) == b'\n', 'INVALID_GIT_BATCH')
        result = verify_payload(payload.__getitem__, payload.keys())
        result['git_revision'] = revision
        return result
    directory = ROOT / PREFIX
    names = {p.relative_to(directory).as_posix() for p in directory.rglob('*') if p.is_file() and '__pycache__' not in p.parts}
    return verify_payload(lambda p: (directory / p).read_bytes(), names)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ref')
    print(json.dumps(verify(parser.parse_args().ref), sort_keys=True))
