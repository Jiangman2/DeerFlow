"""Capture subprocess boundary, no real browser or external request in this suite."""

import asyncio
import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import MappingProxyType
from unittest.mock import MagicMock, patch

from deerflow.ppt_confirmation import proposal

SOURCE = Path(__file__).resolve().parents[2] / "docker/sandbox-ppt-master/ppt_confirmation_capture.py"
SPEC = importlib.util.spec_from_file_location("capture_process_fixture", SOURCE)
capture = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(capture)


class CaptureProcessTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = proposal.PageSnapshot(1, "a" * 64, "b" * 64, b"{}", MappingProxyType({"/api/recommendations": b"{}"}), MappingProxyType({"/": b"<html></html>"}), "c" * 64)
        self.child = MagicMock()
        self.child.returncode = 0
        self.child.poll.return_value = 0
        self.child.communicate.return_value = (b'{"payload":{"stage":"stage1"},"console_errors":0,"confirmation_submitted":false}', None)

    def test_snapshot_roundtrip_preserves_exact_bytes_and_version(self):
        restored = capture.unpack(capture.pack(self.snapshot), proposal)
        self.assertEqual(restored, self.snapshot)

    def test_capture_launch_is_isolated_and_does_not_inherit_credentials_or_proxy(self):
        with patch.dict(os.environ, {"PPT_RUNTIME_CAPABILITY": "fixture-only", "HTTPS_PROXY": "fixture-proxy"}), patch.object(capture.subprocess, "Popen", return_value=self.child) as spawn:
            result = capture.capture(self.snapshot, proposal=proposal)
        args, kwargs = spawn.call_args
        self.assertEqual(args[0][-1], "--capture-child")
        self.assertIn("-I", args[0])
        self.assertIs(kwargs["shell"], False)
        self.assertFalse("PPT_RUNTIME_CAPABILITY" in kwargs["env"])
        self.assertFalse("HTTPS_PROXY" in kwargs["env"])
        self.assertEqual(result.owner_version, self.snapshot.owner_version)
        self.assertEqual(result.payload, {"stage": "stage1"})
        self.child.communicate.assert_called_once_with(capture.pack(self.snapshot), timeout=32)

    def test_timeout_kills_only_owned_child_and_returns_no_proposal(self):
        self.child.poll.return_value = None
        self.child.communicate.side_effect = subprocess.TimeoutExpired("fixture", 32)
        with patch.object(capture.subprocess, "Popen", return_value=self.child), patch.object(capture.os, "killpg", create=True) as kill:
            with self.assertRaises(RuntimeError):
                capture.capture(self.snapshot, proposal=proposal)
        if os.name == "posix":
            kill.assert_called_once()
        else:
            self.child.kill.assert_called_once()
        self.child.wait.assert_called_once_with(timeout=3)
        self.child.stdin.close.assert_called_once()
        self.child.stdout.close.assert_called_once()

    def test_bad_child_output_cannot_become_a_proposal(self):
        for change in ({"console_errors": False}, {"confirmation_submitted": True}, {"payload": []}, {"extra": "not-allowed"}):
            raw = json.dumps({"payload": {"stage": "stage1"}, "console_errors": 0, "confirmation_submitted": False, **change}).encode()
            self.child.communicate.return_value = (raw, None)
            with self.subTest(change=change), patch.object(capture.subprocess, "Popen", return_value=self.child), self.assertRaises(RuntimeError):
                capture.capture(self.snapshot, proposal=proposal)


@unittest.skipUnless(os.environ.get("PPT_CONFIRMATION_TEST_CHROMIUM"), "explicit existing local browser path required")
class RealOfflineCalculationTests(unittest.TestCase):
    def test_real_stage1_calculation_closes_browser_without_confirming(self):
        from test_ppt_confirmation_proposal import OfficialFixture

        with tempfile.TemporaryDirectory(prefix="ppt-owned-capture-") as directory:
            with patch.dict(os.environ, {"PPT_MASTER_CONFIRMATION_PROTOCOL": "1"}):
                fixture = OfficialFixture(directory)
            snapshot = fixture.snapshot()
            # Harness imports select a Windows selector loop; this isolated
            # browser child needs subprocess-capable Proactor, without changing
            # any app/global policy or the upgrade task's runtime.
            loop_factory = asyncio.ProactorEventLoop if os.name == "nt" else asyncio.new_event_loop
            with asyncio.Runner(loop_factory=loop_factory) as runner:
                value = runner.run(capture.calculate(snapshot, proposal, browser_executable=os.environ["PPT_CONFIRMATION_TEST_CHROMIUM"]))
            self.assertIs(value["confirmation_submitted"], False)
            self.assertEqual(value["console_errors"], 0)
            self.assertEqual(value["payload"]["stage"], "stage1")
            self.assertFalse((fixture.confirm / "result.json").exists())
            self.assertFalse((fixture.confirm / "template_selection.json").exists())


if __name__ == "__main__":
    unittest.main()
