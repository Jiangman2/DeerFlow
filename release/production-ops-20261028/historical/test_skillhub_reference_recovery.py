import contextlib
import importlib.util
import json
import pathlib
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('recovery', pathlib.Path(__file__).with_name('restore-portal-skillhub-image-references-20261028-r1.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def deployment():
    return {'metadata': {'uid': m.UID, 'generation': 14}, 'spec': {'replicas': 1, 'template': {'spec': {
        'containers': [{'name': 'server', 'image': m.IMAGES[0]['target'], 'imagePullPolicy': 'Never'}],
        'initContainers': [{'name': 'wait-for-dependencies', 'image': m.IMAGES[1]['target'], 'imagePullPolicy': 'Never'}]}}}}


class Checks(unittest.TestCase):
    def test_exact_deployment_required(self):
        d = deployment()
        with patch.object(m, 'EXPECTED_SPEC', m.fingerprint(d)['spec_sha256']):
            m.validate_deployment(d)
            d['metadata']['generation'] = 15
            with self.assertRaises(m.Refused):
                m.validate_deployment(d)

    def test_changed_init_image_rejected(self):
        d = deployment()
        with patch.object(m, 'EXPECTED_SPEC', m.fingerprint(d)['spec_sha256']):
            d['spec']['template']['spec']['initContainers'][0]['image'] = 'unknown'
            with self.assertRaises(m.Refused):
                m.validate_deployment(d)

    def test_metadata_hash_failure(self):
        with patch.object(m, 'command', return_value=b'{}'):
            with self.assertRaisesRegex(m.Refused, 'METADATA_DIGEST_MISMATCH'):
                m.metadata(m.IMAGES[0]['root'])

    def test_image_platform_and_all_layers(self):
        image = m.IMAGES[0]
        descriptor = {'digest': image['root'], 'size': 10}
        with patch.object(m, 'metadata', side_effect=[{'manifests': [dict(descriptor, platform={'os': 'linux', 'architecture': 'arm64'})]}, {'config': descriptor, 'layers': [descriptor, descriptor]}, {'os': 'linux', 'architecture': 'arm64'}]), patch.object(m, 'verify_blob', return_value=10) as verify:
            r = m.verify_content(image)
        self.assertEqual(verify.call_count, 2)
        self.assertEqual(r['index_digest'], image['root'])

    def simulate(self, mode):
        d = deployment()
        images = {i['source']: i['root'] for i in m.IMAGES}
        if mode == 'conflict':
            images[m.IMAGES[0]['target']] = 'sha256:' + '0' * 64
        if mode == 'existing':
            images.update({i['target']: i['root'] for i in m.IMAGES})
        calls = []
        def get(kind, name):
            if kind == 'node':
                return {'metadata': {'uid': m.NODE_UID}, 'status': {'nodeInfo': {'architecture': 'arm64'}}}
            if kind == 'service':
                return {'metadata': {'uid': m.SERVICE_UID}}
            return d
        def verify(image):
            calls.append('verify:' + image['role'])
            if mode == 'bad_second' and image['role'] == 'wait-for-dependencies':
                raise m.Refused('BAD_LAYER')
            return {'status': 'PASS'}
        def command(args):
            self.assertEqual(args[:5], m.C + ['images', 'tag'])
            image = next(i for i in m.IMAGES if i['target'] == args[-1])
            self.assertIn('verify:server', calls)
            self.assertIn('verify:wait-for-dependencies', calls)
            calls.append('tag:' + image['role'])
            images[image['target']] = image['root']
            return b''
        with tempfile.TemporaryDirectory() as temp, contextlib.ExitStack() as stack:
            for obj, key, value in ((m, 'REPORT_DIR', pathlib.Path(temp) / 'receipt'), (m, 'EXPECTED_SPEC', m.fingerprint(d)['spec_sha256'])):
                stack.enter_context(patch.object(obj, key, value))
            stack.enter_context(patch.object(m.socket, 'gethostname', return_value='datax-01'))
            stack.enter_context(patch.object(m.os, 'geteuid', return_value=0, create=True))
            stack.enter_context(patch.object(m, 'get', side_effect=get))
            stack.enter_context(patch.object(m, 'active_pods', return_value=[]))
            stack.enter_context(patch.object(m, 'refs', side_effect=lambda: dict(images)))
            stack.enter_context(patch.object(m, 'verify_content', side_effect=verify))
            stack.enter_context(patch.object(m, 'command', side_effect=command))
            stack.enter_context(patch.object(m, 'observe', return_value={'pod': 'accepted'}))
            out = {}
            if mode in ('conflict', 'bad_second'):
                with self.assertRaises(m.Refused):
                    m.main(out)
                self.assertFalse(any(c.startswith('tag:') for c in calls))
                self.assertFalse(m.REPORT_DIR.exists())
            else:
                m.main(out)
                self.assertTrue(out['other_deployment_specs_preserved'])
                self.assertEqual([c for c in calls if c.startswith('tag:')], [] if mode == 'existing' else ['tag:server', 'tag:wait-for-dependencies'])

    def test_both_images_verified_before_server_then_init_tags(self):
        self.simulate('success')

    def test_second_image_failure_prevents_all_tags(self):
        self.simulate('bad_second')

    def test_conflicting_target_not_overwritten(self):
        self.simulate('conflict')

    def test_existing_correct_references_not_mutated(self):
        self.simulate('existing')

    def test_wrong_host_before_commands(self):
        with patch.object(m.socket, 'gethostname', return_value='wrong'), patch.object(m, 'get') as get:
            with self.assertRaises(m.Refused):
                m.main({})
            get.assert_not_called()

    def observe_fixture(self, bad_init=False):
        d = deployment()
        d['status'] = {'readyReplicas': 1}
        content = {i['role']: {'index_digest': i['root'], 'manifest_digest': 'sha256:' + '1' * 64, 'config_digest': 'sha256:' + '2' * 64} for i in m.IMAGES}
        p = {'metadata': {'name': 'skillhub-server-pending', 'uid': m.POD_UID}, 'status': {
            'containerStatuses': [{'name': 'server', 'ready': True, 'state': {'running': {'startedAt': '2026-10-28T01:20:00Z'}}, 'imageID': 'docker.io/library/import@' + m.IMAGES[0]['root']}],
            'initContainerStatuses': [{'name': 'wait-for-dependencies', 'state': {'terminated': {'exitCode': 0}}, 'imageID': 'wrong-image' if bad_init else 'docker.io/library/import@' + m.IMAGES[1]['root']}]}}
        endpoints = {'items': [{'endpoints': [{'targetRef': {'uid': m.POD_UID}, 'conditions': {'ready': True}}]}]}
        with patch.object(m, 'get', side_effect=lambda kind, name: {'metadata': {'uid': m.SERVICE_UID}} if kind == 'service' else d), patch.object(m, 'active_pods', return_value=[p]), patch.object(m, 'command', return_value=json.dumps(endpoints).encode()):
            if bad_init:
                with self.assertRaisesRegex(m.Refused, 'RUNNING_IMAGE_ID_MISMATCH'):
                    m.observe(m.fingerprint(d), content)
            else:
                self.assertEqual(m.observe(m.fingerprint(d), content)['uid'], m.POD_UID)

    def test_acceptance_requires_both_images_and_ready_endpoint(self):
        self.observe_fixture()

    def test_wrong_init_image_not_accepted(self):
        self.observe_fixture(bad_init=True)


if __name__ == '__main__':
    unittest.main()
