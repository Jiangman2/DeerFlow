"""Synthetic timeout recovery tests; no cluster or production requests."""
import ast
import copy
import importlib.util
import json
from pathlib import Path
import types
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent


def load(filename):
    spec = importlib.util.spec_from_file_location(filename.replace('-', '_'), HERE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


packager = load('package-oa-v2-multi-after-timeout.py')
fixtures = load('test_oa_v2_integration_preflight.py')


class Tests(unittest.TestCase):
    def setUp(self):
        self.app = types.ModuleType('_packaged_verification_test')
        exec(compile(packager.build(), '<packaged>', 'exec'), self.app.__dict__)
        self.common, self.helpers = self.app.load_helpers()
        self.fixture = fixtures.Tests('test_fixed_two_node_readonly_flow')
        self.fixture.setUp()
        self.deployment = copy.deepcopy(self.fixture.ds['gateway'])
        self.deployment['metadata']['generation'] = 386
        self.deployment['status']['observedGeneration'] = 386
        self.app.PLANNED = self.common.binding(self.deployment)
        config = json.loads((HERE / 'oa-v2-integration-preflight-field-20261023-r1.json').read_text(encoding='utf-8'))['common_configuration']
        observation = dict(config, sources_verified=True, configuration_modified=False,
                           job_submissions=0, provider_requests=0, callback_requests=0,
                           local_route={'status': 'AUTHENTICATED_ROUTE_ACCEPTED',
                                        'expected_missing_job': True, 'http_status': 404})
        self.prior = {'schema': 'portal-oa-v2-multi-enable.v1', 'status': 'INCOMPLETE',
                      'error_code': 'ROLLOUT_OBSERVATION_TIMEOUT', 'patch_acknowledged': True,
                      'patch_request_count': 1, 'planned_gateway': self.app.PLANNED,
                      'before': {role: {k: value[k] for k in ('uid', 'generation', 'spec_sha256')}
                                 for role, value in self.common.EXPECTED.items()},
                      'preflight': {'samples': [{'observation': copy.deepcopy(observation)} for _ in range(2)]}}
        self.checked = [{'local_route': {'requests': int(i < 2)}} for i in range(70)]

    def tearDown(self):
        self.fixture.tearDown()

    def run_observe(self, report, final=None):
        reads = [self.deployment] if final is None else [self.deployment, final]
        with patch.object(self.helpers, 'read_deployment', side_effect=lambda *a: copy.deepcopy(reads.pop(0))), \
             patch.object(self.app, 'pod_status', return_value=[{'pod': 'sample'}]), \
             patch.object(self.helpers, 'other_services', return_value={'frontend': {'generation': 227}}), \
             patch.object(self.helpers, 'verify_pods', return_value=self.checked) as verify:
            self.app.observe(self.common, self.helpers, self.prior, report)
        return verify

    def test_readonly_package_python37_and_no_activation_functions(self):
        source = packager.build()
        for value in (source, self.app.COMMON, self.app.HELPERS, self.common.CHILD):
            ast.parse(value, feature_version=(3, 7))
        for name in ('enable', 'dispatch_patch', 'save', 'write_new', 'plan'):
            self.assertFalse(hasattr(self.helpers, name))
        self.assertNotIn('def enable(', source)
        self.assertNotIn('dispatch_patch', source)
        self.assertNotIn('mkdir(', source)

    def test_unacknowledged_prior_rejected(self):
        self.prior['patch_acknowledged'] = False
        with self.assertRaisesRegex(self.app.Refused, 'PRIOR_NOT_ACKNOWLEDGED_TIMEOUT'):
            self.app.check_prior(self.prior, self.common, self.helpers)

    def test_different_prior_baseline_rejected(self):
        self.prior['before']['frontend']['generation'] += 1
        with self.assertRaisesRegex(self.app.Refused, 'PRIOR_BASELINE_CHANGED'):
            self.app.check_prior(self.prior, self.common, self.helpers)

    def test_prior_config_inconsistency_rejected(self):
        self.prior['preflight']['samples'][0]['observation']['max_result_files'] += 1
        with self.assertRaisesRegex(self.app.Refused, 'INCONSISTENT_OA_CONFIGURATION'):
            self.app.check_prior(self.prior, self.common, self.helpers)

    def test_pending_rollout_reports_counts_without_runtime_exec(self):
        self.deployment['status'].update(updatedReplicas=51, replicas=71, terminatingReplicas=1)
        report = {}
        verify = self.run_observe(report)
        verify.assert_not_called()
        self.assertEqual(report['status'], 'OA_V2_ROLLOUT_NOT_READY')
        self.assertEqual(report['deployment']['counts']['updatedReplicas'], 51)
        self.assertFalse(report['runtime_accepted'])

    def test_old_terminating_replicas_cannot_pass(self):
        self.deployment['status']['terminatingReplicas'] = 1
        report = {}
        self.run_observe(report).assert_not_called()
        self.assertEqual(report['exitcode'], 2)

    def test_live_spec_change_prevents_runtime_probe(self):
        self.deployment['spec']['paused'] = True
        with self.assertRaisesRegex(self.app.Refused, 'GATEWAY_CHANGED_SINCE_ACKNOWLEDGED_PATCH'):
            self.run_observe({})

    def test_fully_ready_verifies_all_without_repatch(self):
        report = {}
        self.run_observe(report, copy.deepcopy(self.deployment)).assert_called_once()
        self.assertEqual(report['accepted_pods'], 70)
        self.assertEqual(report['authenticated_loopback_checks'], 2)
        self.assertTrue(report['runtime_accepted'])
        self.assertTrue(report['v2_enabled'])
        self.assertFalse(report['callback_enabled'])
        self.assertNotIn('pods', report)

    def test_final_concurrent_change_is_not_accepted(self):
        final = copy.deepcopy(self.deployment)
        final['metadata']['generation'] += 1
        report = {}
        with self.assertRaisesRegex(self.app.Refused, 'GATEWAY_CHANGED_DURING_CHECK'):
            self.run_observe(report, final)
        self.assertNotIn('runtime_accepted', report)

    def test_readonly_query_rejects_mutations(self):
        with patch.object(self.common.subprocess, 'run') as run:
            for verb in ('patch', 'delete', 'apply', 'rollout', 'create'):
                with self.assertRaisesRegex(self.app.Refused, 'command_forbidden'):
                    self.common.query([verb, 'deployment', 'gateway'])
        run.assert_not_called()

    def test_pod_diagnostic_omits_private_messages_and_foreign_pods(self):
        self.fixture.rs['metadata']['ownerReferences'][0]['uid'] = self.deployment['metadata']['uid']
        pod = copy.deepcopy(self.fixture.pods[0])
        pod['status']['containerStatuses'][0]['state'] = {'waiting': {'reason': 'CrashLoopBackOff', 'message': 'PRIVATE_CONTENT'}}
        foreign = copy.deepcopy(pod)
        foreign['metadata']['ownerReferences'][0]['uid'] = 'different-owner'
        with patch.object(self.common, 'query', side_effect=[{'items': [self.fixture.rs]}, {'items': [pod, foreign]}]):
            result = self.app.pod_status(self.common, self.deployment)
        self.assertEqual(len(result), 1)
        self.assertNotIn('PRIVATE_CONTENT', json.dumps(result))
        self.assertEqual(result[0]['containers'][0]['waiting_reason'], 'CrashLoopBackOff')


if __name__ == '__main__':
    unittest.main()
