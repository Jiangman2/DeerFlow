"""Cluster-internal identity bridge for SkillHub passive session bootstrap.

The browser never receives this assertion.  Nginx authenticates the existing
DeerFlow session through this endpoint and injects the returned, short-lived
HMAC assertion only into SkillHub's session-bootstrap request.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.gateway.auth.models import User
from app.gateway.deps import get_current_user_from_request

router = APIRouter(tags=["skillhub-session"])

_ASSERTION_ISSUER = "deerflow-gateway"
_ASSERTION_AUDIENCE = "skillhub"
_ASSERTION_TTL_SECONDS = 30
_MINIMUM_SECRET_BYTES = 32


def _is_enabled() -> bool:
    return os.getenv("DEER_FLOW_SKILLHUB_ENABLED", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _bridge_secret() -> bytes:
    value = os.getenv("SKILLHUB_SESSION_BRIDGE_SECRET", "").strip().encode("utf-8")
    if len(value) < _MINIMUM_SECRET_BYTES:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="SkillHub session bridge is not configured",
        )
    return value


def _header_safe_text(value: str | None, *, fallback: str, limit: int) -> str:
    text = (value or fallback).strip()
    text = "".join(character for character in text if character >= " " and character != "\x7f")
    return (text or fallback)[:limit]


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


@router.get(
    "/internal/skillhub/session/assertion",
    include_in_schema=False,
    status_code=status.HTTP_204_NO_CONTENT,
)
async def create_skillhub_session_assertion(
    user: Annotated[User, Depends(get_current_user_from_request)],
) -> Response:
    """Return a signed, role-free identity assertion to an Nginx subrequest."""

    if not _is_enabled():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="SkillHub integration is disabled",
        )
    if user.is_disabled:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User account is disabled")

    issued_at = int(time.time())
    email = str(user.email)
    display_name = _header_safe_text(
        user.display_name,
        fallback=email.split("@", 1)[0],
        limit=255,
    )
    claims = {
        "aud": _ASSERTION_AUDIENCE,
        "email": email,
        "exp": issued_at + _ASSERTION_TTL_SECONDS,
        "iat": issued_at,
        "iss": _ASSERTION_ISSUER,
        "name": display_name,
        "nonce": secrets.token_urlsafe(12),
        "sub": str(user.id),
        "v": 1,
    }
    encoded_claims = _base64url(
        json.dumps(
            claims,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )
    signature = _base64url(
        hmac.new(
            _bridge_secret(),
            encoded_claims.encode("ascii"),
            hashlib.sha256,
        ).digest()
    )

    return Response(
        status_code=status.HTTP_204_NO_CONTENT,
        headers={
            "Cache-Control": "no-store",
            "Pragma": "no-cache",
            "X-SkillHub-Identity": encoded_claims,
            "X-SkillHub-Signature": signature,
        },
    )
