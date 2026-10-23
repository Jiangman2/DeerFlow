"""Synthetic feature activation tests; no cluster, provider, or database calls."""
import ast
import contextlib
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_oa_schema_expansion_host import Cluster as SchemaCluster


HERE = Path(__file__).resolve().parent
PATCH_ARGS = ['patch', 'deployment', 'gateway', '--type=json', '--patch-file=/dev/stdin', '-o', 'json']


def load_host():
    packaged = os.environ.get('OA_PACKAGED_CANDIDATE')
    namespace = {'__name__': 'feature_host_test'}
    if packaged:
        exec(compile(Path(packaged).read_bytes(), packaged, 'exec'), namespace)
        return namespace
    source = (HERE / 'activate-oa-compat.py').read_text(encoding='utf-8')
    names = {'Refused', 'require', 'digest', 'binding', 'ready', 'configuration_refs',
             'configuration_metadata', 'pods_for', 'identities', 'endpoints'}
    selected = [node for node in ast.parse(source).body
                if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names]
    common = '\n\n'.join(ast.get_source_segment(source, node) for node in selected)
    common += "\nBASE = ['kubectl', '--kubeconfig=/etc/kubernetes/admin.conf', '--request-timeout=15s', '-n', 'deer-flow']\n"
    host = (HERE / 'activate-oa-data-qa-feature.py').read_text(encoding='utf-8')
    exec(compile(host.replace("COMMON = ''", 'COMMON = ' + repr(common), 1), '<feature-host>', 'exec'), namespace)
    return namespace


class FeatureCluster(SchemaCluster):
    def __init__(self, n):
        original_evidence = n['evidence']
        self.patch_calls, self.v2_calls = [], []
        self.patch_error = None
        self.after_patch = lambda: None
        self.ack_transform = lambda value: value
        self.disabled = lambda pod: True
        self.config_versions = {}
        self.endpoint_transform = lambda value, role: value
        super().__init__(n)
        for role, deployment in self.deployments.items():
            deployment['metadata']['resourceVersion'] = '123'
        self.deployments['frontend']['metadata']['generation'] = 225
        self.deployments['frontend']['status']['observedGeneration'] = 225
        n['FRONT'] = n['binding'](self.deployments['frontend'])
        n['FEATURE'] = {'candidate_image': 'local/gateway@sha256:' + 'f' * 64,
                        'config_digest': 'sha256:' + 'e' * 64,
                        'sources': {'feature-' + str(i): {'bytes': 100 + i, 'sha256': str(i) * 64} for i in range(10)}}
        n['SCHEMA_CHILD'], n['FEATURE_CHILD'] = 'reviewed schema child', 'reviewed feature child'
        n['claim'], n['save'], n['evidence'] = self.claim, self.save, original_evidence
        n['EXPANSION'] = {'path': '/accepted/expansion/REPORT.json', 'bytes': 53986,
                          'sha256': '1bd741e9db0465a8274c95e9d2e3d21f9ea1770b66b5d00af476dd761a98cba5'}
        n['ACCEPTANCE'] = {'path': '/accepted/feature/REPORT.json', 'bytes': 1759,
                           'sha256': '04f0f94236e3941898c974f45e8351b5764b3f972eaf2f79cde6791c2496fc25'}
        n['PRELOADS'] = [{'hostname': node, 'expected_node_uid': uid, 'node_preload_passed': True,
                          'old_references_preserved': True, 'image_import_may_be_partial': False,
                          'roles': [{'manifest_digest': n['FEATURE']['candidate_image'].split('@')[1],
                                     'config_digest': n['FEATURE']['config_digest'], 'phase_a': 'PASS',
                                     'phase_b': 'PASS', 'content_and_unpack': 'PASS', 'cri_visibility': 'PASS'}]}
                         for node, uid in n['EXPECTED']['gateway']['nodes'].items()]
        before = n['snapshot']()[1]
        self.expansion = {'status': 'OA_SCHEMA_EXPANSION_ACCEPTED_FEATURE_NOT_DEPLOYED', 'schema_revision': n['TARGET'],
                          'schema_migrated': True, 'migration_outcome': 'committed', 'expansion_acknowledged': True,
                          'old_consumer_compatibility_verified': True, 'feature_deployed': False, 'provider_requests': 0,
                          'configuration_metadata_preserved': True, 'frontend_preserved': True,
                          'before': copy.deepcopy(before), 'after': copy.deepcopy(before),
                          'inspect': [{'observation': {'status': 'PASS'}} for unused in range(6)],
                          'verify': [{'observation': {'status': 'PASS'}} for unused in range(6)]}
        self.acceptance = {'status': 'OA_DATA_QA_IMAGE_ACCEPTED_NOT_DEPLOYED', 'production_database_access': False,
                           'production_workload_modified': False, 'provider_requests': 0,
                           'checks': [{'deployment': 'gateway', 'status': 'PASS',
                                       'observation': {'status': 'INSTALLED_OA_DATA_QA_IMAGE_ACCEPTED'},
                                       'pods': [{'containers': [{'imageID': n['FEATURE']['candidate_image']}]}]}]}
        n['receipt'] = lambda pin: copy.deepcopy(self.expansion if pin == n['EXPANSION'] else self.acceptance)
        self.calls.clear()

    def command(self, args, value=None, raw=False, **unused):
        if args[0] == 'patch':
            self.calls.append(copy.deepcopy(args))
            self.patch_calls.append(copy.deepcopy(value))
            assert args == PATCH_ARGS
            assert self.claims == 1 and self.saved[-1]['patch_may_have_applied'] is True
            assert self.saved[-1]['patch_acknowledged'] is False
            if self.patch_error:
                raise self.patch_error
            deployment = self.deployments['gateway']
            assert value[0] == {'op': 'test', 'path': '/metadata/uid', 'value': deployment['metadata']['uid']}
            assert value[1] == {'op': 'test', 'path': '/metadata/resourceVersion', 'value': deployment['metadata']['resourceVersion']}
            assert value[2] == {'op': 'test', 'path': '/spec', 'value': deployment['spec']}
            assert value[3] == {'op': 'replace', 'path': '/spec/template/spec/containers/0/image',
                                'value': self.n['FEATURE']['candidate_image']}
            assert len(value) == 4
            deployment['spec']['template']['spec']['containers'][0]['image'] = self.n['FEATURE']['candidate_image']
            deployment['metadata']['generation'] += 1
            deployment['metadata']['resourceVersion'] = '124'
            deployment['status']['observedGeneration'] += 1
            for pod in self.pods['gateway']:
                pod['metadata']['uid'] += '-feature'
                pod['spec']['containers'][0]['image'] = self.n['FEATURE']['candidate_image']
                pod['status']['containerStatuses'][0].update(imageID=self.n['FEATURE']['candidate_image'],
                                                            containerID=pod['metadata']['uid'] + '-container')
            acknowledged = copy.deepcopy(deployment)
            self.after_patch()
            return self.ack_transform(acknowledged)
        if args[0] == 'exec':
            self.calls.append(copy.deepcopy(args))
            if args[-1] == self.n['V2_PREFLIGHT']:
                self.v2_calls.append(args[1])
                disabled = self.disabled(args[1])
                return {'status': 'PASS' if disabled else 'INCOMPLETE', 'v2_enabled': not disabled,
                        'provider_requests': 0, 'configuration_modified': False}
            request = json.loads(args[-1])
            if args[-2] == self.n['SCHEMA_CHILD']:
                result = {'status': 'PASS', 'revisions': [self.n['TARGET']], 'database_readonly': True,
                          'compatibility_verified': True, 'database_fingerprint': self.n['DATABASE_FINGERPRINT'],
                          'source_hashes': request['pin']['sources']}
            else:
                result = {'status': 'PASS', 'action': request['action'], 'provider_requests': 0,
                          'oa_callback_requests': 0, 'configuration_modified': False, 'sources_verified': True,
                          'v2_enabled': False, 'scheduler_budget': 300, 'oa_budget': 1000,
                          'local_health_http_status': 200, 'database_readonly': True,
                          'revisions': [self.n['TARGET']], 'database_fingerprint': self.n['DATABASE_FINGERPRINT']}
            return self.child_transform(result, request)
        if args[:2] == ['get', 'configmap']:
            self.calls.append(copy.deepcopy(args))
            return (args[2] + '-uid\n' + self.config_versions.get(args[2], '42')).encode()
        result = super().command(args, value=value, raw=raw)
        if args[:2] == ['get', 'endpointslices']:
            return self.endpoint_transform(result, args[args.index('-l') + 1].split('=', 1)[1])
        return result

    def actions(self):
        return [json.loads(c[-1])['action'] for c in self.calls if c[0] == 'exec' and c[-1] != self.n['V2_PREFLIGHT']]


class FeatureHostTests(unittest.TestCase):
    def setUp(self):
        self.n = load_host()
        self.original_command, self.original_receipt = self.n['command'], self.n['receipt']
        self.cluster = FeatureCluster(self.n)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.n['CONTROL'] = str(Path(self.directory.name) / 'unclaimed')

    def run_host(self):
        output = io.StringIO()
        with patch.object(self.n['os'], 'geteuid', return_value=0, create=True), \
                patch.object(self.n['os'], 'uname', return_value=SimpleNamespace(nodename='datax-01'), create=True), \
                patch('sys.argv', ['activate', '--activate-gateway-oa-data-qa-once', '--allow-interrupt-active-tasks']), \
                contextlib.redirect_stdout(output):
            result = self.n['main']()
        return result, json.loads(output.getvalue().splitlines()[-1])

    def assert_preflight_failure(self, error):
        code, result = self.run_host()
        self.assertEqual(code, 1)
        self.assertEqual(result['error_code'], error)
        self.assertEqual(self.cluster.claims, 0)
        self.assertEqual(self.cluster.patch_calls, [])
        self.assertEqual(self.cluster.saved, [])
        self.assertFalse(result['patch_requested'])
        self.assertTrue(result['preflight_retry_available'])

    def test_success_one_cas_image_only_patch_checks_all_seventy_new_pods(self):
        before = copy.deepcopy(self.cluster.deployments)
        runner_pods = copy.deepcopy(self.cluster.pods['ppt-master-runner'])
        code, result = self.run_host()
        self.assertEqual(code, 0)
        self.assertEqual(result['status'], self.n['ACCEPTED'])
        self.assertEqual(len(self.cluster.patch_calls), 1)
        self.assertEqual(len(self.cluster.v2_calls), 70)
        self.assertEqual(len(set(self.cluster.v2_calls)), 70)
        self.assertEqual(self.cluster.actions().count('runtime'), 70)
        self.assertEqual(self.cluster.actions().count('database'), 1)
        self.assertEqual(self.cluster.actions().count('verify'), 3)
        self.assertEqual(result['accepted_pod_count'], 70)
        self.assertTrue(result['feature_image_deployed'])
        self.assertFalse(result['schema_migration_requested'])
        self.assertTrue(result['frontend_acceptance_pending'])
        self.assertTrue(result['oa_business_acceptance_pending'])
        self.assertEqual(self.cluster.deployments['frontend'], before['frontend'])
        self.assertEqual(self.cluster.deployments['ppt-master-runner'], before['ppt-master-runner'])
        self.assertEqual(self.cluster.pods['ppt-master-runner'], runner_pods)
        final_spec = copy.deepcopy(self.cluster.deployments['gateway']['spec'])
        final_spec['template']['spec']['containers'][0]['image'] = before['gateway']['spec']['template']['spec']['containers'][0]['image']
        self.assertEqual(final_spec, before['gateway']['spec'])

    def test_preexisting_new_pod_identity_is_not_a_historical_receipt_gate(self):
        self.cluster.pods['gateway'][0]['metadata']['uid'] = 'replacement-before-activation'
        code, result = self.run_host()
        self.assertEqual(code, 0)

    def test_migration_receipt_must_prove_acknowledged_committed_schema(self):
        for key, value in (('status', 'INCOMPLETE'), ('migration_outcome', 'uncertain'),
                           ('expansion_acknowledged', False), ('schema_revision', '0020_ppt_confirmation')):
            with self.subTest(key=key):
                original = self.cluster.expansion[key]
                self.cluster.expansion[key] = value
                self.assert_preflight_failure('EXPANSION_NOT_ACCEPTED')
                self.cluster.expansion[key] = original

    def test_incomplete_migration_verification_blocks(self):
        self.cluster.expansion['verify'].pop()
        self.assert_preflight_failure('EXPANSION_VERIFICATION_INCOMPLETE')

    def test_feature_acceptance_image_mismatch_blocks(self):
        self.cluster.acceptance['checks'][0]['pods'][0]['containers'][0]['imageID'] = 'wrong'
        self.assert_preflight_failure('FEATURE_IMAGE_NOT_ACCEPTED')

    def test_preload_node_uid_mismatch_blocks(self):
        self.n['PRELOADS'][0]['expected_node_uid'] = 'recreated-node'
        self.assert_preflight_failure('PRELOAD_NOT_ACCEPTED')

    def test_v2_flag_true_on_last_gateway_prevents_patch(self):
        last = max(p['metadata']['name'] for p in self.cluster.pods['gateway'])
        self.cluster.disabled = lambda pod: pod != last
        self.assert_preflight_failure('V2_MUST_BE_DISABLED_BEFORE_ROLLOUT')
        self.assertEqual(len(self.cluster.v2_calls), 70)

    def test_configuration_change_since_expansion_blocks_before_exec(self):
        self.cluster.config_versions['gateway-config'] = '43'
        self.assert_preflight_failure('CONFIGURATION_CHANGED_SINCE_EXPANSION')
        self.assertEqual(self.cluster.v2_calls, [])

    def test_preflight_deployment_drift_blocks(self):
        self.cluster.deployments['gateway']['spec']['replicas'] = 69
        self.assert_preflight_failure('DEPLOYMENT_BASELINE_CHANGED')

    def test_last_gateway_image_drift_blocks(self):
        self.cluster.pods['gateway'][-1]['status']['containerStatuses'][0]['imageID'] = 'wrong@sha256:' + '0' * 64
        self.assert_preflight_failure('POD_IMAGE_CHANGED')

    def test_service_endpoint_gap_blocks_before_claim(self):
        self.cluster.endpoint_transform = lambda value, role: {'items': []} if role == 'gateway' else value
        self.assert_preflight_failure('SERVICE_ENDPOINTS_NOT_READY')

    def test_old_consumer_database_mismatch_blocks(self):
        self.cluster.child_transform = lambda value, request: dict(value, database_fingerprint='0' * 64)
        self.assert_preflight_failure('COMPATIBILITY_DATABASE_CHECK_FAILED')

    def test_pod_restart_during_preflight_blocks(self):
        def change(value, request):
            self.cluster.pods['ppt-master-runner'][0]['status']['containerStatuses'][0]['restartCount'] += 1
            return value
        self.cluster.child_transform = change
        self.assert_preflight_failure('CONTAINERS_CHANGED_DURING_PREFLIGHT')

    def test_uncertain_patch_is_never_retried_or_rolled_back(self):
        self.cluster.patch_error = subprocess.TimeoutExpired('kubectl', 45)
        code, result = self.run_host()
        self.assertEqual(code, 1)
        self.assertEqual(len(self.cluster.patch_calls), 1)
        self.assertTrue(result['patch_may_have_applied'])
        self.assertFalse(result['patch_acknowledged'])
        self.assertIsNone(result['production_workload_mutation'])
        self.assertFalse(result['automatic_rollback'])
        self.assertFalse(result['preflight_retry_available'])

    def test_presend_journal_failure_does_not_claim_a_patch_was_sent(self):
        self.n['save'] = lambda report: (_ for _ in ()).throw(OSError('synthetic save failure'))
        code, result = self.run_host()
        self.assertEqual(code, 1)
        self.assertEqual(self.cluster.patch_calls, [])
        self.assertTrue(result['once_claimed'])
        self.assertFalse(result['patch_may_have_applied'])
        self.assertEqual(result['patch_request_count'], 0)

    def test_patch_acknowledgement_requires_exact_next_generation(self):
        def changed_generation(value):
            value['metadata']['generation'] += 1
            return value
        self.cluster.ack_transform = changed_generation
        code, result = self.run_host()
        self.assertEqual(code, 1)
        self.assertEqual(result['error_code'], 'PATCH_ACK_CHANGED')
        self.assertTrue(result['patch_may_have_applied'])
        self.assertFalse(result['patch_acknowledged'])
        self.assertEqual(len(self.cluster.patch_calls), 1)

    def test_new_runtime_source_failure_blocks_acceptance(self):
        self.cluster.child_transform = lambda value, request: dict(value, sources_verified=False) if request['action'] == 'runtime' else value
        code, result = self.run_host()
        self.assertEqual(code, 1)
        self.assertEqual(result['error_code'], 'FEATURE_RUNTIME_CONTRACT_CHANGED')
        self.assertTrue(result['patch_acknowledged'])
        self.assertEqual(result['accepted_pod_count'], 0)

    def test_new_runtime_v2_true_blocks_acceptance(self):
        self.cluster.child_transform = lambda value, request: dict(value, v2_enabled=True) if request['action'] == 'runtime' else value
        code, result = self.run_host()
        self.assertEqual(code, 1)
        self.assertEqual(result['error_code'], 'FEATURE_RUNTIME_CONTRACT_CHANGED')

    def test_runner_change_during_rollout_is_detected(self):
        self.cluster.after_patch = lambda: self.cluster.deployments['ppt-master-runner']['metadata'].update(generation=56)
        code, result = self.run_host()
        self.assertEqual(code, 1)
        self.assertEqual(result['error_code'], 'RUNNER_CHANGED')

    def test_frontend_change_after_patch_is_detected(self):
        self.cluster.after_patch = lambda: self.cluster.deployments['frontend']['metadata'].update(generation=226)
        code, result = self.run_host()
        self.assertEqual(code, 1)
        self.assertEqual(result['error_code'], 'FRONTEND_BASELINE_CHANGED')

    def test_configuration_change_after_patch_is_detected(self):
        self.cluster.after_patch = lambda: self.cluster.config_versions.update({'gateway-config': '43'})
        code, result = self.run_host()
        self.assertEqual(code, 1)
        self.assertEqual(result['error_code'], 'UNRELATED_STATE_CHANGED')

    def test_pod_restart_after_runtime_acceptance_is_detected(self):
        def change(value, request):
            if request['action'] == 'database':
                self.cluster.pods['gateway'][-1]['status']['containerStatuses'][0]['restartCount'] += 1
            return value
        self.cluster.child_transform = change
        code, result = self.run_host()
        self.assertEqual(code, 1)
        self.assertEqual(result['error_code'], 'GATEWAY_CONTAINERS_CHANGED')
        self.assertEqual(result['accepted_pod_count'], 70)

    def test_endpoint_removed_after_runtime_acceptance_is_detected(self):
        def change(value, request):
            if request['action'] == 'database':
                self.cluster.endpoint_transform = lambda observed, role: {'items': []} if role == 'gateway' else observed
            return value
        self.cluster.child_transform = change
        code, result = self.run_host()
        self.assertEqual(code, 1)
        self.assertEqual(result['error_code'], 'SERVICE_ENDPOINTS_NOT_READY')
        self.assertEqual(len(self.cluster.patch_calls), 1)

    def test_never_ready_gateway_exits_at_deadline(self):
        self.cluster.after_patch = lambda: self.cluster.deployments['gateway']['status'].update(readyReplicas=0)
        with patch.object(self.n['time'], 'monotonic', side_effect=[0, 4201]):
            code, result = self.run_host()
        self.assertEqual(code, 1)
        self.assertEqual(result['error_code'], 'ROLLOUT_DEADLINE')
        self.assertEqual(len(self.cluster.patch_calls), 1)

    def test_command_patch_timeout_is_one_attempt(self):
        with patch.object(self.n['subprocess'], 'run', side_effect=subprocess.TimeoutExpired('kubectl', 45)) as run:
            with self.assertRaises(subprocess.TimeoutExpired):
                self.original_command(PATCH_ARGS, [])
        self.assertEqual(run.call_count, 1)

    def test_command_refuses_other_deployment_and_mutation_verbs(self):
        for args, value in ((['patch', 'deployment', 'ppt-master-runner'], []), (['create', 'job'], None),
                            (['delete', 'pod'], None), (['rollout', 'restart', 'deployment', 'gateway'], None)):
            with self.subTest(args=args), patch.object(self.n['subprocess'], 'run') as run:
                with self.assertRaises(self.n['Refused']):
                    self.original_command(args, value)
                run.assert_not_called()

    def test_nonzero_child_failure_receipt_is_preserved(self):
        failure = {'status': 'INCOMPLETE', 'raw_errors_exported': False, 'provider_requests': 0,
                   'error_code': 'SOURCE_CHANGED'}
        with patch.object(self.n['subprocess'], 'run', return_value=SimpleNamespace(returncode=1, stdout=json.dumps(failure).encode(), stderr=b'private')) as run:
            self.assertEqual(self.original_command(['exec', 'existing-pod']), failure)
        self.assertEqual(run.call_count, 1)

    def test_exec_uses_full_child_deadline_and_never_retries(self):
        with patch.object(self.n['subprocess'], 'run', side_effect=subprocess.TimeoutExpired('kubectl', 210)) as run:
            with self.assertRaises(subprocess.TimeoutExpired):
                self.original_command(['exec', 'existing-pod'])
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args[1]['timeout'], 210)
        self.assertIn('--request-timeout=180s', run.call_args[0][0])

    def test_receipt_hash_is_checked(self):
        path = Path(self.directory.name) / 'receipt.json'
        raw = b'{"status":"synthetic"}'
        path.write_bytes(raw)
        pin = {'path': str(path), 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
        with patch.object(self.n['os'], 'O_NOFOLLOW', 0, create=True):
            self.assertEqual(self.original_receipt(pin), {'status': 'synthetic'})
            pin['sha256'] = '0' * 64
            with self.assertRaisesRegex(self.n['Refused'], 'RECEIPT_HASH'):
                self.original_receipt(pin)

    def test_task_interruption_flag_required_before_work(self):
        with patch('sys.argv', ['activate', '--activate-gateway-oa-data-qa-once']), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                self.n['main']()
        self.assertEqual(self.cluster.calls, [])

    def test_host_source_parses_on_python37(self):
        source = (HERE / 'activate-oa-data-qa-feature.py').read_text(encoding='utf-8')
        ast.parse(source) if sys.version_info[:2] == (3, 7) else ast.parse(source, feature_version=(3, 7))


if __name__ == '__main__':
    unittest.main()
