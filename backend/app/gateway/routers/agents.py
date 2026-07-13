"""CRUD API for custom agents."""

import asyncio
import logging
import re
import shutil

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from deerflow.config.agents_api_config import get_agents_api_config
from deerflow.config.agents_config import (
    AgentConfig,
    list_custom_agents,
    list_team_agents,
    load_agent_config,
    load_agent_soul,
    preserve_non_managed_fields,
)
from deerflow.config.paths import get_paths
from deerflow.runtime.user_context import get_effective_user_id

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["agents"])

AGENT_NAME_PATTERN = re.compile(r"^[A-Za-z0-9-]+$")


class AgentResponse(BaseModel):
    """Response model for a custom agent."""

    name: str = Field(..., description="Agent name (hyphen-case)")
    description: str = Field(default="", description="Agent description")
    model: str | None = Field(default=None, description="Optional model override")
    tool_groups: list[str] | None = Field(default=None, description="Optional tool group whitelist")
    skills: list[str] | None = Field(default=None, description="Optional skill whitelist (None=all, []=none)")
    soul: str | None = Field(default=None, description="SOUL.md content")
    source: str | None = Field(default=None, description="Agent source: personal, team, or legacy")
    published_by_user_id: str | None = Field(default=None, description="User id that published this team agent")
    published_by_current_user: bool = Field(default=False, description="Whether the current user published this team agent")


class AgentsListResponse(BaseModel):
    """Response model for listing all custom agents."""

    agents: list[AgentResponse]


class AgentCreateRequest(BaseModel):
    """Request body for creating a custom agent."""

    name: str = Field(..., description="Agent name (must match ^[A-Za-z0-9-]+$, stored as lowercase)")
    description: str = Field(default="", description="Agent description")
    model: str | None = Field(default=None, description="Optional model override")
    tool_groups: list[str] | None = Field(default=None, description="Optional tool group whitelist")
    skills: list[str] | None = Field(default=None, description="Optional skill whitelist (None=all enabled, []=none)")
    soul: str = Field(default="", description="SOUL.md content — agent personality and behavioral guardrails")


class AgentUpdateRequest(BaseModel):
    """Request body for updating a custom agent."""

    description: str | None = Field(default=None, description="Updated description")
    model: str | None = Field(default=None, description="Updated model override")
    tool_groups: list[str] | None = Field(default=None, description="Updated tool group whitelist")
    skills: list[str] | None = Field(default=None, description="Updated skill whitelist (None=all, []=none)")
    soul: str | None = Field(default=None, description="Updated SOUL.md content")


class AgentPublishRequest(BaseModel):
    """Request body for publishing a personal agent to the team square."""

    overwrite: bool = Field(default=True, description="Whether to replace an existing team agent with the same name")


def _validate_agent_name(name: str) -> None:
    """Validate agent name against allowed pattern.

    Args:
        name: The agent name to validate.

    Raises:
        HTTPException: 422 if the name is invalid.
    """
    if not AGENT_NAME_PATTERN.match(name):
        raise HTTPException(
            status_code=422,
            detail=f"Invalid agent name '{name}'. Must match ^[A-Za-z0-9-]+$ (letters, digits, and hyphens only).",
        )


def _normalize_agent_name(name: str) -> str:
    """Normalize agent name to lowercase for filesystem storage."""
    return name.lower()


def _require_agents_api_enabled() -> None:
    """Reject access unless the custom-agent management API is explicitly enabled."""
    if not get_agents_api_config().enabled:
        raise HTTPException(
            status_code=403,
            detail=("Custom-agent management API is disabled. Set agents_api.enabled=true to expose agent and user-profile routes over HTTP."),
        )


def _agent_config_to_response(agent_cfg: AgentConfig, include_soul: bool = False, *, user_id: str | None = None) -> AgentResponse:
    """Convert AgentConfig to AgentResponse."""
    soul: str | None = None
    if include_soul:
        soul = load_agent_soul(agent_cfg.name, user_id=user_id) or ""

    return AgentResponse(
        name=agent_cfg.name,
        description=agent_cfg.description,
        model=agent_cfg.model,
        tool_groups=agent_cfg.tool_groups,
        skills=agent_cfg.skills,
        soul=soul,
        source="personal",
    )


def _agent_raw_config_from_dir(agent_dir, *, fallback_name: str) -> dict:
    config_file = agent_dir / "config.yaml"
    if not agent_dir.exists() or not config_file.exists():
        raise FileNotFoundError(f"Agent config not found: {config_file}")

    with open(config_file, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if "name" not in data:
        data["name"] = fallback_name
    return data


def _agent_config_from_dir(agent_dir, *, fallback_name: str) -> AgentConfig:
    data = _agent_raw_config_from_dir(agent_dir, fallback_name=fallback_name)
    known_fields = set(AgentConfig.model_fields.keys())
    data = {k: v for k, v in data.items() if k in known_fields}
    return AgentConfig(**data)


def _agent_dir_to_response(agent_dir, *, fallback_name: str, include_soul: bool = True, source: str | None = None, current_user_id: str | None = None) -> AgentResponse:
    raw_config = _agent_raw_config_from_dir(agent_dir, fallback_name=fallback_name)
    known_fields = set(AgentConfig.model_fields.keys())
    agent_cfg = AgentConfig(**{k: v for k, v in raw_config.items() if k in known_fields})
    published_by_user_id = raw_config.get("published_by_user_id")
    if published_by_user_id is not None:
        published_by_user_id = str(published_by_user_id)
    soul = None
    if include_soul:
        soul_path = agent_dir / "SOUL.md"
        soul = soul_path.read_text(encoding="utf-8").strip() if soul_path.exists() else ""
    return AgentResponse(
        name=agent_cfg.name,
        description=agent_cfg.description,
        model=agent_cfg.model,
        tool_groups=agent_cfg.tool_groups,
        skills=agent_cfg.skills,
        soul=soul,
        source=source,
        published_by_user_id=published_by_user_id,
        published_by_current_user=bool(current_user_id and published_by_user_id == current_user_id),
    )


def _shared_agent_dir(name: str):
    paths = get_paths()
    team_dir = paths.team_agent_dir(name)
    if team_dir.exists() and (team_dir / "config.yaml").exists():
        return team_dir, "team"
    legacy_dir = paths.agent_dir(name)
    if legacy_dir.exists() and (legacy_dir / "config.yaml").exists():
        return legacy_dir, "legacy"
    raise FileNotFoundError(name)


def _list_team_agent_responses() -> list[AgentResponse]:
    current_user_id = get_effective_user_id()
    responses: list[AgentResponse] = []
    for agent_cfg in list_team_agents():
        agent_dir, source = _shared_agent_dir(agent_cfg.name)
        responses.append(_agent_dir_to_response(agent_dir, fallback_name=agent_cfg.name, include_soul=True, source=source, current_user_id=current_user_id))
    return responses


@router.get(
    "/agents",
    response_model=AgentsListResponse,
    summary="List Custom Agents",
    description="List all custom agents available in the agents directory, including their soul content.",
)
async def list_agents() -> AgentsListResponse:
    """List all custom agents.

    Returns:
        List of all custom agents with their metadata and soul content.
    """
    _require_agents_api_enabled()

    user_id = get_effective_user_id()
    try:
        agents = list_custom_agents(user_id=user_id)
        return AgentsListResponse(agents=[_agent_config_to_response(a, include_soul=True, user_id=user_id) for a in agents])
    except Exception as e:
        logger.error(f"Failed to list agents: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to list agents: {str(e)}")


@router.get(
    "/team-agents",
    response_model=AgentsListResponse,
    summary="List Team Agents",
    description="List team-shared agents from the agent square, including legacy shared agents.",
)
async def list_team_agents_endpoint() -> AgentsListResponse:
    """List all agents available in the team agent square."""
    _require_agents_api_enabled()

    try:
        agents = await asyncio.to_thread(_list_team_agent_responses)
        return AgentsListResponse(agents=agents)
    except Exception as e:
        logger.error(f"Failed to list team agents: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to list team agents: {str(e)}")


@router.get(
    "/team-agents/{name}",
    response_model=AgentResponse,
    summary="Get Team Agent",
    description="Retrieve details and SOUL.md content for a team-shared agent.",
)
async def get_team_agent(name: str) -> AgentResponse:
    """Get a specific team-shared agent by name."""
    _require_agents_api_enabled()
    _validate_agent_name(name)
    name = _normalize_agent_name(name)

    try:
        agent_dir, source = await asyncio.to_thread(_shared_agent_dir, name)
        current_user_id = get_effective_user_id()
        return await asyncio.to_thread(_agent_dir_to_response, agent_dir, fallback_name=name, include_soul=True, source=source, current_user_id=current_user_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Team agent '{name}' not found")
    except Exception as e:
        logger.error(f"Failed to get team agent '{name}': {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get team agent: {str(e)}")


@router.get(
    "/agents/check",
    summary="Check Agent Name",
    description="Validate an agent name and check if it is available (case-insensitive).",
)
async def check_agent_name(name: str) -> dict:
    """Check whether an agent name is valid and not yet taken.

    Args:
        name: The agent name to check.

    Returns:
        ``{"available": true/false, "name": "<normalized>"}``

    Raises:
        HTTPException: 422 if the name is invalid.
    """
    _require_agents_api_enabled()
    _validate_agent_name(name)
    normalized = _normalize_agent_name(name)
    user_id = get_effective_user_id()
    paths = get_paths()
    # Treat the name as taken if either the per-user path or the legacy shared
    # path holds an agent — picking a name that collides with an unmigrated
    # legacy agent would shadow the legacy entry once migration runs.
    available = not paths.user_agent_dir(user_id, normalized).exists() and not paths.agent_dir(normalized).exists()
    return {"available": available, "name": normalized}


@router.get(
    "/agents/{name}",
    response_model=AgentResponse,
    summary="Get Custom Agent",
    description="Retrieve details and SOUL.md content for a specific custom agent.",
)
async def get_agent(name: str) -> AgentResponse:
    """Get a specific custom agent by name.

    Args:
        name: The agent name.

    Returns:
        Agent details including SOUL.md content.

    Raises:
        HTTPException: 404 if agent not found.
    """
    _require_agents_api_enabled()
    _validate_agent_name(name)
    name = _normalize_agent_name(name)
    user_id = get_effective_user_id()

    try:
        agent_cfg = load_agent_config(name, user_id=user_id)
        return _agent_config_to_response(agent_cfg, include_soul=True, user_id=user_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")
    except Exception as e:
        logger.error(f"Failed to get agent '{name}': {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get agent: {str(e)}")


@router.post(
    "/agents",
    response_model=AgentResponse,
    status_code=201,
    summary="Create Custom Agent",
    description="Create a new custom agent with its config and SOUL.md.",
)
async def create_agent_endpoint(request: AgentCreateRequest) -> AgentResponse:
    """Create a new custom agent.

    Args:
        request: The agent creation request.

    Returns:
        The created agent details.

    Raises:
        HTTPException: 409 if agent already exists, 422 if name is invalid.
    """
    _require_agents_api_enabled()
    _validate_agent_name(request.name)
    normalized_name = _normalize_agent_name(request.name)
    user_id = get_effective_user_id()
    paths = get_paths()

    def _create_agent() -> AgentResponse | None:
        # Worker thread: base-dir resolution, existence checks, directory/file
        # creation, read-back, and failure cleanup are all blocking filesystem
        # IO that must stay off the event loop.
        agent_dir = paths.user_agent_dir(user_id, normalized_name)
        legacy_dir = paths.agent_dir(normalized_name)

        if legacy_dir.exists():
            return None  # signals 409 to the caller

        try:
            try:
                agent_dir.mkdir(parents=True, exist_ok=False)
            except FileExistsError:
                return None  # signals 409 to the caller
            # Write config.yaml
            config_data: dict = {"name": normalized_name}
            if request.description:
                config_data["description"] = request.description
            if request.model is not None:
                config_data["model"] = request.model
            if request.tool_groups is not None:
                config_data["tool_groups"] = request.tool_groups
            if request.skills is not None:
                config_data["skills"] = request.skills

            config_file = agent_dir / "config.yaml"
            with open(config_file, "w", encoding="utf-8") as f:
                yaml.dump(config_data, f, default_flow_style=False, allow_unicode=True)

            # Write SOUL.md
            soul_file = agent_dir / "SOUL.md"
            soul_file.write_text(request.soul, encoding="utf-8")

            logger.info(f"Created agent '{normalized_name}' at {agent_dir}")

            agent_cfg = load_agent_config(normalized_name, user_id=user_id)
            return _agent_config_to_response(agent_cfg, include_soul=True, user_id=user_id)
        except Exception:
            # Clean up partial state on failure before surfacing the error.
            if agent_dir.exists():
                shutil.rmtree(agent_dir)
            raise

    try:
        response = await asyncio.to_thread(_create_agent)
    except Exception as e:
        logger.error(f"Failed to create agent '{request.name}': {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to create agent: {str(e)}")

    if response is None:
        raise HTTPException(status_code=409, detail=f"Agent '{normalized_name}' already exists")

    return response


@router.post(
    "/agents/{name}/publish",
    response_model=AgentResponse,
    summary="Publish Custom Agent",
    description="Publish a personal custom agent to the team agent square.",
)
async def publish_agent(name: str, request: AgentPublishRequest | None = None) -> AgentResponse:
    """Publish a personal agent into the team-shared agent square."""
    _require_agents_api_enabled()
    _validate_agent_name(name)
    name = _normalize_agent_name(name)
    overwrite = True if request is None else request.overwrite
    user_id = get_effective_user_id()
    paths = get_paths()

    def _publish_agent() -> AgentResponse | None:
        source_dir = paths.user_agent_dir(user_id, name)
        if not source_dir.exists() or not (source_dir / "config.yaml").exists():
            raise FileNotFoundError(name)

        team_dir = paths.team_agent_dir(name)
        if team_dir.exists():
            if not overwrite:
                return None
            shutil.rmtree(team_dir)
        team_dir.mkdir(parents=True, exist_ok=False)

        source_cfg = _agent_config_from_dir(source_dir, fallback_name=name)
        config_data: dict = {
            "name": source_cfg.name,
            "description": source_cfg.description,
            "published_by_user_id": user_id,
        }
        if source_cfg.model is not None:
            config_data["model"] = source_cfg.model
        if source_cfg.tool_groups is not None:
            config_data["tool_groups"] = source_cfg.tool_groups
        if source_cfg.skills is not None:
            config_data["skills"] = source_cfg.skills

        with open(team_dir / "config.yaml", "w", encoding="utf-8") as f:
            yaml.dump(config_data, f, default_flow_style=False, allow_unicode=True)

        source_soul = source_dir / "SOUL.md"
        if source_soul.exists():
            (team_dir / "SOUL.md").write_text(source_soul.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            (team_dir / "SOUL.md").write_text("", encoding="utf-8")

        return _agent_dir_to_response(team_dir, fallback_name=name, include_soul=True, source="team", current_user_id=user_id)

    try:
        response = await asyncio.to_thread(_publish_agent)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Personal agent '{name}' not found")
    except Exception as e:
        logger.error(f"Failed to publish agent '{name}': {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to publish agent: {str(e)}")

    if response is None:
        raise HTTPException(status_code=409, detail=f"Team agent '{name}' already exists")

    logger.info(f"Published agent '{name}' to team square")
    return response


@router.delete(
    "/team-agents/{name}",
    status_code=204,
    summary="Unpublish Team Agent",
    description="Remove a team-published agent from the agent square.",
)
async def unpublish_team_agent(name: str) -> None:
    """Remove a team-published agent from the square.

    Legacy shared agents under ``agents/`` are displayed in the square for
    compatibility, but this endpoint only removes entries published into
    ``team_agents/``.
    """
    _require_agents_api_enabled()
    _validate_agent_name(name)
    name = _normalize_agent_name(name)
    user_id = get_effective_user_id()
    paths = get_paths()

    def _remove_team_agent() -> str:
        team_dir = paths.team_agent_dir(name)
        if not team_dir.exists() or not (team_dir / "config.yaml").exists():
            return "legacy" if paths.agent_dir(name).exists() else "missing"

        raw_config = _agent_raw_config_from_dir(team_dir, fallback_name=name)
        published_by = raw_config.get("published_by_user_id")
        if published_by is not None and str(published_by) != user_id:
            return "forbidden"

        shutil.rmtree(team_dir)
        return "deleted"

    try:
        outcome = await asyncio.to_thread(_remove_team_agent)
    except Exception as e:
        logger.error(f"Failed to unpublish team agent '{name}': {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to unpublish team agent: {str(e)}")

    if outcome == "missing":
        raise HTTPException(status_code=404, detail=f"Team agent '{name}' not found")
    if outcome == "legacy":
        raise HTTPException(
            status_code=409,
            detail=(f"Agent '{name}' is a legacy shared agent, not a team-published agent. Move or remove it from the legacy agents directory instead."),
        )
    if outcome == "forbidden":
        raise HTTPException(status_code=403, detail=f"Only the user who published team agent '{name}' can unpublish it")

    logger.info(f"Unpublished team agent '{name}' from team square")


@router.put(
    "/agents/{name}",
    response_model=AgentResponse,
    summary="Update Custom Agent",
    description="Update an existing custom agent's config and/or SOUL.md.",
)
async def update_agent(name: str, request: AgentUpdateRequest) -> AgentResponse:
    """Update an existing custom agent.

    Args:
        name: The agent name.
        request: The update request (all fields optional).

    Returns:
        The updated agent details.

    Raises:
        HTTPException: 404 if agent not found.
    """
    _require_agents_api_enabled()
    _validate_agent_name(name)
    name = _normalize_agent_name(name)
    user_id = get_effective_user_id()

    try:
        agent_cfg = load_agent_config(name, user_id=user_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")

    paths = get_paths()
    agent_dir = paths.user_agent_dir(user_id, name)
    if not agent_dir.exists() and paths.team_agent_dir(name).exists():
        raise HTTPException(
            status_code=409,
            detail=(f"Agent '{name}' exists in the team square but is not scoped to the current user. Create or import a personal copy before updating it."),
        )
    if not agent_dir.exists() and paths.agent_dir(name).exists():
        raise HTTPException(
            status_code=409,
            detail=(f"Agent '{name}' only exists in the legacy shared layout and is not scoped to a user. Run scripts/migrate_user_isolation.py to move legacy agents into the per-user layout before updating."),
        )

    try:
        # Update config if any config fields changed
        # Use model_fields_set to distinguish "field omitted" from "explicitly set to null".
        # This is critical for skills where None means "inherit all" (not "don't change").
        fields_set = request.model_fields_set
        config_changed = bool(
            fields_set
            & {
                "description",
                "model",
                "tool_groups",
                "skills",
            }
        )

        if config_changed:
            updated: dict = {
                "name": agent_cfg.name,
                "description": request.description if "description" in fields_set else agent_cfg.description,
            }
            new_model = request.model if "model" in fields_set else agent_cfg.model
            if new_model is not None:
                updated["model"] = new_model

            new_tool_groups = request.tool_groups if "tool_groups" in fields_set else agent_cfg.tool_groups
            if new_tool_groups is not None:
                updated["tool_groups"] = new_tool_groups

            # skills: None = inherit all, [] = no skills, ["a","b"] = whitelist
            if "skills" in fields_set:
                new_skills = request.skills
            else:
                new_skills = agent_cfg.skills
            if new_skills is not None:
                updated["skills"] = new_skills

            # Carry forward every top-level AgentConfig field this route does
            # not manage (currently ``github:``, plus any future field added
            # to :class:`AgentConfig`). The harness ``update_agent`` tool uses
            # the same helper, so an operator editing the agent description
            # from the Web UI does not silently strip a hand-authored
            # ``github:`` binding — which would otherwise leave the next
            # webhook delivery unable to find the agent in the registry and
            # silently no-op.
            for key, value in preserve_non_managed_fields(agent_cfg).items():
                updated.setdefault(key, value)

            config_file = agent_dir / "config.yaml"
            with open(config_file, "w", encoding="utf-8") as f:
                yaml.dump(updated, f, default_flow_style=False, allow_unicode=True)

        # Update SOUL.md if provided
        if request.soul is not None:
            soul_path = agent_dir / "SOUL.md"
            soul_path.write_text(request.soul, encoding="utf-8")

        logger.info(f"Updated agent '{name}'")

        refreshed_cfg = load_agent_config(name, user_id=user_id)
        return _agent_config_to_response(refreshed_cfg, include_soul=True, user_id=user_id)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update agent '{name}': {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to update agent: {str(e)}")


class UserProfileResponse(BaseModel):
    """Response model for the global user profile (USER.md)."""

    content: str | None = Field(default=None, description="USER.md content, or null if not yet created")


class UserProfileUpdateRequest(BaseModel):
    """Request body for setting the global user profile."""

    content: str = Field(default="", description="USER.md content — describes the user's background and preferences")


@router.get(
    "/user-profile",
    response_model=UserProfileResponse,
    summary="Get User Profile",
    description="Read the global USER.md file that is injected into all custom agents.",
)
async def get_user_profile() -> UserProfileResponse:
    """Return the current USER.md content.

    Returns:
        UserProfileResponse with content=None if USER.md does not exist yet.
    """
    _require_agents_api_enabled()

    try:
        user_md_path = get_paths().user_md_file
        if not user_md_path.exists():
            return UserProfileResponse(content=None)
        raw = user_md_path.read_text(encoding="utf-8").strip()
        return UserProfileResponse(content=raw or None)
    except Exception as e:
        logger.error(f"Failed to read user profile: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to read user profile: {str(e)}")


@router.put(
    "/user-profile",
    response_model=UserProfileResponse,
    summary="Update User Profile",
    description="Write the global USER.md file that is injected into all custom agents.",
)
async def update_user_profile(request: UserProfileUpdateRequest) -> UserProfileResponse:
    """Create or overwrite the global USER.md.

    Args:
        request: The update request with the new USER.md content.

    Returns:
        UserProfileResponse with the saved content.
    """
    _require_agents_api_enabled()

    try:
        paths = get_paths()
        paths.base_dir.mkdir(parents=True, exist_ok=True)
        paths.user_md_file.write_text(request.content, encoding="utf-8")
        logger.info(f"Updated USER.md at {paths.user_md_file}")
        return UserProfileResponse(content=request.content or None)
    except Exception as e:
        logger.error(f"Failed to update user profile: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to update user profile: {str(e)}")


@router.delete(
    "/agents/{name}",
    status_code=204,
    summary="Delete Custom Agent",
    description="Delete a custom agent and all its files (config, SOUL.md, memory).",
)
async def delete_agent(name: str) -> None:
    """Delete a custom agent.

    Args:
        name: The agent name.

    Raises:
        HTTPException: 404 if no per-user copy exists; 409 if only a legacy
            shared copy exists (suggesting the migration script).
    """
    _require_agents_api_enabled()
    _validate_agent_name(name)
    name = _normalize_agent_name(name)
    user_id = get_effective_user_id()
    paths = get_paths()

    def _remove_agent_dir() -> tuple[str, str]:
        # Runs in a worker thread: resolving the base dir, probing the directory
        # (`exists`), and removing it (`rmtree`) are all blocking filesystem IO
        # that must stay off the event loop.
        agent_dir = paths.user_agent_dir(user_id, name)
        if not agent_dir.exists():
            if paths.team_agent_dir(name).exists():
                outcome = "team"
            elif paths.agent_dir(name).exists():
                outcome = "legacy"
            else:
                outcome = "missing"
            return outcome, str(agent_dir)
        shutil.rmtree(agent_dir)
        return "deleted", str(agent_dir)

    try:
        outcome, agent_dir = await asyncio.to_thread(_remove_agent_dir)
    except Exception as e:
        logger.error(f"Failed to delete agent '{name}': {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to delete agent: {str(e)}")

    if outcome == "legacy":
        raise HTTPException(
            status_code=409,
            detail=(f"Agent '{name}' only exists in the legacy shared layout and is not scoped to a user. Run scripts/migrate_user_isolation.py to move legacy agents into the per-user layout before deleting."),
        )
    if outcome == "team":
        raise HTTPException(
            status_code=409,
            detail=(f"Agent '{name}' exists in the team square but is not scoped to the current user. Personal delete cannot remove shared agents."),
        )
    if outcome == "missing":
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")

    logger.info(f"Deleted agent '{name}' from {agent_dir}")
