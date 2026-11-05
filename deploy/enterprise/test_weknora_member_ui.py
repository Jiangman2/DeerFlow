"""Exercise release refusal boundaries with a synthetic Kubernetes transport."""
import contextlib
import copy
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent


def module():
    spec = importlib.util.spec_from_file_location('member_ui_release', HERE / 'activate-weknora-member-ui.py')
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


class Cluster:
    def __init__(self, m):
        self.m = m
        mounts = [{'name': m.RUNTIME_VOLUME, 'mountPath': '/patched-ui'},
                  {'name': 'weknora-v08-preview-patch', 'mountPath': '/opt/deerflow-weknora-patches', 'readOnly': True}]
        self.before = {'metadata': {'uid': m.UID, 'generation': 38, 'resourceVersion': 'base-rv'},
            'spec': {'replicas': 1, 'selector': {'matchLabels': {'app': 'weknora-frontend'}},
                'template': {'metadata': {'labels': {'app': 'weknora-frontend'}}, 'spec': {
                    'containers': [{'name': 'frontend', 'image': m.IMAGE, 'env': [{'name': 'KEEP_SETTING', 'value': 'original'}]}],
                    'initContainers': [{'name': 'patch-legacy-office-preview', 'image': m.IMAGE,
                        'command': ['/bin/sh', '/opt/deerflow-weknora-patches/patch-legacy-office-preview.sh'],
                        'volumeMounts': mounts}],
                    'volumes': [{'name': m.RUNTIME_VOLUME, 'emptyDir': {}},
                        {'name': 'weknora-v08-preview-patch', 'configMap': {'name': m.OLD_CM, 'defaultMode': 420}}]}}},
            'status': {'observedGeneration': 38, 'replicas': 1, 'updatedReplicas': 1,
                       'readyReplicas': 1, 'availableReplicas': 1}}
        m.SPEC = m.digest(self.before['spec'])
        self.current = copy.deepcopy(self.before)
        self.calls = []
        self.commands = []
        self.mutations = []
        self.fault = None
        self.cm_reads = 0
        self.image_id = 'containerd://sha256:' + 'a' * 64
        self.payload = {'files': {'install.sh': 'set -eu\n', 'verify.sh': 'echo test\n'},
            'baseline_hashes': {'index.html': 'a' * 64}, 'final_hashes': {'index.html': 'b' * 64},
            'payload_sha256': 'c' * 64, 'job_expected': {'status': 'TEST_ACCEPTED'}}
        self.job = None

    def pod(self):
        new = self.current['metadata']['generation'] == 39
        suffix = 'new' if new else 'old'
        return {'metadata': {'name': 'ui-' + suffix, 'uid': 'pod-' + suffix,
                'ownerReferences': [{'kind': 'ReplicaSet', 'name': 'ui-rs', 'uid': 'rs-uid', 'controller': True}]},
            'spec': {'nodeName': 'test-node', 'containers': [{'name': 'frontend', 'image': self.m.IMAGE}]},
            'status': {'phase': 'Running', 'containerStatuses': [{'name': 'frontend', 'ready': True,
                'imageID': self.image_id, 'containerID': 'container-' + suffix, 'restartCount': 0}]}}

    def get(self, kind, name=None, extra=None):
        self.calls.append((kind, name, extra))
        if kind == 'deployment':
            if name == self.m.DEPLOYMENT:
                d = copy.deepcopy(self.current)
                if self.fault == 'baseline' and not self.mutations:
                    d['spec']['replicas'] = 2
                if self.fault == 'rollout_drift' and any(x == 'patch' for x in self.mutations):
                    d['spec']['replicas'] = 2
                return d
            return {'metadata': {'uid': name + '-uid', 'generation': 1}, 'spec': {'replicas': 1}}
        if kind == 'pods' and extra and 'job-name=' in extra[-1]:
            state = {'state': {'terminated': {'exitCode': 0}}, 'imageID': self.image_id}
            return {'items': [{'metadata': {'name': 'test-job-pod', 'uid': 'job-pod-uid',
                'ownerReferences': [{'uid': 'job-uid', 'controller': True}]},
                'status': {'phase': 'Succeeded', 'initContainerStatuses': [state, state], 'containerStatuses': [state]}}]}
        if kind == 'pods':
            return {'items': [self.pod()]}
        if kind == 'replicaset':
            return {'metadata': {'uid': 'rs-uid', 'ownerReferences': [{'controller': True, 'kind': 'Deployment', 'uid': self.m.UID}]}}
        if kind == 'node':
            return {'spec': {}, 'status': {'conditions': [{'type': 'Ready', 'status': 'True'}]}}
        if kind == 'configmap':
            self.cm_reads += 1
            value = 'changed' if self.fault == 'preview_drift' and self.cm_reads >= 2 else 'original'
            return {'metadata': {'uid': 'old-cm-uid'}, 'data': {'script.sh': value}}
        if kind == 'job':
            condition = 'Failed' if self.fault == 'job_failed' else 'Complete'
            return {'metadata': {'uid': 'job-uid'}, 'status': {'conditions': [{'type': condition, 'status': 'True'}]}}
        raise AssertionError('Unexpected GET ' + kind)

    def invoke(self, args, body=None, **kwargs):
        self.commands.append(args)
        if args[0] == 'exec':
            if args[-1].startswith('wget '):
                return b'{"status":"MEMBER_UI_PATCH_INSTALLED","version":"members-r1","module_count":59}'
            after = self.current['metadata']['generation'] == 39
            hashes = self.payload['final_hashes'] if after else self.payload['baseline_hashes']
            if self.fault == 'served_asset':
                hashes = {'index.html': '0' * 64}
            return ''.join(v + '  ' + k + '\n' for k, v in hashes.items()).encode()
        if args[0] == 'logs':
            return json.dumps(self.payload['job_expected']).encode()
        if args[0] == 'create':
            self.mutations.append(body['kind'])
            obj = copy.deepcopy(body)
            obj['metadata']['uid'] = 'job-uid' if body['kind'] == 'Job' else 'candidate-cm-uid'
            return json.dumps(obj).encode()
        if args[0] == 'patch':
            self.assert_patch_guards(body)
            planned = copy.deepcopy(self.current)
            spec = planned['spec']['template']['spec']
            spec['volumes'].append(copy.deepcopy(body[3]['value']))
            spec['initContainers'].append(copy.deepcopy(body[4]['value']))
            spec['initContainers'][-1].update(terminationMessagePath='/dev/termination-log', terminationMessagePolicy='File')
            planned['metadata']['generation'] = 39
            planned['metadata']['resourceVersion'] = 'new-rv'
            planned['status']['observedGeneration'] = 39
            if '--dry-run=server' in args:
                if self.fault == 'admission_changes':
                    spec['containers'][0]['env'][0]['value'] = 'unwanted'
                return json.dumps(planned).encode()
            self.mutations.append('patch')
            self.current = planned
            if self.fault == 'lost_patch_reply':
                raise subprocess.TimeoutExpired(args, 35)
            return json.dumps(planned).encode()
        raise AssertionError('Unexpected command ' + repr(args))

    def assert_patch_guards(self, body):
        assert [x['path'] for x in body[:3]] == ['/metadata/uid', '/metadata/resourceVersion', '/spec']
        assert all(x['op'] == 'test' for x in body[:3])
        assert body[2]['value'] == self.before['spec']
        assert len(body) == 5


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.m = module()
        self.c = Cluster(self.m)
        self.temp = tempfile.TemporaryDirectory()
        self.m.CONTROL = Path(self.temp.name) / 'release'
        self.m.get, self.m.invoke = self.c.get, self.c.invoke
        self.report = {'once_claimed': False, 'exitcode': 1}
        self.snapshots = []

    def tearDown(self):
        self.temp.cleanup()

    def run_release(self):
        self.m.run_release(self.report, self.c.payload, lambda: self.snapshots.append(copy.deepcopy(self.report)))

    def test_success_preserves_images_settings_and_unrelated_deployments(self):
        self.run_release()
        self.assertEqual(self.c.mutations, ['ConfigMap', 'Job', 'patch'])
        self.assertEqual(self.report['exitcode'], 0)
        self.assertTrue(self.report['old_image_preserved'])
        self.assertTrue(self.report['other_deployments_preserved'])
        self.assertFalse(any(x[0] == 'secret' for x in self.c.calls))
        self.assertEqual(sum('--dry-run=server' in x for x in self.c.commands), 1)

    def test_changed_deployment_refuses_before_creating_resources(self):
        self.c.fault = 'baseline'
        with self.assertRaisesRegex(self.m.Refused, 'FRONTEND_BASELINE_CHANGED'):
            self.run_release()
        self.assertEqual(self.c.mutations, [])
        self.assertFalse(self.m.CONTROL.exists())

    def test_served_asset_mismatch_refuses_before_any_write(self):
        self.c.fault = 'served_asset'
        with self.assertRaisesRegex(self.m.Refused, 'SERVED_ASSET_HASH_CHANGED'):
            self.run_release()
        self.assertEqual(self.c.mutations, [])

    def test_failed_candidate_job_never_rolls_frontend(self):
        self.c.fault = 'job_failed'
        with self.assertRaisesRegex(self.m.Refused, 'CANDIDATE_JOB_FAILED'):
            self.run_release()
        self.assertEqual(self.c.mutations, ['ConfigMap', 'Job'])

    def test_preview_script_changed_during_validation_refuses_rollout(self):
        self.c.fault = 'preview_drift'
        with self.assertRaisesRegex(self.m.Refused, 'PREVIEW_CONFIGMAP_CHANGED'):
            self.run_release()
        self.assertEqual(self.c.mutations, ['ConfigMap', 'Job'])

    def test_admission_cannot_silently_modify_existing_configuration(self):
        self.c.fault = 'admission_changes'
        with self.assertRaisesRegex(self.m.Refused, 'UNRELATED_FRONTEND_SPEC_CHANGED'):
            self.run_release()
        self.assertEqual(self.c.mutations, ['ConfigMap', 'Job'])

    def test_lost_patch_acknowledgement_is_not_replayed(self):
        self.c.fault = 'lost_patch_reply'
        with self.assertRaises(subprocess.TimeoutExpired):
            self.run_release()
        self.assertEqual(self.c.mutations.count('patch'), 1)
        self.assertTrue(self.report['patch_may_have_applied'])
        self.assertEqual(self.c.current['metadata']['generation'], 39)

    def test_concurrent_change_after_patch_is_reported_without_rollback(self):
        self.c.fault = 'rollout_drift'
        with self.assertRaisesRegex(self.m.Refused, 'FRONTEND_CHANGED_DURING_ROLLOUT'):
            self.run_release()
        self.assertEqual(self.c.mutations.count('patch'), 1)

    def test_candidate_job_cannot_consume_service_account_or_application_environment(self):
        job = self.m.build_job(self.c.before, self.c.pod(), 'new-cm', 'test-job', self.c.payload['files'])
        spec = job['spec']['template']['spec']
        self.assertFalse(spec['automountServiceAccountToken'])
        self.assertEqual(spec['nodeName'], 'test-node')
        self.assertEqual(job['spec']['backoffLimit'], 0)
        for container in spec['initContainers'] + spec['containers']:
            self.assertEqual(container['imagePullPolicy'], 'Never')
            self.assertNotIn('envFrom', container)
            self.assertNotIn('env', container)

    def test_existing_once_directory_and_report_are_never_overwritten(self):
        self.m.CONTROL.mkdir()
        receipt = self.m.CONTROL / 'REPORT.json'
        receipt.write_bytes(b'keep-original-receipt')
        with patch.object(self.m.os, 'geteuid', return_value=0, create=True), \
             patch.object(self.m.socket, 'gethostname', return_value='datax-01'), \
             patch.object(sys, 'argv', ['tool', '--apply-member-ui-fix-once']), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(self.m.main(), 1)
        self.assertIn('ONCE_DIRECTORY_EXISTS_DO_NOT_RERUN', output.getvalue())
        self.assertEqual(receipt.read_bytes(), b'keep-original-receipt')
        self.assertEqual(self.c.mutations, [])


if __name__ == '__main__':
    unittest.main()
