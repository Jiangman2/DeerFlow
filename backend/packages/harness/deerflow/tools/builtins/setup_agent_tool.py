import logging

import yaml
from langchain_core.messages import ToolMessage
from langchain_core.tools import tool
from langgraph.prebuilt import ToolRuntime
from langgraph.types import Command

from deerflow.config.paths import get_paths
from deerflow.config.user_context import resolve_user_id

logger = logging.getLogger(__name__)


@tool
def setup_agent(
    soul: str,
    description: str,
    runtime: ToolRuntime,
) -> Command:
    """Setup the custom DeerFlow agent.

    Args:
        soul: Full SOUL.md content defining the agent's personality and behavior.
        description: One-line description of what the agent does.
    """

    rt_ctx = runtime.context or {}
    agent_name: str | None = rt_ctx.get("agent_name")
    user_id = resolve_user_id(rt_ctx)

    if not agent_name:
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        content="Error: agent_name is required in runtime context.",
                        tool_call_id=runtime.tool_call_id,
                    )
                ]
            }
        )
    if not user_id:
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        content="Error: unable to resolve the current user for agent creation.",
                        tool_call_id=runtime.tool_call_id,
                    )
                ]
            }
        )

    agent_dir = None
    created_agent_dir = False
    try:
        paths = get_paths()
        agent_dir = paths.user_agent_dir(user_id, agent_name)
        created_agent_dir = not agent_dir.exists()
        agent_dir.mkdir(parents=True, exist_ok=True)

        config_data: dict = {"name": agent_name}
        if description:
            config_data["description"] = description

        config_file = agent_dir / "config.yaml"
        with open(config_file, "w", encoding="utf-8") as f:
            yaml.dump(config_data, f, default_flow_style=False, allow_unicode=True)

        soul_file = agent_dir / "SOUL.md"
        soul_file.write_text(soul, encoding="utf-8")

        logger.info(f"[agent_creator] Created agent '{agent_name}' at {agent_dir}")
        return Command(
            update={
                "created_agent_name": agent_name,
                "messages": [ToolMessage(content=f"Agent '{agent_name}' created successfully!", tool_call_id=runtime.tool_call_id)],
            }
        )

    except Exception as e:
        import shutil

        if agent_dir is not None and agent_dir.exists() and created_agent_dir:
            # Cleanup only the new directory we created for this run.
            shutil.rmtree(agent_dir)
        logger.error(f"[agent_creator] Failed to create agent '{agent_name}': {e}", exc_info=True)
        return Command(update={"messages": [ToolMessage(content=f"Error: {e}", tool_call_id=runtime.tool_call_id)]})
