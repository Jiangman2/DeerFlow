"""CNOOC platform-token authentication helpers."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any

import httpx

# Match the original frontend SSO implementation. The data-lake appCode is
# long-lived enough to reuse, but stale appCodes are cleared and retried once
# by check_cnooc_token.
APP_CODE_TTL_SECONDS = 110 * 60
logger = logging.getLogger(__name__)


class CnoocAuthError(Exception):
    """Safe-to-display CNOOC auth error."""

    def __init__(self, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(slots=True)
class CnoocAuthConfig:
    base_url: str
    app_name: str
    app_id: str
    token_check_api_token: str
    token_check_login_user_code: str
    user_info_api_token: str


@dataclass(slots=True)
class CnoocUser:
    platform_user_id: str
    deerflow_user_id: str
    name: str
    email: str
    organization_id: str | None = None
    organization_code: str | None = None
    organization_name: str | None = None


@dataclass(slots=True)
class CnoocTokenCheckResult:
    provider: str
    platform_token_hash: str
    user: CnoocUser


_cached_app_code: tuple[str, float] | None = None


def _require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise CnoocAuthError(f"missing CNOOC OAuth config: {name}", 500)
    return value


def _load_config() -> CnoocAuthConfig:
    return CnoocAuthConfig(
        base_url=_require_env("CNOOC_OAUTH_BASE_URL").rstrip("/"),
        app_name=_require_env("CNOOC_OAUTH_APP_NAME"),
        app_id=_require_env("CNOOC_OAUTH_APP_ID"),
        token_check_api_token=_require_env("CNOOC_OAUTH_TOKEN_CHECK_API_TOKEN"),
        token_check_login_user_code=_require_env("CNOOC_OAUTH_TOKEN_CHECK_LOGIN_USER_CODE"),
        user_info_api_token=_require_env("CNOOC_OAUTH_USER_INFO_API_TOKEN"),
    )


def _digest_token(token: str) -> str:
    return hashlib.sha1(token.encode("utf-8")).hexdigest()


def _safe_user_id(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", value)
    if not safe:
        return _digest_token(value)[:10]
    if safe != value or len(safe) > 64:
        digest = _digest_token(value)[:10]
        prefix = safe[:48].rstrip("_")
        return f"{prefix}_{digest}" if prefix else digest
    return safe


def _query_payload(key: str, token: str) -> str:
    return json.dumps({"jsonObj": {key: token}}, ensure_ascii=False, separators=(",", ":"))


def _api_success(payload: dict[str, Any], label: str, status_code: int = 502) -> None:
    if payload.get("code") != 200:
        code = payload.get("code")
        msg = payload.get("msg") or payload.get("message")
        logger.warning("%s failed: code=%r msg=%r", label, code, msg)
        detail = f"{label} failed"
        if code is not None or msg:
            detail = f"{detail}: code={code!r} msg={str(msg)[:200]!r}"
        raise CnoocAuthError(detail, status_code)


async def _read_json(response: httpx.Response, label: str) -> dict[str, Any]:
    if response.status_code < 200 or response.status_code >= 300:
        raise CnoocAuthError(f"{label} request failed", 502)
    try:
        payload = response.json()
    except ValueError as exc:
        raise CnoocAuthError(f"{label} returned invalid json", 502) from exc
    if not isinstance(payload, dict):
        raise CnoocAuthError(f"{label} returned invalid json", 502)
    return payload


async def _fetch_app_code(client: httpx.AsyncClient, config: CnoocAuthConfig) -> str:
    global _cached_app_code

    if _cached_app_code and _cached_app_code[1] > time.time():
        return _cached_app_code[0]

    response = await client.get(
        f"{config.base_url}/apigateway/appauth/getappid",
        params={
            "appName": config.app_name,
            "appId": config.app_id,
            "timeStamp": str(int(time.time())),
        },
    )
    payload = await _read_json(response, "CNOOC appCode")
    _api_success(payload, "CNOOC appCode")
    app_code = payload.get("data")
    if not isinstance(app_code, str) or not app_code:
        raise CnoocAuthError("CNOOC appCode missing data", 502)
    _cached_app_code = (app_code, time.time() + APP_CODE_TTL_SECONDS)
    return app_code


async def _verify_token(client: httpx.AsyncClient, config: CnoocAuthConfig, app_code: str, token: str) -> None:
    response = await client.get(
        f"{config.base_url}/apigateway/kzxmw/zcjksjfw_9543",
        params={
            "appCode": app_code,
            "query": _query_payload("zhId", token),
        },
        headers={
            "apiToken": config.token_check_api_token,
            "loginUserCode": config.token_check_login_user_code,
        },
    )
    payload = await _read_json(response, "CNOOC token verification")
    _api_success(payload, "CNOOC token verification", 401)


async def _fetch_user_info(client: httpx.AsyncClient, config: CnoocAuthConfig, app_code: str, token: str) -> Any:
    response = await client.get(
        f"{config.base_url}/apigateway/zhy/tokenCheck",
        params={
            "appCode": app_code,
            "query": _query_payload("token", token),
        },
        headers={"apiToken": config.user_info_api_token},
    )
    payload = await _read_json(response, "CNOOC user info")
    _api_success(payload, "CNOOC user info")
    return payload.get("data")


def _required_string(row: dict[str, Any], field: str) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value:
        raise CnoocAuthError(f"missing required user field: {field}", 502)
    return value


def _optional_string(row: dict[str, Any], field: str) -> str | None:
    value = row.get(field)
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _map_user_info(data: Any, token: str) -> CnoocTokenCheckResult:
    if not isinstance(data, dict):
        raise CnoocAuthError("CNOOC user info returned invalid data", 502)

    platform_user_id = _required_string(data, "userid")
    username = _required_string(data, "username")
    name = _required_string(data, "ryxm")
    deerflow_user_id = f"cnooc_{_safe_user_id(platform_user_id)}"
    return CnoocTokenCheckResult(
        provider="cnooc",
        platform_token_hash=_digest_token(token),
        user=CnoocUser(
            platform_user_id=platform_user_id,
            deerflow_user_id=deerflow_user_id,
            name=name,
            email=f"{username.lower()}@cnooc.com.cn",
            organization_id=_optional_string(data, "bmId"),
            organization_code=_optional_string(data, "bmdm"),
            organization_name=_optional_string(data, "bmmc"),
        ),
    )


async def check_cnooc_token(token: str) -> CnoocTokenCheckResult:
    token = token.strip()
    if not token:
        raise CnoocAuthError("token is required", 400)

    config = _load_config()
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0), verify=os.getenv("CNOOC_OAUTH_TLS_VERIFY", "true") != "false") as client:
        app_code = await _fetch_app_code(client, config)
        try:
            await _verify_token(client, config, app_code, token)
            user_info = await _fetch_user_info(client, config, app_code, token)
        except CnoocAuthError:
            global _cached_app_code
            _cached_app_code = None
            app_code = await _fetch_app_code(client, config)
            await _verify_token(client, config, app_code, token)
            user_info = await _fetch_user_info(client, config, app_code, token)
    return _map_user_info(user_info, token)
