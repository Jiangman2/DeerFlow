"""Offline host-wrapper tests; no Kubernetes or provider requests."""
import contextlib
import copy
import importlib.util
import io
import json
import pathlib
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parent


class CandidateHostTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('packager', ROOT / 'package-oa-v2-candidate.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        cls.source, cls.payload = module.build()

    def execute(self, fault=None):
        image = 'deerflow.local/deer-flow-gateway@sha256:eeeb190145dd6677e0da372bbdf36ab207a6826d89960eca2ed49de15d40dcc5'
        dep = {'metadata': {'uid': 'a62fc04c-7a82-4b57-8fbf-a8c7a3839ea1', 'generation': 382},
               'spec': {'replicas': 70, 'template': {'spec': {'containers': [{'name': 'gateway', 'image': image}]}}},
               'status': {'observedGeneration': 382, 'replicas': 70, 'updatedReplicas': 70, 'readyReplicas': 70, 'availableReplicas': 70}}
        pod = {'metadata': {'uid': 'pod-id', 'name': 'gateway-test'}, 'spec': {'containers': [{'name': 'gateway', 'image': image}]},
               'status': {'containerStatuses': [{'name': 'gateway', 'ready': True, 'containerID': 'containerd://original'}]}}
        after = copy.deepcopy(pod)
        current = copy.deepcopy(dep)
        observation = {'status': 'CANDIDATE_OFFLINE_RUNTIME_ACCEPTED'}
        if fault == 'rollout':
            dep['status']['updatedReplicas'] = 69
        if fault == 'image':
            dep['spec']['template']['spec']['containers'][0]['image'] = 'unexpected'
        if fault == 'pod':
            after['metadata']['uid'] = 'replacement'
        if fault == 'container':
            after['status']['containerStatuses'][0]['containerID'] = 'containerd://replacement'
        if fault == 'deployment':
            current['metadata']['generation'] = 383
        if fault == 'candidate':
            observation = {'status': 'INCOMPLETE', 'error_class': 'AssertionError'}
        responses = [dep, {'items': [pod]}, observation, after, current]
        calls = []

        def run(args, **kwargs):
            calls.append(args)
            self.assertFalse(any(v in args for v in ('patch', 'apply', 'delete', 'create')))
            return SimpleNamespace(returncode=0, stdout=json.dumps(responses.pop(0)).encode(), stderr=b'')

        namespace = {'__name__': 'candidate_test'}
        exec(compile(self.source, '<candidate-host>', 'exec'), namespace)
        output = io.StringIO()
        with patch.object(sys, 'argv', ['candidate', '--verify-candidate-offline']), patch('subprocess.run', run), contextlib.redirect_stdout(output):
            try:
                namespace['main']()
                failed = False
            except (AssertionError, SystemExit):
                failed = True
        return failed, output.getvalue(), calls

    def test_complete_readonly_probe(self):
        failed, output, calls = self.execute()
        self.assertFalse(failed)
        result = json.loads(output)
        self.assertFalse(result['ready_for_rollout'])
        self.assertEqual(len(calls), 5)

    def test_rollout_or_identity_drift_refused(self):
        for fault in ('rollout', 'image', 'pod', 'container', 'deployment'):
            with self.subTest(fault=fault):
                self.assertTrue(self.execute(fault)[0])

    def test_candidate_failure_is_visible_and_nonzero(self):
        failed, output, _ = self.execute('candidate')
        self.assertTrue(failed)
        self.assertEqual(json.loads(output)['observation']['error_class'], 'AssertionError')


if __name__ == '__main__':
    unittest.main()
