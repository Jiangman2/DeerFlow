#!/usr/bin/env python3
"""Fetch and cache a WeChat public-account access token from environment variables."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen


TOKEN_URL = "https://api.weixin.qq.com/cgi-bin/token"


def mask(value: str) -> str:
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"


def fetch_token(app_id: str, app_secret: str) -> dict:
    query = urlencode(
        {
            "grant_type": "client_credential",
            "appid": app_id,
            "secret": app_secret,
        }
    )
    with urlopen(f"{TOKEN_URL}?{query}", timeout=20) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if "access_token" not in payload:
        raise RuntimeError(json.dumps(payload, ensure_ascii=False))
    payload["fetched_at"] = int(time.time())
    payload["expires_at"] = payload["fetched_at"] + int(payload.get("expires_in", 7200)) - 300
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch WeChat MP access token without storing AppSecret.")
    parser.add_argument("--cache-dir", required=True, help="Directory for the short-lived token cache.")
    parser.add_argument("--show-token", action="store_true", help="Print the token in full. Avoid unless debugging.")
    args = parser.parse_args()

    app_id = os.getenv("WECHAT_MP_APP_ID")
    app_secret = os.getenv("WECHAT_MP_APP_SECRET")
    if not app_id or not app_secret:
        raise SystemExit("Set WECHAT_MP_APP_ID and WECHAT_MP_APP_SECRET before running this script.")

    cache_dir = Path(args.cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / "access_token.json"

    token_payload = fetch_token(app_id, app_secret)
    cache_path.write_text(json.dumps(token_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    output = {
        "app_id": app_id,
        "access_token": token_payload["access_token"] if args.show_token else mask(token_payload["access_token"]),
        "expires_at": token_payload["expires_at"],
        "cache_path": str(cache_path.resolve()),
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
