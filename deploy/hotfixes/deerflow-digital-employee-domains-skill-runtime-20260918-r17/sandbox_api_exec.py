#!/usr/bin/env python3
"""Execute one command through the managed AIO Sandbox HTTP API.

The command is read from stdin so shell quoting cannot change the audited
payload. This helper is run inside Provisioner, where SANDBOX_API_KEY is already
available through the production Secret.
"""

from __future__ import print_function

import json
import os
import sys
import urllib.request
from base64 import b64decode


def main():
    if len(sys.argv) != 3:
        raise SystemExit("usage: sandbox_api_exec.py SANDBOX_BASE_URL COMMAND_BASE64")
    base_url = sys.argv[1].rstrip("/")
    if not base_url.startswith("http://sandbox-") or ".svc.cluster.local:" not in base_url:
        raise SystemExit("unsafe Sandbox URL")
    try:
        command = b64decode(sys.argv[2].encode("ascii"), validate=True).decode("utf-8")
    except Exception:
        raise SystemExit("invalid base64-encoded Sandbox command")
    if not command.strip():
        raise SystemExit("empty Sandbox command")
    api_key = os.environ.get("SANDBOX_API_KEY", "")
    if not api_key:
        raise SystemExit("SANDBOX_API_KEY is missing")
    body = json.dumps(
        {
            "command": command,
            "async_mode": False,
            "timeout": 120,
            "hard_timeout": 180,
            "max_output_length": 200000,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        base_url + "/v1/bash/exec",
        data=body,
        method="POST",
        headers={
            "Authorization": "Bearer " + api_key,
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=150) as response:
        payload = json.loads(response.read().decode("utf-8"))
    data = payload.get("data") or {}
    stdout = data.get("stdout") or ""
    stderr = data.get("stderr") or ""
    if stdout:
        sys.stdout.write(stdout)
        if not stdout.endswith("\n"):
            sys.stdout.write("\n")
    if stderr:
        sys.stderr.write(stderr)
        if not stderr.endswith("\n"):
            sys.stderr.write("\n")
    if payload.get("success") is False:
        raise SystemExit("Sandbox API reported failure: " + str(payload.get("message") or "unknown"))
    if data.get("status") != "completed" or data.get("exit_code") != 0:
        raise SystemExit(
            "Sandbox command failed: status=%s exit_code=%s"
            % (data.get("status"), data.get("exit_code"))
        )
    print("sandbox_aio_command_api=PASS")


if __name__ == "__main__":
    main()
