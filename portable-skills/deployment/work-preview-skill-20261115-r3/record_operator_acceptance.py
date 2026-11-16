"""Archive the supplied successful r3 receipt without mutating sealed inputs."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ATTACHMENT = Path('C:/Users/15285/.codex/attachments/0fe4c85f-8ce2-49ca-bd3e-bab9b03a3c1a/已粘贴的文本.txt')
RELEASE = ROOT / 'release/energy-work-preview-skill-20261115-r3'


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def write_once(path, raw):
    if path.exists():
        assert path.read_bytes() == raw, path.name
    else:
        with path.open('xb') as output:
            output.write(raw)


def main():
    supplied = ATTACHMENT.read_bytes()
    rows = [json.loads(line) for line in supplied.decode('utf-8-sig').splitlines() if line.startswith('{')]
    matches = [row for row in rows if row.get('schema') == 'energy-programmatic-skill-install.v1']
    assert len(matches) == 1
    result = dict(matches[0])
    assert result.pop('report_bytes') == 10368
    assert result.pop('report_sha256') == 'cf114e7b616df747e706f9efedb3757692abba0ea7f1ef14524fd30ebb362a5b'
    assert result.pop('report_path') == '/var/lib/portal-energy-work-preview-skill-install-20261115-r3/REPORT.json'
    raw = json.dumps(result, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode('ascii')
    assert len(raw) == 10368 and digest(raw) == 'cf114e7b616df747e706f9efedb3757692abba0ea7f1ef14524fd30ebb362a5b'
    assert result['status'] == 'ENERGY_DELIVERY_SKILL_INSTALLED' and result['skill_updated'] is True
    assert result['operation'] == 'db234834b59a4fbca2cc4d466d861bcd'
    assert result['maintenance_pod_absent'] and result['maintenance_configmap_absent']
    assert result['before'] == result['prepublication_deployments'] == result['after_deployments']
    assert result['gateway_modified'] is False and result['provisioner_modified'] is False
    expected_tree = '432383e2ee0d52d49328b6a0ccde56b0d04c10b81e03e4b854a507d9078e44ba'
    assert result['publication']['after_tree_sha256'] == expected_tree
    assert result['ordinary_sandbox_visibility']['skill_tree_sha256'] == expected_tree
    qualification = result['shared_runtime_qualification']
    assert qualification['status'] == 'NONROOT_ARM64_PROGRAMMATIC_SKILL_QUALIFIED'
    assert qualification['uid'] == qualification['gid'] == 1000
    assert qualification['offline_tests']['run'] == 39 and qualification['offline_tests']['passed']
    assert qualification['final_text']['reader_link_survives_two_2000_character_previews']
    assert qualification['final_text']['gateway_418_isolated_message_is_link_only']
    assert qualification['freight_render']['status'] == 'FREIGHT_FALLBACK_RENDER_QUALIFIED'
    assert result['provider_requests'] == result['work_messages_sent'] == 0
    summary = {
        'schema': 'energy-work-preview-operator-acceptance.v1',
        'status': 'SKILL_216_INSTALLED_RUNTIME_ACCEPTED_BUSINESS_PENDING',
        'operation': result['operation'], 'observed_at': result['observed_at'],
        'receipt_bytes': len(raw), 'receipt_sha256': digest(raw),
        'receipt_reconstructed_exactly_from_operator_json': True,
        'skill_tree_sha256': expected_tree, 'gateway_generation_unchanged': 418,
        'ordinary_sandbox_visible': True, 'fixed_png_and_dated_freight_render_qualified': True,
        'provider_requests': 0, 'work_messages_sent': 0,
        'real_future_scheduled_work_delivery_proven': False,
        'authenticated_mobile_html_access_proven': False,
        'old_notifications_replayed': False,
    }
    write_once(RELEASE / 'OPERATOR-OUTPUT.txt', supplied)
    write_once(RELEASE / 'OPERATOR-INSTALL-REPORT.json', raw)
    write_once(RELEASE / 'OPERATOR-ACCEPTANCE.json', (json.dumps(summary, sort_keys=True, indent=2) + '\n').encode('utf-8'))
    print(json.dumps(summary, sort_keys=True))


if __name__ == '__main__':
    main()
