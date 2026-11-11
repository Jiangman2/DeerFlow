"""Offline source/receipt reconciliation; never imports production installers."""
import hashlib
import io
import json
from pathlib import PurePosixPath
import zipfile

ARCHIVE = 'release/released-sync-20261110/'
MANIFEST = ARCHIVE+'SOURCE.json'
MANIFEST_SHA256 = '3f570a30ac92fb02511af9e306b2f8d5188b4b06cf4ad80fce87e7dbf99671b7'
ENERGY = 'skills/public/energy-market-daily/'
FIELD = 'release/energy-market-released-20261110/proxy/field-20261110-1116/'
HUB = 'release/skillhub-auth-remediation-20261106-r1/'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def require(ok, code):
    if not ok:
        raise ValueError(code)


def check(read, path, row):
    p = PurePosixPath(path)
    require(not p.is_absolute() and '..' not in p.parts and '\\' not in path and ':' not in path,
            'RELEASE_UNSAFE_PATH')
    raw = read(path)
    require(len(raw) == row['bytes'] and sha(raw) == row['sha256'], 'RELEASE_FILE_CHANGED: '+path)
    return raw


def verify(read, energy_paths):
    raw = read(MANIFEST)
    require(sha(raw) == MANIFEST_SHA256, 'RELEASE_SEAL_CHANGED')
    seal = json.loads(raw)
    require(seal['schema'] == 'released-source-reconciliation.v1'
            and seal['production_commands_executed'] is False, 'RELEASE_SCOPE_CHANGED')
    for path, row in seal['files'].items():
        check(read, path, row)
    expected = {p for p in seal['files'] if p.startswith(ENERGY)}
    require(set(energy_paths) == expected, 'ENERGY_FILE_SET_CHANGED')
    upstream = json.loads(read(ENERGY+'UPSTREAM.json'))
    require(len(upstream['files']) == 158 and upstream['business_code_modified'] is False
            and upstream['credentials_included'] is False, 'UPSTREAM_SCOPE_CHANGED')
    for path, row in upstream['files'].items():
        check(read, ENERGY+'upstream/'+path, row)
    archive = upstream['archive']
    zipped = check(read, ENERGY+archive['file'], archive)
    with zipfile.ZipFile(io.BytesIO(zipped)) as package:
        names = [n for n in package.namelist() if not n.endswith('/')]
        require(len(names) == len(set(names)) == 158, 'UPSTREAM_ZIP_SET_CHANGED')
        for name in names:
            path = name.removeprefix('energy-daily-v1.0/')
            require(path in upstream['files'] and package.read(name) == read(ENERGY+'upstream/'+path),
                    'UPSTREAM_ZIP_BYTES_CHANGED')
    field = json.loads(read(FIELD+'proxy-activation.normalized.json'))
    require(field['status'] == 'COLLECTOR_PROXY_TRANSPORT_ACCEPTED'
            and field['collector_after']['generation'] == 3 and field['transport_accepted'] is True,
            'ENERGY_RUNTIME_NOT_BOUND')
    for path, key in ((ENERGY+'SKILL.md','skill_sha256'),(ENERGY+'scripts/entry.py','entry_sha256'),
                      ('deploy/enterprise/energy_market_collector.py','collector_sha256')):
        require(sha(read(path)) == field['qualification_after'][key], 'ENERGY_RUNTIME_CHANGED')
    require(field['formal_report_ready'] is False and field['business_acceptance_pending'] is True
            and field['market_data_state'] == 'WAIT_DATA' and field['schedule_created'] is False
            and field['work_messages_sent'] == 0, 'ENERGY_ACCEPTANCE_OVERCLAIM')
    native = json.loads(read(HUB+'ACTIVATION-R2-REPORT-RECONSTRUCTED.json'))
    edge = json.loads(read(HUB+'edge-bound-20261106-r2/REPORT-RECONSTRUCTED.json'))
    require(native['patch_request_count'] == 1 and native['browser_business_acceptance_pending'] is True,
            'SKILLHUB_NATIVE_SCOPE_CHANGED')
    require(edge['status'] == 'SKILLHUB_EDGE_AUTH_ACCEPTED_BROWSER_PENDING'
            and edge['browser_business_acceptance_pending'] is True, 'SKILLHUB_EDGE_SCOPE_CHANGED')
    return {'status':'DEPLOYED_SOURCE_AND_OPERATOR_EVIDENCE_MATCH','energy_original_files':158,
            'source_and_evidence_files':len(seal['files']),'energy_full_report_accepted':False,
            'financial_business_acceptance_pending':True,'production_commands_executed':False}
