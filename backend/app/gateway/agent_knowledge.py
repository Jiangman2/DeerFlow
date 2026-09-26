"""Validate creator bindings using fresh WeKnora RBAC, without delegation.

Public-employee grants are a separate boundary. Never mint the publisher's
session for a viewer or treat a saved selection as a transferable permission.
"""

import asyncio
from typing import Any

from fastapi import HTTPException

from app.gateway.weknora_identity import ensure_weknora_session_for_deerflow_user
from deerflow.config.agent_knowledge_config import AgentKnowledgeScope
from deerflow.config.agents_config import is_personal_agent, load_agent_config, validate_agent_source

_UNAVAILABLE = "Selected knowledge is unavailable or no longer authorized."


async def validate_creator_knowledge_scope(scope: AgentKnowledgeScope | None, actor: Any) -> AgentKnowledgeScope | None:
    if scope is None or scope.mode != "selected":
        return scope
    if actor is None or not getattr(actor, "id", None):
        raise HTTPException(401, "Authenticated creator required for knowledge selection.")
    try:
        return await asyncio.wait_for(_validate_selected(scope, actor), timeout=30)
    except HTTPException as exc:
        status = 403 if exc.status_code in (401, 403, 404) else 502
        raise HTTPException(status, _UNAVAILABLE) from None
    except (TimeoutError, ValueError, TypeError, KeyError):
        raise HTTPException(502, "Unable to verify selected knowledge; try again later.") from None


async def _validate_selected(scope: AgentKnowledgeScope, actor: Any) -> AgentKnowledgeScope:
    from app.gateway.routers.weknora import _request

    session = await ensure_weknora_session_for_deerflow_user(actor)
    identity = await _request("GET", "/auth/me", session.token)
    data = identity.get("data", {})
    if not isinstance(data, dict):
        raise HTTPException(502, _UNAVAILABLE)
    live_user = data.get("user", {})
    expected_user = getattr(session, "user", {})
    if not isinstance(live_user, dict) or not isinstance(expected_user, dict) or not expected_user.get("id") or str(live_user.get("id")) != str(expected_user["id"]):
        raise HTTPException(403, _UNAVAILABLE)
    memberships = data.get("memberships")
    if not isinstance(memberships, list) or not memberships:
        raise HTTPException(403, _UNAVAILABLE)
    tenants = list(dict.fromkeys(str(row["tenant_id"]) for row in memberships if isinstance(row, dict) and row.get("tenant_id") is not None))
    semaphore = asyncio.Semaphore(4)

    async def verify(item):
        if item.tenant_id and item.tenant_id not in tenants:
            raise HTTPException(403, _UNAVAILABLE)
        # A missing hint is bounded; large organizations must select a tenant
        # explicitly instead of triggering an unbounded membership scan.
        if not item.tenant_id and len(tenants) > 8:
            raise HTTPException(403, _UNAVAILABLE)
        async with semaphore:
            for tenant in [item.tenant_id] if item.tenant_id else tenants:
                try:
                    kb_id = item.id if item.type == "kb" else item.kb_id
                    response = await _request("GET", f"/knowledge-bases/{kb_id}", session.token, tenant_id=tenant)
                    kb = response.get("data", {})
                    if not isinstance(kb, dict) or str(kb.get("id")) != kb_id:
                        raise HTTPException(403, _UNAVAILABLE)
                    name = str(kb.get("name") or "")[:512]
                    if item.type == "file":
                        response = await _request("GET", f"/knowledge/{item.id}", session.token, tenant_id=tenant)
                        doc = response.get("data", {})
                        if not isinstance(doc, dict) or str(doc.get("id")) != item.id or str(doc.get("knowledge_base_id")) != kb_id:
                            raise HTTPException(403, _UNAVAILABLE)
                        name = str(doc.get("title") or doc.get("file_name") or "")[:512]
                    # Validate canonical data again: upstream metadata is not a
                    # reason to bypass identifier and payload size constraints.
                    return type(item).model_validate({**item.model_dump(), "name": name, "kb_name": str(kb.get("name") or "")[:512], "tenant_id": tenant})
                except HTTPException as exc:
                    if exc.status_code not in (403, 404):
                        raise
            raise HTTPException(403, _UNAVAILABLE)

    # Await every bounded request even on failure; no detached permission
    # checks may continue after the caller receives a rejection.
    results = await asyncio.gather(*(verify(item) for item in scope.items), return_exceptions=True)
    for result in results:
        if isinstance(result, BaseException):
            raise result
    return AgentKnowledgeScope(mode="selected", items=results)


async def apply_agent_knowledge_scope(config: dict[str, Any], actor: Any, *, publication=None) -> dict[str, str] | None:
    context = config.get("context", {})
    configurable = config.get("configurable", {})
    name = context.get("agent_name", configurable.get("agent_name"))
    if not name or context.get("is_bootstrap", configurable.get("is_bootstrap")):
        return
    try:
        source = validate_agent_source(context.get("agent_source", configurable.get("agent_source")))
    except ValueError:
        raise HTTPException(422, "Invalid agent source.") from None
    user_id = str(actor.id) if actor is not None and getattr(actor, "id", None) else None
    try:
        if publication is not None:
            from deerflow.persistence.agents.base import parse_agent_config

            agent = parse_agent_config(publication.config, name)
        else:
            agent = await asyncio.to_thread(load_agent_config, name, user_id=user_id, source=source)
    except FileNotFoundError:
        # Exact-source loader in the agent factory remains authoritative for
        # existing no-knowledge agent flows, including bootstrap compatibility.
        return
    scope = agent.knowledge_scope if agent else None
    if publication is not None and publication.config.get("resource_policy_version") == 1:
        from app.gateway.employee_knowledge import EmployeeKnowledgeBroker
        from deerflow.config.paths import get_paths

        try:
            await asyncio.wait_for(EmployeeKnowledgeBroker(get_paths()).authorize(publication, user_id, verify_owners=True), timeout=30)
        except PermissionError:
            raise HTTPException(403, "Employee resource authorization is unavailable; ask its publisher to review it.") from None
        except (TimeoutError, RuntimeError):
            raise HTTPException(503, "Unable to verify employee resources; retry later.") from None
        if scope and scope.mode == "selected":
            from deerflow.config.agent_knowledge_config import clear_weknora_context

            clear_weknora_context(config)
            return {}
    if scope is None or scope.mode == "inherit":
        return
    if scope.mode == "selected":
        personal = source == "personal" or (source is None and await asyncio.to_thread(is_personal_agent, name, user_id=user_id))
        if not personal:
            raise HTTPException(409, "Published knowledge invocation requires an owner-approved delegation grant.")
        scope = await validate_creator_knowledge_scope(scope, actor)
    for section in ("context", "configurable"):
        config.setdefault(section, {}).update(scope.to_run_context())
    return {(item.id if item.type == "kb" else item.kb_id): item.tenant_id for item in scope.items if item.tenant_id}
