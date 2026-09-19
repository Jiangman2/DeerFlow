from __future__ import print_function

import base64
import importlib.util
import io
import json
import os
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "sandbox_api_exec",
    str(ROOT / "sandbox_api_exec.py"),
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class FakeResponse(object):
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class SandboxApiExecTests(unittest.TestCase):
    def run_main(self, payload):
        command = base64.b64encode(b"python3 /mnt/skills/custom/test/check.py").decode("ascii")
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.object(
            sys,
            "argv",
            ["sandbox_api_exec.py", "http://sandbox-test.deer-flow.svc.cluster.local:8000", command],
        ), mock.patch.dict(os.environ, {"SANDBOX_API_KEY": "test-key"}, clear=False), mock.patch.object(
            MODULE.urllib.request,
            "urlopen",
            return_value=FakeResponse(payload),
        ) as urlopen, redirect_stdout(stdout), redirect_stderr(stderr):
            MODULE.main()
        request = urlopen.call_args[0][0]
        self.assertEqual(request.full_url, "http://sandbox-test.deer-flow.svc.cluster.local:8000/v1/bash/exec")
        self.assertEqual(request.get_header("Authorization"), "Bearer test-key")
        body = json.loads(request.data.decode("utf-8"))
        self.assertFalse(body["async_mode"])
        self.assertEqual(body["hard_timeout"], 180)
        return stdout.getvalue()

    def test_success_requires_completed_zero_exit(self):
        output = self.run_main(
            {
                "success": True,
                "data": {"status": "completed", "exit_code": 0, "stdout": "skill=PASS\n"},
            }
        )
        self.assertIn("skill=PASS", output)
        self.assertIn("sandbox_aio_command_api=PASS", output)

    def test_nonzero_exit_fails_closed(self):
        with self.assertRaises(SystemExit):
            self.run_main(
                {
                    "success": True,
                    "data": {"status": "completed", "exit_code": 7, "stderr": "boom"},
                }
            )

    def test_rejects_untrusted_url(self):
        command = base64.b64encode(b"true").decode("ascii")
        with mock.patch.object(sys, "argv", ["sandbox_api_exec.py", "http://example.test", command]):
            with self.assertRaises(SystemExit):
                MODULE.main()


if __name__ == "__main__":
    unittest.main()
