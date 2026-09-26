import copy
import hashlib
import importlib.util
import json
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('live_delta_audit', ROOT / 'tools/audit_live_delta.py')
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


class CompareTests(unittest.TestCase):
    def test_matches_and_already_applied_without_modifying_input(self):
        entries = [{'path': 'a.py', 'edits': [{'before': 'VALUE = 1', 'after': 'VALUE = 2'}]}]
        for text in ('VALUE = 1\n', 'VALUE = 2\n'):
            sources = {'a.py': {'source': text}}
            original = copy.deepcopy(sources)
            self.assertEqual(audit.compare(entries, sources, 'gateway')[0]['status'], 'MATCH')
            self.assertEqual(sources, original)

    def test_reports_every_mismatch_not_just_first(self):
        entries = [{'path': name, 'edits': [{'before': 'VALUE = 1', 'after': 'VALUE = 2'},
                                           {'before': 'SECOND = 1', 'after': 'SECOND = 2'}]}
                   for name in ('a.py', 'b.py')]
        results = audit.compare(entries, {name: {'source': 'VALUE = 3\n'} for name in ('a.py', 'b.py')}, 'gateway')
        self.assertEqual([x['status'] for x in results], ['MISMATCH', 'MISMATCH'])
        self.assertTrue(all(len(x['edits']) == 2 for x in results))

    def test_context_audit_does_not_execute_source(self):
        entries = [{'path': 'a.py', 'edits': [{'before': 'VALUE = 1', 'after': 'VALUE = 2'}]}]
        source = 'raise RuntimeError("must never execute")\nVALUE = 1\n'
        self.assertEqual(audit.compare(entries, {'a.py': {'source': source}}, 'gateway')[0]['status'], 'MATCH')

    def test_read_failure_and_ambiguity_fail_closed(self):
        entries = [{'path': 'a.py', 'edits': [{'before': 'VALUE = 1', 'after': 'VALUE = 2'}]}]
        self.assertEqual(audit.compare(entries, {'a.py': {'error': 'missing'}}, 'gateway')[0]['status'], 'READ_FAILED')
        self.assertEqual(audit.compare(entries, {'a.py': {'source': 'VALUE = 1\nVALUE = 1\n'}}, 'gateway')[0]['status'], 'MISMATCH')
        with self.assertRaisesRegex(ValueError, 'scope'):
            audit.compare(entries, {'unrequested.py': {'source': 'secret'}}, 'gateway')


class OwnershipTests(unittest.TestCase):
    def setUp(self):
        self.deployment = {'metadata': {'uid': 'd', 'generation': 2},
                           'spec': {'replicas': 1, 'template': {'spec': {'containers': [
                               {'name': 'gateway', 'image': audit.IMAGES['gateway']} ]}}},
                           'status': {'observedGeneration': 2, 'replicas': 1, 'readyReplicas': 1, 'updatedReplicas': 1}}
        self.rs = {'items': [{'metadata': {'uid': 'rs', 'ownerReferences': [
            {'kind': 'Deployment', 'uid': 'd', 'controller': True}]}}]}
        self.pod = {'metadata': {'name': 'gateway-ok', 'ownerReferences': [
            {'kind': 'ReplicaSet', 'uid': 'rs', 'controller': True}]},
            'spec': {'containers': [{'name': 'gateway', 'image': audit.IMAGES['gateway']}]},
            'status': {'phase': 'Running', 'conditions': [{'type': 'Ready', 'status': 'True'}],
                       'containerStatuses': [{'name': 'gateway', 'ready': True}]}}

    def select(self, pod=None):
        return audit.select_pod('gateway', self.deployment, self.rs, {'items': [pod or self.pod]})

    def test_current_ready_owner(self):
        self.assertEqual(self.select()['metadata']['name'], 'gateway-ok')

    def test_terminating_wrong_owner_unready_wrong_image_rejected(self):
        changes = [
            lambda p: p['metadata'].update(deletionTimestamp='now'),
            lambda p: p['metadata']['ownerReferences'][0].update(uid='foreign'),
            lambda p: p['status']['containerStatuses'][0].update(ready=False),
            lambda p: p['spec']['containers'][0].update(image='unrequested'),
        ]
        for change in changes:
            pod = copy.deepcopy(self.pod)
            change(pod)
            with self.assertRaisesRegex(ValueError, 'no_ready_owned_pod'):
                self.select(pod)

    def test_rolling_or_changed_deployment_rejected(self):
        self.deployment['status']['replicas'] = 2
        with self.assertRaisesRegex(ValueError, 'not_stable'):
            self.select()
        self.deployment['status']['replicas'] = 1
        self.deployment['spec']['template']['spec']['containers'][0]['image'] = 'other'
        with self.assertRaisesRegex(ValueError, 'image_changed'):
            self.select()


class ReaderAndPackageTests(unittest.TestCase):
    def test_both_readers_are_readonly_scoped_and_handle_missing_sources(self):
        for cmd in ([sys.executable, '-B', '-c', audit.PY_READER], ['node', '-e', audit.JS_READER]):
            with tempfile.TemporaryDirectory() as directory:
                base = Path(directory)
                root = base / 'root'
                root.mkdir()
                source = root / 'test.txt'
                original = '海能 Work\r\n'.encode('utf-8')
                source.write_bytes(original)
                outside = base / 'outside.txt'
                outside.write_text('out of scope', encoding='utf-8')
                request = {'root': str(root), 'paths': ['test.txt', 'missing.txt', '../outside.txt']}
                result = subprocess.run(cmd, input=json.dumps(request), text=True, encoding='utf-8',
                                        capture_output=True, check=True, timeout=10)
                sources = json.loads(result.stdout)
                self.assertEqual(sources['test.txt']['source'].encode('utf-8'), original)
                self.assertIn('error', sources['missing.txt'])
                self.assertIn('error', sources['../outside.txt'])
                self.assertEqual(source.read_bytes(), original)
                self.assertEqual(list(root.iterdir()), [source])

    def test_original_distributed_manifest_and_scope(self):
        archive = Path.home() / 'Downloads/deerflow-channel-pdf-preview-20260923-r1.tar.gz'
        if not archive.is_file():
            self.skipTest('original release not available')
        with tempfile.TemporaryDirectory() as directory, tarfile.open(archive) as tar:
            root = Path(directory)
            prefix = 'deerflow-channel-pdf-preview-20260923-r1/'
            for name in ('PACKAGE.SHA256', 'gateway-delta.json', 'frontend-delta.json'):
                (root / name).write_bytes(tar.extractfile(prefix + name).read())
            entries = audit.manifests(root)
            self.assertEqual([len(entries[x]) for x in ('gateway', 'frontend')], [11, 8])
            original = (root / 'gateway-delta.json').read_bytes()
            (root / 'gateway-delta.json').write_bytes(original + b' ')
            with self.assertRaisesRegex(ValueError, 'checksum'):
                audit.manifests(root)
            (root / 'gateway-delta.json').write_bytes(b'[]')
            manifest = (root / 'PACKAGE.SHA256').read_text()
            manifest = manifest.replace(hashlib.sha256(original).hexdigest(), hashlib.sha256(b'[]').hexdigest())
            (root / 'PACKAGE.SHA256').write_text(manifest)
            with self.assertRaisesRegex(ValueError, 'scope'):
                audit.manifests(root)


if __name__ == '__main__':
    unittest.main()
