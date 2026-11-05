"""Isolated wrapper qualification; all Kubernetes calls are fixtures."""
import copy
from contextlib import ExitStack, redirect_stdout
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


SCRIPT = Path(sys.argv.pop(1)).resolve()
spec = importlib.util.spec_from_file_location('company_image_host', SCRIPT)
host = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host)
PIN = host.EXPECTED['gateway']
NODE = {'metadata': {'name': PIN['node'], 'uid': PIN['node_uid']},
        'status': {'conditions': [{'type': 'Ready', 'status': 'True'}, {'type': 'DiskPressure', 'status': 'False'}]},
        'spec': {'taints': [{'key': 'dedicated', 'value': 'gateway', 'effect': 'NoSchedule'}]}}


class Qualification(unittest.TestCase):
    def test_job_uses_only_candidate_image_and_scratch(self):
        job = host.job_spec('synthetic-check', PIN, NODE)
        pod = job['spec']['template']['spec']
        c = pod['containers'][0]
        self.assertEqual(c['image'], PIN['candidate_image'])
        self.assertEqual(c['imagePullPolicy'], 'Never')
        self.assertEqual(c['command'][:3], ['/app/backend/.venv/bin/python', '-I', '-B'])
        self.assertEqual(json.loads(c['command'][5]), PIN['sources'])
        self.assertNotIn('envFrom', c)
        self.assertEqual(c['env'], [{'name': 'HOME', 'value': '/tmp'}, {'name': 'TMPDIR', 'value': '/tmp'}])
        self.assertEqual(pod['volumes'], [{'name': 'scratch', 'emptyDir': {'sizeLimit': '64Mi'}}])
        self.assertFalse(pod['automountServiceAccountToken'])
        self.assertFalse(pod['enableServiceLinks'])
        self.assertEqual(job['spec']['backoffLimit'], 0)
        self.assertEqual(pod['tolerations'], [dict(key='dedicated', value='gateway', effect='NoSchedule', operator='Equal')])
        self.assertTrue(c['securityContext']['readOnlyRootFilesystem'])

    def test_replaced_node_refused(self):
        node = copy.deepcopy(NODE)
        node['metadata']['uid'] = 'recreated'
        with self.assertRaises(host.Refused):
            host.job_spec('synthetic-check', PIN, node)

    def test_node_pressure_and_unexpected_taint_refused(self):
        for changed in ('pressure', 'cordon', 'taint'):
            node = copy.deepcopy(NODE)
            if changed == 'pressure':
                node['status']['conditions'][1]['status'] = 'True'
            elif changed == 'cordon':
                node['spec']['unschedulable'] = True
            else:
                node['spec']['taints'].append({'key': 'maintenance', 'effect': 'NoSchedule'})
            with self.assertRaises(host.Refused):
                host.job_spec('synthetic-check', PIN, node)

    def simulate(self, failure=None):
        expected = copy.deepcopy(host.EXPECTED)
        deployments = {}
        for name, pin in expected.items():
            spec = {'replicas': 70 if name == 'gateway' else 1}
            pin['spec_sha256'] = host.digest(spec)
            deployments[name] = {'metadata': {'uid': pin['uid'], 'generation': pin['generation']}, 'spec': spec,
                'status': dict(observedGeneration=pin['generation'], replicas=spec['replicas'], updatedReplicas=spec['replicas'],
                               readyReplicas=spec['replicas'], availableReplicas=spec['replicas'])}
        child_scope = {'__name__': 'synthetic_child'}
        exec(compile(host.CHILD, '<child>', 'exec'), child_scope)
        observation = dict(status='INSTALLED_WEKNORA_COMPANY_IMAGE_ACCEPTED', checks=child_scope['CHECKS'],
                           production_database_access=False, provider_requests=0, real_http_requests=0,
                           membership_grants=0, production_configuration_used=False)
        calls, created = [], []
        gateway_reads = 0

        def command(args, value=None, raw=False):
            nonlocal gateway_reads
            calls.append(args)
            if args[:2] == ['get', 'deployment']:
                result = copy.deepcopy(deployments[args[2]])
                if args[2] == 'gateway':
                    gateway_reads += 1
                    if failure == 'before' or (failure == 'after' and gateway_reads > 1):
                        result['metadata']['generation'] += 1
                return result
            if args[:2] == ['get', 'node']:
                return NODE
            if args[0] == 'create':
                created.append(value)
                if failure == 'create-uncertain':
                    raise TimeoutError()
                return {'metadata': {'name': value['metadata']['name'], 'uid': 'synthetic-job-uid'}}
            if args[:2] == ['get', 'job']:
                return {'metadata': {'uid': 'synthetic-job-uid'}, 'status': {'succeeded': 1}}
            if args[:2] == ['get', 'pods']:
                return {'items': [{'metadata': {'name': 'synthetic-pod', 'uid': 'synthetic-pod-uid',
                    'ownerReferences': [{'uid': 'wrong' if failure == 'owner' else 'synthetic-job-uid', 'controller': True}]},
                    'spec': {'nodeName': PIN['node'], 'containers': [{'image': PIN['candidate_image']}]},
                    'status': {'phase': 'Succeeded', 'containerStatuses': [{'imageID': 'wrong' if failure == 'image' else PIN['candidate_image'],
                        'state': {'terminated': {'exitCode': 1 if failure == 'exit' else 0}}}]}}]}
            if args[0] == 'logs':
                result = dict(observation)
                if failure == 'child':
                    result['checks'] = []
                return json.dumps(result).encode()
            raise AssertionError('UNEXPECTED_COMMAND')

        with tempfile.TemporaryDirectory() as temporary, ExitStack() as mocks:
            mocks.enter_context(patch.object(host, 'CONTROL', str(Path(temporary) / 'once')))
            mocks.enter_context(patch.object(host, 'EXPECTED', expected))
            mocks.enter_context(patch.object(host, 'accepted_field_reports', lambda: None))
            mocks.enter_context(patch.object(host, 'command', command))
            mocks.enter_context(patch.object(host.os, 'geteuid', lambda: 0, create=True))
            mocks.enter_context(patch.object(host.os, 'uname', lambda: SimpleNamespace(nodename='datax-01'), create=True))
            mocks.enter_context(patch.object(sys, 'argv', ['checker', '--verify-preloaded-images-once']))
            with redirect_stdout(io.StringIO()):
                code = host.main()
            report = json.loads((Path(host.CONTROL) / 'REPORT.json').read_text())
        self.assertTrue(all(a[0] in ('get', 'create', 'logs') for a in calls))
        self.assertLessEqual(len(created), 1)
        self.assertEqual(code, 0 if failure is None else 1)
        self.assertEqual(report['production_workload_modified'], False)
        if failure == 'before':
            self.assertFalse(created)
        if failure == 'create-uncertain':
            self.assertEqual(len(report['temporary_jobs_created']), 1)
        return report

    def test_success_one_job_only(self):
        result = self.simulate()
        self.assertEqual(result['status'], 'WEKNORA_COMPANY_IMAGE_ACCEPTED_NOT_DEPLOYED')
        self.assertEqual(len(result['checks']), 1)

    def test_changed_baseline_never_creates_job(self):
        self.simulate('before')

    def test_changed_final_baseline_refuses_acceptance(self):
        self.simulate('after')

    def test_uncertain_create_is_never_repeated(self):
        self.simulate('create-uncertain')

    def test_replaced_job_pod_owner_refused(self):
        self.simulate('owner')

    def test_wrong_runtime_image_refused(self):
        self.simulate('image')

    def test_nonzero_exit_refused(self):
        self.simulate('exit')

    def test_incomplete_child_refused(self):
        self.simulate('child')

    @unittest.skipUnless(hasattr(host.os, 'O_NOFOLLOW') and hasattr(host.os, 'geteuid') and host.os.geteuid() == 0, 'Linux root receipt fixture')
    def test_protected_report_hash_and_symlink_rejection(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory) / 'report.json'
            raw = b'{"status":"ACCEPTED"}'
            p.write_bytes(raw)
            p.chmod(0o600)
            prior = {'report_path': str(p), 'report_bytes': len(raw), 'report_sha256': hashlib.sha256(raw).hexdigest(), 'status': 'ACCEPTED'}
            with patch.object(host, 'PRIORS', [prior]):
                host.accepted_field_reports()
                p.write_bytes(raw.replace(b'ACCEPTED', b'REJECTED'))
                with self.assertRaises(host.Refused):
                    host.accepted_field_reports()
                p.unlink()
                other = Path(directory) / 'other.json'
                other.write_bytes(raw)
                p.symlink_to(other)
                with self.assertRaises(OSError):
                    host.accepted_field_reports()


if __name__ == '__main__':
    unittest.main()
