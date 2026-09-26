"""Authorize the effective run model, including every fallback candidate.

Catalog visibility alone is not model:use. Explicit denial never becomes an
allow through fail-open or by selecting an unconfigured provider-returned name.
"""

from __future__ import annotations

import asyncio
import logging
from copy import deepcopy

from langgraph.errors import GraphBubbleUp

from deerflow.authz.principal import build_principal_from_context
from deerflow.authz.provider import AuthzDecision, AuthzRequest
from deerflow.authz.runtime import construct_authorization_provider, resolve_authorization_provider, resolve_authorization_provider_spec
from deerflow.config.authorization_config import AuthorizationConfig

logger = logging.getLogger(__name__)


class ModelAuthorizationError(ValueError):
    """No permitted model can safely be selected for this run."""


def _inputs(model_name, context, app_config):
    config = getattr(app_config, "authorization", None)
    if not isinstance(config, AuthorizationConfig):
        raise ModelAuthorizationError("Model authorization configuration is unavailable")
    if not config.enabled:
        return None
    config = config.model_copy(deep=True)
    try:
        principal = deepcopy(build_principal_from_context(context, default_role=config.default_role))
    except GraphBubbleUp:
        raise
    except Exception:
        raise ModelAuthorizationError("Model authorization identity is unavailable") from None
    names = list(dict.fromkeys(model.name for model in app_config.models))
    if not names:
        raise ModelAuthorizationError("No chat models are configured")
    return config, principal, names, model_name if model_name in names else names[0]


def _allowed(decision):
    if not isinstance(decision, AuthzDecision) or not isinstance(decision.allow, bool):
        raise TypeError("Invalid model authorization decision")
    return decision.allow


def _candidates(selected, names, denied):
    if not isinstance(selected, list) or any(not isinstance(name, str) for name in selected):
        raise TypeError("Invalid model visibility result")
    visible = set(selected)
    return [name for name in names if name in visible and name not in denied]


def _provider_error(config, model_name, denied):
    logger.warning("Model authorization provider failed")
    if not config.fail_closed and model_name not in denied:
        return model_name
    raise ModelAuthorizationError("No models are authorized for this run (provider unavailable)") from None


def authorize_model_name(model_name, *, context, app_config, authorization_provider=None):
    """Synchronous graph factories use the provider's synchronous contract."""
    inputs = _inputs(model_name, context, app_config)
    if inputs is None:
        return model_name
    config, principal, names, model_name = inputs
    denied = set()
    try:
        provider = authorization_provider if authorization_provider is not None else resolve_authorization_provider(config)
        if provider is None:
            raise ValueError("Enabled authorization has no provider")
        if _allowed(provider.authorize(AuthzRequest(principal=principal, resource="model", action="use", target=model_name))):
            return model_name
        denied.add(model_name)
        selected = provider.filter_resources(principal, "model", list(names))
        for candidate in _candidates(selected, names, denied):
            if _allowed(provider.authorize(AuthzRequest(principal=principal, resource="model", action="use", target=candidate))):
                return candidate
            denied.add(candidate)
    except GraphBubbleUp:
        raise
    except Exception:
        return _provider_error(config, model_name, denied)
    raise ModelAuthorizationError("No models are authorized for this run")


async def authorize_model_name_async(model_name, *, context, app_config, authorization_provider=None):
    """Child loops construct and call async providers on their execution loop."""
    inputs = _inputs(model_name, context, app_config)
    if inputs is None:
        return model_name
    config, principal, names, model_name = inputs
    denied = set()
    try:
        provider = authorization_provider
        if provider is None:
            spec = await asyncio.to_thread(resolve_authorization_provider_spec, config)
            provider = construct_authorization_provider(spec, config)
        if provider is None:
            raise ValueError("Enabled authorization has no provider")
        if _allowed(await provider.aauthorize(AuthzRequest(principal=principal, resource="model", action="use", target=model_name))):
            return model_name
        denied.add(model_name)
        selected = await asyncio.to_thread(provider.filter_resources, principal, "model", list(names))
        for candidate in _candidates(selected, names, denied):
            if _allowed(await provider.aauthorize(AuthzRequest(principal=principal, resource="model", action="use", target=candidate))):
                return candidate
            denied.add(candidate)
    except GraphBubbleUp:
        raise
    except Exception:
        return _provider_error(config, model_name, denied)
    raise ModelAuthorizationError("No models are authorized for this run")
