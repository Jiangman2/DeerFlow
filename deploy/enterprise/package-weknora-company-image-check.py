"""Build one installed-image qualification Job; no deployment or membership writes."""
import argparse
import ast
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def replace_once(source, old, new):
    assert source.count(old) == 1, 'QUALIFIED_WRAPPER_SHAPE_CHANGED'
    return source.replace(old, new, 1)


def build():
    release = json.loads((HERE / 'weknora-company-preload-build-20261024-r1.json').read_text(encoding='utf-8'))
    field = json.loads((HERE / 'weknora-company-preloads-field-20261024-r1.json').read_text(encoding='utf-8'))
    profile = release['profile']
    role = profile['roles'][0]
    assert len(profile['roles']) == 1 and role['role'] == 'gateway-forward'
    assert field['accepted_nodes'] == 5 and len(field['observations']) == 5
    assert {r['hostname'] for r in field['observations']} == set(profile['nodes'])
    for receipt in field['observations']:
        assert receipt['node_preload_passed'] and receipt['old_references_preserved']
        assert not receipt['image_import_may_be_partial']
        assert receipt['expected_node_uid'] == profile['nodes'][receipt['hostname']]['expected_node_uid']
        assert len(receipt['roles']) == 1
        result = receipt['roles'][0]
        assert result['manifest_digest'] == role['manifest_digest'] and result['config_digest'] == role['config_digest']
        assert all(result[k] == 'PASS' for k in ('phase_a', 'phase_b', 'cri_visibility', 'content_and_unpack'))
    sources = {k.removeprefix('app/backend/'): v['sha256'] for k, v in release['qualification']['source_files'].items()}
    expected = {
        'gateway': {'uid': 'a62fc04c-7a82-4b57-8fbf-a8c7a3839ea1', 'generation': 386,
                    'spec_sha256': '890cc7b2aa7fc8bff6abaebfe99f6f7149ac17e17405bc7e056ecd24263516b4',
                    'node': 'ecs-zdz-pro-0018', 'node_uid': profile['nodes']['ecs-zdz-pro-0018']['expected_node_uid'],
                    'candidate_image': 'deerflow.local/deer-flow-gateway@' + role['manifest_digest'],
                    'config_digest': role['config_digest'], 'sources': sources},
        'weknora-app': {'uid': '44aca5b9-8fc1-44d6-af61-37c6d62723d5', 'generation': 82,
                        'spec_sha256': 'e7f481fc2b552098b8a8249beb2eb064212218cc48d5bbade2807446dc7ee83c'},
    }
    source = (HERE / 'verify-oa-compat-images.py').read_text(encoding='utf-8')
    child = (HERE / 'weknora-company-installed-check.py').read_text(encoding='utf-8')
    checks = ast.literal_eval(next(n.value for n in ast.parse(child).body if isinstance(n, ast.Assign) and n.targets[0].id == 'CHECKS'))
    source = replace_once(source, 'EXPECTED = {}', 'EXPECTED = ' + repr(expected))
    source = replace_once(source, "CHILD = ''", 'CHILD = ' + repr(child))
    for old, new in {
        'portal-oa-compat-image-check-20261022-r1': 'portal-weknora-company-image-check-20261024-r1',
        'portal-oa-compat-check-': 'portal-wk-company-check-',
        'portal-oa-image-check': 'portal-wk-company-check',
        'portal-oa-compat-installed-images.v1': 'portal-weknora-company-installed-image.v1',
        'COMPAT_IMAGES_ACCEPTED_NOT_DEPLOYED': 'WEKNORA_COMPANY_IMAGE_ACCEPTED_NOT_DEPLOYED',
        'INSTALLED_COMPAT_IMAGE_CHECK': 'INSTALLED_WEKNORA_COMPANY_IMAGE_CHECK',
        'Two bounded image qualification Jobs; no production deployment or DB changes.':
            'One isolated synthetic image check; no production DB, network calls, memberships or rollout.',
        '# Validate both consumers before creating either Job.': '# Validate the baseline before creating the single synthetic Job.',
    }.items():
        source = source.replace(old, new)
    source = replace_once(source, "for deployment, pin in EXPECTED.items():\n            name =", "for deployment, pin in [('gateway', EXPECTED['gateway'])]:\n            name =")
    source = replace_once(source, "outcome.get('status') == 'INSTALLED_COMPAT_IMAGE_ACCEPTED'\n                    and len(outcome.get('checks', [])) == 5",
        "outcome.get('status') == 'INSTALLED_WEKNORA_COMPANY_IMAGE_ACCEPTED'\n                    and outcome.get('checks') == " + repr(checks)
        + " and outcome.get('provider_requests') == 0 and outcome.get('real_http_requests') == 0\n                    and outcome.get('membership_grants') == 0 and outcome.get('production_configuration_used') is False")
    source = replace_once(source, "and row['pods'][0]['containers'][0]['imageID'], 'MISSING_RUNTIME_IMAGE_RECEIPT')",
        "and row['pods'][0]['containers'][0]['imageID'] and row['pods'][0]['phase'] == 'Succeeded'\n                    and row['pods'][0]['containers'][0]['exit_code'] == 0, 'MISSING_RUNTIME_IMAGE_RECEIPT')")
    source = replace_once(source, "('status', 'error_class', 'production_database_access')",
        "('status', 'error_class', 'check_code', 'missing_python_module', 'production_database_access')")
    source = replace_once(source, "and not status.get('terminatingReplicas') and not status.get('unavailableReplicas')",
        "and not spec.get('paused') and not status.get('terminatingReplicas') and not status.get('unavailableReplicas')")
    source = replace_once(source, "require(all(t.get('effect') in ('NoSchedule', 'PreferNoSchedule') for t in taints), 'NODE_NOEXECUTE_TAINT')",
        "require(all(t.get('effect') == 'NoSchedule' and t.get('key') == 'dedicated' and t.get('value') == 'gateway' for t in taints), 'NODE_UNEXPECTED_TAINT')")
    source = replace_once(source, "'provider_requests': 0, 'automatic_retry': False, 'ready_for_rollout': False}",
        "'provider_requests': 0, 'membership_grants': 0, 'automatic_retry': False, 'ready_for_rollout': False}")
    priors = []
    for name in ('weknora-company-bootstrap-field-20261024-r1.json', 'weknora-company-viewer-field-20261024-r1.json'):
        receipt = json.loads((HERE / name).read_text(encoding='utf-8'))
        priors.append({k: receipt[k] for k in ('report_path', 'report_bytes', 'report_sha256', 'status')})
    guard = '''def accepted_field_reports():
    import stat
    for pin in PRIORS:
        fd = os.open(pin['report_path'], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(fd)
            require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022
                    and info.st_size == pin['report_bytes'], 'PRIOR_REPORT_METADATA_CHANGED')
            with os.fdopen(fd, 'rb', closefd=False) as stream:
                raw = stream.read(pin['report_bytes'] + 1)
            require(len(raw) == pin['report_bytes'] and hashlib.sha256(raw).hexdigest() == pin['report_sha256'], 'PRIOR_REPORT_CHANGED')
            require(json.loads(raw)['status'] == pin['status'], 'PRIOR_NOT_ACCEPTED')
        finally:
            os.close(fd)


'''
    source = replace_once(source, 'def main():', 'PRIORS = ' + repr(priors) + '\n\n\n' + guard + 'def main():')
    source = replace_once(source, '    os.mkdir(CONTROL, 0o700)', '    accepted_field_reports()\n    os.mkdir(CONTROL, 0o700)')
    ast.parse(source, feature_version=(3, 7))
    ast.parse(child, feature_version=(3, 12))
    return source


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    raw = build().encode()
    with Path(args.output).open('xb') as f:
        f.write(raw)
    result = {'schema': 'portal-weknora-company-image-check-package.v1', 'bytes': len(raw),
              'sha256': hashlib.sha256(raw).hexdigest(), 'output': str(Path(args.output)),
              'production_deployed': False, 'installed_image_acceptance_pending': True}
    (HERE / 'weknora-company-image-check-package-20261024-r1.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))
