"""Seal reviewed source into a new standalone artifact; never deploy it."""
import argparse
import base64
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[2]


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reviewed', action='store_true', required=True)
    parser.parse_args()
    host = (ROOT / 'activate-task-conversation-context-20261114-r1.py').read_bytes()
    child = (ROOT / 'qualify_context_child.py').read_bytes()
    code = (ROOT / 'backend/packages/harness/deerflow/sandbox/tools.py').read_bytes()
    tests = json.loads((ROOT / 'OPERATOR-VALIDATION.json').read_bytes())
    context_tests = json.loads((ROOT / 'VALIDATION.json').read_bytes())
    field = json.loads((ROOT / 'FIELD-BASELINE.json').read_bytes())
    assert tests['passed'] is True and tests['failures'] == tests['errors'] == tests['skipped'] == 0
    assert tests['operator_template_sha256'] == digest(host) and tests['qualification_child_sha256'] == digest(child)
    assert context_tests['status'] == 'OFFLINE_TESTS_PASSED_NOT_DEPLOYED' and context_tests['candidate_tools_sha256'] == digest(code)
    assert field['production_parent_source_sample_matched'] is True and field['gateway']['generation'] == 416
    assert field['fixed_source_hashes']['packages/harness/deerflow/sandbox/tools.py']['sha256'] == 'a8e8b4f3ff94f79e2e0458414f4c2d55310bdd170a8859c00b99dd1d65ad929c'
    assert digest(code) == 'e598a0fd0e9d9a83ecdb781be6a345bf974cdef957852d181732f3596ad58330' and len(code) == 100988
    assert 0 < len(child) <= 65536
    sealed = host
    for name, content in (('CHILD', child), ('CODE', code)):
        marker = (name + '_ASSET = {}  # Sealer fills only after independent source review.').encode()
        assert sealed.count(marker) == 1
        asset = {'base64': base64.b64encode(content).decode('ascii'), 'sha256': digest(content)}
        sealed = sealed.replace(marker, (name + '_ASSET = ' + repr(asset)).encode('ascii'))
    compile(sealed, '<sealed-task-context-host>', 'exec')
    compile(child, '<readonly-task-context-child>', 'exec')
    release = REPO / 'release/task-conversation-context-20261114-r1'
    if release.exists():
        raise ValueError('RELEASE_ALREADY_EXISTS_REVIEW_REQUIRED')
    release.mkdir()
    filename = 'activate-task-conversation-context-20261114-r1.py'
    with (release / filename).open('xb') as output:
        output.write(sealed)
    manifest = {'schema': 'task-conversation-context-source-release.v1', 'production_mutation_performed': False,
        'before_generation': 416, 'after_generation': 417, 'only_new_configmap_volume_and_mount': True,
        'operator': {'filename': filename, 'bytes': len(sealed), 'sha256': digest(sealed)},
        'operator_template': {'bytes': len(host), 'sha256': digest(host)}, 'child': {'bytes': len(child), 'sha256': digest(child)},
        'candidate_source': {'bytes': len(code), 'sha256': digest(code)}, 'parent_source_sha256': 'a8e8b4f3ff94f79e2e0458414f4c2d55310bdd170a8859c00b99dd1d65ad929c',
        'field_baseline_sha256': digest((ROOT / 'FIELD-BASELINE.json').read_bytes()),
        'operator_tests': tests['tests'], 'full_module_and_skill_integration_tests': context_tests['tests'],
        'actual_python37_guard_pending': True, 'production_business_acceptance_pending': True}
    with (release / 'SOURCE.json').open('x', encoding='utf-8', newline='\n') as output:
        output.write(json.dumps(manifest, sort_keys=True, indent=2) + '\n')
    print(json.dumps(manifest, sort_keys=True))


if __name__ == '__main__':
    main()
