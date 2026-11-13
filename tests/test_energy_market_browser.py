import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "energy_browser_entry", BASE / "release/post44-source-sync-20261113/parent/skills/public/energy-market-daily/scripts/entry.py"
)
entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entry)


class BrowserTests(unittest.TestCase):
    def test_linux_uses_qualified_headless_shell_without_path_lookup(self):
        with tempfile.TemporaryDirectory() as folder:
            browser = Path(folder) / "headless_shell"
            browser.write_bytes(b"fixture")
            with (
                patch.object(entry.sys, "platform", "linux"),
                patch.object(entry, "LINUX_REPORT_BROWSER", str(browser)),
                patch.object(entry.shutil, "which") as which,
            ):
                self.assertEqual(entry.report_browser(), str(browser))
                which.assert_not_called()

    def test_missing_qualified_browser_does_not_fall_back_to_crashing_chrome(self):
        with (
            patch.object(entry.sys, "platform", "linux"),
            patch.object(
                entry, "LINUX_REPORT_BROWSER", "/absent/qualified/headless_shell"
            ),
            patch.object(entry.shutil, "which") as which,
        ):
            with self.assertRaisesRegex(ValueError, "APPROVED_BROWSER_REQUIRED"):
                entry.report_browser()
            which.assert_not_called()

    def test_explicit_browser_keeps_original_override_contract(self):
        with tempfile.TemporaryDirectory() as folder:
            browser = Path(folder) / "test-browser"
            browser.write_bytes(b"fixture")
            with patch.object(entry.sys, "platform", "linux"):
                self.assertEqual(entry.report_browser(str(browser)), str(browser))

    def test_default_is_the_observed_arm64_headless_binary(self):
        self.assertEqual(
            entry.LINUX_REPORT_BROWSER,
            "/root/.cache/ms-playwright/chromium_headless_shell-1228/chrome-linux/headless_shell",
        )


if __name__ == "__main__":
    unittest.main()
