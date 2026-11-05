import hashlib
import json
from pathlib import Path
import runpy
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = '/candidate.py'


class Tests(unittest.TestCase):
    def ns(self):
        # exec retains one shared globals dictionary for installed wrappers.
        n = {'__name__': 'offline_candidate'}
        exec(compile(Path(SCRIPT).read_bytes(), SCRIPT, 'exec'), n)
        return n

    def test_wrong_host_fails_before_report_or_commands(self):
        n = self.ns()
        with patch.object(n['os'], 'uname', return_value=SimpleNamespace(nodename='datax-02')):
            with self.assertRaisesRegex(n['Refused'], 'resume_datax01_only'):
                n['load_inputs']()

    def test_prior_report_hash_rejected(self):
        n = self.ns()
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'REPORT.json'
            p.write_bytes(b'{}')
            with self.assertRaisesRegex(n['Refused'], 'resume_report_hash'):
                n['pinned_report'](str(p), 2, '0' * 64, n['require'])

    def test_report_symlink_rejected(self):
        n = self.ns()
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'report'
            p.write_bytes(b'{}')
            link = Path(d) / 'link'
            link.symlink_to(p)
            with self.assertRaises(OSError):
                n['pinned_report'](str(link), 2, hashlib.sha256(b'{}').hexdigest(), n['require'])

    def test_import_already_requested_refused(self):
        n = self.ns()
        n['pinned_report'] = lambda *a: dict(operation=n['FAILED_OPERATION'], image_import_requested=True,
            image_import_may_be_partial=False, error_code='resource_based_storage_admission_failed')
        with patch.object(n['os'], 'uname', return_value=SimpleNamespace(nodename='datax-01')):
            with self.assertRaisesRegex(n['Refused'], 'resume_not_preimport'):
                n['resume_evidence'](n['require'])

    def test_exact_image_and_node_scope_preserved(self):
        n = self.ns()
        n['resume_evidence'] = lambda require: {'checked': True}
        scope, resource, profile, prepared = n['load_inputs']()
        row = profile['roles'][0]
        self.assertEqual(row['manifest_digest'], 'sha256:0b0cdbbbf95794ab0ce6f96e09f6a449152d05761c23b47dcda3d5c9cf2a3140')
        self.assertEqual(row['config_digest'], 'sha256:5a3f917506bd172815346f7021fcb864ae62d522903f30dedc34ff2dc406afa2')
        scope.node_roles('datax-01')
        with self.assertRaises(n['Refused']):
            scope.node_roles('datax-02')
        self.assertEqual(resource.CONTROL, n['NEW_CONTROL'])

    def test_storage_gate_adds_allowance_and_preserves_reserves(self):
        n = self.ns()
        n['resume_evidence'] = lambda require: {}
        scope, resource, _, _ = n['load_inputs']()
        sample = dict(path=n['NEW_CONTROL'], device=1, capacity_bytes=316482899968,
            available_bytes=62276112384, total_inodes=1000000, available_inodes=900000)
        fake = SimpleNamespace(incremental_budget=lambda *args: [dict(required_bytes=1141718954, appended_header_count=2)])
        result = resource.resource_gate(fake, {}, [], [sample], 4096)
        self.assertTrue(result['pass'])
        row = result['devices'][0]
        self.assertEqual(row['control_write_allowance_bytes'], 64 * 1024**2)
        self.assertEqual(row['required_bytes'], 53926466597 + 64 * 1024**2)
        sample['available_bytes'] = 52040716288
        self.assertFalse(resource.resource_gate(fake, {}, [], [sample], 4096)['pass'])

    def test_new_once_marker_and_old_sources_unchanged(self):
        n = self.ns()
        self.assertEqual(n['CONTROL'], '/var/lib/portal-runner-oa-schema-compatibility-datax01-20261022-r2')
        for key, value in n['SOURCES'].items():
            self.assertEqual(hashlib.sha256(value).hexdigest(), n['SOURCE_PINS'][key]['sha256'])
        self.assertIn('--resume-datax01-preload-once', n['main'].__code__.co_consts)


if __name__ == '__main__':
    unittest.main()
