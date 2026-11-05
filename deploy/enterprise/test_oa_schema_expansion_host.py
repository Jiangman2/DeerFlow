"""Synthetic Kubernetes/child tests; these never contact a cluster or database."""
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


HERE = Path(__file__).resolve().parent


def load_host():
    packaged = os.environ.get('OA_PACKAGED_CANDIDATE')
    if packaged:
        namespace = {'__name__': 'host_test'}
        exec(compile(Path(packaged).read_bytes(), packaged, 'exec'), namespace)
        return namespace
    source = (HERE / 'activate-oa-compat.py').read_text(encoding='utf-8')
    tree = ast.parse(source)
    names = {'Refused', 'require', 'digest', 'binding', 'ready', 'configuration_refs',
             'configuration_metadata', 'pods_for', 'identities', 'endpoints'}
    common = '\n\n'.join(ast.get_source_segment(source, node) for node in tree.body
                         if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names)
    host = (HERE / 'expand-oa-schema.py').read_text(encoding='utf-8')
    host = host.replace("COMMON = ''", 'COMMON = ' + repr(common), 1)
    namespace = {'__name__': 'host_test'}
    exec(compile(host, str(HERE / 'expand-oa-schema.py'), 'exec'), namespace)
    return namespace


class Cluster:
    def __init__(self, n):
        self.n = n
        self.calls, self.saved = [], []
        self.claims = 0
        self.child_transform = lambda result, args: result
        self.expand_error = None
        self.post_change = None
        self.deployments, self.pods, self.services = {}, {}, {}
        for role, count in (('gateway', 70), ('ppt-master-runner', 1), ('frontend', 1)):
            image = 'local/' + role + '@sha256:' + ('a' if role == 'gateway' else 'b') * 64
            nodes = ['gateway-node-' + str(i) for i in range(5)] if role == 'gateway' else [role + '-node']
            spec = {'replicas': count, 'selector': {'matchLabels': {'app': role}},
                    'template': {'spec': {'containers': [{'name': role, 'image': image,
                        'envFrom': [{'configMapRef': {'name': role + '-config'}}]}]}}}
            d = {'metadata': {'uid': role + '-deployment', 'generation': 383 if role == 'gateway' else 55},
                 'spec': spec, 'status': {k: count for k in ('replicas', 'readyReplicas', 'updatedReplicas', 'availableReplicas')}}
            d['status']['observedGeneration'] = d['metadata']['generation']
            self.deployments[role] = d
            pin = dict(n['binding'](d), candidate_image=image, config_digest='sha256:' + 'c' * 64,
                       nodes={node: node + '-uid' for node in nodes},
                       sources={'bootstrap': '1' * 64, 'helper': '2' * 64})
            if role == 'frontend':
                n['FRONT'] = n['binding'](d)
            else:
                n['EXPECTED'][role] = pin
            self.pods[role] = [
                {'metadata': {'name': role + '-' + str(i), 'uid': role + '-uid-' + str(i),
                              'ownerReferences': [{'controller': True, 'uid': role + '-rs'}]},
                 'spec': {'nodeName': nodes[i % len(nodes)], 'containers': [{'name': role, 'image': image}]},
                 'status': {'containerStatuses': [{'ready': True, 'imageID': image,
                            'containerID': 'container-' + role + '-' + str(i), 'restartCount': 0}]}}
                for i in range(count)]
            self.services[role] = {'metadata': {'uid': role + '-service'}, 'spec': {'ports': [{'port': 8001}]}}
        self.runtime = {role: [{'uid': p['metadata']['uid'], 'observation': {'status': 'PASS'}}
                              for p in pods] for role, pods in self.pods.items() if role != 'frontend'}
        n['CHILD'] = 'reviewed child'
        n['MIGRATION_SOURCE'] = 'reviewed migration'
        n['nodes_gate'] = lambda deployments: {'status': 'ROLE_NODE_COVERAGE_ACCEPTED', 'observed_node_count': 18}
        n['evidence'] = lambda: self.runtime
        n['command'] = self.command
        n['claim_control'] = self.claim
        n['save_report'] = self.save

    def claim(self, report):
        self.claims += 1
        report['once_claimed'] = True

    def save(self, report):
        self.saved.append(copy.deepcopy(report))

    def command(self, args, value=None, raw=False, expansion=False):
        self.calls.append(copy.deepcopy(args))
        if args[0] == 'exec':
            request = json.loads(args[-1])
            action = request['action']
            if action == 'expand':
                assert expansion is True
                assert self.claims == 1
                assert self.saved[-1]['migration_outcome'] == 'uncertain'
                assert self.saved[-1]['migration_may_have_applied'] is True
                assert request['expected_migration_tree_sha256'] == '3' * 64
                if self.expand_error:
                    raise self.expand_error
            else:
                assert expansion is False
            result = {'status': 'PASS', 'action': action, 'database_readonly': action != 'expand',
                      'revisions': [self.n['BASE_REVISION'] if action == 'inspect' else self.n['TARGET_REVISION']],
                      'database_fingerprint': self.n['DATABASE_FINGERPRINT'],
                      'source_hashes': request['pin']['sources'], 'migration_tree_sha256': '3' * 64,
                      'migration_head': self.n['BASE_REVISION'], 'migration_outcome': 'committed' if action == 'expand' else 'not_requested',
                      'schema_migrated': action == 'expand', 'provider_requests': 0,
                      'raw_errors_exported': False, 'compatibility_verified': action == 'verify',
                      'active_runs': 37}
            if action == 'verify' and self.post_change:
                self.post_change()
            return self.child_transform(result, request)
        assert args[0] == 'get', 'Mutation command escaped: ' + repr(args)
        resource = args[1]
        if resource == 'deployment':
            return copy.deepcopy(self.deployments[args[2]])
        if resource == 'service':
            return copy.deepcopy(self.services[args[2]])
        if resource == 'configmap':
            assert raw
            return (args[2] + '-uid\n42').encode()
        selector = args[args.index('-l') + 1]
        role = selector.split('=', 1)[1]
        if resource == 'replicasets':
            return {'items': [{'metadata': {'uid': role + '-rs', 'ownerReferences': [
                {'uid': role + '-deployment', 'controller': True}]}}]}
        if resource == 'pods':
            return {'items': copy.deepcopy(self.pods[role])}
        if resource == 'endpointslices':
            return {'items': [{'endpoints': [{'conditions': {'ready': True}, 'targetRef': {'uid': p['metadata']['uid']}}
                                            for p in self.pods[role]]}]}
        raise AssertionError(args)

    def actions(self):
        return [json.loads(c[-1])['action'] for c in self.calls if c[0] == 'exec']


class HostTests(unittest.TestCase):
    def setUp(self):
        self.n = load_host()
        self.original_command = self.n['command']
        self.original_evidence = self.n['evidence']
        self.cluster = Cluster(self.n)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.n['CONTROL'] = str(Path(self.directory.name) / 'unclaimed')

    def run_host(self):
        out = io.StringIO()
        with patch.object(self.n['os'], 'geteuid', return_value=0, create=True), \
                patch.object(self.n['os'], 'uname', return_value=SimpleNamespace(nodename='datax-01'), create=True), \
                patch('sys.argv', ['expand', '--expand-oa-schema-once']), contextlib.redirect_stdout(out):
            code = self.n['main']()
        return code, json.loads(out.getvalue())

    def assert_preflight_failure(self, expected):
        code, result = self.run_host()
        self.assertEqual(code, 1)
        self.assertEqual(result['error_code'], expected)
        self.assertEqual(self.cluster.claims, 0)
        self.assertEqual(self.cluster.saved, [])
        self.assertNotIn('expand', self.cluster.actions())
        self.assertTrue(result['preflight_retry_available'])
        self.assertFalse(result['schema_migration_requested'])

    def test_success_exactly_one_expansion_and_six_checks_each_side(self):
        code, result = self.run_host()
        self.assertEqual(code, 0)
        self.assertEqual(self.cluster.actions(), ['inspect'] * 6 + ['expand'] + ['verify'] * 6)
        self.assertEqual(result['status'], self.n['ACCEPTED'])
        self.assertTrue(result['schema_migrated'])
        self.assertTrue(result['expansion_acknowledged'])
        self.assertFalse(result['feature_deployed'])
        self.assertFalse(result['production_workload_mutation'])
        self.assertTrue(result['active_tasks_allowed'])
        report = self.cluster.saved[-1]
        self.assertEqual(report['before'], report['after'])
        self.assertEqual(sum(len(x) for x in report['after']['pods'].values()), 71)

    def test_deployment_drift_rejected_before_claim(self):
        self.cluster.deployments['gateway']['spec']['replicas'] = 69
        self.assert_preflight_failure('DEPLOYMENT_BASELINE_CHANGED')

    def test_image_mismatch_on_last_of_seventy_pods_rejected(self):
        self.cluster.pods['gateway'][-1]['status']['containerStatuses'][0]['imageID'] = 'local@sha256:' + 'f' * 64
        self.assert_preflight_failure('POD_IMAGE_CHANGED')

    def test_frontend_readiness_rejected(self):
        self.cluster.deployments['frontend']['status']['readyReplicas'] = 0
        self.assert_preflight_failure('FRONTEND_BASELINE_CHANGED')

    def test_installed_source_mismatch_rejected(self):
        self.cluster.child_transform = lambda r, a: dict(r, source_hashes={'bootstrap': 'f' * 64, 'helper': '2' * 64})
        self.assert_preflight_failure('INSTALLED_SOURCES_CHANGED')

    def test_database_fingerprint_mismatch_rejected(self):
        self.cluster.child_transform = lambda r, a: dict(r, database_fingerprint='f' * 64)
        self.assert_preflight_failure('DATABASE_FINGERPRINT_CHANGED')

    def test_fresh_pods_may_differ_from_historical_acceptance(self):
        for row in self.cluster.runtime['gateway']:
            row['uid'] = 'historical-' + row['uid']
        code, result = self.run_host()
        self.assertEqual(code, 0)

    def test_pod_restart_during_inspection_prevents_expansion(self):
        def transform(result, args):
            self.cluster.pods['gateway'][-1]['status']['containerStatuses'][0]['restartCount'] += 1
            return result
        self.cluster.child_transform = transform
        self.assert_preflight_failure('LIVE_STATE_CHANGED_DURING_PREFLIGHT')

    def test_timeout_retains_uncertain_journal_and_never_retries(self):
        self.cluster.expand_error = subprocess.TimeoutExpired('kubectl', 210)
        code, result = self.run_host()
        self.assertEqual(code, 1)
        self.assertEqual(self.cluster.actions().count('expand'), 1)
        self.assertNotIn('verify', self.cluster.actions())
        self.assertEqual(result['migration_outcome'], 'uncertain')
        self.assertTrue(result['migration_may_have_applied'])
        self.assertIsNone(result['schema_migrated'])
        self.assertFalse(result['expansion_acknowledged'])
        self.assertFalse(result['preflight_retry_available'])
        self.assertEqual(self.cluster.saved[-1]['migration_outcome'], 'uncertain')

    def test_failed_postverification_keeps_acknowledged_schema_fact(self):
        def transform(r, a):
            return dict(r, compatibility_verified=False) if a['action'] == 'verify' else r
        self.cluster.child_transform = transform
        code, result = self.run_host()
        self.assertEqual(code, 1)
        self.assertEqual(result['error_code'], 'OLD_CONSUMER_COMPATIBILITY_FAILED')
        self.assertTrue(result['schema_migrated'])
        self.assertTrue(result['expansion_acknowledged'])
        self.assertFalse(result['feature_deployed'])
        self.assertEqual(self.cluster.actions().count('expand'), 1)

    def test_live_post_change_is_not_accepted(self):
        self.cluster.post_change = lambda: self.cluster.services['gateway']['spec'].update(type='changed')
        code, result = self.run_host()
        self.assertEqual(code, 1)
        self.assertEqual(result['error_code'], 'LIVE_STATE_CHANGED_AFTER_EXPANSION')
        self.assertTrue(result['schema_migrated'])

    def test_explicit_child_refusal_retains_safe_diagnostic(self):
        def transform(r, a):
            if a['action'] == 'expand':
                return dict(r, status='INCOMPLETE', migration_outcome='not_requested', schema_migrated=False,
                            error_code='DATABASE_OPERATION_FAILED', error_category='lock_timeout', sqlstate='55P03',
                            message='postgresql://secret:password@private/database')
            return r
        self.cluster.child_transform = transform
        code, result = self.run_host()
        self.assertEqual(code, 1)
        self.assertFalse(result['migration_may_have_applied'])
        self.assertEqual(result['last_child_failure']['sqlstate'], '55P03')
        self.assertNotIn('secret', json.dumps(self.cluster.saved))

    def test_nonzero_exec_preserves_sanitized_child_failure(self):
        failure = {'status': 'INCOMPLETE', 'migration_outcome': 'uncertain', 'raw_errors_exported': False,
                   'provider_requests': 0, 'error_code': 'PROCESS_DEADLINE'}
        result = SimpleNamespace(returncode=124, stdout=json.dumps(failure).encode(), stderr=b'private')
        with patch.object(self.n['subprocess'], 'run', return_value=result) as run:
            self.assertEqual(self.original_command(['exec', 'existing-pod'], expansion=True), failure)
        self.assertEqual(run.call_count, 1)
        self.assertIn('--request-timeout=180s', run.call_args[0][0])
        self.assertEqual(run.call_args[1]['timeout'], 210)

    def test_failure_after_reported_commit_is_not_accepted_or_falsely_rolled_back(self):
        def transform(r, a):
            return dict(r, status='INCOMPLETE', error_code='INSTALLED_SOURCES_CHANGED') if a['action'] == 'expand' else r
        self.cluster.child_transform = transform
        code, result = self.run_host()
        self.assertEqual(code, 1)
        self.assertTrue(result['schema_migrated'])
        self.assertEqual(result['migration_outcome'], 'committed')
        self.assertFalse(result['expansion_acknowledged'])
        self.assertFalse(result['feature_deployed'])
        self.assertNotIn('verify', self.cluster.actions())

    def test_receipt_hash_is_enforced(self):
        path = Path(self.directory.name) / 'receipt.json'
        raw = b'{"status": "accepted"}'
        path.write_bytes(raw)
        pin = {'path': str(path), 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
        with patch.object(self.n['os'], 'O_NOFOLLOW', 0, create=True):
            self.assertEqual(self.n['receipt'](pin), {'status': 'accepted'})
            pin['sha256'] = '0' * 64
            with self.assertRaisesRegex(self.n['Refused'], 'RECEIPT_HASH'):
                self.n['receipt'](pin)

    def test_both_accepted_receipt_shapes_and_image_binding(self):
        compatibility = {'status': 'COMPATIBILITY_ROLLOUT_ACCEPTED_SCHEMA_UNCHANGED',
                         'schema_revision': self.n['BASE_REVISION'], 'schema_migration_requested': False,
                         'frontend_preserved': True, 'configuration_metadata_preserved': True,
                         'after': {role: {k: pin[k] for k in ('uid', 'generation', 'spec_sha256')}
                                   for role, pin in self.n['EXPECTED'].items()}, 'runtime': self.cluster.runtime}
        feature = {'status': 'OA_DATA_QA_IMAGE_ACCEPTED_NOT_DEPLOYED', 'production_database_access': False,
                   'production_workload_modified': False, 'schema_migrated': False, 'provider_requests': 0,
                   'checks': [{'deployment': 'gateway', 'status': 'PASS',
                               'observation': {'status': 'INSTALLED_OA_DATA_QA_IMAGE_ACCEPTED'},
                               'pods': [{'containers': [{'imageID': self.n['FEATURE_IMAGE']}]}]}]}
        self.n['RECEIPTS'] = {'compatibility': {'kind': 'compatibility'}, 'feature_image': {'kind': 'feature'}}
        self.n['receipt'] = lambda p: compatibility if p['kind'] == 'compatibility' else feature
        self.assertEqual(self.original_evidence(), self.cluster.runtime)
        feature['checks'][0]['pods'][0]['containers'][0]['imageID'] = 'wrong'
        with self.assertRaisesRegex(self.n['Refused'], 'FEATURE_IMAGE_RECEIPT_IMAGE_CHANGED'):
            self.original_evidence()

    def test_actual_exec_timeout_is_never_retried(self):
        with patch.object(self.n['subprocess'], 'run', side_effect=subprocess.TimeoutExpired('kubectl', 210)) as run:
            with self.assertRaises(subprocess.TimeoutExpired):
                self.original_command(['exec', 'existing-pod'], expansion=True)
        self.assertEqual(run.call_count, 1)

    def test_unparseable_exec_is_never_retried(self):
        with patch.object(self.n['subprocess'], 'run', return_value=SimpleNamespace(returncode=0, stdout=b'not-json', stderr=b'')) as run:
            with self.assertRaises(ValueError):
                self.original_command(['exec', 'existing-pod'], expansion=True)
        self.assertEqual(run.call_count, 1)

    def test_kubernetes_mutation_verbs_are_forbidden(self):
        for verb in ('patch', 'apply', 'create', 'delete', 'rollout', 'replace'):
            with self.subTest(verb=verb), patch.object(self.n['subprocess'], 'run') as run:
                with self.assertRaises(self.n['Refused']):
                    self.original_command([verb, 'deployment', 'gateway'])
                run.assert_not_called()

    def test_missing_explicit_option_exits_before_any_action(self):
        with patch('sys.argv', ['expand']), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                self.n['main']()
        self.assertEqual(self.cluster.calls, [])

    def test_source_parses_as_python37(self):
        source = (HERE / 'expand-oa-schema.py').read_text(encoding='utf-8')
        if sys.version_info[:2] == (3, 7):
            ast.parse(source)
        else:
            ast.parse(source, feature_version=(3, 7))


if __name__ == '__main__':
    unittest.main()
