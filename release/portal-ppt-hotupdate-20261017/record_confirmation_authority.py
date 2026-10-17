"""Retain only the three relevant direct user messages from the named source chat."""
import hashlib
import json
from pathlib import Path

SOURCE = Path('C:/Users/15285/.codex/sessions/2026/09/24/rollout-2026-09-24T17-55-01-01a0d2d7-0015-7a73-93c3-791b713a47f6.jsonl')
IDS = {
    'msg_01a12eee-fd8d-7d81-8c50-d50486340624': 'INITIAL_REFUSAL',
    'msg_01a12ef3-3006-7a82-bceb-9c12dce0c479': 'LIMIT_TO_PPT_MASTER_CONFIRMATION',
    'msg_01a12ef4-64c1-7891-95c5-e2bbf4464dae': 'SUBSEQUENT_EXPLICIT_AUTHORIZATION',
}


def main():
    selected = []
    with SOURCE.open(encoding='utf-8') as handle:
        for line_number, line in enumerate(handle, 1):
            item = json.loads(line)
            payload = item.get('payload', {})
            if not isinstance(payload, dict) or payload.get('id') not in IDS:
                continue
            assert payload.get('role') == 'user' and payload.get('type') == 'message'
            text = '\n'.join(c['text'] for c in payload['content'] if c.get('type') == 'input_text')
            selected.append({'source_line': line_number, 'message_id': payload['id'],
                'timestamp_utc': item['timestamp'], 'role': 'user', 'meaning': IDS[payload['id']],
                'text': text, 'text_utf8_sha256': hashlib.sha256(text.encode('utf-8')).hexdigest()})
            if len(selected) == len(IDS):
                break
    assert len(selected) == 3
    assert '允许仅修改' in selected[-1]['text'] and '确认实现' in selected[-1]['text']
    record = {
        'schema': 'portal-ppt-confirmation-existing-user-authority.v1',
        'status': 'EXISTING_EXPLICIT_USER_AUTHORITY_RECONCILED',
        'source_chat': '01a0d2d7-0015-7a73-93c3-791b713a47f6',
        'source_file': str(SOURCE), 'messages': selected,
        'order': 'Initial refusal superseded by later explicit permission, retaining the PPT-only scope limit',
        'authorized_scope': 'Only ppt-master confirmation implementation: version-bound conditional submission, duplicate/expired receipt handling and automatic confirmation/resume within the already requested feature',
        'excluded_scope': ['Other Skills', 'Upstream stage order', 'Choice/schema business rules or validation', 'Unrelated vendor-tree replacement'],
        'repository_guidance_relationship': 'This exact user-approved pending feature is a narrow existing exception to the general two-modification vendor guidance; no new permission is inferred from a lock date or assistant plan.',
        'production_deployment_authorized_by_this_record': False,
        'scope_only_not_runtime_qualification': True,
    }
    destination = Path(__file__).with_name('PPT-CONFIRMATION-SCOPE-AUTHORITY.json')
    destination.write_text(json.dumps(record, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    raw = destination.read_bytes()
    print(json.dumps({'path':str(destination),'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),
                      'last_direct_user_text':selected[-1]['text']}, ensure_ascii=True))


if __name__ == '__main__':
    main()
