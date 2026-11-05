import copy
import contextlib
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

    def job(self):
        return self.n['job_spec']('test', self.pin, self.node)

    def test_no_production_mounts_or_credentials(self):
        s = self.job()['spec']['template']['spec']
        self.assertFalse(s['automountServiceAccountToken'])
        self.assertEqual(s['volumes'], [{'name': 'scratch', 'emptyDir': {'sizeLimit': '64Mi'}}])
        self.assertEqual(set(e['name'] for e in s['containers'][0]['env']), {'HOME', 'TMPDIR'})
        self.assertNotIn('envFrom', s['containers'][0])
        self.assertNotIn('hostNetwork', s)

    def test_taint_and_cached_image_binding(self):
        s = self.job()['spec']['template']['spec']
        self.assertEqual(s['tolerations'][0], {'key': 'dedicated', 'value': 'gateway', 'effect': 'NoSchedule', 'operator': 'Equal'})
        self.assertEqual(s['containers'][0]['imagePullPolicy'], 'Never')
        self.assertEqual(s['containers'][0]['image'], self.pin['candidate_image'])
        self.assertEqual(s['containers'][0]['command'][:4], ['/app/backend/.venv/bin/python', '-I', '-B', '-c'])

    def test_deadline_and_no_retries(self):
        s = self.job()['spec']
        self.assertEqual(s['backoffLimit'], 0)
        self.assertEqual(s['activeDeadlineSeconds'], 180)
        self.assertEqual(s['ttlSecondsAfterFinished'], 600)

    def test_node_drift_refused(self):
        self.node['metadata']['uid'] = 'different'
        with self.assertRaises(self.n['Refused']):
            self.job()

    def test_disk_pressure_refused(self):
        self.node['status']['conditions'][1]['status'] = 'True'
        with self.assertRaises(self.n['Refused']):
            self.job()

    def test_noexecute_refused(self):
        self.node['spec']['taints'][0]['effect'] = 'NoExecute'
        with self.assertRaises(self.n['Refused']):
            self.job()

    def test_deployment_drift_and_partial_rollout_refused(self):
        d = {'metadata': {'uid': 'uid', 'generation': 1}, 'spec': {'replicas': 2}, 'status': {
            'observedGeneration': 1, 'replicas': 2, 'updatedReplicas': 2, 'readyReplicas': 2, 'availableReplicas': 2}}
        pin = {'uid': 'uid', 'generation': 1, 'spec_sha256': self.n['digest'](d['spec'])}
        self.n['validate_deployment'](d, pin)
        for field in ('replicas', 'updatedReplicas', 'readyReplicas', 'availableReplicas'):
            bad = copy.deepcopy(d)
            bad['status'][field] = 1
            with self.assertRaises(self.n['Refused']):
                self.n['validate_deployment'](bad, pin)
        d['spec']['replicas'] = 3
        with self.assertRaises(self.n['Refused']):
            self.n['validate_deployment'](d, pin)

    def test_pod_wrong_owner_refused(self):
        self.n['command'] = lambda *a, **k: {'items': [{'metadata': {'ownerReferences': [{'uid': 'wrong', 'controller': True}]}}]}
        with self.assertRaises(self.n['Refused']):
            self.n['inspect_pods']({'metadata': {'name': 'test', 'uid': 'expected'}}, self.pin)

    def test_full_host_flow_creates_only_two_isolated_jobs(self):
        n = self.n
        deployments = {}
        for name, pin in n['EXPECTED'].items():
            spec = {'replicas': 1}
            pin['spec_sha256'] = n['digest'](spec)
            deployments[name] = {'metadata': {'uid': pin['uid'], 'generation': pin['generation']}, 'spec': spec,
                'status': {'observedGeneration': pin['generation'], 'replicas': 1, 'updatedReplicas': 1, 'readyReplicas': 1, 'availableReplicas': 1}}
        jobs = []
        def command(args, value=None, raw=False):
            self.assertNotIn(args[0], ('patch', 'apply', 'delete', 'exec'))
            if args[:2] == ['get', 'deployment']:
                return deployments[args[2]]
            if args[:2] == ['get', 'node']:
                pin = next(p for p in n['EXPECTED'].values() if p['node'] == args[2])
                node = copy.deepcopy(self.node)
                node['metadata'] = {'name': pin['node'], 'uid': pin['node_uid']}
                return node
            if args[0] == 'create':
                self.assertEqual(value['kind'], 'Job')
                value['metadata']['uid'] = str(len(jobs))
                jobs.append(value)
                return value
            job = jobs[-1]
            if args[:2] == ['get', 'job']:
                return {'metadata': job['metadata'], 'status': {'succeeded': 1}}
            if args[:2] == ['get', 'pods']:
                spec = copy.deepcopy(job['spec']['template']['spec'])
                spec['nodeName'] = spec['nodeSelector']['kubernetes.io/hostname']
                return {'items': [{'metadata': {'name': 'check-pod', 'uid': 'pod-id', 'ownerReferences': [{'uid': job['metadata']['uid'], 'controller': True}]},
                    'spec': spec, 'status': {'phase': 'Succeeded', 'containerStatuses': [{'imageID': spec['containers'][0]['image'], 'state': {'terminated': {'exitCode': 0}}}]}}]}
            if args[0] == 'logs':
                return json.dumps({'status': 'INSTALLED_COMPAT_IMAGE_ACCEPTED', 'checks': list(range(5)), 'production_database_access': False}).encode()
            raise AssertionError(args)
        n['command'] = command
        with tempfile.TemporaryDirectory() as directory, patch.object(n['os'], 'uname', return_value=SimpleNamespace(nodename='datax-01')), patch('sys.argv', ['check', '--verify-preloaded-images-once']), contextlib.redirect_stdout(io.StringIO()):
            n['CONTROL'] = directory + '/check'
            self.assertEqual(n['main'](), 0)
            self.assertEqual(len(jobs), 2)
            report = json.loads(Path(n['CONTROL'], 'REPORT.json').read_text())
            self.assertFalse(report['production_workload_modified'])
            self.assertFalse(report['ready_for_rollout'])


if __name__ == '__main__':
    unittest.main()
