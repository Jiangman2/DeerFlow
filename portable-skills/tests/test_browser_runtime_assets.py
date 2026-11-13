"""Sealed runtime dependency is validated and staged without touching a project."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'energy-market-daily/scripts'))
import browser_runtime as runtime
from common import Failure


class RuntimeAssetsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='browser-assets-test-')
        self.parent = Path(self.temp.name) / 'skills'
        self.skill = self.parent / 'public/energy-market-daily'
        (self.skill / 'assets').mkdir(parents=True)
        self.root = self.parent / '.energy-browser-runtime-test/linux-arm64'
        (self.root / 'chrome-linux').mkdir(parents=True)
        contents = {'chrome-linux/headless_shell': b'fixture executable', 'chrome-linux/icudtl.dat': b'fixture data'}
        self.manifest = {'schema':'energy-browser-runtime.v1','architecture':'linux/arm64','source_image_id':'sha256:' + '1' * 64,
                         'executable':'chrome-linux/headless_shell', 'files':[]}
        for name, raw in contents.items():
            (self.root / name).write_bytes(raw)
            self.manifest['files'].append({'path':name,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),'executable':name.endswith('headless_shell')})
        self.binding = {'schema':'energy-browser-runtime-binding.v1','directory':'../../.energy-browser-runtime-test/linux-arm64','manifest_sha256':''}
        self.seal()
        self.patches = (patch.object(runtime, 'SKILL', self.skill), patch.object(runtime.platform, 'system', return_value='Linux'),
                        patch.object(runtime.platform, 'machine', return_value='aarch64'))
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def seal(self):
        raw = (json.dumps(self.manifest, sort_keys=True, separators=(',', ':')) + '\n').encode()
        (self.root / 'RUNTIME.json').write_bytes(raw)
        self.binding['manifest_sha256'] = hashlib.sha256(raw).hexdigest()
        (self.skill / 'assets/browser-runtime.json').write_text(json.dumps(self.binding), encoding='utf-8')

    def test_copies_exact_runtime_to_unique_local_directory_and_cleans_it(self):
        with runtime.runtime_browser() as executable:
            path = Path(executable)
            self.assertEqual(path.read_bytes(), b'fixture executable')
            self.assertNotEqual(path, self.root / 'chrome-linux/headless_shell')
            self.assertEqual((path.parent / 'icudtl.dat').read_bytes(), b'fixture data')
            self.assertTrue(os.access(path, os.X_OK))
        self.assertFalse(path.exists())
        self.assertEqual((self.root / 'chrome-linux/headless_shell').read_bytes(), b'fixture executable')

    def test_missing_binding_uses_portable_browser_discovery(self):
        (self.skill / 'assets/browser-runtime.json').unlink()
        with runtime.runtime_browser() as executable:
            self.assertIsNone(executable)

    def test_other_architecture_does_not_copy_arm64_binary(self):
        with patch.object(runtime.platform, 'machine', return_value='x86_64'), patch.object(runtime, 'copy_file', side_effect=AssertionError('wrong architecture')):
            with runtime.runtime_browser() as executable:
                self.assertIsNone(executable)

    def test_changed_binary_is_rejected_before_launch(self):
        (self.root / 'chrome-linux/headless_shell').write_bytes(b'changed executable')
        with self.assertRaisesRegex(Failure, 'BROWSER_RUNTIME_FILE_CHANGED|BROWSER_RUNTIME_SOURCE_INVALID'):
            with runtime.runtime_browser():
                self.fail('must not expose changed binary')

    def test_changed_manifest_is_rejected(self):
        (self.root / 'RUNTIME.json').write_bytes(b'{}')
        with self.assertRaisesRegex(Failure, 'BROWSER_RUNTIME_MANIFEST_CHANGED'):
            with runtime.runtime_browser():
                self.fail('must not accept changed manifest')

    def test_binding_cannot_escape_shared_skills_root(self):
        self.binding['directory'] = '../../../private/linux-arm64'
        (self.skill / 'assets/browser-runtime.json').write_text(json.dumps(self.binding), encoding='utf-8')
        with self.assertRaisesRegex(Failure, 'BROWSER_RUNTIME_BINDING_INVALID'):
            with runtime.runtime_browser():
                self.fail('must not inspect escaped path')

    def test_extra_unsealed_runtime_file_is_rejected(self):
        (self.root / 'extra-file').write_bytes(b'unsealed')
        with self.assertRaisesRegex(Failure, 'BROWSER_RUNTIME_EXTRA_FILE'):
            with runtime.runtime_browser():
                self.fail('must reject extra file')

    def test_missing_runtime_file_is_rejected(self):
        (self.root / 'chrome-linux/icudtl.dat').unlink()
        with self.assertRaisesRegex(Failure, 'BROWSER_RUNTIME_FILE_MISSING'):
            with runtime.runtime_browser():
                self.fail('must reject missing file')

    def test_inventory_has_explicit_size_and_path_bounds(self):
        for name in ('../escaped', '/absolute', 'chrome-linux/../escaped', 'chrome-linux\\escaped'):
            self.manifest['files'][0]['path'] = name
            with self.assertRaisesRegex(Failure, 'BROWSER_RUNTIME_PATH_INVALID'):
                runtime.inventory(self.manifest)
        self.manifest['files'][0]['path'] = 'chrome-linux/headless_shell'
        self.manifest['files'][0]['bytes'] = runtime.MAX_FILE + 1
        with self.assertRaisesRegex(Failure, 'BROWSER_RUNTIME_INVENTORY_INVALID'):
            runtime.inventory(self.manifest)

    def test_measured_production_executable_fits_finite_inventory_bound(self):
        self.manifest['files'][0]['bytes'] = 298683784
        self.assertEqual(runtime.inventory(self.manifest)['chrome-linux/headless_shell']['bytes'], 298683784)
        self.manifest['files'][0]['bytes'] = runtime.MAX_FILE + 1
        with self.assertRaisesRegex(Failure, 'BROWSER_RUNTIME_INVENTORY_INVALID'):
            runtime.inventory(self.manifest)

    def test_nonexecutable_local_temp_reports_explicit_environment_failure(self):
        original = os.access
        with patch.object(runtime.os, 'access', side_effect=lambda path, mode: False if mode & os.X_OK else original(path, mode)):
            with self.assertRaisesRegex(Failure, 'BROWSER_LOCAL_TEMP_NOEXEC'):
                with runtime.runtime_browser():
                    self.fail('must not launch from noexec temp')

    def public_runtime_binding(self):
        parent = self.skill.parent / self.root.parent.name
        self.root.parent.rename(parent)
        self.root = parent / 'linux-arm64'
        self.binding['directory'] = '../.energy-browser-runtime-test/linux-arm64'
        (self.skill / 'assets/browser-runtime.json').write_text(json.dumps(self.binding), encoding='utf-8')

    def test_public_category_binding_works_without_pvc_root_runtime(self):
        old_root = self.root
        self.public_runtime_binding()
        self.assertFalse(old_root.exists())
        with runtime.runtime_browser() as executable:
            self.assertEqual(Path(executable).read_bytes(), b'fixture executable')

    def test_doctor_observes_category_runtime_without_copy_or_launch(self):
        self.public_runtime_binding()
        with patch.object(runtime, 'copy_file', side_effect=AssertionError('doctor must not copy')):
            result = runtime.observe_runtime()
        self.assertTrue(result['operator_runtime_ready'])
        self.assertFalse(result['browser_launch_verified'])

    def test_missing_root_is_an_operator_blocker_with_exact_code(self):
        self.root.rename(self.root.with_name('elsewhere'))
        observed = runtime.observe_runtime()
        self.assertFalse(observed['operator_runtime_ready'])
        self.assertEqual(observed['error_code'], 'BROWSER_RUNTIME_ROOT_NOT_MOUNTED')
        self.assertEqual(observed['recovery_scope'], 'operator')
        with self.assertRaises(Failure) as error:
            with runtime.runtime_browser():
                self.fail('unmounted runtime must stop')
        self.assertFalse(error.exception.details['dependency_installation_can_repair'])

    def test_missing_manifest_has_distinct_diagnostic(self):
        (self.root / 'RUNTIME.json').unlink()
        self.assertEqual(runtime.observe_runtime()['error_code'], 'BROWSER_RUNTIME_MANIFEST_MISSING')

    @unittest.skipUnless(os.name == 'posix', 'POSIX symlinks')
    def test_source_symlink_is_rejected(self):
        path = self.root / 'chrome-linux/headless_shell'
        path.unlink()
        path.symlink_to(self.root / 'chrome-linux/icudtl.dat')
        with self.assertRaisesRegex(Failure, 'BROWSER_RUNTIME_LINK_REFUSED'):
            with runtime.runtime_browser():
                self.fail('must not follow runtime link')


if __name__ == '__main__':
    unittest.main()
