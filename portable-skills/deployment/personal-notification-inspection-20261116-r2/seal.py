"""Bind a read-only inspector to the November 16 16:13 personal notification."""
import ast
import base64
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
THREAD = 'a5f059b0-1858-4687-8d16-2de8f853c418'
RELEASE = ROOT / 'release/energy-personal-notification-inspection-20261116-r2'
NAME = 'inspect-energy-personal-notification-20261116-r2.py'


def build():
    parent = ROOT / 'release/energy-personal-delivery-20261116-r1/inspect-energy-personal-delivery-20261116-r1.py'
    raw = parent.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == '234e54727dd6db353446e6711c9b4d567e9d31dfe79c8b113422d2c273f04af5'
    scope = {'__name__': 'readonly_inspector_parent'}
    exec(compile(raw, str(parent), 'exec'), scope)
    child = base64.b64decode(scope['ASSET']['base64']).decode().replace('9a762837-e4c4-4376-a14d-fcefbacd4bbc', THREAD)
    marker = '\ndef main():\n'
    assert child.count(marker) == 1 and child.count('await diagnose_thread(conn,thread)') == 1
    child = child.replace(marker, '\n' + (HERE / 'extra.py').read_text(encoding='utf-8') + marker)
    child = child.replace('await diagnose_thread(conn,thread)', 'await diagnose_current_personal(conn,thread)')
    child = child.replace('energy-personal-delivery-child.v1', 'energy-personal-notification-child.v2')
    data = child.encode()
    asset = {'base64': base64.b64encode(data).decode(), 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
    host = raw.decode().replace('9a762837-e4c4-4376-a14d-fcefbacd4bbc', THREAD)
    node = next(n for n in ast.parse(host).body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'ASSET' for t in n.targets))
    lines = host.splitlines(keepends=True)
    lines[node.lineno - 1:node.end_lineno] = ['ASSET=' + repr(asset) + '\n']
    host = ''.join(lines).replace('energy-personal-delivery-inspection.v1', 'energy-personal-notification-inspection.v2')
    host = host.replace('READ_ONLY_NAMED_TASK_EXPLICIT_WEBHOOK_AND_EXECUTION', 'READ_ONLY_LATEST_PERSONAL_NOTIFICATION_AND_GATEWAY_BASELINE')
    for text in (host, child):
        ast.parse(text, feature_version=(3, 7))
        compile(text, '<personal-notification-inspector>', 'exec')
    return host.encode(), data


def main():
    host, child = build()
    RELEASE.mkdir(parents=True, exist_ok=True)
    meta = {'schema': 'personal-notification-inspection-package.v2', 'filename': NAME, 'bytes': len(host),
            'sha256': hashlib.sha256(host).hexdigest(), 'child_sha256': hashlib.sha256(child).hexdigest(),
            'thread_id': THREAD, 'database_readonly': True, 'provider_requests': 0, 'work_messages_sent': 0,
            'tasks_replayed': False, 'project_files_read': False, 'unknown_formatter_code_executed': False}
    for name, value in ((NAME, host), ('SOURCE.json', (json.dumps(meta, sort_keys=True, indent=2) + '\n').encode())):
        target = RELEASE / name
        assert not target.exists() or target.read_bytes() == value, 'Never replace a different sealed inspection tool'
        if not target.exists():
            target.write_bytes(value)
    print(json.dumps(meta, sort_keys=True))


if __name__ == '__main__':
    main()
