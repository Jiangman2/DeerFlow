"""Scope the readonly inspector to the first post-update report task."""
import ast
import base64
import hashlib
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
THREADS = ['cd2ca7b0-139c-4576-ab5f-c67e9837db10']
RELEASE = ROOT / 'release/energy-webhook-thread-20261116-r2'
NAME = 'inspect-energy-webhook-thread-20261116-r2.py'


def build():
    path = HERE.parent / 'energy-work-delivery-20261116-r1/seal.py'
    spec = importlib.util.spec_from_file_location('qualified_named_inspector', path)
    parent = importlib.util.module_from_spec(spec);spec.loader.exec_module(parent)
    parent.THREADS = THREADS
    host, child = parent.build()
    host, child = host.decode(), child.decode()
    marker = '\ndef main():\n'
    assert child.count(marker) == 1
    child = child.replace(marker, '\n' + (HERE / 'extra.py').read_text(encoding='utf-8') + marker)
    child = child.replace('energy-work-delivery-child.v1', 'energy-explicit-webhook-thread-child.v2')
    # Use AST to replace the single sealed asset, retaining the seven-command
    # read-only transport and its rollout-aware durable observation handling.
    asset = {'base64': base64.b64encode(child.encode()).decode(), 'bytes': len(child.encode()),
             'sha256': hashlib.sha256(child.encode()).hexdigest()}
    node = next(n for n in ast.parse(host).body if isinstance(n, ast.Assign) and any(
        isinstance(t, ast.Name) and t.id == 'ASSET' for t in n.targets))
    lines = host.splitlines(keepends=True)
    lines[node.lineno - 1:node.end_lineno] = ['ASSET=' + repr(asset) + '\n']
    host = ''.join(lines).replace('energy-work-delivery-inspection.v1', 'energy-explicit-webhook-thread-inspection.v2')
    host = host.replace('READ_ONLY_NAMED_TASK_EXECUTION_AND_NOTIFICATION', 'READ_ONLY_NAMED_TASK_EXPLICIT_WEBHOOK_AND_EXECUTION')
    for source in (host, child):
        ast.parse(source, feature_version=(3, 7));compile(source, '<explicit-webhook-readonly>', 'exec')
    return host.encode(), child.encode()


def main():
    host, child = build()
    meta = {'schema': 'energy-explicit-webhook-thread-inspection-package.v2', 'filename': NAME, 'bytes': len(host),
        'sha256': hashlib.sha256(host).hexdigest(), 'child_sha256': hashlib.sha256(child).hexdigest(),
        'thread_ids': THREADS, 'production_executed': False, 'tasks_retried': False, 'messages_sent': 0,
        'database_readonly': True, 'webhook_urls_exported': False, 'original_task_and_private_target_examined_in_memory': True}
    RELEASE.mkdir(parents=True, exist_ok=True)
    for name, raw in [(NAME, host), ('SOURCE.json', (json.dumps(meta, sort_keys=True, indent=2) + '\n').encode())]:
        path = RELEASE / name
        if path.exists():assert path.read_bytes() == raw
        else:path.write_bytes(raw)
    print(json.dumps(meta, sort_keys=True))


if __name__ == '__main__':main()
