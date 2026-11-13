"""Staged Skill identity and fixture execution boundaries for publication checks."""
import base64
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

PATH = Path(__file__).resolve().parents[1] / 'deployment/browser-runtime-20261112/publish_verify_child.py'
SPEC = importlib.util.spec_from_file_location('browser_publish_child', PATH)
child = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(child)


class PublishVerificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='epv-')
        self.root = Path(self.temp.name)
        self.flags = [patch.object(os, name, getattr(os, name, 0), create=True) for name in ('O_NOFOLLOW', 'O_NONBLOCK')]
        for flag in self.flags:
            flag.start()
        self.windows_stamp = None
        if os.name == 'nt':
            # This child runs only in Linux. NT's fd/path ctime and inode views
            # differ; Windows exercises the hash/layout rules with a size stamp.
            self.windows_stamp = patch.object(child, 'stamp', side_effect=lambda state: (state.st_size,))
            self.windows_stamp.start()

    def tearDown(self):
        if self.windows_stamp:
            self.windows_stamp.stop()
        for flag in reversed(self.flags):
            flag.stop()
        self.temp.cleanup()

    def skill(self):
        root = self.root / 'skill'
        root.mkdir()
        files = {}
        for index in range(105):
            name = 'file-%03d.txt' % index
            raw = ('synthetic %d' % index).encode()
            (root / name).write_bytes(raw)
            files[name] = {'bytes': len(raw), 'sha256': child.sha(raw)}
        name = 'assets/browser-runtime.json'
        (root / 'assets').mkdir()
        raw = b'{}'
        (root / name).write_bytes(raw)
        files[name] = {'bytes': len(raw), 'sha256': child.sha(raw)}
        (root / 'MANIFEST.json').write_bytes(child.canonical({'schema': 'energy-daily-portable-files.v1', 'version': '2.0.1-portable', 'files': files}))
        return root, child.sha(child.canonical(child.inventory(root)))

    def test_exact_107_file_tree_and_106_manifest_entries_accepted(self):
        root, expected = self.skill()
        self.assertEqual(len(child.verify_skill(expected, root)), 107)

    def test_staged_file_change_is_rejected(self):
        root, expected = self.skill()
        (root / 'file-000.txt').write_text('changed')
        with self.assertRaisesRegex(child.Refused, 'STAGED_SKILL_TREE_CHANGED'):
            child.verify_skill(expected, root)

    def test_inconsistent_manifest_rejected_even_if_tree_hash_matches(self):
        root, expected = self.skill()
        manifest = json.loads((root / 'MANIFEST.json').read_bytes())
        manifest['files']['file-000.txt']['sha256'] = '0' * 64
        (root / 'MANIFEST.json').write_bytes(child.canonical(manifest))
        tree = child.sha(child.canonical(child.inventory(root)))
        with self.assertRaisesRegex(child.Refused, 'STAGED_MANIFEST_CONTENT_CHANGED'):
            child.verify_skill(tree, root)

    def test_runtime_manifest_hash_and_source_image_are_pinned(self):
        runtime = self.root / 'runtime'
        runtime.mkdir()
        raw = child.canonical({'schema': 'energy-browser-runtime.v1', 'architecture': 'linux/arm64',
                               'source_image_id': 'sha256:fb36911f899cc43f1228ee4d0701130761b43b3e9792320bf4e0f16f41f0bec8'})
        (runtime / 'RUNTIME.json').write_bytes(raw)
        self.assertEqual(child.verify_runtime_manifest(child.sha(raw), runtime), raw)
        with self.assertRaisesRegex(child.Refused, 'STAGED_RUNTIME_MANIFEST_CHANGED'):
            child.verify_runtime_manifest('0' * 64, runtime)
        other = json.loads(raw)
        other['source_image_id'] = 'sha256:' + '0' * 64
        raw = child.canonical(other)
        (runtime / 'RUNTIME.json').write_bytes(raw)
        with self.assertRaisesRegex(child.Refused, 'STAGED_RUNTIME_IDENTITY'):
            child.verify_runtime_manifest(child.sha(raw), runtime)

    def test_fixture_code_is_hash_pinned_before_execution(self):
        files = {name: base64.b64encode(b'changed code').decode() for name in child.FIXTURES}
        with self.assertRaisesRegex(child.Refused, 'FIXTURE_CHANGED'):
            child.write_fixtures(files, self.root)
        self.assertFalse((self.root / 'tests/test_energy_market_daily.py').exists())

    def test_real_fixed_fixture_subset_is_sufficient(self):
        tests = PATH.parents[2] / 'tests'
        files = {name: base64.b64encode((tests / name).read_bytes()).decode() for name in child.FIXTURES}
        # The publication child pins the historical fixture, while the general
        # behavior suite has since grown. Exercise the exact sealed fixture.
        archived = PATH.parents[3] / 'release/post44-source-sync-20261113/qualification-fixtures/test_energy_market_daily.py'
        files['test_energy_market_daily.py'] = base64.b64encode(archived.read_bytes()).decode()
        child.write_fixtures(files, self.root)
        self.assertTrue((self.root / 'tests/fixtures/approved.json').is_file())

    def test_wrong_identity_does_not_read_shared_sources(self):
        output = io.StringIO()
        with patch.object(child.os, 'geteuid', return_value=0, create=True), patch.object(child, 'verify_skill', side_effect=AssertionError('must not read')):
            with contextlib.redirect_stdout(output):
                self.assertEqual(child.main(), 1)
        self.assertEqual(json.loads(output.getvalue())['error_code'], 'FIXED_ARM64_NONROOT_IDENTITY_REQUIRED')

    def test_wrong_package_payload_does_not_read_shared_sources(self):
        payload = {'package_sha256': '0' * 64, 'after_tree_sha256': '1' * 64, 'runtime_manifest_sha256': '2' * 64,
                   'png_sha256': '3' * 64, 'test_files': {}}
        output = io.StringIO()
        with patch.object(child.os, 'geteuid', return_value=1000, create=True), patch.object(child.os, 'getegid', return_value=1000, create=True), \
             patch.object(child.platform, 'machine', return_value='aarch64'), patch.object(child.sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(child.canonical(payload)))), \
             patch.object(child, 'verify_skill', side_effect=AssertionError('must not read')):
            with contextlib.redirect_stdout(output):
                self.assertEqual(child.main(), 1)
        self.assertEqual(json.loads(output.getvalue())['error_code'], 'PUBLICATION_PAYLOAD_INVALID')

    @unittest.skipUnless(os.name == 'posix', 'POSIX symlink rejection')
    def test_symlink_in_shared_tree_is_rejected(self):
        root, expected = self.skill()
        (root / 'file-000.txt').unlink()
        (root / 'file-000.txt').symlink_to(root / 'file-001.txt')
        with self.assertRaisesRegex(child.Refused, 'READONLY_SOURCE_LINK'):
            child.inventory(root)


if __name__ == '__main__':
    unittest.main()
