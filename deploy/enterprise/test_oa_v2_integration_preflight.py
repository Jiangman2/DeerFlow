"""No Kubernetes, provider, OA, or production database requests."""
import ast
import copy
import importlib.util
import io
import json
from pathlib import Path
import types
import unittest
from urllib import error
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('oa_preflight', HERE / 'inspect-oa-v2-integration.py')
host = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host)
child = types.ModuleType('child_test_only')
exec(compile(host.CHILD, '<child-test>', 'exec'), child.__dict__)


class Tests(unittest.TestCase):
    def setUp(self):
        self.before_expected = host.EXPECTED
        image = 'deerflow.local/deer-flow-gateway@sha256:' + 'a' * 64
        self.ds = {}
        for role, n in [('gateway', 70), ('frontend', 2), ('ppt-master-runner', 1)]:
            self.ds[role] = {'metadata': {'name': role, 'uid': role + '-uid', 'generation': 384},
                'spec': {'replicas': n, 'template': {'metadata': {'labels': {'app': role}}, 'spec': {'containers': [
                    {'name': role, 'image': image, 'env': [{'name': 'OA_POLICY_FLOW_INGEST_KEY', 'value': 'never-export-me'}]}]}}},
                'status': {'replicas': n, 'updatedReplicas': n, 'readyReplicas': n, 'availableReplicas': n, 'observedGeneration': 384}}
        host.EXPECTED = {k: dict(host.binding(v), image=image) for k, v in self.ds.items()}
        self.rs = {'metadata': {'name': 'gateway-rs', 'uid': 'rs-uid', 'ownerReferences': [
            {'controller': True, 'kind': 'Deployment', 'uid': 'gateway-uid'}]},
            'spec': {'template': copy.deepcopy(self.ds['gateway']['spec']['template'])}}
        self.rs['spec']['template']['metadata']['labels']['pod-template-hash'] = 'hash'
        self.pods = [{'metadata': {'name': 'pod-' + str(i), 'uid': 'uid-' + str(i), 'ownerReferences': [
                {'controller': True, 'kind': 'ReplicaSet', 'uid': 'rs-uid'}]},
            'spec': {'nodeName': 'node-' + str(i), 'containers': [{'name': 'gateway', 'image': image}]},
            'status': {'phase': 'Running', 'containerStatuses': [{'name': 'gateway', 'ready': True,
                'state': {'running': {}}, 'imageID': image, 'containerID': 'containerd://id-' + str(i), 'restartCount': 0}]}}
            for i in range(2)]
        self.config = types.SimpleNamespace(enabled=True, ingest_key='PRIVATE-INGEST', multi_result_enabled=False,
            callback_enabled=False, callback_url='https://oa.example.test/secretpath?token=PRIVATE-QUERY',
            callback_bearer_token='PRIVATE-BEARER', callback_signing_secret='PRIVATE-SIGNING',
            owner_user_id='PRIVATE-OWNER', assistant_id='PRIVATE-AGENT', callback_ca_bundle=None,
            max_result_files=10, max_result_bytes=10485760, max_total_result_bytes=52428800,
            recursion_limit=1000, run_timeout_seconds=1800, max_callback_attempts=8)

    def tearDown(self):
        host.EXPECTED = self.before_expected

    def reader(self, args, **kwargs):
        if args[0] == 'exec':
            return {'sources_verified': True, 'job_submissions': 0, 'provider_requests': 0, 'callback_requests': 0}
        self.assertEqual(args[0], 'get')
        if args[1] == 'deployment':
            return copy.deepcopy(self.ds[args[2]])
        if args[1] == 'replicasets':
            return {'items': [copy.deepcopy(self.rs)]}
        if args[1] == 'pods':
            return {'items': copy.deepcopy(self.pods)}
        if args[1] == 'pod':
            return copy.deepcopy(next(p for p in self.pods if p['metadata']['name'] == args[2]))
        self.fail('Unexpected read')

    def test_fixed_two_node_readonly_flow(self):
        value = host.inspect_cluster(self.reader)
        self.assertEqual(len(value['samples']), 2)
        self.assertEqual(value['maximum_loopback_get_requests'], 2)
        self.assertNotIn('never-export-me', json.dumps(value))
        self.assertFalse(value['workload_modified'])

    def test_deployment_drift_rejected_before_exec(self):
        self.ds['gateway']['metadata']['generation'] += 1
        with self.assertRaisesRegex(ValueError, 'deployment_changed_or_unready'):
            host.inspect_cluster(self.reader)

    def test_incomplete_rollout_is_rejected(self):
        self.ds['gateway']['status']['readyReplicas'] = 69
        with self.assertRaisesRegex(ValueError, 'deployment_changed_or_unready'):
            host.inspect_cluster(self.reader)

    def test_replicaset_hash_is_normalized_but_template_drift_is_rejected(self):
        self.assertEqual(len(host.select_pods(self.ds['gateway'], [self.rs], self.pods)), 2)
        self.rs['spec']['template']['spec']['containers'][0]['env'].append({'name': 'CHANGED', 'value': '1'})
        with self.assertRaisesRegex(ValueError, 'two_ready_gateway_nodes'):
            host.select_pods(self.ds['gateway'], [self.rs], self.pods)

    def test_single_node_and_foreign_owner_are_rejected(self):
        pods = copy.deepcopy(self.pods)
        pods[1]['spec']['nodeName'] = pods[0]['spec']['nodeName']
        with self.assertRaises(ValueError):
            host.select_pods(self.ds['gateway'], [self.rs], pods)
        pods = copy.deepcopy(self.pods)
        pods[1]['metadata']['ownerReferences'][0]['uid'] = 'foreign'
        with self.assertRaises(ValueError):
            host.select_pods(self.ds['gateway'], [self.rs], pods)

    def test_container_image_and_readiness_rejected(self):
        for field, value in [('ready', False), ('imageID', 'other')]:
            pod = copy.deepcopy(self.pods[0])
            pod['status']['containerStatuses'][0][field] = value
            with self.assertRaises(ValueError):
                host.identity(pod)

    def test_container_change_during_probe_rejected(self):
        def read(args, **kwargs):
            result = self.reader(args, **kwargs)
            if args[0] == 'exec':
                self.pods[0]['status']['containerStatuses'][0]['restartCount'] += 1
            return result
        with self.assertRaisesRegex(ValueError, 'pod_changed_after_probe'):
            host.inspect_cluster(read)

    def test_deployment_change_after_probe_rejected(self):
        def read(args, **kwargs):
            result = self.reader(args, **kwargs)
            if args[0] == 'exec':
                self.ds['frontend']['metadata']['generation'] += 1
            return result
        with self.assertRaisesRegex(ValueError, 'deployment_changed_after_probe'):
            host.inspect_cluster(read)

    def test_config_output_has_presence_only_and_no_paths(self):
        output = json.dumps(child.describe(self.config))
        for value in ('PRIVATE-', 'secretpath', 'token='):
            self.assertNotIn(value, output)
        self.assertIn('oa.example.test', output)

    def test_fixed_authenticated_get_and_not_found_contract(self):
        test = self
        class Opener:
            def open(self, req, timeout):
                test.assertEqual(req.full_url, 'http://127.0.0.1:8001/api/v1/integrations/oa/policy-flow/jobs/00000000-0000-0000-0000-000000000000')
                test.assertEqual(req.get_method(), 'GET')
                test.assertEqual(req.get_header('Authorization'), 'Bearer PRIVATE-INGEST')
                test.assertEqual(timeout, 10)
                raise error.HTTPError(req.full_url, 404, 'missing', {}, io.BytesIO(b'{"detail":"OA policy-flow job not found"}'))
        result = child.local_probe(self.config, Opener())
        self.assertEqual(result['status'], 'AUTHENTICATED_ROUTE_ACCEPTED')
        self.assertNotIn('PRIVATE-INGEST', json.dumps(result))

    def test_redirects_never_receive_authentication(self):
        self.assertIsNone(child.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://example.test'))

    def test_disabled_integration_makes_no_request(self):
        self.config.enabled = False
        self.assertEqual(child.local_probe(self.config, object())['requests'], 0)

    def test_unknown_routes_and_oversize_not_accepted(self):
        class Opener:
            def __init__(self, data):
                self.data = data
            def open(self, req, timeout):
                response = io.BytesIO(self.data)
                response.code = 404
                return response
        for data in (b'{"detail":"Not Found"}', b'x' * 9000):
            result = child.local_probe(self.config, Opener(data))
            self.assertNotEqual(result['status'], 'AUTHENTICATED_ROUTE_ACCEPTED')

    def test_query_rejects_mutations(self):
        with patch.object(host.subprocess, 'run', side_effect=AssertionError('must not execute')):
            for verb in ('patch', 'delete', 'apply'):
                with self.assertRaises(ValueError):
                    host.query([verb, 'deployment', 'gateway'])

    def test_source_hash_gate_runs_before_config_or_http(self):
        with patch.object(child, 'read_pinned_sources', side_effect=ValueError('source_hash_changed')), \
             patch.object(child, 'local_probe', side_effect=AssertionError('must not request')):
            with self.assertRaisesRegex(ValueError, 'source_hash_changed'):
                child.inspect({})

    def test_configuration_failure_never_echoes_raw_error(self):
        # The entrypoint returns only an exception class, not secret-bearing text.
        self.assertNotIn("str(exc)", host.CHILD)
        ast.parse((HERE / 'inspect-oa-v2-integration.py').read_text(encoding='utf-8'), feature_version=(3, 7))
        ast.parse(host.CHILD, feature_version=(3, 12))


if __name__ == '__main__':
    unittest.main(verbosity=2)
