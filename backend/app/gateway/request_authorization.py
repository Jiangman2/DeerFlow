"""One operator policy snapshot and loop-local provider per Gateway request."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from fastapi import Request
from langgraph.errors import GraphBubbleUp

from app.gateway.sandbox_authz import authenticated_request_context
from deerflow.authz.principal import build_principal_from_context
from deerflow.authz.provider import AuthorizationProvider, AuthzDecision, AuthzRequest, Principal
from deerflow.authz.runtime import construct_authorization_provider, resolve_authorization_provider_spec
from deerflow.authz.sandbox_authz import safe_app_config_async
from deerflow.config.authorization_config import AuthorizationConfig

logger = logging.getLogger(__name__)
_STATE_KEY = "_deerflow_request_authorization"


@dataclass
class RequestAuthorization:
    config: AuthorizationConfig | None = None
    principal: Principal | None = None
    provider: AuthorizationProvider | None = None
    blocked: bool = False

    @property
    def disabled(self) -> bool:
        return self.config is not None and not self.config.enabled and not self.blocked

    @property
    def allow_provider_error(self) -> bool:
        return not self.blocked and self.config is not None and not self.config.fail_closed

    async def allowed(self, resource: str, action: str, target: str) -> bool:
        if self.disabled:
            return True
        if self.blocked:
            return False
        if self.provider is None or self.principal is None:
            return self.allow_provider_error
        try:
            decision = await self.provider.aauthorize(AuthzRequest(principal=self.principal, resource=resource, action=action, target=target))
            if not isinstance(decision, AuthzDecision) or not isinstance(decision.allow, bool):
                raise TypeError("Invalid authorization decision")
            return decision.allow
        except GraphBubbleUp:
            raise
        except Exception:
            # Do not expose arbitrary provider exception text or policy inputs.
            logger.warning("Gateway authorization decision failed (%s:%s)", resource, action)
            return self.allow_provider_error

    async def filter_models(self, names: list[str]) -> list[str]:
        if self.disabled:
            return list(names)
        if self.blocked:
            return []
        if self.provider is None or self.principal is None:
            return list(names) if self.allow_provider_error else []
        try:
            # The required filter_resources API is synchronous; it must not
            # block the ASGI loop. Construction and aauthorize remain on-loop.
            selected = await asyncio.to_thread(self.provider.filter_resources, self.principal, "model", list(names))
            if not isinstance(selected, list) or any(not isinstance(name, str) for name in selected):
                raise TypeError("Invalid model visibility result")
            allowed = set(selected)
            return [name for name in names if name in allowed]
        except GraphBubbleUp:
            raise
        except Exception:
            logger.warning("Gateway model visibility filtering failed")
            return list(names) if self.allow_provider_error else []


async def resolve_request_authorization(request: Request, user=None) -> RequestAuthorization:
    """Resolve once per authenticated request; never cache provider instances globally.

    Missing/corrupt operator policy and unresolved identity always fail closed.
    Only provider faults can use the valid config's fail-open setting. Every new
    request reloads policy, so hot reload does not depend on object-id caches.
    """
    cached = getattr(request.state, _STATE_KEY, None)
    if isinstance(cached, RequestAuthorization):
        return cached
    result = RequestAuthorization(blocked=True)
    try:
        app_config = await safe_app_config_async()
        config = getattr(app_config, "authorization", None)
        if not isinstance(config, AuthorizationConfig):
            raise TypeError("Authorization configuration is unavailable")
        result.config = config.model_copy(deep=True)
        if not result.config.enabled:
            result.blocked = False
        else:
            context = await authenticated_request_context(request, user)
            result.principal = build_principal_from_context(context, default_role=result.config.default_role)
            result.blocked = False
    except GraphBubbleUp:
        raise
    except Exception:
        logger.warning("Gateway authorization configuration or identity is unavailable")

    if not result.blocked and not result.disabled:
        try:
            spec = await asyncio.to_thread(resolve_authorization_provider_spec, result.config)
            result.provider = construct_authorization_provider(spec, result.config)
            if result.provider is None:
                raise ValueError("Enabled authorization has no provider")
        except GraphBubbleUp:
            raise
        except Exception:
            logger.warning("Gateway authorization provider is unavailable")
    setattr(request.state, _STATE_KEY, result)
    return result
