import hashlib
import importlib.util
import json
import pathlib
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('recovery', pathlib.Path(__file__).with_name('restore-portal-dispatcher-image-reference-20261028-r1.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def deployment():
    return {'metadata': {'uid': m.UID, 'generation': 2}, 'spec': {'replicas': 2, 'template': {'spec': {'containers': [{'name': 'dispatcher', 'image': m.TARGET, 'imagePullPolicy': 'Never'}]}}}}


class RecoveryTests(unittest.TestCase):
    def test_original_baseline_only(self):
        d = deployment()
        m.validate_deployment(d)
        d['spec']['template']['spec']['containers'][0]['image'] = 'new-candidate'
        with self.assertRaises(m.Refused):
            m.validate_deployment(d)

    def test_descriptor_rejects_malformed_or_unbounded(self):
        for d in ({'digest': 'not-a-digest', 'size': 1}, {'digest': m.ROOT, 'size': -1}, {'digest': m.ROOT, 'size': 8589934593}):
            with self.assertRaises(m.Refused):
                m.descriptor(d)

    def test_metadata_digest_checked_before_parsing(self):
        with patch.object(m, 'command', return_value=b'{}'):
            with self.assertRaisesRegex(m.Refused, 'METADATA_DIGEST_MISMATCH'):
                m.metadata(m.ROOT)

    def test_complete_selected_platform_layers_verified(self):
        index = {'manifests': [{'digest': m.ROOT, 'size': 100, 'platform': {'os': 'linux', 'architecture': 'arm64'}}]}
        manifest = {'config': {'digest': m.ROOT, 'size': 2}, 'layers': [{'digest': m.ROOT, 'size': 9}, {'digest': m.ROOT, 'size': 10}]}
        with patch.object(m, 'metadata', side_effect=[index, manifest, {'os': 'linux', 'architecture': 'arm64'}]), patch.object(m, 'verify_blob', side_effect=[9, 10]) as verify:
            r = m.verify_content()
        self.assertEqual(verify.call_count, 2)
        self.assertEqual(r['verified_compressed_bytes'], 19)

    def test_wrong_platform_refused(self):
        with patch.object(m, 'metadata', return_value={'manifests': []}), patch.object(m, 'verify_blob') as verify:
            with self.assertRaises(m.Refused):
                m.verify_content()
            verify.assert_not_called()

    def test_wrong_host_before_any_command(self):
        with patch.object(m.socket, 'gethostname', return_value='wrong'), patch.object(m, 'command') as command:
            with self.assertRaises(m.Refused):
                m.main({})
            command.assert_not_called()

    def run_main(self, conflict=False, bad_content=False):
        d = deployment()
        def get(kind, name):
            if kind == 'node':
                return {'metadata': {'uid': m.NODE_UID}, 'status': {'nodeInfo': {'architecture': 'arm64'}}}
            return d
        initial = {m.SOURCE: m.ROOT}
        if conflict:
            initial[m.TARGET] = 'sha256:' + '0' * 64
        with tempfile.TemporaryDirectory() as temp, patch.object(m, 'REPORT_DIR', pathlib.Path(temp) / 'receipt'), patch.object(m.socket, 'gethostname', return_value='datax-01'), patch.object(m.os, 'geteuid', return_value=0, create=True), patch.object(m, 'get', side_effect=get), patch.object(m, 'active_pods', return_value=[]), patch.object(m, 'refs', side_effect=[initial, initial, {m.SOURCE: m.ROOT, m.TARGET: m.ROOT}]), patch.object(m, 'verify_content', side_effect=m.Refused('BAD_LAYER') if bad_content else None, return_value={'status': 'PASS'}), patch.object(m, 'observe', return_value=[{'pod': 'one'}, {'pod': 'two'}]), patch.object(m, 'command', return_value=b'') as command:
            out = {}
            if conflict or bad_content:
                with self.assertRaises(m.Refused):
                    m.main(out)
                command.assert_not_called()
                self.assertFalse(m.REPORT_DIR.exists())
            else:
                m.main(out)
                command.assert_called_once_with(m.C + ['images', 'tag', m.SOURCE, m.TARGET])
                self.assertTrue(out['other_deployment_specs_preserved'])
                self.assertEqual(out['status'], 'DISPATCHER_REFERENCE_RESTORED_RUNTIME_READY_BROWSER_PENDING')

    def test_only_mutation_is_exact_tag_after_content_verification(self):
        self.run_main()

    def test_conflicting_target_not_overwritten(self):
        self.run_main(conflict=True)

    def test_failed_content_never_tags(self):
        self.run_main(bad_content=True)


if __name__ == '__main__':
    unittest.main()
