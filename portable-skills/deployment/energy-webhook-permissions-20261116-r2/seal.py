"""Retain immutable r1 and add bounded per-command project observations."""
import ast
import base64
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
RELEASE = ROOT / 'release/energy-webhook-permissions-20261116-r2'
NAME = 'inspect-energy-webhook-permissions-20261116-r2.py'


def build():
    path = ROOT / 'release/energy-webhook-permissions-20261116-r1/inspect-energy-webhook-permissions-20261116-r1.py'
    raw = path.read_bytes();assert hashlib.sha256(raw).hexdigest() == '483dfa86c7cb38a2aa8a3431fdc7aee5e1002d8eb1d235ed89f35d4830deaebb'
    scope = {'__name__': 'sealed_readonly_definitions'};exec(compile(raw, str(path), 'exec'), scope)
    child = base64.b64decode(scope['ASSET']['base64']).decode()
    marker = '\ndef main():\n';assert child.count(marker) == 1
    child = child.replace(marker, '\n' + (HERE / 'extra.py').read_text(encoding='utf-8') + marker)
    child = child.replace('energy-webhook-permissions-child.v1', 'energy-webhook-permissions-child.v2')
    asset = {'base64': base64.b64encode(child.encode()).decode(), 'bytes': len(child.encode()), 'sha256': hashlib.sha256(child.encode()).hexdigest()}
    host = raw.decode();node = next(n for n in ast.parse(host).body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'ASSET' for t in n.targets))
    lines = host.splitlines(keepends=True);lines[node.lineno - 1:node.end_lineno] = ['ASSET=' + repr(asset) + '\n']
    host = ''.join(lines).replace('energy-webhook-permissions-inspection.v1', 'energy-webhook-permissions-inspection.v2')
    for source in (host, child):ast.parse(source, feature_version=(3, 7));compile(source, '<readonly-permissions-v2>', 'exec')
    return host.encode(), child.encode()


def main():
    host, child = build();RELEASE.mkdir(parents=True, exist_ok=True)
    meta = {'schema': 'energy-webhook-permissions-package.v2', 'filename': NAME, 'bytes': len(host),
        'sha256': hashlib.sha256(host).hexdigest(), 'child_sha256': hashlib.sha256(child).hexdigest(),
        'production_executed': False, 'messages_sent': 0, 'database_readonly': True, 'files_modified': False,
        'target_thread': 'cd2ca7b0-139c-4576-ab5f-c67e9837db10', 'max_explicit_sender_projects': 4}
    for name, raw in ((NAME, host), ('SOURCE.json', (json.dumps(meta, sort_keys=True, indent=2) + '\n').encode())):
        path = RELEASE / name
        if path.exists():assert path.read_bytes() == raw
        else:path.write_bytes(raw)
    print(json.dumps(meta, sort_keys=True))


if __name__ == '__main__':main()
