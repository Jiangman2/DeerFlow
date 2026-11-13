"""Browser discovery and failure recovery under ordinary user permissions."""
import os
from pathlib import Path
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'energy-market-daily/scripts'))
import daily
from common import Failure


class BrowserRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='energy-browser-')
        self.root = Path(self.temp.name)
        self.environment = patch.dict(os.environ, {}, clear=True)
        self.environment.start()
        self.home = patch.object(Path, 'home', return_value=self.root / 'home')
        self.home.start()
        self.which = patch.object(daily.shutil, 'which', return_value=None)
        self.which.start()
        original_access = os.access
        self.access = patch.object(os, 'access', side_effect=lambda path, mode: Path(path).is_relative_to(self.root) and original_access(path, mode))
        self.access.start()

    def tearDown(self):
        self.access.stop()
        self.which.stop()
        self.home.stop()
        self.environment.stop()
        self.temp.cleanup()

    def executable(self, name):
        file = self.root / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(b'fixture, not launched')
        file.chmod(0o755)
        return str(file)

    def test_inaccessible_cache_stat_does_not_block_system_browser(self):
        system = self.executable('system/chromium')
        os.environ['PLAYWRIGHT_BROWSERS_PATH'] = str(self.root / 'denied')
        original = Path.is_dir

        def guarded(path):
            if path == self.root / 'denied':
                raise PermissionError('fixture')
            return original(path)

        with patch.object(Path, 'is_dir', guarded), patch.object(daily.shutil, 'which', return_value=system):
            self.assertEqual(daily.browser_candidates(), [system])

    def test_inaccessible_cache_glob_does_not_block_system_browser(self):
        cache = self.root / 'denied'
        cache.mkdir()
        system = self.executable('system/chromium')
        os.environ['PLAYWRIGHT_BROWSERS_PATH'] = str(cache)
        original = Path.glob

        def guarded(path, pattern):
            if path == cache:
                raise PermissionError('fixture')
            return original(path, pattern)

        with patch.object(Path, 'glob', guarded), patch.object(daily.shutil, 'which', return_value=system):
            self.assertEqual(daily.browser_candidates(), [system])

    def test_inaccessible_candidate_file_is_skipped(self):
        explicit = self.executable('denied/chromium')
        system = self.executable('system/chromium')
        os.environ['REPORT_BROWSER_EXECUTABLE'] = explicit
        original = Path.is_file

        def guarded(path):
            if str(path) == explicit:
                raise PermissionError('fixture')
            return original(path)

        with patch.object(Path, 'is_file', guarded), patch.object(daily.shutil, 'which', return_value=system):
            self.assertEqual(daily.browser_candidates(), [system])

    def test_unknown_home_still_finds_system_browser(self):
        system = self.executable('system/chromium')
        with patch.object(Path, 'home', side_effect=RuntimeError('no home')), patch.object(daily.shutil, 'which', return_value=system):
            self.assertEqual(daily.browser_candidates(), [system])

    def test_accessible_headless_shell_and_duplicate_system_aliases(self):
        shell = self.executable('cache/chromium_headless_shell-1228/chrome-linux/headless_shell')
        system = self.executable('system/chromium')
        os.environ['PLAYWRIGHT_BROWSERS_PATH'] = str(self.root / 'cache')
        with patch.object(daily.shutil, 'which', return_value=system):
            self.assertEqual(daily.browser_candidates(), [shell, system])

    @unittest.skipUnless(os.name == 'posix', 'POSIX execute permission')
    def test_nonexecutable_files_are_skipped(self):
        executable = self.executable('system/chromium')
        Path(executable).chmod(0o644)
        os.environ['REPORT_BROWSER_EXECUTABLE'] = executable
        self.assertEqual(daily.browser_candidates(), [])

    @unittest.skipUnless(os.name == 'posix' and hasattr(os, 'geteuid') and os.geteuid() != 0, 'Requires real nonroot POSIX permissions')
    def test_actual_unsearchable_directory_under_nonroot_user(self):
        denied = self.root / 'private'
        self.executable('private/cache/chromium_headless_shell-1228/chrome-linux/headless_shell')
        os.environ['PLAYWRIGHT_BROWSERS_PATH'] = str(denied / 'cache')
        system = self.executable('system/chromium')
        denied.chmod(0o000)
        try:
            with patch.object(daily.shutil, 'which', return_value=system):
                self.assertEqual(daily.browser_candidates(), [system])
        finally:
            denied.chmod(0o700)

    def fake_playwright(self, launch):
        tool = SimpleNamespace(chromium=SimpleNamespace(launch=launch))
        class Context:
            def __enter__(self):
                return tool
            def __exit__(self, *args):
                return False
        return patch.dict(sys.modules, {'playwright.sync_api': SimpleNamespace(sync_playwright=lambda: Context())})

    def report_page(self):
        def screenshot(path, **kwargs):
            Path(path).write_bytes(b'\x89PNG\r\n\x1a\n' + b'\x00' * 8 + struct.pack('>II', 780, 1200) + b'\x00' * 1024)
        return SimpleNamespace(route=lambda *a: None, goto=lambda *a, **kw: None, evaluate=lambda *a: True,
                               set_viewport_size=lambda *a: None, screenshot=screenshot)

    def test_accessible_headless_shell_is_used_before_default_cache(self):
        launched = []
        page = self.report_page()
        def launch(**kwargs):
            launched.append(kwargs)
            return SimpleNamespace(new_page=lambda **kw: page, close=lambda: None)
        with self.fake_playwright(launch), patch.object(daily, 'browser_candidates', return_value=['/cache/headless_shell']):
            result = daily.png(self.root / 'report.html')
        self.assertEqual(launched[0]['executable_path'], '/cache/headless_shell')
        self.assertEqual(result['browser_fallback_count'], 0)

    def test_close_failure_does_not_block_next_browser(self):
        launched = []
        page = self.report_page()
        def failed(**kwargs):
            raise RuntimeError('closed browser')
        def launch(**kwargs):
            launched.append(kwargs)
            if len(launched) == 1:
                return SimpleNamespace(new_page=failed, close=failed)
            return SimpleNamespace(new_page=lambda **kw: page, close=lambda: None)
        with self.fake_playwright(launch), patch.object(daily, 'browser_candidates', return_value=['/cache/headless_shell']):
            result = daily.png(self.root / 'report.html')
        self.assertEqual(result['browser_fallback_count'], 1)
        self.assertEqual(len(launched), 2)

    def test_unavailable_browser_reports_stages_without_raw_error(self):
        def launch(**kwargs):
            raise PermissionError('a sensitive path must not be echoed')
        with self.fake_playwright(launch), patch.object(daily, 'browser_candidates', return_value=[]):
            with self.assertRaises(Failure) as caught:
                daily.png(self.root / 'report.html')
        self.assertEqual(caught.exception.code, 'NO_USABLE_BROWSER')
        self.assertEqual(caught.exception.details['failed_stages'], ['launch'])
        self.assertTrue(caught.exception.details['prepared_sources_preserved'])
        self.assertNotIn('sensitive', str(caught.exception.details))


if __name__ == '__main__':
    unittest.main()
