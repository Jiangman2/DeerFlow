"""Scope the qualified read-only comparison to one newly named report chat."""
import ast
import base64
import hashlib
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
THREADS = ['d99e944a-6d4b-4f22-9f53-f85d04a12fc0']
RELEASE = ROOT / 'release/energy-work-delivery-20261116-r1'
NAME = 'inspect-energy-work-delivery-20261116-r1.py'
RENAMES = {
    'energy-two-result-comparison.v1': 'energy-work-delivery-inspection.v1',
    'energy-two-task-child.v1': 'energy-work-delivery-child.v1',
    'EXACT_TWO_THREADS_REQUIRED': 'EXACT_NAMED_THREAD_REQUIRED',
    'TWO_TASKS_READONLY_COLLECTED': 'NAMED_TASK_READONLY_COLLECTED',
    'TWO_TASKS_PARTIALLY_OBSERVED': 'NAMED_TASK_PARTIALLY_OBSERVED',
    'TWO_NAMED_TASK_RESULTS_OBSERVED_NO_CHANGE': 'NAMED_TASK_RESULT_AND_NOTIFICATION_OBSERVED_NO_CHANGE',
    'READ_ONLY_TWO_NAMED_TASK_EXECUTION_AND_NOTIFICATION': 'READ_ONLY_NAMED_TASK_EXECUTION_AND_NOTIFICATION',
}


def build():
    path = HERE.parent / 'energy-two-task-comparison-20261116-r1/seal.py'
    spec = importlib.util.spec_from_file_location('qualified_comparison_sealer', path)
    parent = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(parent)
    parent.THREADS = list(THREADS)
    host, child = parent.build()
    host, child = host.decode(), child.decode()
    for before, after in RENAMES.items():
        host, child = host.replace(before, after), child.replace(before, after)
    asset = {'base64': base64.b64encode(child.encode()).decode(),
             'bytes': len(child.encode()), 'sha256': hashlib.sha256(child.encode()).hexdigest()}
    host = parent.assign(host, 'ASSET', asset)
    for source in (host, child):
        compile(source, '<single-chat-readonly-inspection>', 'exec')
        ast.parse(source, feature_version=(3, 7))
    return host.encode(), child.encode()


def main():
    host, child = build()
    metadata = {'schema': 'energy-work-delivery-inspection-package.v1', 'filename': NAME,
                'bytes': len(host), 'sha256': hashlib.sha256(host).hexdigest(),
                'child_sha256': hashlib.sha256(child).hexdigest(), 'thread_ids': THREADS,
                'production_executed': False, 'database_readonly': True,
                'provider_requests': 0, 'work_messages_sent': 0, 'tasks_retried': False,
                'mutations_supported': False, 'notification_ciphertext_read': False,
                'raw_messages_or_commands_exported': False}
    RELEASE.mkdir(parents=True, exist_ok=True)
    for name, raw in [(NAME, host), ('SOURCE.json', (json.dumps(metadata, indent=2, sort_keys=True) + '\n').encode())]:
        target = RELEASE / name
        if target.exists():
            assert target.read_bytes() == raw, 'EXISTING_RELEASE_DIFFERS'
        else:
            target.write_bytes(raw)
    print(json.dumps(metadata, sort_keys=True))


if __name__ == '__main__':
    main()
