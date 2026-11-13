import ast
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'deployment/qualify-energy-market-daily-portable-20261111-r1.py'
spec = importlib.util.spec_from_file_location('portable_qualify', SCRIPT)
host = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host)


class QualificationTests(unittest.TestCase):
    def test_host_python37_syntax(self):
        ast.parse(SCRIPT.read_text(encoding='utf-8'), feature_version=(3, 7))

    def test_wrong_host_no_commands_no_claim(self):
        output = io.StringIO()
        with patch.object(host.os, 'name', 'nt'), patch.object(host.Operation, 'execute', side_effect=AssertionError('must not run')):
            with contextlib.redirect_stdout(output):
                self.assertEqual(host.main(['--qualify-portable-daily-once']), 1)
        data = json.loads(output.getvalue())
        self.assertFalse(data['once_claimed'])
        self.assertEqual(data['commands'], 0)
        self.assertEqual(data['error_code'], 'DATAX01_ROOT_REQUIRED')

    def test_no_existing_deployment_or_secret_mutations(self):
        op = host.Operation()
        op.pod_uid = 'synthetic-owned-uid'
        for args in (['patch', 'deployment', 'gateway'], ['get', 'secret', 'deer-flow-env', '-o', 'json'], ['delete', 'pod', 'business-pod'],
                     ['exec', '-i', op.name, '-c', 'qualification', '--', 'sh', '-c', 'env'], ['get', 'deployments', '--raw', '/api/v1/secrets']):
            with self.subTest(args=args):
                with patch.object(host.subprocess, 'Popen', side_effect=AssertionError('must reject first')):
                    with self.assertRaises(host.Refused):
                        op.command(args)

    def test_delete_uid_mismatch_refused(self):
        op = host.Operation()
        op.pod_uid = 'synthetic-owned-uid'
        args = ['delete', '--raw', '/api/v1/namespaces/deer-flow/pods/' + op.name, '-f', '-']
        body = host.canonical({'apiVersion': 'v1', 'kind': 'DeleteOptions', 'preconditions': {'uid': 'other'}, 'gracePeriodSeconds': 5})
        with self.assertRaises(host.Refused):
            op.command(args, body)

    def test_only_cached_image_and_isolated_storage(self):
        pod = host.manifest('operation', 'pod')['spec']
        container = pod['containers'][0]
        self.assertEqual(container['imagePullPolicy'], 'Never')
        self.assertFalse(pod['automountServiceAccountToken'])
        self.assertNotIn('hostNetwork', pod)
        self.assertNotIn('serviceAccountName', pod)
        self.assertTrue(all('emptyDir' in volume for volume in pod['volumes']))
        self.assertTrue(container['securityContext']['readOnlyRootFilesystem'])
        self.assertEqual(container['securityContext']['capabilities'], {'drop': ['ALL']})

    def test_baseline_state_drift_refused(self):
        op = host.Operation()
        with patch.object(op, 'command', return_value={'items': []}):
            with self.assertRaisesRegex(host.Refused, 'DEPLOYMENT_SET_CHANGED'):
                op.baseline()


if __name__ == '__main__':
    unittest.main()
