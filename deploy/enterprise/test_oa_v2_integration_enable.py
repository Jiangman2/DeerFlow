"""Synthetic transaction and protocol tests; no cluster or network access."""
import ast
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import types
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


app = load('oa_enable', 'enable-oa-v2-integration.py')
fixtures = load('oa_fixture', 'test_oa_v2_integration_preflight.py')
pack = load('oa_package', 'package-oa-v2-integration-enable.py')


class Tests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.Tests('test_fixed_two_node_readonly_flow')
        self.fixture.setUp()
        self.common = fixtures.host
        self.common.require = app.need
        self.old_config = app.BASELINE_CONFIG
        app.BASELINE_CONFIG = json.loads((HERE / 'oa-v2-integration-preflight-field-20261023-r1.json').read_text(encoding='utf-8'))['common_configuration']
        self.deployment = self.fixture.ds['gateway']
        self.deployment['metadata']['resourceVersion'] = '1234'
        c = self.deployment['spec']['template']['spec']['containers'][0]
        c['env'] = [{'name': 'MODEL_SETTING', 'value': 'preserve'}]
        c['envFrom'] = [{'secretRef': {'name': 'deer-flow-env'}}]
        self.common.EXPECTED['gateway'] = dict(self.common.binding(self.deployment), image=c['image'])

    def tearDown(self):
        self.fixture.tearDown()
        app.BASELINE_CONFIG = self.old_config

    def observation(self, enabled, head=False):
        return dict(app.BASELINE_CONFIG, enabled=True, multi_result_enabled=enabled, callback_enabled=enabled,
                    sources_verified=True, configuration_modified=False, job_submissions=0, provider_requests=0,
                    callback_requests=int(head), local_route={'status': 'AUTHENTICATED_ROUTE_ACCEPTED', 'expected_missing_job': True, 'http_status': 404})

    def test_patch_changes_only_three_flags_and_cas(self):
        before = copy.deepcopy(self.deployment)
        expected, ops = app.plan(self.common, self.deployment)
        self.assertEqual(self.deployment, before)
        self.assertEqual(ops[:2], [{'op': 'test', 'path': '/metadata/uid', 'value': 'gateway-uid'},
                                  {'op': 'test', 'path': '/metadata/resourceVersion', 'value': '1234'}])
        self.assertEqual([x['value'] for x in ops[2:]], app.OVERRIDES)
        desired = copy.deepcopy(before)
        desired['spec']['template']['spec']['containers'][0]['env'].extend(app.OVERRIDES)
        desired['metadata']['generation'] += 1
        self.assertEqual(expected, self.common.binding(desired))

    def test_absent_env_array_added_without_replacing_other_fields(self):
        del self.deployment['spec']['template']['spec']['containers'][0]['env']
        self.common.EXPECTED['gateway'].update(self.common.binding(self.deployment))
        _, ops = app.plan(self.common, self.deployment)
        self.assertEqual(ops[-1]['path'], '/spec/template/spec/containers/0/env')
        self.assertEqual(ops[-1]['value'], app.OVERRIDES)

    def test_live_drift_or_unready_rejected(self):
        for change in ('generation', 'ready'):
            d = copy.deepcopy(self.deployment)
            if change == 'generation': d['metadata']['generation'] += 1
            else: d['status']['readyReplicas'] = 69
            with self.assertRaises(app.Refused): app.plan(self.common, d)

    def test_new_oa_env_is_not_overwritten(self):
        self.deployment['spec']['template']['spec']['containers'][0]['env'].append(app.OVERRIDES[0])
        self.common.EXPECTED['gateway'].update(self.common.binding(self.deployment))
        with self.assertRaisesRegex(app.Refused, 'EXPLICIT_OA_ENV_CHANGED'): app.plan(self.common, self.deployment)

    def test_shared_secret_source_change_rejected(self):
        self.deployment['spec']['template']['spec']['containers'][0]['envFrom'][0]['secretRef']['name'] = 'changed'
        self.common.EXPECTED['gateway'].update(self.common.binding(self.deployment))
        with self.assertRaisesRegex(app.Refused, 'ENV_FROM_CHANGED'): app.plan(self.common, self.deployment)

    def test_config_flags_and_preserved_budget_validated(self):
        for enabled in (False, True): app.validate_config(self.observation(enabled), enabled)
        bad = self.observation(True); bad['oa_recursion_limit'] = 300
        with self.assertRaisesRegex(app.Refused, 'OA_CONFIG_CHANGED'): app.validate_config(bad, True)
        bad = self.observation(True); bad['callback_enabled'] = False
        with self.assertRaises(app.Refused): app.validate_config(bad, True)

    def test_expected_missing_job_required(self):
        app.validate_route(self.observation(False))
        bad = self.observation(False); bad['local_route']['expected_missing_job'] = False
        with self.assertRaises(app.Refused): app.validate_route(bad)

    def test_callback_candidate_code_and_failure_rejected(self):
        pin = self.common.identity(self.fixture.pods[0]); samples = [{'identity': pin}]
        captured = []
        def read(args, **kwargs):
            if args[0] == 'get': return self.fixture.pods[0]
            source = args[args.index('-c', args.index('--')) + 1]
            ast.parse(source, feature_version=(3, 12)); captured.append(source)
            observation = self.observation(True, True)
            observation['callback_transport'] = {'http_status': 405, 'head_requests': 1, 'tls_verification': 'REQUIRED'}
            return observation
        with patch.object(self.common, 'query', side_effect=read):
            result = app.callback_preflight(self.common, samples, [])
        self.assertEqual(result[0]['transport']['http_status'], 405)
        self.assertIn('trust_env=False', captured[0]); self.assertIn('follow_redirects=False', captured[0])
        self.assertNotIn('local_probe(config)\n', captured[0])
        original_read = read
        def failing(args, **kwargs):
            result = original_read(args, **kwargs)
            if args[0] == 'exec': result['callback_transport'] = {'status': 'INCOMPLETE', 'error_class': 'ConnectError', 'head_requests': 1}
            return result
        evidence = []
        with patch.object(self.common, 'query', side_effect=failing), self.assertRaisesRegex(app.Refused, 'CALLBACK_HTTPS'):
            app.callback_preflight(self.common, samples, evidence)
        self.assertEqual(evidence[0]['transport']['error_class'], 'ConnectError')

    def test_patch_timeout_is_uncertain_and_not_retried(self):
        with patch.object(app.subprocess, 'run', side_effect=subprocess.TimeoutExpired('kubectl', 40)) as run:
            value = app.dispatch_patch(Path('/private/PATCH.json'))
        self.assertTrue(value['command_timed_out']); self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0][-4:], ['--patch-file', str(Path('/private/PATCH.json')), '-o', 'name'])

    def test_rollout_timeout_and_concurrent_change(self):
        expected = self.common.binding(self.deployment)
        d = copy.deepcopy(self.deployment); d['status']['readyReplicas'] = 60
        with patch.object(app, 'read_deployment', return_value=d), self.assertRaisesRegex(app.Refused, 'OBSERVATION_TIMEOUT'):
            app.wait_ready(self.common, expected, 10, lambda x: None, now=lambda: 11, sleep=lambda x: None)
        d['metadata']['generation'] += 1
        with patch.object(app, 'read_deployment', return_value=d), self.assertRaisesRegex(app.Refused, 'CHANGED_AFTER_PATCH'):
            app.wait_ready(self.common, expected, 10, lambda x: None)

    def test_all_70_pods_required(self):
        with patch.object(self.common, 'query', side_effect=self.fixture.reader), self.assertRaises(app.Refused):
            app.current_pods(self.common, self.deployment)

    def test_all_runtime_pods_checked_only_two_loopback_calls(self):
        pins = [{'pod': 'p'+str(i), 'uid': 'u'+str(i), 'node': 'node'+str(i % 5)} for i in range(70)]
        sources = []
        def read(args, **kwargs):
            sources.append(args[args.index('-c', args.index('--')) + 1])
            return self.observation(True)
        with patch.object(app, 'current_pods', return_value=pins), patch.object(self.common, 'query', side_effect=read):
            result = app.verify_pods(self.common, self.deployment)
        self.assertEqual(len(result), 70)
        self.assertEqual(sum("result['local_route'] = local_probe(config)" in s for s in sources), 2)

    def test_restart_during_acceptance_rejected(self):
        pins = [{'pod': 'p'+str(i), 'uid': 'u'+str(i), 'node': 'node'+str(i % 2)} for i in range(70)]
        after = copy.deepcopy(pins); after[0]['uid'] = 'replacement'
        with patch.object(app, 'current_pods', side_effect=[pins, after]), patch.object(self.common, 'query', return_value=self.observation(True)), self.assertRaisesRegex(app.Refused, 'PODS_CHANGED'):
            app.verify_pods(self.common, self.deployment)

    def test_package_python37_and_pinned_receipts(self):
        raw = pack.build(); ast.parse(raw, feature_version=(3, 7))
        module = types.ModuleType('qualified_package'); exec(compile(raw, '<package>', 'exec'), module.__dict__)
        common = module.load_common()
        self.assertEqual(common.EXPECTED['gateway']['generation'], 384)
        self.assertEqual(module.RECEIPTS['gateway']['accepted_field'], 'runtime_accepted')
        self.assertEqual(module.RECEIPTS['frontend']['accepted_field'], 'stage_passed')
        self.assertNotIn('POLLING_ENABLED', raw)

    def test_receipt_digest_and_acceptance_gate(self):
        value = {'status': 'ACCEPTED', 'runtime_accepted': True}
        raw = json.dumps(value).encode()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'receipt.json'; path.write_bytes(raw)
            pin = {'path': str(path.resolve()), 'bytes': len(raw), 'sha256': app.hashlib.sha256(raw).hexdigest(), 'status': 'ACCEPTED', 'accepted_field': 'runtime_accepted'}
            # Windows has no O_NOFOLLOW and test files are not root-owned.
            fake_stat = types.SimpleNamespace(st_mode=app.stat.S_IFREG, st_uid=0, st_nlink=1, st_size=len(raw))
            with patch.object(app, 'RECEIPTS', {'sample':pin}), patch.object(app.os, 'O_NOFOLLOW', 0, create=True), patch.object(app.os, 'fstat', return_value=fake_stat):
                app.receipts()
                pin['sha256'] = '0'*64
                with self.assertRaisesRegex(app.Refused, 'RECEIPT_DIGEST'): app.receipts()


if __name__ == '__main__':
    unittest.main()
