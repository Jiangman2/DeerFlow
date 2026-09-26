import logging

from langchain_core.messages import ToolMessage
from langchain_core.tools import tool
from langgraph.types import Command

from deerflow.config.agents_config import (
    MAX_AGENT_DESCRIPTION_LENGTH,
    SOUL_FILENAME,
    is_personal_agent,
    validate_agent_name,
)
from deerflow.config.paths import get_paths
from deerflow.persistence.agents import get_agent_store
from deerflow.runtime.user_context import resolve_runtime_user_id
from deerflow.skills.agent_selection import validate_agent_skills
from deerflow.tools.types import Runtime

logger = logging.getLogger(__name__)


@tool(parse_docstring=True)
def setup_agent(
    soul: str,
    description: str,
    runtime: Runtime,
    skills: list[str] | None = None,
) -> Command:
    """Setup the custom DeerFlow agent.

    Args:
        soul: Full SOUL.md content defining the agent's personality and behavior.
        description: One-line description of what the agent does.
        skills: Optional list of skill names this agent should use. None means use all enabled skills, empty list means no skills.
    """

    # Reject empty / whitespace-only soul before touching the filesystem.
    # Without this guard the tool would happily persist an empty SOUL.md and
    # still report success, which caused the frontend to enter the "agent
    # created" state for an unusable agent (issue #3549). Failing loud lets
    # the model retry instead of silently producing a broken artifact and,
    # together with the upstream agent_name fix, prevents the global default
    # SOUL.md from being overwritten with empty content.
    if not soul or not soul.strip():
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        content="Error: soul content is empty; refusing to create agent with an empty SOUL.md",
                        tool_call_id=runtime.tool_call_id,
                    )
                ]
            }
        )

    if len(description) > MAX_AGENT_DESCRIPTION_LENGTH:
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        content=(f"Error: description must be {MAX_AGENT_DESCRIPTION_LENGTH} characters or fewer"),
                        tool_call_id=runtime.tool_call_id,
                        status="error",
                    )
                ]
            }
        )

    agent_name: str | None = runtime.context.get("agent_name") if runtime.context else None
    try:
        if runtime.context and runtime.context.get("agent_source") not in (None, "personal"):
            raise ValueError("Shared agent selection cannot be changed through setup_agent")
        agent_name = validate_agent_name(agent_name)
        if agent_name:
            user_id = resolve_runtime_user_id(runtime)
            form_selection = bool(runtime.context and runtime.context.get("is_bootstrap") is True and "agent_skill_selection" in runtime.context)
            if form_selection:
                # A form selection is user intent, not an authorization grant.
                # Validate against this creator's live catalog at the write.
                skills = runtime.context["agent_skill_selection"]
            skills = validate_agent_skills(skills, user_id=user_id)
            store = get_agent_store()
            config_data: dict = {"name": agent_name}
            # Upstream #5324/#5579 preserve creation-form configuration during
            # conversational bootstrap. Never inherit a shared fallback as if
            # it were owned by this creator.
            before = store.read_definition(agent_name, user_id=user_id) if is_personal_agent(agent_name, user_id=user_id) else None
            if description:
                config_data["description"] = description
            if skills is not None or form_selection:
                config_data["skills"] = skills

            if before is not None:
                store.patch_definition(agent_name, config_data, soul, user_id=user_id, expected_revision=before.revision)
            else:
                # Creation races fail instead of replacing another creator
                # request's already-committed definition.
                store.create(agent_name, config_data, soul, user_id=user_id)
        else:
            paths = get_paths()
            paths.base_dir.mkdir(parents=True, exist_ok=True)
            (paths.base_dir / SOUL_FILENAME).write_text(soul, encoding="utf-8")

        logger.info("[agent_creator] Created agent '%s'", agent_name)
        return Command(
            update={
                "created_agent_name": agent_name,
                "messages": [ToolMessage(content=f"Agent '{agent_name}' created successfully!", tool_call_id=runtime.tool_call_id)],
            }
        )

    except Exception as e:
        logger.error(f"[agent_creator] Failed to create agent '{agent_name}': {e}", exc_info=True)
        return Command(update={"messages": [ToolMessage(content=f"Error: {e}", tool_call_id=runtime.tool_call_id, status="error")]})
