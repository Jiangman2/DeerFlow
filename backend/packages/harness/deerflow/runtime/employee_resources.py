"""Narrow server-registered resource services, independent of Gateway imports."""

from __future__ import annotations

import asyncio

from langchain.tools import tool

from deerflow.config.agent_knowledge_config import AgentKnowledgeScope
from deerflow.config.paths import get_paths
from deerflow.persistence.agents.runtime import EmployeePublicationBindings
from deerflow.runtime.user_context import resolve_runtime_user_id
from deerflow.runtime.weknora_context import WEKNORA_TOOL_NAMES
from deerflow.tools.types import Runtime

_search = None
_authorize = None
AUTHORIZATION_TIMEOUT_SECONDS = 20


def register_employee_resource_services(*, search, authorize):
    global _search, _authorize
    _search, _authorize = search, authorize


def read_employee_publication(thread_id, user_id):
    if not thread_id or not user_id:
        return None
    return EmployeePublicationBindings(get_paths()).read(thread_id, user_id=user_id)


async def authorize_employee_execution(thread_id, user_id):
    publication = await asyncio.to_thread(read_employee_publication, thread_id, user_id)
    if publication is None:
        raise PermissionError("Published employee admission is no longer available")
    if publication.config.get("resource_policy_version") == 1:
        if _authorize is None:
            raise RuntimeError("Employee resource authorization service is unavailable")
        try:
            await asyncio.wait_for(_authorize(publication, user_id), timeout=AUTHORIZATION_TIMEOUT_SECONDS)
        except TimeoutError:
            raise RuntimeError("Employee resource authorization timed out; no tool was executed") from None
    return publication


def has_delegated_knowledge(publication):
    return bool(publication is not None and publication.config.get("resource_policy_version") == 1 and AgentKnowledgeScope.model_validate(publication.config.get("knowledge_scope") or {}).mode == "selected")


def scope_employee_config(app_config, publication):
    if publication is None:
        return app_config
    # Invocation-only evidence must not be promoted to a user's reusable
    # personal memory (or a same-name personal Agent's memory). Conversation
    # checkpoints remain available; existing memory files are never changed.
    # This boundary applies to legacy publications too, independently of the
    # resource grant schema. A namesake is never a shared memory namespace.
    return app_config.model_copy(update={"memory": app_config.memory.model_copy(update={"enabled": False, "injection_enabled": False})})


def scope_employee_tools(tools, publication, *, knowledge_scope=None):
    if publication is not None:
        knowledge_scope = AgentKnowledgeScope.model_validate(publication.config.get("knowledge_scope") or {})
    if knowledge_scope is not None and knowledge_scope.mode == "disabled":
        tools = [t for t in tools if t.name not in WEKNORA_TOOL_NAMES and t.name != "employee_knowledge_search"]
    if publication is not None:
        tools = [t for t in tools if t.name not in {"memory_search", "memory_add", "memory_update", "memory_delete"}]
    if not has_delegated_knowledge(publication):
        return tools
    # A published selection is not permission to browse, download, invoke
    # other KB agents, or override the broker's fixed resource scope.
    return [t for t in tools if t.name not in WEKNORA_TOOL_NAMES and t.name != "employee_knowledge_search"] + [employee_knowledge_search]


@tool("employee_knowledge_search", parse_docstring=True)
async def employee_knowledge_search(query: str, runtime: Runtime) -> dict:
    """Retrieve bounded evidence from this employee's owner-approved knowledge.

    This is question answering only, not a document browser or download API.
    Cite returned numbered evidence; never invent source links or resource IDs.

    Args:
        query: A focused question, at most 2000 characters.
        runtime: Injected authenticated execution context.
    """
    if _search is None:
        raise RuntimeError("Employee knowledge service is unavailable")
    config = getattr(runtime, "config", None) or {}
    context = getattr(runtime, "context", None)
    context = context if isinstance(context, dict) else {}
    # Children inherit graph checkpoint coordinates, not a new business
    # conversation. The executor explicitly propagates its admitted parent ID.
    thread_id = context.get("thread_id") or config.get("configurable", {}).get("thread_id")
    if not thread_id:
        raise PermissionError("Employee conversation identity is required")
    return await _search(thread_id=thread_id, viewer_id=resolve_runtime_user_id(runtime), query=query)
