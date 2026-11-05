"""Offline v2-only activation contract; never access Kubernetes or OA."""
import ast
import copy
import importlib.util
import json
from pathlib import Path, PurePosixPath
import subprocess
import types
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


app = load('multi_enable', 'enable-oa-v2-multi-only.py')
fixtures = load('multi_fixtures', 'test_oa_v2_integration_preflight.py')
pack = load('multi_package', 'package-oa-v2-multi-only.py')


def apply_patch(document, operations):
    result = copy.deepcopy(document)
    for op in operations:
        parts = op['path'].strip('/').split('/')
        parent = result
        for key in parts[:-1]:
            parent = parent[int(key)] if isinstance(parent, list) else parent[key]
        key = parts[-1]
        if isinstance(parent, list) and key != '-':
            key = int(key)
        if op['op'] == 'test':
            if parent[key] != op['value']:
                raise ValueError('CAS_FAILED')
        elif key == '-':
            parent.append(copy.deepcopy(op['value']))
        else:
            parent[key] = copy.deepcopy(op['value'])
    return result


class Tests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.Tests('test_fixed_two_node_readonly_flow')
        self.fixture.setUp()
        self.common = fixtures.host
        self.old_config = app.BASELINE_CONFIG
        app.BASELINE_CONFIG = json.loads((HERE / 'oa-v2-integration-preflight-field-20261023-r1.json').read_text(encoding='utf-8'))['common_configuration']
        self.deployment = self.fixture.ds['gateway']
        self.deployment['metadata']['resourceVersion'] = '1234'
        self.container = self.deployment['spec']['template']['spec']['containers'][0]
        self.container['env'] = [{'name': 'MODEL_CONFIG', 'value': 'preserve'},
                                 {'name': 'OA_POLICY_FLOW_RECURSION_LIMIT', 'value': '1000'}]
        self.container['envFrom'] = [{'secretRef': {'name': 'deer-flow-env'}}]
        self.bind()

    def tearDown(self):
        self.fixture.tearDown()
        app.BASELINE_CONFIG = self.old_config

    def bind(self):
        self.common.EXPECTED['gateway'] = dict(self.common.binding(self.deployment), image=self.container['image'])

    def observation(self, multi=False):
        return dict(app.BASELINE_CONFIG, multi_result_enabled=multi, callback_enabled=False,
                    sources_verified=True, configuration_modified=False,
                    job_submissions=0, provider_requests=0, callback_requests=0,
                    local_route={'status': 'AUTHENTICATED_ROUTE_ACCEPTED', 'expected_missing_job': True, 'http_status': 404})

    def test_only_multi_and_callback_false_change(self):
        before = copy.deepcopy(self.deployment)
        binding, ops = app.plan(self.common, self.deployment)
        self.assertEqual(self.deployment, before)
        result = apply_patch(before, ops)
        wanted = copy.deepcopy(before)
        wanted['spec']['template']['spec']['containers'][0]['env'].extend(app.OVERRIDES)
        self.assertEqual(result, wanted)
        result['metadata']['generation'] += 1
        self.assertEqual(binding, self.common.binding(result))
        self.assertEqual(app.OVERRIDES, [
            {'name': 'OA_POLICY_FLOW_MULTI_RESULT_ENABLED', 'value': 'true'},
            {'name': 'OA_POLICY_FLOW_CALLBACK_ENABLED', 'value': 'false'}])

    def test_absent_env_array(self):
        del self.container['env']
        self.bind()
        _, ops = app.plan(self.common, self.deployment)
        result = apply_patch(self.deployment, ops)
        self.assertEqual(result['spec']['template']['spec']['containers'][0]['env'], app.OVERRIDES)

    def test_existing_value_from_replaced_without_duplicate(self):
        self.container['env'].extend([
            {'name': 'OA_POLICY_FLOW_MULTI_RESULT_ENABLED', 'valueFrom': {'configMapKeyRef': {'name': 'old', 'key': 'multi'}}},
            {'name': 'OA_POLICY_FLOW_CALLBACK_ENABLED', 'value': 'false'}])
        self.bind()
        _, ops = app.plan(self.common, self.deployment)
        changed = apply_patch(self.deployment, ops)['spec']['template']['spec']['containers'][0]
        self.assertEqual(changed['env'][-2:], app.OVERRIDES)
        self.assertEqual(len(ops), 3)
        self.assertEqual(changed['envFrom'], self.container['envFrom'])

    def test_duplicate_env_refused(self):
        self.container['env'].append(copy.deepcopy(self.container['env'][0]))
        self.bind()
        with self.assertRaisesRegex(app.Refused, 'DUPLICATE_ENV_KEYS'):
            app.plan(self.common, self.deployment)

    def test_changed_baseline_refused(self):
        self.container['image'] = 'different'
        with self.assertRaisesRegex(app.Refused, 'GATEWAY_BASELINE_CHANGED'):
            app.plan(self.common, self.deployment)

    def test_unready_refused(self):
        self.deployment['status']['readyReplicas'] = 69
        with self.assertRaises(app.Refused):
            app.plan(self.common, self.deployment)

    def test_shared_secret_reference_change_refused(self):
        self.container['envFrom'][0]['secretRef']['name'] = 'other'
        self.bind()
        with self.assertRaisesRegex(app.Refused, 'ENV_FROM_CHANGED'):
            app.plan(self.common, self.deployment)

    def test_cas_rejects_concurrent_write(self):
        _, ops = app.plan(self.common, self.deployment)
        changed = copy.deepcopy(self.deployment)
        changed['metadata']['resourceVersion'] = '9999'
        with self.assertRaisesRegex(ValueError, 'CAS_FAILED'):
            apply_patch(changed, ops)

    def test_callback_enabled_and_budget_drift_refused(self):
        for enabled in (False, True):
            app.validate_config(self.observation(enabled), enabled)
        for key, value in [('callback_enabled', True), ('oa_recursion_limit', 300), ('enabled', False)]:
            item = self.observation(True)
            item[key] = value
            with self.assertRaisesRegex(app.Refused, 'OA_CONFIG_CHANGED'):
                app.validate_config(item, True)

    def test_provider_or_callback_request_not_allowed(self):
        for key in ('callback_requests', 'provider_requests', 'job_submissions'):
            item = self.observation(True)
            item[key] = 1
            with self.assertRaises(app.Refused):
                app.validate_config(item, True)

    def test_current_non_switch_configuration_is_preserved(self):
        observation = self.observation()
        observation['callback_ca_configured'] = True
        observation['max_result_files'] = 12
        actual = app.current_configuration([{'observation': observation}] * 2)
        self.assertTrue(actual['callback_ca_configured'])
        self.assertEqual(actual['max_result_files'], 12)
        app.BASELINE_CONFIG = actual
        app.validate_config(dict(observation, multi_result_enabled=True), True)
        with self.assertRaises(app.Refused):
            app.validate_config(dict(observation, max_result_files=10, multi_result_enabled=True), True)

    def test_enabled_callback_and_inconsistent_config_stop_before_patch(self):
        observation = self.observation()
        for bad in (dict(observation, callback_enabled=True), dict(observation, max_result_files=12)):
            with self.assertRaises(app.Refused):
                app.current_configuration([{'observation': observation}, {'observation': bad}])

    def test_invalid_authentication_probe_refused(self):
        item = self.observation()
        item['local_route']['http_status'] = 401
        with self.assertRaises(app.Refused):
            app.validate_route(item)

    def test_all_70_verified_only_two_loopback_gets(self):
        pins = [{'pod': 'p' + str(i), 'uid': 'u' + str(i), 'node': 'n' + str(i % 5)} for i in range(70)]
        sources = []
        def query(args, **kwargs):
            sources.append(args[args.index('-c', args.index('--')) + 1])
            return self.observation(True)
        with patch.object(app, 'current_pods', return_value=pins), patch.object(self.common, 'query', side_effect=query):
            checked = app.verify_pods(self.common, self.deployment)
        self.assertEqual(len(checked), 70)
        self.assertEqual(sum("result['local_route'] = local_probe(config)" in code for code in sources), 2)
        self.assertTrue(all(row['callback_enabled'] is False for row in checked))

    def test_changed_pod_rejected(self):
        pins = [{'pod': 'p' + str(i), 'uid': 'u' + str(i), 'node': 'n' + str(i % 5)} for i in range(70)]
        changed = copy.deepcopy(pins)
        changed[0]['uid'] = 'replacement'
        with patch.object(app, 'current_pods', side_effect=[pins, changed]), patch.object(self.common, 'query', return_value=self.observation(True)):
            with self.assertRaisesRegex(app.Refused, 'PODS_CHANGED'):
                app.verify_pods(self.common, self.deployment)

    def test_once_record_blocks_before_any_queries(self):
        with patch.object(app.os.path, 'lexists', return_value=True), patch.object(app, 'receipts') as receipts:
            with self.assertRaisesRegex(app.Refused, 'ONCE_DIRECTORY_EXISTS'):
                app.enable(self.common, {}, lambda x: None)
        receipts.assert_not_called()

    def test_timeout_uncertain_and_no_retry(self):
        with patch.object(app.subprocess, 'run', side_effect=subprocess.TimeoutExpired('kubectl', 40)) as run:
            result = app.dispatch_patch(Path('/private/PATCH.json'))
        self.assertTrue(result['command_timed_out'])
        self.assertEqual(run.call_count, 1)

    def test_packaged_baseline_source_and_no_callback_transport(self):
        source = pack.build()
        ast.parse(source, feature_version=(3, 7))
        module = types.ModuleType('multi_packaged')
        exec(compile(source, '<packaged>', 'exec'), module.__dict__)
        common = module.load_common()
        self.assertEqual(common.EXPECTED['gateway']['generation'], 385)
        self.assertEqual(common.EXPECTED['frontend']['generation'], 227)
        self.assertEqual(common.EXPECTED['ppt-master-runner']['generation'], 55)
        self.assertEqual(len(common.SOURCE_PINS), 4)
        self.assertEqual(len(common.EXPECTED), 5)
        self.assertNotIn('callback_preflight', source)
        self.assertNotIn("stream('HEAD'", source)
        self.assertNotIn("callback_enabled=True", source)
        self.assertIn('nonconfidential-notice-activation', module.RECEIPTS['frontend']['path'])

    def test_complete_transaction_single_patch_and_preserves_other_services(self):
        inspection = {'samples': [{'observation': self.observation()} for _ in range(2)],
                      'deployments': {'gateway': self.common.binding(self.deployment)}}
        preserved = {'frontend': {'generation': 227}}
        report = {'once_claimed': False, 'patch_request_count': 0, 'patch_acknowledged': False}
        live = [copy.deepcopy(self.deployment)]
        ops = []
        saves = []
        def write(path, raw):
            ops.extend(json.loads(raw))
        def dispatch(path):
            live[0] = apply_patch(live[0], ops)
            live[0]['metadata']['generation'] += 1
            live[0]['status']['observedGeneration'] += 1
            return {'command_returncode': 0, 'command_timed_out': False}
        with patch.object(app.os.path, 'lexists', return_value=False), patch.object(app, 'receipts'), \
             patch.object(self.common, 'inspect_cluster', return_value=inspection), \
             patch.object(app, 'other_services', return_value=preserved), \
             patch.object(app, 'read_deployment', side_effect=lambda *a: copy.deepcopy(live[0])), \
             patch.object(app.os, 'mkdir'), patch.object(app.Path, 'resolve', return_value=PurePosixPath('/var/lib')), \
             patch.object(app, 'write_new', side_effect=write), \
             patch.object(app, 'save', side_effect=lambda value: saves.append(copy.deepcopy(value))), \
             patch.object(app, 'dispatch_patch', side_effect=dispatch) as call, \
             patch.object(app, 'verify_pods', return_value=[{'callback_enabled': False}] * 70):
            app.enable(self.common, report, lambda x: None)
        self.assertEqual(call.call_count, 1)
        self.assertEqual(report['accepted_pods'], 70)
        self.assertTrue(report['v2_enabled'])
        self.assertFalse(report['callback_enabled'])
        self.assertTrue(report['other_deployments_preserved'])
        self.assertTrue(any(row.get('patch_may_have_applied') and not row['patch_acknowledged'] for row in saves))
        self.assertEqual(report['exitcode'], 0)

    def test_already_enabled_verified_without_patch_or_once_claim(self):
        before = {'gateway': self.common.binding(self.deployment), 'frontend': {'generation': 227}}
        inspection = {'samples': [{'observation': self.observation(True)} for _ in range(2)], 'deployments': before}
        report = {'once_claimed': False}
        with patch.object(app.os.path, 'lexists', return_value=False), patch.object(app, 'receipts'), \
             patch.object(self.common, 'inspect_cluster', return_value=inspection), \
             patch.object(app, 'read_deployment', return_value=self.deployment), \
             patch.object(app, 'other_services', return_value={'frontend': before['frontend']}), \
             patch.object(app, 'verify_pods', return_value=[{}] * 70), patch.object(app, 'dispatch_patch') as dispatch:
            app.enable(self.common, report, lambda x: None)
        dispatch.assert_not_called()
        self.assertFalse(report['once_claimed'])
        self.assertEqual(report['status'], 'OA_V2_MULTI_ALREADY_ENABLED_CALLBACK_DISABLED')


if __name__ == '__main__':
    unittest.main()
