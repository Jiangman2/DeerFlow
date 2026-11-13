"""Filesystem publication keeps old live state until exact nonroot acceptance."""
import base64
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
import zipfile

SCRIPT = Path(__file__).resolve().parents[1] / 'deployment/browser-runtime-20261112/publish_browser_fix_child.py'
SPEC = importlib.util.spec_from_file_location('browser_fix_publisher', SCRIPT)
publisher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(publisher)


class BrowserFixPublisherTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='browser-publish-test-')
        self.base = Path(self.temporary.name)
        self.parent = self.base / 'skills'
        self.live = self.parent / 'public/energy-market-daily'
        self.live.mkdir(parents=True)
        (self.live / 'SKILL.md').write_bytes(b'old accepted skill')
        (self.live / 'original-evidence.json').write_bytes(b'{"old":"evidence"}')
        self.source = self.base / 'image/chrome-linux'
        self.source.mkdir(parents=True)
        for name, raw, mode in (('headless_shell', b'headless browser fixture', 0o755), ('icudtl.dat', b'icu fixture', 0o644), ('LICENSE', b'license fixture', 0o644)):
            path = self.source / name
            path.write_bytes(raw)
            path.chmod(mode)
        runtime_parent = self.parent / '.energy-browser-runtime-20261112-r1'
        self.patches = [patch.object(publisher, name, value) for name, value in {
            'ROOT': self.live, 'PARENT': self.parent, 'SOURCE': self.source, 'RUNTIME_PARENT': runtime_parent,
            'RUNTIME': runtime_parent / 'linux-arm64', 'OLD_COUNT': 2,
        }.items()]
        for item in self.patches:
            item.start()
        self.old_rows = publisher.inventory(self.live)
        self.old_hash = publisher.tree_hash(self.old_rows)
        self.old_patch = patch.object(publisher, 'OLD_TREE', self.old_hash)
        self.old_patch.start()
        self.package = self.code_package()
        self.package_hash = hashlib.sha256(self.package).hexdigest()
        self.package_patch = patch.object(publisher, 'PACKAGE_SHA', self.package_hash)
        self.package_patch.start()
        files = []
        for path in sorted(self.source.iterdir()):
            raw = path.read_bytes()
            files.append({'path': 'chrome-linux/' + path.name, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(), 'executable': path.name == 'headless_shell'})
        self.manifest = {'schema': 'energy-browser-runtime.v1', 'architecture': 'linux/arm64', 'source_image_id': publisher.IMAGE_ID,
                         'executable': 'chrome-linux/headless_shell', 'files': files, 'licenses': ['chrome-linux/LICENSE']}
        self.runtime_hash = publisher.sha(publisher.canonical(self.manifest) + b'\n')
        self.operation = '1234567890abcdef1234567890abcdef'
        self.payload = {'action': 'stage', 'operation': self.operation, 'package': base64.b64encode(self.package).decode(),
                        'package_sha256': self.package_hash, 'runtime_manifest': self.manifest, 'runtime_manifest_sha256': self.runtime_hash}

    def tearDown(self):
        self.package_patch.stop()
        self.old_patch.stop()
        for item in reversed(self.patches):
            item.stop()
        self.temporary.cleanup()

    def code_package(self, extra=None):
        files = {'assets/history/sample.json': b'{}'}
        files.update({'file-%03d.txt' % number: ('code fixture %d' % number).encode() for number in range(104)})
        inventory = {name: {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()} for name, raw in files.items()}
        files['MANIFEST.json'] = json.dumps({'schema': 'energy-daily-portable-files.v1', 'version': '2.0.1-portable', 'files': inventory}).encode()
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w') as archive:
            for name, raw in files.items():
                archive.writestr('energy-market-daily/' + name, raw)
            if extra:
                archive.writestr(extra, b'escape fixture')
        return output.getvalue()

    def acceptance(self, stage):
        return {'action': 'publish', 'operation': self.operation, 'acceptance': {
            'status': 'NONROOT_ARM64_BROWSER_AND_FIXED_REPORT_QUALIFIED', 'uid': 1000, 'gid': 1000,
            'package_sha256': self.package_hash, 'runtime_manifest_sha256': self.runtime_hash,
            'after_tree_sha256': stage['after_tree_sha256'], 'png_sha256': 'a' * 64}}

    def assert_old_live(self):
        self.assertEqual(publisher.inventory(self.live), self.old_rows)

    def test_release_path_and_payload_cannot_escape(self):
        self.assertEqual(publisher.release_path(self.operation), self.parent / ('.energy-browser-fix-release-20261112-r1-' + self.operation))
        for operation in ('../escape', '0' * 31, '0' * 33, None):
            with self.assertRaises(publisher.Refused):
                publisher.release_path(operation)

    def test_runtime_manifest_rejects_wrong_image_path_duplicates_and_bounds(self):
        cases = []
        for key, value in (('source_image_id', 'sha256:' + '0' * 64), ('architecture', 'linux/amd64')):
            value_manifest = copy.deepcopy(self.manifest)
            value_manifest[key] = value
            cases.append(value_manifest)
        for name in ('../escape', '/absolute', 'chrome-linux/../escape', 'chrome-linux\\escape'):
            value_manifest = copy.deepcopy(self.manifest)
            value_manifest['files'][0]['path'] = name
            cases.append(value_manifest)
        value_manifest = copy.deepcopy(self.manifest)
        value_manifest['files'].append(value_manifest['files'][0])
        cases.append(value_manifest)
        value_manifest = copy.deepcopy(self.manifest)
        value_manifest['files'][0]['bytes'] = publisher.MAX_FILE + 1
        cases.append(value_manifest)
        for manifest in cases:
            digest = publisher.sha(publisher.canonical(manifest) + b'\n')
            with self.assertRaises(publisher.Refused):
                publisher.runtime_rows(manifest, digest)
        self.assert_old_live()

    def test_proven_arm64_browser_size_is_accepted_with_bounded_metadata_only(self):
        manifest = copy.deepcopy(self.manifest)
        browser = next(row for row in manifest['files'] if row['path'] == manifest['executable'])
        browser['bytes'] = 298683784
        digest = publisher.sha(publisher.canonical(manifest) + b'\n')
        rows, raw = publisher.runtime_rows(manifest, digest)
        self.assertEqual(rows['chrome-linux/headless_shell']['bytes'], 298683784)
        self.assertLess(len(raw), 4096)
        browser['bytes'] = 384 * 1024 * 1024 + 1
        digest = publisher.sha(publisher.canonical(manifest) + b'\n')
        with self.assertRaisesRegex(publisher.Refused, 'RUNTIME_ROW_INVALID'):
            publisher.runtime_rows(manifest, digest)
        self.assert_old_live()

    def test_changed_old_live_is_rejected_before_staging(self):
        (self.live / 'SKILL.md').write_bytes(b'colleague change')
        with self.assertRaisesRegex(publisher.Refused, 'SKILL_TREE_CHANGED'):
            publisher.stage(self.payload)
        self.assertFalse(publisher.release_path(self.operation).exists())
        self.assertFalse(publisher.RUNTIME_PARENT.exists())

    def test_package_identity_is_required_before_runtime_install(self):
        self.payload['package_sha256'] = '0' * 64
        with self.assertRaisesRegex(publisher.Refused, 'PACKAGE_IDENTITY'):
            publisher.stage(self.payload)
        self.assert_old_live()
        self.assertFalse(publisher.RUNTIME_PARENT.exists())

    @unittest.skipUnless(os.name == 'posix', 'Actual Linux executable modes')
    def test_stage_backs_up_old_skill_and_seals_exact_shared_runtime(self):
        result = publisher.stage(self.payload)
        self.assertEqual(result['status'], 'BROWSER_FIX_STAGED')
        self.assertEqual(result['after_files'], 107)
        self.assertFalse(result['skill_modified'])
        self.assert_old_live()
        release = publisher.release_path(self.operation)
        self.assertEqual(publisher.inventory(release / 'backup'), self.old_rows)
        candidate = release / 'candidate'
        binding = json.loads((candidate / 'assets/browser-runtime.json').read_bytes())
        self.assertEqual(binding['manifest_sha256'], self.runtime_hash)
        self.assertEqual(stat.S_IMODE(release.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((publisher.RUNTIME / 'chrome-linux/headless_shell').stat().st_mode), 0o755)
        self.assertEqual(stat.S_IMODE((publisher.RUNTIME / 'chrome-linux/icudtl.dat').stat().st_mode), 0o644)
        self.assertEqual(publisher.verify_runtime(self.manifest, self.runtime_hash)['files'], 3)
        self.assertFalse((release / 'SWITCH.json').exists())

    @unittest.skipUnless(os.name == 'posix', 'Actual Linux executable modes')
    def test_publish_requires_exact_uid1000_render_acceptance(self):
        staged = publisher.stage(self.payload)
        acceptance = self.acceptance(staged)
        for key, wrong in (('uid', 0), ('uid', True), ('gid', 0), ('png_sha256', 'invalid'), ('after_tree_sha256', 'b' * 64), ('runtime_manifest_sha256', 'c' * 64)):
            changed = copy.deepcopy(acceptance)
            changed['acceptance'][key] = wrong
            with self.assertRaisesRegex(publisher.Refused, 'NONROOT_RENDER_ACCEPTANCE_REQUIRED'):
                publisher.publish(changed)
            self.assert_old_live()
        result = publisher.publish(acceptance)
        self.assertEqual(result['status'], 'BROWSER_FIX_SKILL_PUBLISHED')
        self.assertEqual(result['after_files'], 107)
        self.assertEqual(publisher.inventory(publisher.release_path(self.operation) / 'retired-original'), self.old_rows)
        verified = publisher.verify({'action': 'verify', 'operation': self.operation})
        self.assertEqual(verified['tree_sha256'], staged['after_tree_sha256'])
        self.assertEqual(verified['after_tree_sha256'], verified['tree_sha256'])

    @unittest.skipUnless(os.name == 'posix', 'Actual Linux executable modes')
    def test_stage_inspection_reads_all_guards_and_rejects_candidate_tamper(self):
        staged = publisher.stage(self.payload)
        payload = {'action': 'inspect-stage', 'operation': self.operation}
        result = publisher.inspect_stage(payload)
        self.assertEqual(result['status'], 'BROWSER_FIX_STAGED')
        self.assertEqual(result['after_tree_sha256'], staged['after_tree_sha256'])
        self.assertEqual(result['after_files'], 107)
        self.assertEqual(result['runtime']['manifest_sha256'], self.runtime_hash)
        self.assert_old_live()
        (publisher.release_path(self.operation) / 'candidate/file-001.txt').write_bytes(b'changed after nonroot check')
        with self.assertRaisesRegex(publisher.Refused, 'SKILL_TREE_CHANGED'):
            publisher.inspect_stage(payload)
        self.assert_old_live()

    @unittest.skipUnless(os.name == 'posix', 'Actual Linux executable modes')
    def test_candidate_change_cannot_publish_old_acceptance(self):
        staged = publisher.stage(self.payload)
        (publisher.release_path(self.operation) / 'candidate/file-000.txt').write_bytes(b'changed candidate')
        with self.assertRaisesRegex(publisher.Refused, 'SKILL_TREE_CHANGED'):
            publisher.publish(self.acceptance(staged))
        self.assert_old_live()

    @unittest.skipUnless(os.name == 'posix', 'Actual Linux executable modes')
    def test_runtime_change_cannot_publish_old_acceptance(self):
        staged = publisher.stage(self.payload)
        (publisher.RUNTIME / 'chrome-linux/icudtl.dat').write_bytes(b'changed runtime')
        with self.assertRaisesRegex(publisher.Refused, 'INSTALLED_RUNTIME_CHANGED'):
            publisher.publish(self.acceptance(staged))
        self.assert_old_live()

    @unittest.skipUnless(os.name == 'posix', 'Actual Linux executable modes')
    def test_existing_runtime_is_preserved_for_operator_review(self):
        publisher.RUNTIME_PARENT.mkdir()
        marker = publisher.RUNTIME_PARENT / 'existing'
        marker.write_bytes(b'do not overwrite')
        with self.assertRaisesRegex(publisher.Refused, 'RUNTIME_EXISTS_REVIEW_REQUIRED'):
            publisher.stage(self.payload)
        self.assertEqual(marker.read_bytes(), b'do not overwrite')
        self.assert_old_live()

    @unittest.skipUnless(os.name == 'posix', 'Actual Linux executable modes')
    def test_source_extra_file_is_not_copied(self):
        (self.source / 'unexpected-private.json').write_bytes(b'not part of qualified image directory')
        with self.assertRaisesRegex(publisher.Refused, 'IMAGE_BROWSER_INVENTORY_CHANGED'):
            publisher.stage(self.payload)
        self.assert_old_live()
        self.assertFalse(publisher.RUNTIME_PARENT.exists())

    @unittest.skipUnless(os.name == 'posix', 'POSIX symlinks')
    def test_source_symlink_is_rejected_before_runtime_install(self):
        (self.source / 'alias').symlink_to(self.source / 'icudtl.dat')
        with self.assertRaisesRegex(publisher.Refused, 'PATH_LINK_REFUSED'):
            publisher.stage(self.payload)
        self.assert_old_live()

    @unittest.skipUnless(os.name == 'posix', 'POSIX privilege bits')
    def test_setuid_image_file_is_rejected(self):
        (self.source / 'headless_shell').chmod(0o4755)
        with self.assertRaisesRegex(publisher.Refused, 'RUNTIME_SOURCE_TYPE_OR_BOUND'):
            publisher.stage(self.payload)
        self.assert_old_live()

    @unittest.skipUnless(os.name == 'posix', 'Actual Linux executable modes')
    def test_second_rename_failure_preserves_old_files_and_uncertain_receipt(self):
        staged = publisher.stage(self.payload)
        rename = publisher.os.rename
        def fail_install(source, destination):
            if Path(source).name == 'candidate':
                raise OSError('simulated filesystem interruption')
            return rename(source, destination)
        with patch.object(publisher.os, 'rename', side_effect=fail_install):
            with self.assertRaises(OSError):
                publisher.publish(self.acceptance(staged))
        release = publisher.release_path(self.operation)
        self.assertFalse(self.live.exists())
        self.assertEqual(publisher.inventory(release / 'retired-original'), self.old_rows)
        self.assertEqual(json.loads((release / 'SWITCH.json').read_bytes())['phase'], 'OLD_RETIRED_NEW_NOT_INSTALLED')
        self.assertTrue((release / 'candidate').exists())


if __name__ == '__main__':
    unittest.main()
