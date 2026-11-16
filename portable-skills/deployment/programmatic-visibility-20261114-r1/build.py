"""Seal a read-only postcheck for the exact accepted 2.1.3 installation."""
import ast
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
RELEASE = ROOT / 'release/energy-daily-programmatic-visibility-20261114-r1'
INSTALLER = ROOT / 'release/energy-daily-programmatic-20261114-r1/install-energy-programmatic-skill-20261114-r1.py'
INSTALLER_SHA = '214b624bdbd7751a128c00cb6824f96a51bad32808168563cd9ca0b4ddcb22a9'


def build():
    raw = INSTALLER.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == INSTALLER_SHA
    assignments = [node for node in ast.parse(raw.decode()).body if isinstance(node, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == 'VISIBILITY_CHILD' for t in node.targets)]
    assert len(assignments) == 1
    child = ast.literal_eval(assignments[0].value)
    assert 'EXPECTED_SKILL_COUNT = 108' in child
    source = (HERE.parent / 'freight-link-20261114-r1/verify_visibility.py').read_text(encoding='utf-8')
    source = source.replace('import argparse\n', 'import argparse\n')
    source = source.replace('"""Read-only postcheck of the accepted freight/link Skill publication."""',
                            '"""Read-only postcheck of the accepted 2.1.3 Skill publication."""')
    start = source.index("INSTALLER = Path(")
    end = source.index("CLAIM = ", start)
    source = source[:start] + (
        "RECEIPT = Path('/var/lib/portal-energy-programmatic-skill-install-20261114-r1/REPORT.json')\n"
        "RECEIPT_SHA = '3d16bea239ecde303aa32399e3a9d1c839bbb49a3f98ce4327ef5cc143b46d42'\n"
        "TREE = 'a7b41e944717a2fa567455a3b98e1e36e09920c1c0d6afc4fe786d9452b01a5d'\n"
        "OPERATION = 'af7750a214e54d2ca4e951667e514e02'\n"
        'VISIBILITY_CHILD = ' + repr(child) + '\n'
    ) + source[end:]
    source = source.replace('energy-freight-links-visibility.v1', 'energy-programmatic-skill-visibility.v1')
    source = source.replace('fixed_read(RECEIPT, RECEIPT_SHA, 6929)', 'fixed_read(RECEIPT, RECEIPT_SHA, 9093)')
    old = ("        scope = {'__name__': 'accepted_installer_for_readonly_child'}\n"
           "        exec(compile(fixed_read(INSTALLER, INSTALLER_SHA, 406152), str(INSTALLER), 'exec'), scope)\n"
           "        inspector = Inspector(scope['VISIBILITY_CHILD'])")
    assert source.count(old) == 1
    source = source.replace(old, '        inspector = Inspector(VISIBILITY_CHILD)')
    old_guard = "             'INSTALLED_RECEIPT_MISMATCH')"
    assert source.count(old_guard) == 1
    source = source.replace(old_guard,
        "             'INSTALLED_RECEIPT_MISMATCH')\n"
        "        need(receipt.get('schema') == 'energy-programmatic-skill-install.v1'\n"
        "             and receipt.get('status') == 'ENERGY_DELIVERY_SKILL_INSTALLED_VISIBILITY_PENDING'\n"
        "             and receipt.get('maintenance_pod_absent') is True\n"
        "             and receipt.get('maintenance_configmap_absent') is True\n"
        "             and receipt.get('before') == receipt.get('after_deployments')\n"
        "             and receipt.get('gateway_modified') is False\n"
        "             and receipt.get('shared_runtime_qualification', {}).get('status') == 'NONROOT_ARM64_PROGRAMMATIC_SKILL_QUALIFIED'\n"
        "             and receipt.get('shared_runtime_qualification', {}).get('offline_tests', {}).get('run') == 27\n"
        "             and receipt.get('shared_runtime_qualification', {}).get('offline_tests', {}).get('passed') is True,\n"
        "             'INSTALLED_QUALIFICATION_MISMATCH')")
    result = source.encode('utf-8')
    compile(result, '<sealed-programmatic-visibility>', 'exec')
    ast.parse(source, feature_version=(3, 7))
    ast.parse(child, feature_version=(3, 7))
    return result, child


def main():
    raw, child = build()
    filename = 'verify-energy-programmatic-skill-20261114-r1.py'
    meta = {'schema': 'energy-programmatic-visibility-package.v1', 'filename': filename,
            'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(),
            'accepted_operation': 'af7750a214e54d2ca4e951667e514e02',
            'accepted_receipt_sha256': '3d16bea239ecde303aa32399e3a9d1c839bbb49a3f98ce4327ef5cc143b46d42',
            'visibility_child_sha256': hashlib.sha256(child.encode()).hexdigest(),
            'installer_source_sha256': INSTALLER_SHA,
            'read_only': True, 'provider_requests': 0, 'messages_sent': 0, 'production_executed': False}
    RELEASE.mkdir(parents=True, exist_ok=True)
    for name, value in ((filename, raw), ('SOURCE.json', (json.dumps(meta, sort_keys=True, indent=2) + '\n').encode())):
        target = RELEASE / name
        if target.exists():
            assert target.read_bytes() == value, 'EXISTING_RELEASE_DIFFERS'
        else:
            target.write_bytes(value)
    print(json.dumps(meta, sort_keys=True))


if __name__ == '__main__':
    main()
