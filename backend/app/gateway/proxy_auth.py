import base64
import hmac
import os
import time
from hashlib import sha256

from fastapi import HTTPException


def _secret() -> bytes:
    value = os.getenv("DEERFLOW_PROXY_SECRET") or ""
    if not value:
        raise HTTPException(status_code=500, detail="proxy secret not configured")
    return value.encode("utf-8")


def verify_proxy_headers(user_id: str, ts: str, sig: str, max_skew_seconds: int = 300) -> None:
    if not user_id or not ts or not sig:
        raise HTTPException(status_code=401, detail="missing proxy headers")
    try:
        ts_int = int(ts)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="invalid proxy timestamp") from exc
    now = int(time.time())
    if abs(now - ts_int) > max_skew_seconds:
        raise HTTPException(status_code=401, detail="stale proxy timestamp")
    msg = f"{user_id}.{ts}".encode()
    digest = hmac.new(_secret(), msg, sha256).digest()
    expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    if not hmac.compare_digest(expected, sig):
        raise HTTPException(status_code=401, detail="invalid proxy signature")
