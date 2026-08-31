"""CNOOC platform-token authentication helpers."""

from __future__ import annotations

import asyncio
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
DEFAULT_CONNECT_TIMEOUT_SECONDS = 5.0
DEFAULT_READ_TIMEOUT_SECONDS = 30.0
DEFAULT_REQUEST_ATTEMPTS = 2
DEFAULT_RETRY_BACKOFF_SECONDS = 0.5
logger = logging.getLogger(__name__)


class _CnoocHttpxLogFilter(logging.Filter):
    """Prevent platform login tokens from appearing in httpx request logs."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if "/apigateway/" not in message:
            return True
        redacted = re.sub(
            r"([?&](?:query|token)=)[^&\s\"]+",
            r"\1<redacted>",
            message,
            flags=re.IGNORECASE,
        )
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


_httpx_logger = logging.getLogger("httpx")
if not any(isinstance(item, _CnoocHttpxLogFilter) for item in _httpx_logger.filters):
    _httpx_logger.addFilter(_CnoocHttpxLogFilter())


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
class CnoocDataLakeExchangeConfig:
    base_url: str
    app_name: str
    app_id: str
    api_token: str
    login_user_code: str
    callback_id: str


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


def _positive_float_env(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _positive_int_env(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _truthy_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _client_timeout() -> httpx.Timeout:
    return httpx.Timeout(
        connect=_positive_float_env("CNOOC_OAUTH_CONNECT_TIMEOUT_SECONDS", DEFAULT_CONNECT_TIMEOUT_SECONDS),
        read=_positive_float_env("CNOOC_OAUTH_READ_TIMEOUT_SECONDS", DEFAULT_READ_TIMEOUT_SECONDS),
        write=_positive_float_env("CNOOC_OAUTH_WRITE_TIMEOUT_SECONDS", DEFAULT_CONNECT_TIMEOUT_SECONDS),
        pool=_positive_float_env("CNOOC_OAUTH_POOL_TIMEOUT_SECONDS", DEFAULT_CONNECT_TIMEOUT_SECONDS),
    )


async def _get_with_retry(
    client: httpx.AsyncClient,
    url: str,
    *,
    label: str,
    params: dict[str, str],
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    attempts = _positive_int_env("CNOOC_OAUTH_REQUEST_ATTEMPTS", DEFAULT_REQUEST_ATTEMPTS)
    backoff = _positive_float_env("CNOOC_OAUTH_RETRY_BACKOFF_SECONDS", DEFAULT_RETRY_BACKOFF_SECONDS)
    last_error: Exception | None = None

    for attempt in range(1, attempts + 1):
        try:
            response = await client.get(url, params=params, headers=headers)
        except httpx.TimeoutException as exc:
            last_error = exc
            logger.warning("%s timed out (attempt %d/%d)", label, attempt, attempts)
        except httpx.RequestError as exc:
            last_error = exc
            logger.warning(
                "%s transport failed (attempt %d/%d): %s",
                label,
                attempt,
                attempts,
                type(exc).__name__,
            )
        else:
            if response.status_code < 500:
                return response
            logger.warning(
                "%s upstream HTTP %d (attempt %d/%d)",
                label,
                response.status_code,
                attempt,
                attempts,
            )
            last_error = CnoocAuthError(f"{label} upstream unavailable", 503)

        if attempt < attempts:
            await asyncio.sleep(backoff * attempt)

    raise CnoocAuthError(f"{label} temporarily unavailable", 503) from last_error


async def _post_with_retry(
    client: httpx.AsyncClient,
    url: str,
    *,
    label: str,
    headers: dict[str, str],
    json_body: dict[str, Any],
) -> httpx.Response:
    attempts = _positive_int_env("CNOOC_OAUTH_REQUEST_ATTEMPTS", DEFAULT_REQUEST_ATTEMPTS)
    backoff = _positive_float_env("CNOOC_OAUTH_RETRY_BACKOFF_SECONDS", DEFAULT_RETRY_BACKOFF_SECONDS)
    last_error: Exception | None = None

    for attempt in range(1, attempts + 1):
        try:
            response = await client.post(url, headers=headers, json=json_body)
        except httpx.TimeoutException as exc:
            last_error = exc
            logger.warning("%s timed out (attempt %d/%d)", label, attempt, attempts)
        except httpx.RequestError as exc:
            last_error = exc
            logger.warning(
                "%s transport failed (attempt %d/%d): %s",
                label,
                attempt,
                attempts,
                type(exc).__name__,
            )
        else:
            if response.status_code < 500:
                return response
            logger.warning(
                "%s upstream HTTP %d (attempt %d/%d)",
                label,
                response.status_code,
                attempt,
                attempts,
            )
            last_error = CnoocAuthError(f"{label} upstream unavailable", 503)

        if attempt < attempts:
            await asyncio.sleep(backoff * attempt)

    raise CnoocAuthError(f"{label} temporarily unavailable", 503) from last_error


def _load_config() -> CnoocAuthConfig:
    return CnoocAuthConfig(
        base_url=_require_env("CNOOC_OAUTH_BASE_URL").rstrip("/"),
        app_name=_require_env("CNOOC_OAUTH_APP_NAME"),
        app_id=_require_env("CNOOC_OAUTH_APP_ID"),
        token_check_api_token=_require_env("CNOOC_OAUTH_TOKEN_CHECK_API_TOKEN"),
        token_check_login_user_code=_require_env("CNOOC_OAUTH_TOKEN_CHECK_LOGIN_USER_CODE"),
        user_info_api_token=_require_env("CNOOC_OAUTH_USER_INFO_API_TOKEN"),
    )


def _load_data_lake_exchange_config() -> CnoocDataLakeExchangeConfig:
    return CnoocDataLakeExchangeConfig(
        base_url=_require_env("CNOOC_OAUTH_BASE_URL").rstrip("/"),
        app_name=_require_env("CNOOC_OAUTH_APP_NAME"),
        app_id=_require_env("CNOOC_OAUTH_APP_ID"),
        api_token=_require_env("CNOOC_DATALAKE_TOKEN_API_TOKEN"),
        login_user_code=_require_env("CNOOC_DATALAKE_TOKEN_LOGIN_USER_CODE"),
        callback_id=os.getenv("CNOOC_DATALAKE_CALLBACK_ID", "deerflow").strip() or "deerflow",
    )


def hash_cnooc_platform_token(token: str) -> str:
    """Return the non-reversible database binding for a platform token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _digest_token(token: str) -> str:
    """Keep the historical digest used only in derived safe user IDs."""
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


async def _fetch_app_code(
    client: httpx.AsyncClient,
    config: CnoocAuthConfig | CnoocDataLakeExchangeConfig,
) -> str:
    global _cached_app_code

    if _cached_app_code and _cached_app_code[1] > time.time():
        return _cached_app_code[0]

    response = await _get_with_retry(
        client,
        f"{config.base_url}/apigateway/appauth/getappid",
        label="CNOOC appCode",
        params={
            "appName": config.app_name,
            "appId": config.app_id,
            # The platform app-auth contract expects the first eight digits of
            # the Unix-seconds string, not the full ten-digit epoch value.
            "timeStamp": str(int(time.time()))[:8],
        },
    )
    payload = await _read_json(response, "CNOOC appCode")
    _api_success(payload, "CNOOC appCode")
    app_code = payload.get("data")
    if not isinstance(app_code, str) or not app_code:
        raise CnoocAuthError("CNOOC appCode missing data", 502)
    _cached_app_code = (app_code, time.time() + APP_CODE_TTL_SECONDS)
    return app_code


async def _exchange_data_lake_token(
    client: httpx.AsyncClient,
    config: CnoocDataLakeExchangeConfig,
    app_code: str,
    challenge: str,
) -> str:
    response = await _post_with_retry(
        client,
        f"{config.base_url}/apigateway/zcshf/zcjksjfw_9725",
        label="CNOOC Data Lake token exchange",
        headers={
            "appCode": app_code,
            "loginUserCode": config.login_user_code,
            "apiToken": config.api_token,
        },
        json_body={
            "jsonObj": {
                "callbackId": config.callback_id,
                "challenge": challenge,
            }
        },
    )
    payload = await _read_json(response, "CNOOC Data Lake token exchange")
    _api_success(payload, "CNOOC Data Lake token exchange", 403)
    token = payload.get("data")
    if not isinstance(token, str) or not token.strip():
        raise CnoocAuthError("CNOOC Data Lake token exchange missing data", 502)
    return token.strip()


async def _verify_token(client: httpx.AsyncClient, config: CnoocAuthConfig, app_code: str, token: str) -> None:
    response = await _get_with_retry(
        client,
        f"{config.base_url}/apigateway/kzxmw/zcjksjfw_9543",
        label="CNOOC token verification",
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
    response = await _get_with_retry(
        client,
        f"{config.base_url}/apigateway/zhy/tokenCheck",
        label="CNOOC user info",
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
        platform_token_hash=hash_cnooc_platform_token(token),
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
    async with httpx.AsyncClient(
        timeout=_client_timeout(),
        verify=_truthy_env("CNOOC_OAUTH_TLS_VERIFY", True),
        # This is an internal identity service.  Never send bearer-equivalent
        # platform tokens through the enterprise Internet proxy.
        trust_env=_truthy_env("CNOOC_OAUTH_TRUST_ENV", False),
    ) as client:
        app_code = await _fetch_app_code(client, config)
        try:
            await _verify_token(client, config, app_code, token)
            user_info = await _fetch_user_info(client, config, app_code, token)
        except CnoocAuthError as exc:
            if exc.status_code == 503:
                raise
            global _cached_app_code
            _cached_app_code = None
            app_code = await _fetch_app_code(client, config)
            await _verify_token(client, config, app_code, token)
            user_info = await _fetch_user_info(client, config, app_code, token)
    return _map_user_info(user_info, token)


async def issue_data_lake_token(challenge: str) -> str:
    """Exchange a persisted callback challenge for a Data Lake platform token."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", challenge):
        raise CnoocAuthError("invalid Data Lake challenge", 400)

    config = _load_data_lake_exchange_config()
    async with httpx.AsyncClient(
        timeout=_client_timeout(),
        verify=_truthy_env("CNOOC_OAUTH_TLS_VERIFY", True),
        trust_env=_truthy_env("CNOOC_OAUTH_TRUST_ENV", False),
    ) as client:
        app_code = await _fetch_app_code(client, config)
        try:
            return await _exchange_data_lake_token(client, config, app_code, challenge)
        except CnoocAuthError as exc:
            if exc.status_code == 503:
                raise
            global _cached_app_code
            _cached_app_code = None
            app_code = await _fetch_app_code(client, config)
            return await _exchange_data_lake_token(client, config, app_code, challenge)
