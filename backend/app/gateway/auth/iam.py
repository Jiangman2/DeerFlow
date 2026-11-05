"""CNOOC IAM authorization-code helpers and IAM account identity mapping."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urljoin

import httpx

from deerflow.config.auth_config import IAMAuthConfig

logger = logging.getLogger(__name__)

_AD_ACCOUNT_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_CORPORATE_DOMAIN = "cnooc.com.cn"
_LEGACY_IAM_SUFFIXES = ("@iam-sso.example.com", "@sso.invalid")


class IAMAuthError(RuntimeError):
    """IAM error that is safe to turn into a generic login failure."""


@dataclass(frozen=True, slots=True)
class IAMToken:
    uid: str
    access_token: str


@dataclass(frozen=True, slots=True)
class IAMIdentity:
    uid: str
    login_name: str | None
    application_accounts: tuple[str, ...]


def account_email(application_account: str) -> str:
    """Map the deployment-guaranteed AD account to its corporate mailbox."""
    account = application_account.strip().casefold()
    if not _AD_ACCOUNT_RE.fullmatch(account):
        raise IAMAuthError("IAM application account contains unsupported characters")
    return f"{account}@{_CORPORATE_DOMAIN}"


def legacy_account_email(application_account: str) -> str:
    """Return the retired synthetic address so old IAM records can migrate."""
    import hashlib

    normalized = application_account.strip().casefold()
    if not _AD_ACCOUNT_RE.fullmatch(normalized):
        raise IAMAuthError("IAM application account contains unsupported characters")
    return f"iam-account-{hashlib.sha256(normalized.encode()).hexdigest()}@iam-sso.example.com"


def is_legacy_account_email(value: str) -> bool:
    return value.startswith("iam-account-") and value.endswith(_LEGACY_IAM_SUFFIXES)


def _endpoint(config: IAMAuthConfig, path: str) -> str:
    if not config.base_url:
        raise IAMAuthError("IAM base URL is not configured")
    return urljoin(config.base_url.rstrip("/") + "/", path.lstrip("/"))


def build_authorization_url(config: IAMAuthConfig, *, redirect_uri: str, state: str) -> str:
    if not config.client_id:
        raise IAMAuthError("IAM client ID is not configured")
    params = {
        "response_type": "code",
        "client_id": config.client_id,
        "redirect_uri": redirect_uri,
        "state": state,
    }
    return f"{_endpoint(config, config.authorization_path)}?{urlencode(params)}"


def _unwrap_data(payload: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise IAMAuthError(f"IAM {label} returned invalid JSON")
    # The deployed CNOOC IAM endpoint returns a direct object with ``errcode``.
    # Keep the wrapped response shape as a compatibility fallback for newer IAM
    # gateways, but do not change the request wire format used in production.
    errcode = payload.get("errcode")
    if errcode is not None and str(errcode) not in {"0", "200"}:
        raise IAMAuthError(f"IAM {label} was rejected")
    code = payload.get("code")
    if code is not None and str(code) not in {"0", "200"}:
        raise IAMAuthError(f"IAM {label} was rejected")
    data = payload.get("data", payload)
    if not isinstance(data, dict):
        raise IAMAuthError(f"IAM {label} returned invalid data")
    return data


async def exchange_code(config: IAMAuthConfig, *, code: str, redirect_uri: str) -> IAMToken:
    if not config.client_id or not config.client_secret:
        raise IAMAuthError("IAM client credentials are not configured")
    # CNOOC IAM's documented endpoint expects query parameters, not a JSON
    # OAuth token payload. ``redirect_uri`` is checked at authorization time
    # and is deliberately not sent to this endpoint.
    params = {
        "client_id": config.client_id,
        "client_secret": config.client_secret,
        "code": code,
        # Keep IAM's established OAuth-compatible token request exactly
        # compatible with the prior deployed client.
        "grant_type": "authorization_code",
    }
    try:
        async with httpx.AsyncClient(timeout=config.request_timeout_seconds, trust_env=True) as client:
            response = await client.post(_endpoint(config, config.token_path), params=params)
            response.raise_for_status()
            data = _unwrap_data(response.json(), label="token exchange")
    except (httpx.HTTPError, ValueError) as exc:
        raise IAMAuthError("Unable to complete IAM token exchange") from exc
    token = data.get("access_token") or data.get("accessToken")
    uid = data.get("uid") or data.get("userId")
    if not isinstance(token, str) or not token or not isinstance(uid, (str, int)):
        raise IAMAuthError("IAM token exchange returned incomplete identity")
    return IAMToken(uid=str(uid), access_token=token)


def _accounts(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    result: list[str] = []
    for item in value:
        candidate = item.get("spRole") if isinstance(item, dict) else item
        if isinstance(candidate, str) and candidate.strip():
            result.append(candidate.strip())
    return tuple(dict.fromkeys(result))


async def get_user_info(config: IAMAuthConfig, token: IAMToken) -> IAMIdentity:
    try:
        async with httpx.AsyncClient(timeout=config.request_timeout_seconds, trust_env=True) as client:
            response = await client.get(
                _endpoint(config, config.userinfo_path),
                params={
                    "access_token": token.access_token,
                    "client_id": config.client_id,
                },
            )
            response.raise_for_status()
            data = _unwrap_data(response.json(), label="user information")
    except (httpx.HTTPError, ValueError) as exc:
        raise IAMAuthError("Unable to fetch IAM user information") from exc
    accounts = _accounts(data.get("spRoleList") or data.get("applicationAccounts"))
    if not accounts:
        raise IAMAuthError("IAM user has no authorized DeerFlow application account")
    login_name = data.get("loginName")
    return IAMIdentity(
        uid=token.uid,
        login_name=login_name if isinstance(login_name, str) else None,
        application_accounts=accounts,
    )
