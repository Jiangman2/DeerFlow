#!/usr/bin/env python3
"""Probe both APIs from the AIO container of a disposable PPT Sandbox Pod.

Run with ``kubectl exec -i POD -c sandbox -- python3 -`` and this file on stdin.
The Pod must be created from the target Provisioner manifest and be Ready first.
No production thread workspace is touched; the file check uses /tmp in the
disposable container.
"""

from __future__ import annotations

import json
import os
import urllib.request


AIO_URL = "http://127.0.0.1:8080"
HOST_URL = "http://127.0.0.1:8788"
CANARY_FILE = "/tmp/ppt-master-dual-container-canary.txt"
CANARY_TEXT = "ppt-master-dual-container-canary-ok"


def _request_json(opener, url: str, *, key: str = "", body: dict | None = None) -> dict:
    headers = {"Authorization": "Bearer " + key} if key else {}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers)
    with opener.open(request, timeout=10) as response:
        if response.status != 200:
            raise RuntimeError("unexpected HTTP status for " + url)
        result = json.load(response)
    if not isinstance(result, dict):
        raise RuntimeError("non-object JSON response for " + url)
    return result


def probe(*, key: str, opener=None) -> None:
    if not key:
        raise RuntimeError("SANDBOX_API_KEY is required for the AIO canary")
    if opener is None:
        # Loopback checks must not traverse an enterprise HTTP proxy.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    _request_json(opener, AIO_URL + "/v1/sandbox", key=key)
    print("ppt_master_canary_aio_8080=PASS")

    command = "printf '%s' '" + CANARY_TEXT + "' > " + CANARY_FILE
    execution = _request_json(
        opener,
        AIO_URL + "/v1/shell/exec",
        key=key,
        body={"command": command, "timeout": 15, "hard_timeout": 15},
    )
    result = execution.get("data")
    if (
        execution.get("success") is not True
        or not isinstance(result, dict)
        or result.get("status") != "completed"
        or result.get("exit_code") != 0
    ):
        raise RuntimeError("AIO shell command did not complete successfully")
    print("ppt_master_canary_aio_command=PASS")

    read = _request_json(
        opener,
        AIO_URL + "/v1/file/read",
        key=key,
        body={"file": CANARY_FILE},
    )
    file_data = read.get("data")
    if (
        read.get("success") is not True
        or not isinstance(file_data, dict)
        or file_data.get("content") != CANARY_TEXT
    ):
        raise RuntimeError("AIO file API did not return the canary content")
    print("ppt_master_canary_aio_read_file=PASS")

    host = _request_json(opener, HOST_URL + "/health")
    if host.get("status") != "ok":
        raise RuntimeError("PPT Agent Host is not healthy")
    print("ppt_master_canary_agent_host_8788=PASS")


if __name__ == "__main__":
    probe(key=os.environ.get("SANDBOX_API_KEY", ""))
