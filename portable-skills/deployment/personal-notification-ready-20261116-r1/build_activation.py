"""Seal the one-module personal-notification fix against the observed Gateway 419."""
import ast
import base64
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
RELEASE = ROOT / 'release/energy-personal-notification-ready-20261116-r1'
NAME = 'activate-energy-personal-notification-20261116-r1.py'
CONTROL = '/var/lib/portal-energy-personal-notification-20261116-r1'
TARGET = '/app/backend/app/scheduler/work_notification_delivery.py'


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def assign(source, name, value):
    node = next(n for n in ast.parse(source).body if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == name for t in n.targets))
    lines = source.splitlines(True)
    lines[node.lineno - 1:node.end_lineno] = [name + ' = ' + repr(value) + '\n']
    return ''.join(lines)


def function(source, name, replacement, owner=None):
    tree = ast.parse(source)
    nodes = tree.body if owner is None else next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == owner).body
    node = next(n for n in nodes if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)
    lines = source.splitlines(True)
    lines[node.lineno - 1:node.end_lineno] = [replacement.rstrip() + '\n']
    return ''.join(lines)


def build():
    parent = ROOT / 'release/scheduler-concurrency-20261116-r2/activate-scheduler-concurrency-20261116-r2.py'
    raw = parent.read_bytes()
    inventory = json.loads((parent.parent / 'SOURCE.json').read_bytes())
    assert len(raw) == inventory['bytes'] and digest(raw) == inventory['sha256']
    host = raw.decode()
    original = {'__name__': 'retained_operator_definitions'}
    exec(compile(host, '<retained-operator>', 'exec'), original)
    child = base64.b64decode(original['CHILD_ASSET']['base64']).decode()
    candidate = (HERE / 'backend/app/scheduler/work_notification_delivery.py').read_bytes()
    code_meta = {'bytes': len(candidate), 'sha256': digest(candidate)}
    expected = original['EXPECTED_SOURCES']
    assert expected[TARGET] == {'bytes': 5209, 'sha256': '2127f2f5cc89dea6ca1772cb0e3c79ecc88ce449dd2a707c1d4a6bacca674b12'}
    fragments = (HERE / 'operator_fragments.py').read_text(encoding='utf-8')
    tree = ast.parse(fragments)
    globals_added = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        text = '\n'.join(fragments.splitlines()[node.lineno - 1:node.end_lineno])
        if node.name.startswith('operator_'):
            text = text.replace('def operator_' + node.name[9:] + '(', 'def ' + node.name[9:] + '(', 1)
            host = function(host, node.name[9:], '\n'.join('    ' + line if line else '' for line in text.splitlines()), 'Operator')
        elif node.name in {'build_spec', 'cas_patch', 'new_configmap', 'replace_scheduler'}:
            host = function(host, node.name, text)
        else:
            globals_added.append(text)
    anchor = '\n\nclass Operator:'
    assert host.count(anchor) == 1
    host = host.replace(anchor, '\n\n' + '\n\n'.join(globals_added) + anchor)
    bindings = {
        'BEFORE_GENERATION': 419,
        'BEFORE_SPEC_SHA': '08a8afffb65eaca6259fe8b01111a5c68a2523fba639f17a4b25611e9ccb98c8',
        'OLD_CM': 'deer-flow-scheduler-concurrency-32-a4d2f3af8d2f0889',
        'CONTROL': None,
        'LABEL': 'portal.personal-notification-ready/operation',
    }
    for key, value in bindings.items():
        if key != 'CONTROL':
            host = assign(host, key, value)
    host = host.replace("CONTROL = Path('/var/lib/portal-scheduler-concurrency-20261116-r2')", "CONTROL = Path('" + CONTROL + "')")
    extra = "\nTARGET = " + repr(TARGET) + "\nCODE_META = " + repr(code_meta)
    extra += "\nCODE_ASSET = " + repr({'base64': base64.b64encode(candidate).decode(), **code_meta})
    extra += "\nFIX_VOLUME = 'personal-notification-ready'\n"
    host = host.replace('\n\nclass Refused', extra + '\n\nclass Refused', 1)
    host = host.replace('scheduler-concurrency-activation.v1', 'energy-personal-notification-activation.v1')
    host = host.replace('scheduler-concurrency-private-plan.v1', 'energy-personal-notification-private-plan.v1')
    host = host.replace("'tool_revision': 'r2'", "'tool_revision': 'r1'")
    host = host.replace('--activate-concurrency-once', '--activate-personal-notification-once')
    host = host.replace('activate-scheduler-concurrency-20261116-r2.py', NAME)
    host = host.replace('SCHEDULER_CONCURRENCY_32_DEPLOYED_RUNTIME_ACCEPTED', 'PERSONAL_NOTIFICATION_READER_LINK_DEPLOYED_RUNTIME_ACCEPTED')
    host = host.replace('SCHEDULER_BUDGET_PATCH_NOT_OBSERVED_REVIEW_REQUIRED', 'PERSONAL_NOTIFICATION_PATCH_NOT_OBSERVED_REVIEW_REQUIRED')
    host = host.replace('GATEWAY_419', 'GATEWAY_420').replace('GATEWAY_418', 'GATEWAY_419')
    host = host.replace('CONCURRENCY_32_ROLLOUT_REQUESTED', 'PERSONAL_NOTIFICATION_ROLLOUT_REQUESTED')
    host = host.replace('SCHEDULER_CONCURRENCY_AND_LEASE_AWARE_MULTI_INSTANCE_ONLY', 'ONE_PERSONAL_WORK_NOTIFICATION_DELIVERY_MODULE_ONLY')
    host = host.replace("'scheduler_max_concurrent_runs_before':3", "'scheduler_max_concurrent_runs_before':32")
    host = host.replace("'scheduler_multi_instance_before':False", "'scheduler_multi_instance_before':True")
    host = host.replace('all_existing_gateway_spec_fields_except_configmap_name_preserved', 'all_existing_gateway_fields_except_delivery_mount_and_added_volume_preserved')
    host = host.replace('CREATE_NEW_BUDGET_CONFIGMAP', 'CREATE_NEW_NOTIFICATION_SOURCE_CONFIGMAP')
    host = host.replace('READ_GATEWAY_419_AND_FIXED_CONFIGMAPS', 'READ_GATEWAY_419_AND_PRESERVED_CONFIGMAPS')
    # Restore fixed existing config bytes, instead of changing any YAML.
    host = host.replace('operator.child = load_child()', 'operator.child = load_child()\n        load_code()')
    # The retained readonly verifier reconstructs every saved input and CAS patch.
    old = "rebuilt = replace_scheduler(maps[OLD_CM]['data']['config.yaml'].encode('utf-8'))"
    assert host.count(old) == 1
    host = host.replace(old, "rebuilt = load_code()\n        need(self.plan['config'] == accepted_profile() and self.plan['candidate_source'] == CODE_META, 'PRIVATE_PROFILE_BINDING')")
    old = "new_configmap(maps[OLD_CM], self.plan['new_configmap'], self.operation, rebuilt)"
    assert host.count(old) == 1
    host = host.replace(old, "new_configmap(self.plan['new_configmap'], self.operation, rebuilt)")
    host = host.replace('candidate_config_sha256', 'candidate_source_sha256')
    host = host.replace('Gateway 418 -> 419: global scheduler concurrency 3 -> 32 and lease-aware multi-instance mode.',
                        'Gateway 419 -> 420: refresh exact owned daily result before sending personal Work notification.')
    child = function(child, 'observe', (HERE / 'qualification_observe.py').read_text(encoding='utf-8'))
    child = function(child, 'check_config', (HERE / 'qualification_config.py').read_text(encoding='utf-8'))
    child = child.replace('SCHEDULER_CONCURRENCY_READONLY_QUALIFIED', 'PERSONAL_NOTIFICATION_READONLY_QUALIFIED')
    child = child.replace('scheduler-concurrency-qualification-child.v1', 'personal-notification-qualification-child.v1')
    # Qualification support is isolated code: no app import, real DB or sender.
    child = child.replace('\ndef observe(', '\n' + (HERE / 'qualification_functions.py').read_text(encoding='utf-8') + '\ndef observe(', 1)
    host = assign(host, 'CHILD_ASSET', {'base64': base64.b64encode(child.encode()).decode(), 'sha256': digest(child.encode())})
    for text in (host, child):
        compile(text, '<personal-notification-release>', 'exec')
        ast.parse(text, feature_version=(3, 7))
    return host.encode(), child.encode(), code_meta


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--seal', action='store_true')
    options = parser.parse_args()
    host, child, code = build()
    metadata = {'schema': 'personal-notification-release.v1', 'filename': NAME,
                'bytes': len(host), 'sha256': digest(host), 'child_sha256': digest(child),
                'candidate_source': code, 'gateway_before': 419, 'gateway_after': 420,
                'scheduler_max_concurrent_runs_preserved': 32, 'scheduler_budget_preserved': 10000,
                'work_messages_sent': 0, 'production_executed': False}
    if not options.seal:
        print(json.dumps(dict(metadata, sealed=False), sort_keys=True))
        return
    RELEASE.mkdir(parents=True, exist_ok=True)
    for name, raw in ((NAME, host), ('qualification_child.sealed.py', child),
                      ('SOURCE.json', (json.dumps(metadata, sort_keys=True, indent=2) + '\n').encode())):
        path = RELEASE / name
        assert not path.exists() or path.read_bytes() == raw, 'Never replace a different sealed release'
        if not path.exists():
            path.write_bytes(raw)
    print(json.dumps(metadata, sort_keys=True))


if __name__ == '__main__':
    main()
