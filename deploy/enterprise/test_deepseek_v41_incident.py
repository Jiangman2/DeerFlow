"""Offline Linux Python 3.7 tests for the historical incident collector."""

import hashlib
import importlib.util
import json
import os
import pathlib
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("incident", pathlib.Path(__file__).with_name("collect-deepseek-v41-incident.py"))
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


class IncidentTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = os.open(self.directory.name, os.O_RDONLY | os.O_DIRECTORY)
        self.operation = "0" * 32
        self.parent = pathlib.Path(self.directory.name) / ("portal-deepseek-v41-preparation-" + self.operation)
        self.parent.mkdir(mode=0o700)
        self.report = {
            "operation": self.operation,
            "status": "INCOMPLETE",
            "secret_creation_requested": False,
            "workload_modified": False,
            "provider": {"http_status": 503, "stage": "flash", "provider_requests_attempted": 1},
            "authorization": "DO_NOT_EXPORT",
            "raw_response": "DO_NOT_EXPORT",
        }
        self.save()

    def save(self):
        raw = json.dumps(self.report).encode()
        self.file = self.parent / "REPORT.json"
        self.file.write_bytes(raw)
        self.file.chmod(0o600)
        os.utime(str(self.file), (1792546200, 1792546200))
        self.pin = ("fixture", self.operation, len(raw), hashlib.sha256(raw).hexdigest(), "flash", 1)

    def tearDown(self):
        os.close(self.root)
        self.directory.cleanup()

    def test_safe_projection_and_explicit_missing_request_id(self):
        result = M.read_report(self.root, self.pin)
        self.assertNotIn("DO_NOT_EXPORT", json.dumps(result))
        self.assertEqual(result["upstream_request_id"], "NOT_RECORDED")
        self.assertTrue(result["report_file_mtime_utc"].endswith("+00:00"))
        self.assertTrue(result["report_file_mtime_shanghai"].endswith("+08:00"))
        self.assertEqual(result["timestamp_meaning"], "REPORT_FILE_MTIME_APPROXIMATE_END_NOT_REQUEST_START")

    def test_mismatched_hash_refused(self):
        pin = list(self.pin)
        pin[3] = "f" * 64
        with self.assertRaises(M.Refused):
            M.read_report(self.root, pin)

    def test_unsafe_permissions_refused(self):
        self.file.chmod(0o644)
        with self.assertRaises(M.Refused):
            M.read_report(self.root, self.pin)

    def test_symlink_refused(self):
        self.file.rename(self.parent / "real.json")
        self.file.symlink_to("real.json")
        with self.assertRaises(OSError):
            M.read_report(self.root, self.pin)

    def test_wrong_historical_stage_refused(self):
        self.report["provider"]["stage"] = "tool_resume"
        self.save()
        with self.assertRaises(M.Refused):
            M.read_report(self.root, self.pin)

    def test_possible_mutation_refused(self):
        self.report["secret_creation_requested"] = True
        self.save()
        with self.assertRaises(M.Refused):
            M.read_report(self.root, self.pin)

    def test_replaced_pod_address_not_attributed_to_incident(self):
        reply = type("Reply", (), {"returncode": 0, "stdout": json.dumps({"metadata": {"uid": "different"}, "status": {"podIP": "10.1.2.3"}}).encode()})()
        metadata = type("Stat", (), {"st_uid": 0, "st_mode": 0o755})()
        with patch.object(M.shutil, "which", return_value="/usr/bin/kubectl"):
            with patch.object(M.os, "lstat", return_value=metadata):
                with patch.object(M.subprocess, "run", return_value=reply) as run:
                    result = M.current_pod()
        self.assertNotIn("10.1.2.3", json.dumps(result))
        self.assertEqual(result["status"], "POD_IDENTITY_CHANGED_ADDRESSES_WITHHELD")
        self.assertIn("get", run.call_args[0][0])
        self.assertNotIn("exec", run.call_args[0][0])


if __name__ == "__main__":
    unittest.main()
