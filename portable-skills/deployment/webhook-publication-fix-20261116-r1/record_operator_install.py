"""Archive operator evidence only when the server's saved receipt hash matches."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SOURCE = Path('C:/Users/15285/.codex/attachments/805173dd-3b18-481c-859a-f497029392bf/已粘贴的文本.txt')
RELEASE = ROOT / 'release/energy-publication-fix-20261116-r1'


def main():
    raw = SOURCE.read_bytes();text = raw.decode('utf-8-sig');decoder = json.JSONDecoder();values = [];index = 0
    while index < len(text):
        if text[index] == '{':
            try:
                value, length = decoder.raw_decode(text[index:]);values.append(value);index += length;continue
            except ValueError:pass
        index += 1
    report = values[-1]
    assert report['status'] == 'ENERGY_DELIVERY_SKILL_INSTALLED' and report['skill_updated'] is True
    assert report['operation'] == 'f1aa3b4d4f1249d4a270d1a2b4cebd2b'
    assert report['code_package_sha256'] == '8e3fa5af23c875dc27338b7bd2c779a6a94923664d9b2f5bcde0c51f9b4979b0'
    assert not report['gateway_modified'] and not report['provisioner_modified'] and not report['unrelated_deployment_drift_observed']
    assert report['maintenance_pod_absent'] is True and report['maintenance_configmap_absent'] is True
    qualification = report['shared_runtime_qualification'];publication = report['publication'];visibility = report['ordinary_sandbox_visibility']
    assert qualification['offline_tests'] == dict(qualification['offline_tests'], run=62, errors=0, failures=0, skipped=0, passed=True)
    assert qualification['uid'] == qualification['gid'] == 1000
    assert qualification['publication_permissions'] == {'four_artifact_modes_0644': True, 'private_project_modes_0600': True, 'gateway_file_authorization_modified': False}
    assert publication['after_tree_sha256'] == '9ddcf11a813158d00aa119de28955aeeb71c7eca60926b58be5ab67d5579e864'
    assert publication['after_files'] == 110 and visibility['status'] == 'ORDINARY_SANDBOX_PUBLIC_RUNTIME_VISIBLE'
    assert visibility['skill_tree_sha256'] == publication['after_tree_sha256']
    assert qualification['png_sha256'] == 'bedd5af036641c695510cca936b1e530ed7600238a8892fe88b5d7d5953bdd9b'
    assert qualification['freight_render']['png_sha256'] == '4604973d99ff4413862e251a5a03dbf4ea6bc0e36641693ee1ac0e45ceadc3ab'
    assert report['work_messages_sent'] == report['provider_requests'] == 0
    saved = {key: value for key, value in report.items() if key not in ('report_path', 'report_bytes', 'report_sha256')}
    saved_raw = json.dumps(saved, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')
    assert len(saved_raw) == report['report_bytes'] == 10205
    assert hashlib.sha256(saved_raw).hexdigest() == report['report_sha256'] == '4155131e5d19b4ddf026f803a3a37bf086123b639fd5401ab83bcb0422761dd0'
    accepted = {'schema': 'energy-publication-fix-operator-acceptance.v1', 'version': '2.1.8-portable',
                'operation': report['operation'], 'observed_at': report['observed_at'], 'runtime_install_accepted': True,
                'receipt_sha256': report['report_sha256'], 'skill_tree_sha256': publication['after_tree_sha256'],
                'uid1000_mode_checks_passed': True, 'offline_tests_passed': 62, 'fixed_and_freight_pngs_accepted': True,
                'ordinary_sandbox_views_sampled': 1, 'ordinary_sandbox_visible': True,
                'protected_deployment_specs_unchanged': True, 'gateway_rollout_completion_not_established_by_this_tool': True,
                'provider_requests': 0, 'messages_sent': 0, 'real_group_webhook_business_acceptance': 'PENDING'}
    outputs = {'OPERATOR-OUTPUT.txt': raw, 'OPERATOR-INSTALL-REPORT.json': saved_raw,
               'OPERATOR-OBSERVATION.json': (json.dumps(report, sort_keys=True, indent=2, ensure_ascii=False) + '\n').encode(),
               'OPERATOR-ACCEPTANCE.json': (json.dumps(accepted, sort_keys=True, indent=2) + '\n').encode()}
    for name, value in outputs.items():
        path = RELEASE / name
        assert not path.exists() or path.read_bytes() == value, 'Never rewrite a different operator receipt'
        if not path.exists():path.write_bytes(value)
    print(json.dumps(accepted, sort_keys=True))


if __name__ == '__main__':main()
