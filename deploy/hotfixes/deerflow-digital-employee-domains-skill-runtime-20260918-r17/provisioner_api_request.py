#!/usr/bin/env python3
"""Make one authenticated loopback Provisioner API request inside its Pod."""

from __future__ import annotations

import os
import sys
import urllib.request


def main() -> None:
    if len(sys.argv) != 4:
        raise SystemExit("usage: provisioner_api_request.py METHOD PATH JSON_OR_EMPTY")
    method, path, payload = sys.argv[1:]
    if method not in {"GET", "POST", "DELETE"} or not path.startswith("/api/"):
        raise SystemExit("unsafe Provisioner request")
    key = os.environ.get("PROVISIONER_API_KEY", "")
    if not key:
        raise SystemExit("PROVISIONER_API_KEY is missing")
    data = payload.encode("utf-8") if payload else None
    request = urllib.request.Request(
        "http://127.0.0.1:8002" + path,
        data=data,
        method=method,
        headers={"X-API-Key": key, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read().decode("utf-8")
        if response.status < 200 or response.status >= 300:
            raise SystemExit("Provisioner returned HTTP %d" % response.status)
        print(body)


if __name__ == "__main__":
    main()
