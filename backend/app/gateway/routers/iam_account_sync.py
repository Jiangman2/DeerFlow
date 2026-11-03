"""CNOOC IAM BIM account synchronization endpoint.

This is intentionally separate from browser authentication.  BIM sends an
encrypted, signed request; Gateway authenticates it with dedicated service
credentials and returns an encrypted response.  No browser cookie, CSRF token,
or user-controlled identity participates in this path.
"""

from __future__ import annotations

import logging
import os
import secrets
from collections.abc import Mapping
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse

from app.gateway.auth.iam import IAMAuthError, account_email, is_legacy_account_email, legacy_account_email
from app.gateway.auth.iam_codec import (
    IAMCodecError,
    canonicalize_signed_fields,
    decrypt_json_request,
    encrypt_json_response,
    sm3_hexdigest,
)
from app.gateway.deps import get_local_provider
from deerflow.config.app_config import get_app_config

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/iam/account-sync", tags=["iam-account-sync"])
_MAX_ENCRYPTED_BODY_BYTES = 128 * 1024


def _string(payload: Mapping[str, Any], key: str, *, required: bool = True) -> str | None:
    value = payload.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    if required:
        raise HTTPException(status_code=400, detail=f"IAM request requires {key}")
    return None


def _account_name(payload: Mapping[str, Any], *, required: bool = True) -> str | None:
    return _string(payload, "__NAME__", required=False) or _string(payload, "oaAccount", required=required)


def _state(payload: Mapping[str, Any], *, required: bool = True) -> bool | None:
    value = payload.get("state") if payload.get("state") is not None else payload.get("__ENABLE__")
    if value is None:
        if required:
            raise HTTPException(status_code=400, detail="IAM request requires __ENABLE__")
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
        return value.strip().lower() == "true"
    raise HTTPException(status_code=400, detail="IAM enablement value is invalid")


def _account_schema() -> list[dict[str, Any]]:
    return [
        {"multivalued": False, "name": "__NAME__", "required": True, "type": "String"},
        {"multivalued": False, "name": "fullName", "required": False, "type": "String"},
        {"multivalued": False, "name": "orgcode", "required": False, "type": "String"},
    ]


def _sync_key() -> str:
    value = os.getenv("IAM_ACCOUNT_SYNC_SM4_KEY", "")
    if not value:
        raise HTTPException(status_code=503, detail="IAM account synchronization is not configured")
    return value


def _verify_request(payload: Mapping[str, Any]) -> None:
    configured_user = os.getenv("IAM_ACCOUNT_SYNC_REMOTE_USER", "")
    configured_password = os.getenv("IAM_ACCOUNT_SYNC_REMOTE_PASSWORD", "")
    supplied_user = _string(payload, "bimRemoteUser")
    supplied_password = _string(payload, "bimRemotePwd")
    signature = _string(payload, "signature", required=False)
    credentials_match = configured_user and configured_password and secrets.compare_digest(supplied_user, configured_user) and secrets.compare_digest(supplied_password, configured_password)
    if not credentials_match:
        raise HTTPException(status_code=401, detail="IAM account synchronization authentication failed")
    # Existing BIM installations send a vendor signature whose canonicalisation
    # is not SM3(field=value).  The encrypted transport plus remote credentials
    # remain mandatory for that established protocol.  A deployment may opt in
    # to strict SM3 verification only after BIM has been configured to produce
    # this canonical form.
    require_sm3 = os.getenv("IAM_ACCOUNT_SYNC_REQUIRE_SM3_SIGNATURE", "").strip().lower() in {"1", "true", "yes", "on"}
    if require_sm3:
        if not signature:
            raise HTTPException(status_code=401, detail="IAM account synchronization signature is required")
        expected_signature = sm3_hexdigest(canonicalize_signed_fields(payload))
        if not secrets.compare_digest(signature.lower(), expected_signature.lower()):
            raise HTTPException(status_code=401, detail="IAM account synchronization signature failed")


def _is_iam_user(user: Any) -> bool:
    return getattr(user, "oauth_provider", None) == "iam" or is_legacy_account_email(str(getattr(user, "email", "")))


async def _find_or_migrate_sync_user(provider: Any, account: str, email: str):
    user = await provider.get_user_by_email(email)
    if user is not None:
        if not _is_iam_user(user):
            raise HTTPException(status_code=409, detail="IAM account conflicts with an existing local account")
        return user
    legacy = await provider.get_user_by_email(legacy_account_email(account))
    if legacy is not None:
        if not _is_iam_user(legacy):
            raise HTTPException(status_code=409, detail="IAM account conflicts with an existing local account")
        legacy.email = email
        legacy.oauth_provider = "iam"
        return await provider.update_user(legacy)
    try:
        user = await provider.create_user(email=email, password=None, system_role="user", needs_setup=False)
    except ValueError:
        user = await provider.get_user_by_email(email)
        if user is None or not _is_iam_user(user):
            raise HTTPException(status_code=409, detail="IAM account could not be synchronized") from None
    user.oauth_provider = "iam"
    return await provider.update_user(user)


async def _apply_user_create(payload: Mapping[str, Any]) -> str:
    account = _account_name(payload)
    assert account is not None
    try:
        email = account_email(account)
    except IAMAuthError as exc:
        raise HTTPException(status_code=400, detail="IAM account name is invalid") from exc
    provider = get_local_provider()
    user = await _find_or_migrate_sync_user(provider, account, email)
    changed = False
    full_name = _string(payload, "fullName", required=False)
    orgcode = _string(payload, "orgcode", required=False)
    active = _state(payload, required=False)
    if full_name is not None and user.display_name != full_name:
        user.display_name, changed = full_name, True
    if orgcode is not None and user.organization_code != orgcode:
        user.organization_code, changed = orgcode, True
    if active is not None and user.is_disabled == active:
        user.is_disabled, user.token_version, changed = not active, user.token_version + 1, True
    if changed:
        user = await provider.update_user(user)
    return str(user.id)


async def _apply_user_update(payload: Mapping[str, Any], *, delete: bool = False) -> str:
    user_id = _string(payload, "bimUid")
    provider = get_local_provider()
    user = await provider.get_user(user_id)
    if user is None or not _is_iam_user(user):
        raise HTTPException(status_code=404, detail="IAM account was not found")
    active = False if delete else _state(payload)
    assert active is not None
    changed = user.is_disabled == active
    user.is_disabled = not active
    if changed:
        user.token_version += 1
    full_name = _string(payload, "fullName", required=False)
    if full_name is not None:
        user.display_name = full_name
    orgcode = _string(payload, "orgcode", required=False)
    if orgcode is not None:
        user.organization_code = orgcode
    user = await provider.update_user(user)
    enablement_field = "delete" if delete else ("state" if payload.get("state") is not None else "__ENABLE__")
    logger.info(
        "IAM account update applied: request_id=%s bim_uid=%s enablement_field=%s enabled=%s is_disabled=%s",
        _string(payload, "bimRequestId", required=False) or "",
        user_id,
        enablement_field,
        active,
        user.is_disabled,
    )
    return str(user.id)


async def _handle(operation: str, request: Request) -> PlainTextResponse:
    if not get_app_config().auth.iam.enabled:
        raise HTTPException(status_code=404, detail="IAM authentication is not enabled")
    body = await request.body()
    if not body or len(body) > _MAX_ENCRYPTED_BODY_BYTES:
        raise HTTPException(status_code=400, detail="IAM encrypted request body is invalid")
    key = _sync_key()
    try:
        payload = decrypt_json_request(body, key)
        _verify_request(payload)
        request_id = _string(payload, "bimRequestId")
        assert request_id is not None
        if operation == "schema":
            result: dict[str, Any] = {"resultCode": "0", "bimRequestId": request_id, "account": _account_schema()}
        elif operation == "create":
            result = {"resultCode": "0", "bimRequestId": request_id, "uid": await _apply_user_create(payload)}
        elif operation == "update":
            result = {"resultCode": "0", "bimRequestId": request_id, "uid": await _apply_user_update(payload)}
        else:
            result = {"resultCode": "0", "bimRequestId": request_id, "uid": await _apply_user_update(payload, delete=True)}
    except HTTPException:
        raise
    except IAMCodecError as exc:
        raise HTTPException(status_code=400, detail="IAM encrypted request body is invalid") from exc
    return PlainTextResponse(
        encrypt_json_response(result, key),
        media_type="text/plain",
    )


@router.post("/SchemaService")
@router.post("")
async def schema_service(request: Request) -> PlainTextResponse:
    return await _handle("schema", request)


@router.post("/UserCreateService")
async def user_create_service(request: Request) -> PlainTextResponse:
    return await _handle("create", request)


@router.post("/UserUpdateService")
async def user_update_service(request: Request) -> PlainTextResponse:
    return await _handle("update", request)


@router.post("/UserDeleteService")
async def user_delete_service(request: Request) -> PlainTextResponse:
    return await _handle("delete", request)
