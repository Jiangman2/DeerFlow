import contextlib
import io
import json
import unittest
from unittest.mock import patch

import audit_build_inputs as audit


def objects():
    deployment = {'metadata': {'uid': audit.DEP_UID, 'generation': 4},
                  'spec': {'replicas': 1, 'private_env': 'DO_NOT_PRINT'}}
    pod = {'metadata': {'uid': audit.POD_UID},
           'spec': {'nodeName': 'datax-03', 'containers': [{'name': 'app', 'image': audit.BASE_IMAGE}]},
           'status': {'containerStatuses': [{'name': 'app', 'ready': True, 'imageID': audit.BASE_CONFIG,
                                             'state': {'running': {}}, 'containerID': 'containerd://test', 'restartCount': 0}]}}
    return deployment, pod


class InventoryTest(unittest.TestCase):
    def test_baseline_redacts_spec_and_preserves_identity(self):
        with patch.object(audit, 'kube', side_effect=[json.dumps(x).encode() for x in objects()]):
            value = audit.baseline()
        self.assertEqual(value['pod_uid'], audit.POD_UID)
        self.assertNotIn('DO_NOT_PRINT', json.dumps(value))
        self.assertEqual(value['spec_sha256'], audit.digest(objects()[0]['spec']))

    def test_baseline_rejects_drift(self):
        for key in ('uid', 'image', 'ready', 'replicas'):
            with self.subTest(key=key):
                d, p = objects()
                if key == 'uid': p['metadata']['uid'] = 'different'
                if key == 'image': p['spec']['containers'][0]['image'] = 'different'
                if key == 'ready': p['status']['containerStatuses'][0]['ready'] = False
                if key == 'replicas': d['spec']['replicas'] = 2
                with patch.object(audit, 'kube', side_effect=[json.dumps(x).encode() for x in (d, p)]):
                    with self.assertRaises(audit.CheckFailure): audit.baseline()

    def test_no_kubernetes_mutation_verbs(self):
        with patch.object(audit, 'run') as run:
            for verb in ('apply', 'patch', 'delete', 'scale', 'create', 'replace'):
                with self.assertRaises(audit.CheckFailure): audit.kube([verb])
            run.assert_not_called()

    def test_complete_is_not_build_or_release_permission(self):
        calls = []
        def run(argv, **kwargs):
            calls.append(argv)
            if argv[0] == 'ctr': return (audit.BASE_IMAGE+'\n'+audit.BUILDER_IMAGE+'\nprivate/other\n').encode()
            return json.dumps({'status': {'id': audit.BASE_CONFIG, 'size': 123,
                                          'repoDigests': ['private/other@sha256:secret'],
                                          'environment': ['API_KEY=DO_NOT_PRINT']}}).encode()
        output = io.StringIO()
        with patch.object(audit.os, 'geteuid', return_value=0, create=True), \
             patch.object(audit.socket, 'gethostname', return_value='datax-01'), \
             patch.object(audit, 'baseline', return_value={'pod_uid': audit.POD_UID}), \
             patch.object(audit, 'kube', return_value=json.dumps({'sha256': audit.BASE_BINARY, 'process_stable': True}).encode()), \
             patch.object(audit, 'storage', return_value={}), \
             patch.object(audit.shutil, 'which', return_value='/usr/bin/tool'), \
             patch.object(audit, 'run', side_effect=run), contextlib.redirect_stdout(output):
            audit.collect()
        text = output.getvalue()
        self.assertNotIn('DO_NOT_PRINT', text)
        self.assertNotIn('private/other', text)
        result = json.loads(text.splitlines()[-1])
        self.assertFalse(result['ready_for_build'])
        self.assertFalse(result['ready_for_rollout'])
        self.assertFalse(result['build_started'])
        self.assertEqual(result['provider_requests'], 0)
        self.assertEqual(calls[0], ['ctr', '-n', 'k8s.io', 'images', 'list', '-q'])

    def test_wrong_host_fails_before_external_command(self):
        with patch.object(audit.os, 'geteuid', return_value=0, create=True), \
             patch.object(audit.socket, 'gethostname', return_value='datax-02'), \
             patch.object(audit, 'baseline') as baseline:
            with self.assertRaises(audit.CheckFailure): audit.collect()
            baseline.assert_not_called()

    def test_remote_binary_probe_has_no_document_or_environ_read(self):
        for forbidden in ('/proc/1/environ', 'os.kill', 'requests.', 'urllib', 'API_KEY'):
            self.assertNotIn(forbidden, audit.REMOTE_BINARY)
        compile(audit.REMOTE_BINARY, '<remote_probe>', 'exec')


if __name__ == '__main__':
    unittest.main()
