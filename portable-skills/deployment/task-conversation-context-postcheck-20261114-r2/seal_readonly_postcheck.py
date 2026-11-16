"""Seal r2 only after an operator report proves the exact single API default."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[2]
OPERATION = '86bbcfd1fb7f40819284fa431ecce9f1'
REQUESTED = 'd9a8a00422329427b737b3f67d1c13f35482a16d6614cbc3365b705ad198b616'
PARENT_SHA = 'dff040f55c50089d3f1b3f3c738f00699ed2ec844fc476182c81ef63dd8309c8'


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def need(ok, code):
    if not ok:
        raise ValueError(code)


def proven_defaulting(report):
    need(report.get('schema') == 'task-context-spec-drift-readonly.v1'
         and report.get('status') == 'RECORDED_PATCH_SPEC_DIFF_OBSERVED_NO_CHANGE'
         and report.get('accepted_operation') == OPERATION, 'FIELD_REPORT_IDENTITY')
    need(all(report.get(key) is True for key in ('only_new_volume_default_mode_420_difference', 'new_volume_present_once',
        'new_volume_configmap_matches_recorded', 'new_readonly_source_mount_exact', 'stable_deployment_spec')), 'EXACT_SINGLE_DEFAULT_NOT_PROVEN')
    need(all(report.get(key) is False for key in ('workload_modified', 'files_modified', 'original_receipts_modified', 'diff_limit_reached')),
         'FIELD_REPORT_SCOPE')
    mode = report.get('new_volume_default_mode')
    need(type(mode) is int and mode == 420, 'DEFAULT_MODE_NOT_420')
    deployment = report.get('deployment', {})
    need(deployment.get('uid') == 'a62fc04c-7a82-4b57-8fbf-a8c7a3839ea1' and deployment.get('generation') == 417
         and deployment.get('desired') == 70 and re.fullmatch('[0-9a-f]{64}', str(deployment.get('spec_sha256', ''))), 'FIELD_DEPLOYMENT_IDENTITY')
    rows = report.get('differences')
    need(isinstance(rows, list) and len(rows) == 1, 'ONE_DIFFERENCE_REQUIRED')
    row = rows[0]
    need(re.fullmatch(r'/template/spec/volumes/[0-9]+/configMap/defaultMode', row.get('path', ''))
         and row.get('expected') == {'present': False, 'type': 'missing', 'sha256': None}
         and row.get('actual') == {'present': True, 'type': 'int', 'sha256': digest(canonical(420))}, 'FIELD_DIFFERENCE_VALUE')
    cm = report.get('new_configmap', {})
    need(set(cm) == {'present', 'immutable', 'operation_label_matches', 'only_tools_source', 'source_matches_candidate'}
         and all(value is True for value in cm.values()), 'FIELD_CONFIGMAP_BINDING')
    return {'schema': 'task-context-single-defaulting-field-proof.v1', 'field_report_sha256': digest(canonical(report)),
        'gateway_uid': deployment['uid'], 'generation': 417, 'requested_spec_sha256': REQUESTED,
        'actual_spec_sha256': deployment['spec_sha256'], 'difference_path': row['path'], 'actual_default_mode': 420, 'difference_count': 1}


def read_report(path):
    raw = path.read_bytes()
    need(len(raw) <= 1048576, 'INPUT_REPORT_BOUND')
    matches = []
    for line in raw.decode('utf-8-sig').splitlines():
        if line.startswith('{'):
            value = json.loads(line)
            if value.get('schema') == 'task-context-spec-drift-readonly.v1':
                matches.append(value)
    need(len(matches) == 1, 'ONE_OPERATOR_REPORT_REQUIRED')
    return raw, matches[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reviewed', action='store_true', required=True)
    parser.add_argument('--field-report', type=Path, required=True)
    args = parser.parse_args()
    input_raw, report = read_report(args.field_report)
    proof = proven_defaulting(report)
    host = (ROOT / 'verify-task-conversation-context-20261114-r2.py').read_bytes()
    tests = json.loads((ROOT / 'VALIDATION.json').read_bytes())
    need(tests['passed'] is True and tests['failures'] == tests['errors'] == 0 and tests['template_sha256'] == digest(host), 'OFFLINE_TEST_BINDING')
    parent = (REPO / 'release/task-conversation-context-20261114-r1/activate-task-conversation-context-20261114-r1.py').read_bytes()
    need(digest(parent) == PARENT_SHA, 'IMMUTABLE_PARENT_SOURCE_HASH')
    parent_marker = b'PARENT_ASSET = {}  # Filled only after source review and field-diff proof.'
    field_marker = b'FIELD_DEFAULTING = {}  # Must bind one real, exact observed defaulting difference.'
    need(host.count(parent_marker) == host.count(field_marker) == 1, 'TEMPLATE_RESOURCE_MARKERS')
    asset = {'base64': base64.b64encode(parent).decode('ascii'), 'sha256': PARENT_SHA}
    sealed = host.replace(parent_marker, ('PARENT_ASSET = ' + repr(asset)).encode('ascii'))
    sealed = sealed.replace(field_marker, ('FIELD_DEFAULTING = ' + repr(proof)).encode('ascii'))
    compile(sealed, '<readonly-postcheck-r2>', 'exec')
    release = REPO / 'release/task-conversation-context-postcheck-20261114-r2'
    need(not release.exists(), 'NEW_RELEASE_ALREADY_EXISTS')
    release.mkdir()
    target = release / 'verify-task-conversation-context-20261114-r2.py'
    with target.open('xb') as stream:
        stream.write(sealed)
    for name, value in (('FIELD-DEFAULTING.json', proof), ('SOURCE.json', {
        'schema': 'task-context-readonly-postcheck-release.v1', 'operator': {'filename': target.name, 'bytes': len(sealed), 'sha256': digest(sealed)},
        'template_sha256': digest(host), 'immutable_parent_sha256': PARENT_SHA, 'field_input_bytes': len(input_raw),
        'field_input_sha256': digest(input_raw), 'field_report_sha256': proof['field_report_sha256'],
        'prior_operation': OPERATION, 'only_observed_defaulting_accepted': True, 'old_releases_modified': False,
        'create_patch_delete_supported': False, 'tests': tests['tests'], 'production_requests': 0})):
        with (release / name).open('x', encoding='utf-8', newline='\n') as stream:
            stream.write(json.dumps(value, sort_keys=True, indent=2) + '\n')
    print(json.dumps({'status': 'FIELD_BOUND_READONLY_POSTCHECK_SEALED_NOT_EXECUTED', 'bytes': len(sealed), 'sha256': digest(sealed)}))


if __name__ == '__main__':
    main()
