"""Linux 3.7 tests for the exact packaged host; synthetic Kubernetes only."""
import ast
import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


class Tests(unittest.TestCase):
    def setUp(self):
        self.n = {'__name__': 'offline_check'}
        exec(compile(Path('/candidate.py').read_bytes(), '/candidate.py', 'exec'), self.n)
        self.pin = self.n['EXPECTED']['gateway']
        self.node = {'metadata': {'uid': self.pin['node_uid'], 'name': self.pin['node']},
                     'spec': {'taints': [{'key': 'dedicated', 'value': 'gateway', 'effect': 'NoSchedule'}]},
                     'status': {'conditions': [{'type': 'Ready', 'status': 'True'}, {'type': 'DiskPressure', 'status': 'False'}]}}

    def test_job_has_no_production_mounts_or_credentials(self):
        job = self.n['job_spec']('test', self.pin, self.node)
        spec = job['spec']['template']['spec']
        self.assertEqual(spec['volumes'], [{'name': 'scratch', 'emptyDir': {'sizeLimit': '64Mi'}}])
        self.assertFalse(spec['automountServiceAccountToken'])
        self.assertFalse(spec['enableServiceLinks'])
        self.assertNotIn('envFrom', spec['containers'][0])
        self.assertNotIn('hostNetwork', spec)
        env = {r['name']: r['value'] for r in spec['containers'][0]['env']}
        self.assertEqual(env, {'HOME': '/tmp', 'TMPDIR': '/tmp', 'OA_POLICY_FLOW_ENABLED': 'false', 'OA_POLICY_FLOW_MULTI_RESULT_ENABLED': 'false'})
        self.assertTrue(spec['containers'][0]['securityContext']['readOnlyRootFilesystem'])

    def test_job_is_bounded_and_uses_preloaded_digest(self):
        job = self.n['job_spec']('test', self.pin, self.node)
        self.assertEqual(job['spec']['backoffLimit'], 0)
        self.assertEqual(job['spec']['activeDeadlineSeconds'], 180)
        spec = job['spec']['template']['spec']
        self.assertEqual(spec['containers'][0]['imagePullPolicy'], 'Never')
        self.assertEqual(spec['containers'][0]['image'], self.pin['candidate_image'])
        self.assertEqual(spec['tolerations'], [{'key': 'dedicated', 'value': 'gateway', 'effect': 'NoSchedule', 'operator': 'Equal'}])

    def test_node_drift_and_unhealthy_refused(self):
        for field in ('uid', 'ready', 'disk', 'cordon', 'noexecute'):
            with self.subTest(field=field):
                node = copy.deepcopy(self.node)
                if field == 'uid':
                    node['metadata']['uid'] = 'other'
                elif field == 'ready':
                    node['status']['conditions'][0]['status'] = 'False'
                elif field == 'disk':
                    node['status']['conditions'][1]['status'] = 'True'
                elif field == 'cordon':
                    node['spec']['unschedulable'] = True
                else:
                    node['spec']['taints'][0]['effect'] = 'NoExecute'
                with self.assertRaises(self.n['Refused']):
                    self.n['job_spec']('test', self.pin, node)

    def receipt(self, directory):
        prior = self.n['PRIOR']
        p = Path(directory, 'REPORT.json')
        raw = json.dumps(dict(status='COMPATIBILITY_ROLLOUT_ACCEPTED_SCHEMA_UNCHANGED', after=prior['after'], schema_revision='0020_ppt_confirmation')).encode()
        p.write_bytes(raw)
        p.chmod(0o600)
        prior.update(report_path=str(p), report_bytes=len(raw), report_sha256=hashlib.sha256(raw).hexdigest())
        return p

    def test_prior_report_hash_is_required(self):
        with tempfile.TemporaryDirectory() as d:
            p = self.receipt(d)
            self.n['accepted_compatibility_report']()
            p.write_bytes(p.read_bytes().replace(b'0020', b'0021'))
            with self.assertRaises(self.n['Refused']):
                self.n['accepted_compatibility_report']()

    def test_prior_symlink_refused(self):
        with tempfile.TemporaryDirectory() as d:
            p = self.receipt(d)
            alias = Path(d, 'alias')
            alias.symlink_to(p)
            self.n['PRIOR']['report_path'] = str(alias)
            with self.assertRaises(OSError):
                self.n['accepted_compatibility_report']()

    def flow(self, fault=None):
        n, jobs = self.n, []
        deployments = {}
        for name, pin in n['EXPECTED'].items():
            spec = {'replicas': 1}
            pin['spec_sha256'] = n['digest'](spec)
            deployments[name] = {'metadata': {'uid': pin['uid'], 'generation': pin['generation']}, 'spec': spec,
                                'status': {'observedGeneration': pin['generation'], 'replicas': 1, 'updatedReplicas': 1, 'readyReplicas': 1, 'availableReplicas': 1}}
        tree = ast.parse(n['CHILD'])
        checks = ast.literal_eval(next(r.value for r in tree.body if isinstance(r, ast.Assign) and r.targets[0].id == 'CHECKS'))

        def command(args, value=None, raw=False):
            self.assertNotIn(args[0], ('patch', 'apply', 'delete', 'exec'))
            if args[:2] == ['get', 'deployment']:
                result = copy.deepcopy(deployments[args[2]])
                if fault == 'before_drift' or (fault == 'after_drift' and jobs):
                    result['metadata']['generation'] += 1
                return result
            if args[:2] == ['get', 'node']:
                return self.node
            if args[0] == 'create':
                self.assertEqual(value['kind'], 'Job')
                value['metadata']['uid'] = 'job-id'
                jobs.append(value)
                return value
            job = jobs[-1]
            if args[:2] == ['get', 'job']:
                return {'metadata': job['metadata'], 'status': {'succeeded': 1}}
            if args[:2] == ['get', 'pods']:
                spec = copy.deepcopy(job['spec']['template']['spec'])
                spec['nodeName'] = spec['nodeSelector']['kubernetes.io/hostname']
                image = spec['containers'][0]['image'] if fault != 'image' else 'sha256:wrong'
                owner = job['metadata']['uid'] if fault != 'owner' else 'other'
                return {'items': [{'metadata': {'name': 'check-pod', 'uid': 'pod-id', 'ownerReferences': [{'uid': owner, 'controller': True}]},
                                  'spec': spec, 'status': {'phase': 'Succeeded', 'containerStatuses': [{'imageID': image, 'state': {'terminated': {'exitCode': 0}}}]}}]}
            if args[0] == 'logs':
                result = dict(status='INSTALLED_OA_DATA_QA_IMAGE_ACCEPTED', checks=checks, production_database_access=False,
                              provider_requests=0, oa_callback_requests=0, schema_migrated=False)
                if fault == 'partial_checks':
                    result['checks'] = result['checks'][:-1]
                if fault == 'callback':
                    result['oa_callback_requests'] = 1
                if fault == 'db_access':
                    result['production_database_access'] = True
                return json.dumps(result).encode()
            raise AssertionError(args)

        n['command'] = command
        with tempfile.TemporaryDirectory() as directory, patch.object(n['os'], 'uname', return_value=SimpleNamespace(nodename='datax-01')), patch('sys.argv', ['check', '--verify-preloaded-images-once']), contextlib.redirect_stdout(io.StringIO()):
            self.receipt(directory)
            n['CONTROL'] = directory + '/check'
            code = n['main']()
            report = json.loads(Path(n['CONTROL'], 'REPORT.json').read_text())
            self.assertFalse(report['production_workload_modified'])
            self.assertFalse(report['ready_for_rollout'])
        return code, jobs, report

    def test_one_job_acceptance_preserves_three_deployments(self):
        code, jobs, report = self.flow()
        self.assertEqual(code, 0)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(set(self.n['EXPECTED']), {'gateway', 'ppt-master-runner', 'frontend'})
        self.assertEqual(report['status'], 'OA_DATA_QA_IMAGE_ACCEPTED_NOT_DEPLOYED')

    def test_baseline_drift_creates_no_job(self):
        code, jobs, _ = self.flow('before_drift')
        self.assertEqual((code, jobs), (1, []))

    def test_drift_during_check_prevents_acceptance(self):
        self.assertEqual(self.flow('after_drift')[0], 1)

    def test_wrong_runtime_image_refused(self):
        self.assertEqual(self.flow('image')[0], 1)

    def test_wrong_job_owner_refused(self):
        self.assertEqual(self.flow('owner')[0], 1)

    def test_incomplete_child_checks_refused(self):
        self.assertEqual(self.flow('partial_checks')[0], 1)

    def test_unexpected_callback_refused(self):
        self.assertEqual(self.flow('callback')[0], 1)

    def test_unexpected_production_db_access_refused(self):
        self.assertEqual(self.flow('db_access')[0], 1)


if __name__ == '__main__':
    unittest.main()
