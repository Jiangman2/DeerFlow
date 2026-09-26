"""Gateway admission of server-owned Digital Employee publication receipts."""

import asyncio

from fastapi import HTTPException

from deerflow.config.paths import get_paths
from deerflow.persistence.agents.publications import PublicationConflict
from deerflow.persistence.agents.runtime import EmployeePublicationBindings


async def admit_agent_publication(config: dict, *, thread_id: str, user_id: str, commit: bool = True, expected_revision: str | None = None):
    context = config.get("context", {})
    configurable = config.get("configurable", {})
    for key in ("agent_name", "agent_source", "is_bootstrap"):
        if key in context and key in configurable and context[key] != configurable[key]:
            raise HTTPException(409, "Conflicting employee runtime identity; refresh the conversation.")
    name = context.get("agent_name", configurable.get("agent_name"))
    source = context.get("agent_source", configurable.get("agent_source"))
    for section in ("context", "configurable"):
        target = config.setdefault(section, {})
        target.pop("agent_publication_id", None)
        target.pop("agent_publication_revision", None)
    try:
        snapshot = await asyncio.to_thread(
            lambda: EmployeePublicationBindings(get_paths()).admit(
                thread_id,
                user_id=user_id,
                name=name,
                source=source,
                is_bootstrap=bool(context.get("is_bootstrap", configurable.get("is_bootstrap"))),
                commit=commit,
                expected_revision=expected_revision,
            )
        )
    except (FileNotFoundError, ValueError, PublicationConflict):
        raise HTTPException(409, "Employee publication is unavailable or this conversation has a different version binding. Refresh the employee and start a new conversation.") from None
    except PermissionError:
        raise HTTPException(403, "Employee resource approval expired or was revoked; contact its publisher.") from None
    except OSError:
        raise HTTPException(503, "Unable to verify employee publication; retry later.") from None
    if snapshot:
        for section in ("context", "configurable"):
            config[section].update(agent_name=snapshot.name, agent_source="team", agent_publication_id=snapshot.publication_id, agent_publication_revision=snapshot.revision)
    return snapshot


async def branch_agent_publication(*, source_thread_id: str, target_thread_id: str, owner_user_id: str | None, actor):
    """Admit a same-owner branch before writing any checkpoint or thread row.

    Ownership must come from the authorized thread row, not request metadata.
    Legacy unowned chats have no per-user employee receipt to copy.
    """
    from app.gateway.agent_knowledge import apply_agent_knowledge_scope

    if not owner_user_id:
        return None
    bindings = EmployeePublicationBindings(get_paths())
    try:
        snapshot = await asyncio.to_thread(bindings.read, source_thread_id, user_id=owner_user_id)
        if snapshot is None:
            return None
        if actor is None or str(getattr(actor, "id", "")) != owner_user_id or getattr(actor, "is_disabled", False):
            raise PermissionError("Employee branch requires its authenticated owner")
        config = {"context": {"agent_name": snapshot.name, "agent_source": "team"}}
        # Includes fresh upstream owner approval, not merely the local grant.
        await apply_agent_knowledge_scope(config, actor, publication=snapshot)
        return await asyncio.to_thread(bindings.branch, source_thread_id, target_thread_id, user_id=owner_user_id)
    except (FileNotFoundError, ValueError, PublicationConflict):
        raise HTTPException(409, "Employee publication is unavailable; this conversation cannot be branched.") from None
    except PermissionError:
        raise HTTPException(403, "Employee resource approval expired or was revoked; contact its publisher.") from None
    except OSError:
        raise HTTPException(503, "Unable to verify employee publication; retry later.") from None
