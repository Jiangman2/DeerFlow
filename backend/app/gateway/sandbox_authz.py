"""Request-scoped sandbox policy; storage ownership is not caller authority."""

from __future__ import annotations

import logging

from fastapi import Request
from langgraph.errors import GraphBubbleUp

from app.gateway.auth_disabled import AUTH_SOURCE_INTERNAL
from app.gateway.deps import get_current_user_from_request, get_local_provider
from app.gateway.internal_auth import INTERNAL_OWNER_USER_ID_HEADER_NAME, INTERNAL_SYSTEM_ROLE
from deerflow.authz.sandbox_authz import authorize_sandbox_execution_async, safe_app_config_async
from deerflow.config.authorization_config import AuthorizationConfig
from deerflow.sandbox.exceptions import SandboxAuthorizationError

logger = logging.getLogger(__name__)


async def authorize_sandbox_for_request(request: Request) -> None:
    """Check the authenticated actor before a Gateway sandbox side effect.

    Explicitly disabled policy preserves legacy behavior. Missing config or
    identity is never a disabled policy, nor does a thread owner/admin read
    scope, body field, query parameter or internal-looking header confer a role.
    Provider failures alone obey the configured fail-open setting.
    """
    config = await safe_app_config_async()
    authorization = getattr(config, "authorization", None)
    if not isinstance(authorization, AuthorizationConfig):
        raise SandboxAuthorizationError("Sandbox authorization configuration is unavailable")
    if not authorization.enabled:
        return

    context = await authenticated_request_context(request)
    await authorize_sandbox_execution_async(context=context, app_config=config)


async def authenticated_request_context(request: Request, user=None) -> dict:
    """Shared trusted actor mapping for sandbox, route and model checks.

    An optional user must already have been resolved by the server's strict
    authentication path. It is never accepted from a client payload.
    """
    try:
        if user is None:
            user = await get_current_user_from_request(request)
        is_internal = getattr(request.state, "auth_source", None) == AUTH_SOURCE_INTERNAL
        role = getattr(user, "system_role", None)
        if is_internal and role == INTERNAL_SYSTEM_ROLE:
            # AuthMiddleware already validated the token and normalized this
            # owner's ID into the synthetic user. Never trust the raw header as
            # an identity, and never turn a failed owner lookup into default_role.
            if request.headers.get(INTERNAL_OWNER_USER_ID_HEADER_NAME):
                owner = await get_local_provider().get_user(str(user.id))
                if owner is None or str(owner.id) != str(user.id) or getattr(owner, "is_disabled", False) or owner.system_role == INTERNAL_SYSTEM_ROLE:
                    raise SandboxAuthorizationError("Sandbox authorization identity is unavailable")
                user = owner
                role = user.system_role
            else:
                role = None  # A genuine unattributed internal service uses default_role.
        if getattr(user, "id", None) is None or getattr(user, "is_disabled", False):
            raise SandboxAuthorizationError("Sandbox authorization identity is unavailable")
        context = {
            "user_id": str(user.id),
            "user_role": role,
            "oauth_provider": getattr(user, "oauth_provider", None),
            "oauth_id": getattr(user, "oauth_id", None),
            "is_internal": is_internal,
        }
    except (SandboxAuthorizationError, GraphBubbleUp):
        raise
    except Exception:
        logger.warning("Sandbox request identity could not be verified")
        raise SandboxAuthorizationError("Sandbox authorization identity is unavailable") from None

    return context
