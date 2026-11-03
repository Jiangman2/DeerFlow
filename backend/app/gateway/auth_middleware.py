"""Global authentication middleware — fail-closed safety net.

Rejects unauthenticated requests to non-public paths with 401. When a
request passes the cookie check, resolves the JWT payload to a real
``User`` object and stamps it into both ``request.state.user`` and the
``deerflow.runtime.user_context`` contextvar so that repository-layer
owner filtering works automatically via the sentinel pattern.

Fine-grained permission checks remain in authz.py decorators.
"""

import hmac
import os
from collections.abc import Callable

from fastapi import HTTPException, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp

from app.gateway.auth.errors import AuthErrorCode, AuthErrorResponse
from app.gateway.auth_disabled import (
    AUTH_SOURCE_AUTH_DISABLED,
    AUTH_SOURCE_INTERNAL,
    AUTH_SOURCE_SESSION,
    get_auth_disabled_user,
    is_auth_disabled,
)
from app.gateway.authz import _ALL_PERMISSIONS, AuthContext
from app.gateway.internal_auth import INTERNAL_AUTH_HEADER_NAME, get_internal_user, is_valid_internal_auth_token
from deerflow.runtime.user_context import reset_current_user, set_current_user

_OA_POLICY_FLOW_PATH = "/api/v1/integrations/oa/policy-flow/jobs"

# Paths that never require authentication.
_PUBLIC_PATH_PREFIXES: tuple[str, ...] = (
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/api/v1/auth/oauth/",
    "/api/v1/auth/callback/",
    "/api/v1/auth/iam/",
    # IAM's BIM account service authenticates encrypted payloads itself.
    "/api/v1/iam/account-sync/",
    # Inbound webhooks authenticate themselves via provider-specific signatures
    # (e.g. GitHub's X-Hub-Signature-256), not session cookies.
    "/api/webhooks/",
    # Group OA child resources authenticate with their own scoped Bearer key.
    "/api/v1/integrations/oa/policy-flow/jobs/",
)

# Exact auth paths that are public (login/register/status check).
# /api/v1/auth/me, /api/v1/auth/change-password etc. are NOT public.
_PUBLIC_EXACT_PATHS: frozenset[str] = frozenset(
    {
        "/api/v1/auth/login/local",
        "/api/v1/auth/register",
        "/api/v1/auth/logout",
        "/api/v1/auth/setup-status",
        "/api/v1/auth/initialize",
        "/api/v1/auth/providers",
        "/api/v1/auth/cnooc/login",
        "/api/v1/data-lake/auth/callback",
        # BIM calls the base SchemaService path without a trailing slash.
        "/api/v1/iam/account-sync",
        # Machine-to-machine click collector. The route itself requires the
        # dedicated external click Bearer key.
        "/api/agent_click",
        # Service-to-service endpoint: the route validates the configured
        # Bailian model key before accepting multipart audio.
        "/api/weknora/bailian/v1/audio/transcriptions",
        "/api/weknora/bailian/v1/embeddings",
        "/api/weknora/internal-models/v1/embeddings",
        "/api/weknora/internal-models/v1/rerank",
        # Cluster-internal Codex Agent endpoint. The route validates its own
        # scoped relay Bearer key and never accepts a browser session cookie.
        "/api/codex/deepseek/v1/responses",
        # The OA submission route authenticates its own scoped machine key.
        "/api/v1/integrations/oa/policy-flow/jobs",
    }
)


def _is_public(path: str) -> bool:
    stripped = path.rstrip("/")
    if stripped in _PUBLIC_EXACT_PATHS:
        return True
    return any(path.startswith(prefix) for prefix in _PUBLIC_PATH_PREFIXES)


def _is_oa_policy_flow_path(path: str) -> bool:
    return path.rstrip("/") == _OA_POLICY_FLOW_PATH or path.startswith(_OA_POLICY_FLOW_PATH + "/")


def _has_valid_oa_policy_flow_credential(request: Request) -> bool:
    expected = os.getenv("OA_POLICY_FLOW_INGEST_KEY", "").strip()
    if len(expected) < 32:
        return False
    authorization = request.headers.get("authorization", "")
    supplied = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
    if not supplied:
        supplied = request.headers.get("x-oa-integration-key", "").strip()
    return bool(supplied) and hmac.compare_digest(supplied, expected)


class AuthMiddleware(BaseHTTPMiddleware):
    """Strict auth gate: reject requests without a valid session.

    Two-stage check for non-public paths:

    1. Cookie presence — return 401 NOT_AUTHENTICATED if missing
    2. JWT validation via ``get_optional_user_from_request`` — return 401
       TOKEN_INVALID if the token is absent, malformed, expired, or the
       signed user does not exist / is stale

    On success, stamps ``request.state.user`` and the
    ``deerflow.runtime.user_context`` contextvar so that repository-layer
    owner filters work downstream without every route needing a
    ``@require_auth`` decorator. Routes that need per-resource
    authorization (e.g. "user A cannot read user B's thread by guessing
    the URL") should additionally use ``@require_permission(...,
    owner_check=True)`` for explicit enforcement — but authentication
    itself is fully handled here.
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if _is_oa_policy_flow_path(request.url.path):
            # Authenticate before FastAPI parses the multipart body. The route
            # repeats the check as defense in depth, but this early gate prevents
            # unauthenticated clients from consuming upload/temp-file resources.
            if not os.getenv("OA_POLICY_FLOW_INGEST_KEY", "").strip():
                return JSONResponse(status_code=503, content={"detail": "OA policy-flow integration is unavailable"})
            if not _has_valid_oa_policy_flow_credential(request):
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Invalid OA integration credential"},
                    headers={"WWW-Authenticate": "Bearer"},
                )
            request.state.auth_source = "oa_policy_flow"
            return await call_next(request)

        if _is_public(request.url.path):
            return await call_next(request)

        internal_user = None
        if is_valid_internal_auth_token(request.headers.get(INTERNAL_AUTH_HEADER_NAME)):
            # Extract the channel owner user ID from the trusted header.
            # When present, the synthetic internal user carries the actual
            # owner identity so that get_effective_user_id() and per-user
            # filesystem paths (custom skills, memory, thread data) resolve
            # to the IM channel user instead of falling back to "default".
            from app.gateway.internal_auth import INTERNAL_OWNER_USER_ID_HEADER_NAME

            owner_user_id = request.headers.get(INTERNAL_OWNER_USER_ID_HEADER_NAME)
            if owner_user_id:
                owner_user_id = owner_user_id.strip()
            internal_user = get_internal_user(owner_user_id=owner_user_id or None)

        auth_source = AUTH_SOURCE_SESSION
        access_token = request.cookies.get("access_token")

        # Non-public path: require session cookie
        if internal_user is not None:
            user = internal_user
            auth_source = AUTH_SOURCE_INTERNAL
        elif access_token:
            # Strict JWT validation: reject junk/expired tokens with 401
            # right here instead of silently passing through. This closes
            # the "junk cookie bypass" gap (AUTH_TEST_PLAN test 7.5.8):
            # without this, non-isolation routes like /api/models would
            # accept any cookie-shaped string as authentication.
            #
            # We call the *strict* resolver so that fine-grained error
            # codes (token_expired, token_invalid, user_not_found, …)
            # propagate from AuthErrorCode, not get flattened into one
            # generic code. BaseHTTPMiddleware doesn't let HTTPException
            # bubble up, so we catch and render it as JSONResponse here.
            from app.gateway.deps import get_current_user_from_request

            try:
                user = await get_current_user_from_request(request)
            except HTTPException as exc:
                if not is_auth_disabled():
                    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
                user = get_auth_disabled_user()
                auth_source = AUTH_SOURCE_AUTH_DISABLED
        elif is_auth_disabled():
            user = get_auth_disabled_user()
            auth_source = AUTH_SOURCE_AUTH_DISABLED
        else:
            return JSONResponse(
                status_code=401,
                content={
                    "detail": AuthErrorResponse(
                        code=AuthErrorCode.NOT_AUTHENTICATED,
                        message="Authentication required",
                    ).model_dump()
                },
            )

        # Stamp both request.state.user (for the contextvar pattern)
        # and request.state.auth (so @require_permission's "auth is
        # None" branch short-circuits instead of running the entire
        # JWT-decode + DB-lookup pipeline a second time per request).
        request.state.user = user
        request.state.auth_source = auth_source
        request.state.auth = AuthContext(user=user, permissions=_ALL_PERMISSIONS)
        token = set_current_user(user)
        try:
            return await call_next(request)
        finally:
            reset_current_user(token)
