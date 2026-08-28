"""Data Lake platform-token acquisition and persisted callback endpoints."""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field, ValidationError
from starlette.responses import JSONResponse

from app.gateway.auth.cnooc import (
    CnoocAuthError,
    check_cnooc_token,
    hash_cnooc_platform_token,
    issue_data_lake_token,
)
from app.gateway.auth.platform_token_cookie import (
    CNOOC_PLATFORM_TOKEN_COOKIE_NAME,
    clear_cnooc_platform_token_cookie,
    set_cnooc_platform_token_cookie,
)
from app.gateway.deps import get_current_user_from_request, get_local_provider
from deerflow.persistence.data_lake_auth import DataLakeAuthChallengeRepository
from deerflow.persistence.engine import get_session_factory

router = APIRouter(prefix="/api/v1/data-lake/auth", tags=["data-lake-auth"])
logger = logging.getLogger(__name__)

_CALLBACK_BODY_LIMIT = 16 * 1024
_CHALLENGE_TTL = timedelta(seconds=60)
_INT64_MIN = -(2**63)
_INT64_MAX = 2**63 - 1
_SAFE_HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
}


class DataLakeCallbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    challenge: str = Field(min_length=32, max_length=256, pattern=r"^[A-Za-z0-9_-]+$")
    callback_id: str | None = Field(default=None, max_length=128)
    timestamp: int | None = Field(
        default=None,
        strict=True,
        ge=_INT64_MIN,
        le=_INT64_MAX,
        json_schema_extra={"format": "int64"},
    )


class BoundUserInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject: str = Field(min_length=1, max_length=256)
    account: str = Field(min_length=1, max_length=256)
    email: EmailStr = Field(max_length=320)
    display_name: str = Field(max_length=256)


class DataLakeCallbackAcceptedResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: Literal["accepted"] = "accepted"
    challenge: str
    user_info: BoundUserInfo


class DataLakeAuthorizationResponse(BaseModel):
    code: Literal["authorized", "already_authorized"]


class DataLakeSessionResponse(BaseModel):
    authorized: bool


class DataLakeCallbackErrorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: Literal[
        "invalid_request",
        "invalid_or_expired_challenge",
        "callback_replayed",
        "callback_payload_mismatch",
        "request_too_large",
        "callback_rate_limited",
        "user_unavailable",
        "internal_error",
    ]
    message: str = Field(max_length=256)


def _challenge_hash(challenge: str) -> str:
    return hashlib.sha256(challenge.encode("utf-8")).hexdigest()


def _platform_token_matches_user(user, token: str) -> bool:
    stored_hash = getattr(user, "cnooc_platform_token_hash", None)
    if not token or not stored_hash:
        return False
    return secrets.compare_digest(hash_cnooc_platform_token(token), stored_hash)


def _checked_identity_matches_user(user, platform_user_id: str) -> bool:
    return getattr(user, "oauth_provider", None) == "cnooc" and bool(getattr(user, "oauth_id", None)) and secrets.compare_digest(str(user.oauth_id), platform_user_id)


def _callback_payload_hash(payload: DataLakeCallbackRequest) -> str:
    canonical = json.dumps(payload.model_dump(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _request_id(request: Request) -> str:
    trace_id = getattr(getattr(request, "state", None), "trace_id", None)
    return str(trace_id) if trace_id else secrets.token_hex(16)


def _error_response(request: Request, status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"code": code, "message": message[:256]},
        headers={**_SAFE_HEADERS, "X-Request-Id": _request_id(request)},
    )


def get_data_lake_challenge_repository() -> DataLakeAuthChallengeRepository:
    session_factory = get_session_factory()
    if session_factory is None:
        raise RuntimeError("Data Lake authentication requires SQL persistence")
    return DataLakeAuthChallengeRepository(session_factory)


async def _read_callback_payload(
    request: Request,
) -> DataLakeCallbackRequest | JSONResponse:
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > _CALLBACK_BODY_LIMIT:
            return _error_response(
                request,
                status.HTTP_413_CONTENT_TOO_LARGE,
                "request_too_large",
                "Callback request body exceeds 16 KiB.",
            )
    try:
        return DataLakeCallbackRequest.model_validate_json(bytes(body))
    except ValidationError:
        return _error_response(
            request,
            status.HTTP_400_BAD_REQUEST,
            "invalid_request",
            "Malformed JSON, unknown fields, or invalid callback fields.",
        )


@router.post("/token", response_model=DataLakeAuthorizationResponse)
async def authorize_data_lake(
    request: Request,
    response: Response,
    user=Depends(get_current_user_from_request),
    repository: DataLakeAuthChallengeRepository = Depends(get_data_lake_challenge_repository),
) -> DataLakeAuthorizationResponse:
    """Ensure the browser has a platform token without exposing it to JavaScript."""
    cookie_token = request.cookies.get(CNOOC_PLATFORM_TOKEN_COOKIE_NAME, "").strip()
    if _platform_token_matches_user(user, cookie_token):
        response.headers.update(_SAFE_HEADERS)
        return DataLakeAuthorizationResponse(code="already_authorized")

    challenge = secrets.token_urlsafe(32)
    try:
        await repository.create(
            challenge_hash=_challenge_hash(challenge),
            user_id=str(user.id),
            expires_at=datetime.now(UTC) + _CHALLENGE_TTL,
        )
    except Exception:
        logger.exception("Failed to persist a Data Lake authentication challenge")
        return _error_response(
            request,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "internal_error",
            "Gateway persistence failure.",
        )
    try:
        token = await issue_data_lake_token(challenge)
        checked = await check_cnooc_token(token)
    except CnoocAuthError as exc:
        permission_denied = exc.status_code == status.HTTP_403_FORBIDDEN
        return _error_response(
            request,
            exc.status_code,
            ("data_lake_permission_denied" if permission_denied else "data_lake_authorization_failed"),
            ("The current account does not have Data Lake access." if permission_denied else str(exc)),
        )

    if not _checked_identity_matches_user(user, checked.user.platform_user_id):
        mismatch_response = _error_response(
            request,
            status.HTTP_403_FORBIDDEN,
            "data_lake_identity_mismatch",
            "The Data Lake identity does not match the current user.",
        )
        clear_cnooc_platform_token_cookie(mismatch_response, request)
        return mismatch_response

    try:
        await get_local_provider().bind_cnooc_platform_token(str(user.id), checked.platform_token_hash)
    except ValueError:
        logger.warning(
            "Rejected conflicting Data Lake platform-token binding for user %s",
            user.id,
        )
        return _error_response(
            request,
            status.HTTP_409_CONFLICT,
            "data_lake_token_conflict",
            "Data Lake authorization could not be bound to this user.",
        )
    except Exception:
        logger.exception(
            "Failed to persist a Data Lake platform-token binding for user %s",
            user.id,
        )
        return _error_response(
            request,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "internal_error",
            "Gateway persistence failure.",
        )

    set_cnooc_platform_token_cookie(response, token, request)
    response.headers.update(_SAFE_HEADERS)
    return DataLakeAuthorizationResponse(code="authorized")


@router.get("/session", response_model=DataLakeSessionResponse)
async def validate_data_lake_session(
    request: Request,
    response: Response,
    user=Depends(get_current_user_from_request),
):
    """Validate that the HttpOnly platform token belongs to this user."""
    token = request.cookies.get(CNOOC_PLATFORM_TOKEN_COOKIE_NAME, "").strip()
    if _platform_token_matches_user(user, token):
        response.headers.update(_SAFE_HEADERS)
        return DataLakeSessionResponse(authorized=True)

    invalid_response = JSONResponse(
        status_code=status.HTTP_401_UNAUTHORIZED,
        content={"authorized": False},
        headers=_SAFE_HEADERS,
    )
    clear_cnooc_platform_token_cookie(invalid_response, request)
    return invalid_response


@router.post(
    "/callback",
    operation_id="receiveDataLakeAuthCallback",
    response_model=DataLakeCallbackAcceptedResponse,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": DataLakeCallbackRequest.model_json_schema()}},
        }
    },
    responses={
        400: {
            "model": DataLakeCallbackErrorResponse,
            "description": "Malformed JSON, unknown fields, or invalid callback fields.",
        },
        409: {
            "model": DataLakeCallbackErrorResponse,
            "description": "Challenge is unavailable, expired, replayed, or conflicts with an earlier callback.",
        },
        413: {
            "model": DataLakeCallbackErrorResponse,
            "description": "Request body exceeds the 16 KiB callback limit.",
        },
        429: {
            "model": DataLakeCallbackErrorResponse,
            "description": "Callback rate limit exceeded by the upstream deployment limiter.",
        },
        500: {
            "model": DataLakeCallbackErrorResponse,
            "description": "Gateway persistence or user-resolution failure.",
        },
    },
)
async def receive_data_lake_auth_callback(
    request: Request,
    repository: DataLakeAuthChallengeRepository = Depends(get_data_lake_challenge_repository),
):
    """Resolve a persisted challenge and return its bound DeerFlow identity."""
    parsed = await _read_callback_payload(request)
    if isinstance(parsed, JSONResponse):
        return parsed

    try:
        resolution = await repository.accept_callback(
            challenge_hash=_challenge_hash(parsed.challenge),
            callback_id=parsed.callback_id,
            callback_timestamp=parsed.timestamp,
            payload_hash=_callback_payload_hash(parsed),
        )
    except Exception:
        logger.exception("Failed to consume a Data Lake authentication callback")
        return _error_response(
            request,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "internal_error",
            "Gateway persistence failure.",
        )

    if resolution.status == "invalid_or_expired":
        return _error_response(
            request,
            status.HTTP_409_CONFLICT,
            "invalid_or_expired_challenge",
            "Challenge is missing, expired, or unavailable.",
        )
    if resolution.status == "replayed":
        return _error_response(
            request,
            status.HTTP_409_CONFLICT,
            "callback_replayed",
            "Challenge was already used by a different callback.",
        )
    if resolution.status == "payload_mismatch":
        return _error_response(
            request,
            status.HTTP_409_CONFLICT,
            "callback_payload_mismatch",
            "Callback payload conflicts with an earlier callback.",
        )

    try:
        user = await get_local_provider().get_user(resolution.user_id or "")
    except Exception:
        logger.exception("Failed to resolve the user bound to a Data Lake callback")
        user = None
    if user is None or user.is_disabled:
        return _error_response(
            request,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "user_unavailable",
            "The user bound to this challenge is unavailable.",
        )

    account = user.email.partition("@")[0] or user.email
    result = DataLakeCallbackAcceptedResponse(
        challenge=parsed.challenge,
        user_info=BoundUserInfo(
            subject=user.oauth_id or str(user.id),
            account=account,
            email=user.email,
            display_name=user.display_name or account,
        ),
    )
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=result.model_dump(mode="json"),
        headers={**_SAFE_HEADERS, "X-Request-Id": _request_id(request)},
    )
