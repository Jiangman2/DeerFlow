"""Build the installed joint-image check, bound to the successful compat rollout."""
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
    field = json.loads((HERE / 'oa-compat-activation-field-20261022-r4.json').read_text(encoding='utf-8'))['observation']
    assert field['status'] == 'COMPATIBILITY_ROLLOUT_ACCEPTED_SCHEMA_UNCHANGED'
    assert field['runtime_accepted'] == {'gateway': 70, 'ppt-master-runner': 1}
    pins = json.loads((HERE / 'oa-data-qa-installed-image-pins-20261022.json').read_text())
    expected = dict(field['after'])
    expected['frontend'] = dict(uid='bb0b3f6e-31f7-4073-8b76-7650ea5b96ee', generation=225,
                                spec_sha256='1d3af5d2091bbb29fb4a45a3743acc03fbff9bf190c23c67b5bc43bb4fc37123')
    expected['gateway'] = dict(expected['gateway'], **{k: pins[k] for k in ('node', 'node_uid', 'candidate_image', 'config_digest', 'sources')})
    source = (HERE / 'verify-oa-compat-images.py').read_text()
    child = (HERE / 'oa-data-qa-installed-check.py').read_text()
    child_tree = ast.parse(child)
    checks = ast.literal_eval(next(n.value for n in child_tree.body if isinstance(n, ast.Assign) and n.targets[0].id == 'CHECKS'))
    source = replace_once(source, 'EXPECTED = {}', 'EXPECTED = ' + repr(expected))
    source = source.replace('portal-oa-compat-image-check-20261022-r1', 'portal-oa-data-qa-image-check-20261022-r1')
    source = source.replace('portal-oa-compat-check-', 'portal-oa-feature-check-')
    source = source.replace('portal-oa-compat-installed-images.v1', 'portal-oa-data-qa-installed-image.v1')
    source = source.replace('COMPAT_IMAGES_ACCEPTED_NOT_DEPLOYED', 'OA_DATA_QA_IMAGE_ACCEPTED_NOT_DEPLOYED')
    source = source.replace('INSTALLED_COMPAT_IMAGE_CHECK', 'INSTALLED_OA_DATA_QA_IMAGE_CHECK')
    source = replace_once(source, 'Two bounded image qualification Jobs; no production deployment or DB changes.',
                          'One bounded OA/Data QA image Job; no production database or workload changes.')
    source = replace_once(source, "for deployment, pin in EXPECTED.items():\n            name =", "for deployment, pin in [('gateway', EXPECTED['gateway'])]:\n            name =")
    source = replace_once(source, "outcome.get('status') == 'INSTALLED_COMPAT_IMAGE_ACCEPTED'\n                    and len(outcome.get('checks', [])) == 5",
                          "outcome.get('status') == 'INSTALLED_OA_DATA_QA_IMAGE_ACCEPTED'\n                    and outcome.get('checks') == " + repr(checks)
                          + " and outcome.get('provider_requests') == 0 and outcome.get('oa_callback_requests') == 0\n                    and outcome.get('schema_migrated') is False")
    # A successful human-pasted summary is not a replacement for the protected
    # full server report. Verify that exact report locally before creating a Job.
    guard = '''def accepted_compatibility_report():
    p = Path(PRIOR['report_path'])
    import stat
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    fd = os.open(str(p), flags)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022
                and info.st_size == PRIOR['report_bytes'], 'COMPATIBILITY_RECEIPT_METADATA_CHANGED')
        raw = os.read(fd, PRIOR['report_bytes'] + 1)
        require(len(raw) == PRIOR['report_bytes'] and hashlib.sha256(raw).hexdigest() == PRIOR['report_sha256'],
                'COMPATIBILITY_RECEIPT_CHANGED')
        receipt = json.loads(raw)
        require(receipt['status'] == 'COMPATIBILITY_ROLLOUT_ACCEPTED_SCHEMA_UNCHANGED'
                and receipt['after'] == PRIOR['after'] and receipt['schema_revision'] == '0020_ppt_confirmation',
                'COMPATIBILITY_NOT_ACCEPTED')
    finally:
        os.close(fd)


'''
    prior = {k: field[k] for k in ('report_path', 'report_bytes', 'report_sha256', 'after')}
    source = replace_once(source, 'def main():', 'PRIOR = ' + repr(prior) + '\n\n\n' + guard + 'def main():')
    source = replace_once(source, '    os.mkdir(CONTROL, 0o700)', '    accepted_compatibility_report()\n    os.mkdir(CONTROL, 0o700)')
    source = replace_once(source, "('status', 'error_class', 'production_database_access')",
                          "('status', 'error_class', 'check_code', 'missing_python_module', 'production_database_access')")
    source = replace_once(source, "and row['pods'][0]['containers'][0]['imageID'], 'MISSING_RUNTIME_IMAGE_RECEIPT')",
                          "and row['pods'][0]['containers'][0]['imageID'] and row['pods'][0]['phase'] == 'Succeeded'\n"
                          "                    and row['pods'][0]['containers'][0]['exit_code'] == 0, 'MISSING_RUNTIME_IMAGE_RECEIPT')")
    # Explicit opt-out avoids any image default unexpectedly enabling v2 in the
    # isolated checker; no production environment is mounted or changed.
    source = replace_once(source, "{'name': 'TMPDIR', 'value': '/tmp'}]", "{'name': 'TMPDIR', 'value': '/tmp'},\n"
                          "                            {'name': 'OA_POLICY_FLOW_ENABLED', 'value': 'false'},\n"
                          "                            {'name': 'OA_POLICY_FLOW_MULTI_RESULT_ENABLED', 'value': 'false'}]")
    source = replace_once(source, "CHILD = ''", 'CHILD = ' + repr(child))
    ast.parse(source, feature_version=(3, 7))
    ast.parse(child, feature_version=(3, 12))
    return source


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    raw = build().encode()
    with Path(args.output).open('xb') as stream:
        stream.write(raw)
    print(json.dumps({'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(), 'production_modified': False}))
