"""One-file scope, retained production boundaries, and real journal recovery."""
import ast
import base64
import copy
import importlib.util
import json
from pathlib import Path
import types
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('personal_notification_builder', HERE / 'build_activation.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)
HOST, CHILD, CODE = builder.build()
host = types.ModuleType('personal_notification_host')
child = types.ModuleType('personal_notification_child')
exec(compile(HOST, '<host>', 'exec'), host.__dict__)
exec(compile(CHILD, '<child>', 'exec'), child.__dict__)
BASE = b'max_recursion_limit: 10000\nrun_ownership:\n  heartbeat_enabled: true\nscheduler:\n  recursion_limit: 10000\n  max_concurrent_runs: 32\n  multi_instance: true\n'
PROFILE = {'bytes': len(BASE), 'sha256': host.sha(BASE), 'max_concurrent_runs': 32, 'multi_instance': True}

# Reuse the field-qualified ownership/defaulting, endpoint, real transport,
# read-only journal and recovery tests without editing their historical files.
parent = (HERE.parent / 'scheduler-budget-20261113-r5/test_scheduler_budget.py').read_text(encoding='utf-8')
parent = parent.replace('415', '419').replace('416', '420').replace('len(sources), 13', 'len(sources), 17')
parent = parent.replace('--activate-scheduler-budget-once', '--activate-personal-notification-once')
parent = parent.replace('SCHEDULER_BUDGET_10000_DEPLOYED_RUNTIME_ACCEPTED', 'PERSONAL_NOTIFICATION_READER_LINK_DEPLOYED_RUNTIME_ACCEPTED')
parent = parent.replace('SCHEDULER_BUDGET_PATCH_NOT_OBSERVED_REVIEW_REQUIRED', 'PERSONAL_NOTIFICATION_PATCH_NOT_OBSERVED_REVIEW_REQUIRED')
old = """        with self.assertRaisesRegex(host.Refused, 'NOT_SEALED'):
            host.load_child()"""
new = """        self.assertEqual(host.load_child(), CHILD.decode())
        with mock.patch.object(host, 'CHILD_ASSET', {}):
            with self.assertRaisesRegex(host.Refused, 'NOT_SEALED'):
                host.load_child()"""
assert parent.count(old) == 1
parent = parent.replace(old, new)
old = "b'max_recursion_limit: 10000\\nscheduler:\\n  recursion_limit: 300\\nkeep: true\\n'"
parent = parent.replace(old, 'BASE')
old = """                return {'config_sha256': host.OLD_CONFIG_SHA, 'candidate_sha256': self.plan['new_config_sha256'],
                    'scheduler_recursion_limit': 300, 'candidate_scheduler_recursion_limit': 10000,
                    'max_recursion_limit': 10000, 'source_files_verified': 13, 'versions': child.PACKAGES,
                    'semantic_change_only_scheduler_recursion_limit': True}"""
new = """                return {'config_sha256': host.sha(BASE), 'scheduler_recursion_limit': 10000,
                    'max_recursion_limit': 10000, 'source_files_verified': 17, 'versions': child.PACKAGES,
                    'max_concurrent_runs':32, 'scheduler_multi_instance':True,
                    'isolated_notification_cases':10, 'postgres_query_compiled':True}"""
assert parent.count(old) == 1
parent = parent.replace(old, new)
old = "with mock.patch.object(host, 'CONTROL', control),"
new = "with mock.patch.object(host, 'CONTROL', control), mock.patch.object(host, 'accepted_profile', return_value=PROFILE),"
assert parent.count(old) == 1
parent = parent.replace(old, new)
tree = ast.parse(parent)
selected = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom, ast.FunctionDef))
            or isinstance(n, ast.ClassDef) and n.name not in {'BudgetReplacementTests', 'SpecTests', 'StrictYamlTests'}]
exec(compile(ast.fix_missing_locations(ast.Module(body=selected, type_ignores=[])), '<retained-production-boundaries>', 'exec'), globals())


class PersonalScopeTests(unittest.TestCase):
    def build(self, source=None):
        source = source or fixture()
        with mock.patch.object(host, 'BEFORE_SPEC_SHA', host.sha(host.canonical(source['spec']))):
            return host.build_spec(source, 'gateway-personal-ready-' + 'a' * 12)

    def test_only_one_existing_mount_changes_and_readonly_volume_is_added(self):
        before = fixture()
        saved = copy.deepcopy(before)
        candidate = self.build(before)
        self.assertEqual(before, saved)
        pod = candidate['template']['spec']
        self.assertEqual(pod['volumes'][-1], {'name': host.FIX_VOLUME, 'configMap': {
            'name': 'gateway-personal-ready-' + 'a' * 12, 'defaultMode': 420}})
        old_mounts = before['spec']['template']['spec']['containers'][0]['volumeMounts']
        mounts = pod['containers'][0]['volumeMounts']
        changed = [i for i, pair in enumerate(zip(old_mounts, mounts)) if pair[0] != pair[1]]
        self.assertEqual(len(changed), 1)
        index = changed[0]
        self.assertEqual(mounts[index]['mountPath'], host.TARGET)
        self.assertEqual(mounts[index]['subPath'], 'work_notification_delivery.py')
        self.assertTrue(mounts[index]['readOnly'])
        pod['volumes'].pop()
        mounts[index] = copy.deepcopy(old_mounts[index])
        self.assertEqual(candidate, before['spec'])

    def test_patch_changes_actual_source_and_tests_complete_original(self):
        before = fixture()
        candidate = self.build(before)
        patch = host.cas_patch(before, candidate)
        self.assertEqual([row['op'] for row in patch], ['test'] * 4 + ['replace'])
        self.assertEqual([row['path'] for row in patch[:4]], ['/metadata/uid', '/metadata/resourceVersion', '/metadata/generation', '/spec'])
        self.assertEqual(patch[3]['value'], before['spec'])
        self.assertEqual(patch[4], {'op': 'replace', 'path': '/spec', 'value': candidate})
        self.assertNotEqual(patch[4]['value'], before['spec'])
        self.assertEqual(candidate['template']['spec']['volumes'][0], before['spec']['template']['spec']['volumes'][0])

    def test_refuses_missing_duplicate_mutable_or_shadowed_notification_mount(self):
        for mutation in ('missing', 'duplicate', 'writable', 'subpath', 'shadow', 'preoccupied', 'image'):
            with self.subTest(mutation=mutation):
                source = fixture()
                pod = source['spec']['template']['spec']
                mounts = pod['containers'][0]['volumeMounts']
                target = next(row for row in mounts if row['mountPath'] == host.TARGET)
                if mutation == 'missing': mounts.remove(target)
                elif mutation == 'duplicate': mounts.append(copy.deepcopy(target))
                elif mutation == 'writable': target['readOnly'] = False
                elif mutation == 'subpath': target['subPath'] = 'wrong.py'
                elif mutation == 'shadow': mounts.append({'name':'shadow', 'mountPath':'/app/backend/app/scheduler'})
                elif mutation == 'preoccupied': pod['volumes'].append({'name':host.FIX_VOLUME})
                else: pod['containers'][0]['image'] = 'unqualified:image'
                with self.assertRaises(host.Refused): self.build(source)

    def test_configmap_has_exact_one_module_and_tampered_asset_fails(self):
        raw = host.load_code()
        self.assertEqual(len(raw), CODE['bytes'])
        cm = host.new_configmap('gateway-personal-ready-' + 'a' * 12, 'b' * 32, raw)
        self.assertTrue(cm['immutable'])
        self.assertEqual(set(cm['data']), {'work_notification_delivery.py'})
        self.assertEqual(cm['data']['work_notification_delivery.py'].encode(), raw)
        with self.assertRaises(host.Refused): host.new_configmap(cm['metadata']['name'], 'b' * 32, raw + b'\n')
        with mock.patch.object(host, 'CODE_ASSET', {}):
            with self.assertRaises(host.Refused): host.load_code()

    def test_no_yaml_mutation_and_followup_uses_own_readonly_tool(self):
        with self.assertRaisesRegex(host.Refused, 'MUTATION_NOT_SUPPORTED'): host.replace_scheduler(BASE)
        self.assertNotIn('activate-scheduler-concurrency-20261116-r2.py', HOST.decode())
        self.assertIn(builder.NAME + ' --verify-readonly', HOST.decode())
        report = host.Operator('fixed-child').report
        self.assertEqual(report['scheduler_max_concurrent_runs_before'], 32)
        self.assertEqual(report['scheduler_max_concurrent_runs_after'], 32)
        self.assertTrue(report['scheduler_multi_instance_before'])

    def test_accepted_profile_is_bound_to_saved_concurrency_plan(self):
        plan = {'operation': 'f0f944d091bc434caff845c24d19a836', 'candidate_spec_sha256': host.BEFORE_SPEC_SHA,
                'new_configmap': host.OLD_CM, 'new_config_sha256': 'a4d2f3af8d2f08895a29b977b83512e05fd5d958a75340f88bec39390d26a0fe', 'new_config_bytes':16000}
        with mock.patch.object(host, 'private_read', return_value=host.canonical(plan)), \
             mock.patch.object(host, 'sha', return_value='cbc3e668c0f2c5cbe9a31a66ff3ac2d56908ad86adb5f5b15ed8a307b043a48d'):
            self.assertEqual(host.accepted_profile()['max_concurrent_runs'], 32)
            plan['new_configmap'] = 'wrong-config'
            with mock.patch.object(host, 'private_read', return_value=host.canonical(plan)):
                with self.assertRaises(host.Refused): host.accepted_profile()

    def test_preserved_config_needs_exact_hash_integer_budgets_and_heartbeat(self):
        for key, value in ((b'heartbeat_enabled: true', b'heartbeat_enabled: false'),
                           (b'max_concurrent_runs: 32', b'max_concurrent_runs: true'),
                           (b'multi_instance: true', b'multi_instance: false'),
                           (b'recursion_limit: 10000', b'recursion_limit: 300')):
            raw = BASE.replace(key, value)
            with self.subTest(key=key), self.assertRaises(child.Refused):
                child.check_config(raw, dict(PROFILE, bytes=len(raw), sha256=child.digest(raw)))
        self.assertEqual(child.check_config(BASE, PROFILE)['scheduler']['max_concurrent_runs'], 32)
        with self.assertRaisesRegex(child.Refused, 'CONFIG_HASH_CHANGED'):
            child.check_config(BASE + b'\n', PROFILE)

    def test_production_pins_and_audit_remain_strict_despite_offline_fixture(self):
        self.assertEqual(child.PACKAGES, {'langchain':'1.2.15', 'langchain-core':'1.3.3', 'langgraph':'1.1.9'})
        self.assertNotIn('from deerflow.config', CHILD.decode())
        self.assertNotIn('from deerflow.persistence', CHILD.decode())
        for event in ('socket.__new__', 'subprocess.Popen'):
            with self.assertRaises(child.Refused): child.audit(event, ())


if __name__ == '__main__':
    unittest.main()
