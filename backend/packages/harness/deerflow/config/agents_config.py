"""Configuration and loaders for custom agents.

Custom agents are stored per-user under ``{base_dir}/users/{user_id}/agents/{name}/``.
Team-shared agents live under ``{base_dir}/team_agents/{name}/``. A legacy shared
layout at ``{base_dir}/agents/{name}/`` is still readable so that installations
that pre-date user isolation continue to work until they run the
``scripts/migrate_user_isolation.py`` migration. New personal writes always target
the per-user layout.
"""

import logging
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from deerflow.config.paths import get_paths
from deerflow.runtime.user_context import get_effective_user_id

logger = logging.getLogger(__name__)

SOUL_FILENAME = "SOUL.md"
AGENT_NAME_PATTERN = re.compile(r"^[\w\-\u3400-\u9fff]+$", re.UNICODE)


def _blank_to_none(value: str | None) -> str | None:
    """Normalize a whitespace-only string to ``None``; leave real values untouched."""
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


class GitHubTriggerConfig(BaseModel):
    """Per-event trigger filter inside a :class:`GitHubBinding`."""

    actions: list[str] | None = None
    require_mention: bool = False
    allow_authors: list[str] = Field(default_factory=list)
    mention_login: str | None = None

    @field_validator("mention_login")
    @classmethod
    def _normalize_mention_login(cls, value: str | None) -> str | None:
        return _blank_to_none(value)


class GitHubBinding(BaseModel):
    """One agent-to-repository binding with per-event trigger overrides."""

    repo: str
    triggers: dict[str, GitHubTriggerConfig] = Field(default_factory=dict)


class GitHubAgentConfig(BaseModel):
    """Top-level ``github:`` block on a custom agent's ``config.yaml``."""

    installation_id: int | None = None
    bot_login: str | None = None
    recursion_limit: int | None = None
    bindings: list[GitHubBinding] = Field(default_factory=list)

    @field_validator("bot_login")
    @classmethod
    def _normalize_bot_login(cls, value: str | None) -> str | None:
        return _blank_to_none(value)

    @model_validator(mode="after")
    def _unique_binding_repos(self) -> "GitHubAgentConfig":
        """Reject duplicate ``repo`` values across ``bindings``."""
        seen: set[str] = set()
        dupes: set[str] = set()
        for binding in self.bindings:
            if binding.repo in seen:
                dupes.add(binding.repo)
            seen.add(binding.repo)
        if dupes:
            raise ValueError(
                f"Agent github.bindings has duplicate repos {sorted(dupes)}. "
                "Each repo must appear at most once; merge their `triggers:` maps into a single binding."
            )
        return self


def validate_agent_name(name: str | None) -> str | None:
    """Validate a custom agent name before using it in filesystem paths."""
    if name is None:
        return None
    if not isinstance(name, str):
        raise ValueError("Invalid agent name. Expected a string or None.")
    if not AGENT_NAME_PATTERN.fullmatch(name):
        raise ValueError(f"Invalid agent name '{name}'. Must match pattern: {AGENT_NAME_PATTERN.pattern}")
    return name


class AgentConfig(BaseModel):
    """Configuration for a custom agent."""

    name: str
    description: str = ""
    model: str | None = None
    tool_groups: list[str] | None = None
    # skills controls which skills are loaded into the agent's prompt:
    # - None (or omitted): load all enabled skills (default fallback behavior)
    # - [] (explicit empty list): disable all skills
    # - ["skill1", "skill2"]: load only the specified skills
    skills: list[str] | None = None
    # Optional binding to GitHub repositories so this agent can respond to
    # webhook events from the gateway dispatcher. None preserves existing agents.
    github: GitHubAgentConfig | None = None


MANAGED_AGENT_CONFIG_FIELDS: frozenset[str] = frozenset({"name", "description", "model", "tool_groups", "skills"})


def preserve_non_managed_fields(existing_cfg: AgentConfig) -> dict[str, object]:
    """Return top-level config fields that update surfaces do not manage."""
    return existing_cfg.model_dump(exclude_unset=True, exclude=MANAGED_AGENT_CONFIG_FIELDS)


def resolve_agent_dir(name: str, *, user_id: str | None = None) -> Path:
    """Return the on-disk directory for an agent, preferring the per-user layout.

    Resolution order:
    1. ``{base_dir}/users/{user_id}/agents/{name}/`` (per-user, current layout).
    2. ``{base_dir}/agents/{name}/`` (legacy shared layout — read-only fallback).

    If neither exists, the per-user path is returned so callers that intend to
    create the agent write into the new layout.

    Args:
        name: Validated agent name.
        user_id: Owner of the agent. Defaults to the effective user from the
            request context (or ``"default"`` in no-auth mode).
    """
    paths = get_paths()
    effective_user = user_id or get_effective_user_id()
    user_path = paths.user_agent_dir(effective_user, name)
    # Require config.yaml to confirm this is a genuine agent directory,
    # not a leftover from memory/storage writes (see #3390).
    if user_path.exists() and (user_path / "config.yaml").exists():
        return user_path

    team_path = paths.team_agent_dir(name)
    if team_path.exists() and (team_path / "config.yaml").exists():
        return team_path

    legacy_path = paths.agent_dir(name)
    if legacy_path.exists() and (legacy_path / "config.yaml").exists():
        return legacy_path

    return user_path


def load_agent_config(name: str | None, *, user_id: str | None = None) -> AgentConfig | None:
    """Load the custom or default agent's config from its directory.

    Reads from the per-user layout first, then the team-shared layout, then the
    legacy shared layout for installations that have not yet been migrated.

    Args:
        name: The agent name.
        user_id: Owner of the agent. Defaults to the effective user from the
            current request context.

    Returns:
        AgentConfig instance, or ``None`` if ``name`` is ``None``.

    Raises:
        FileNotFoundError: If the agent directory or config.yaml does not exist.
        ValueError: If config.yaml cannot be parsed.
    """

    if name is None:
        return None

    name = validate_agent_name(name)
    agent_dir = resolve_agent_dir(name, user_id=user_id)
    config_file = agent_dir / "config.yaml"

    if not agent_dir.exists():
        raise FileNotFoundError(f"Agent directory not found: {agent_dir}")

    if not config_file.exists():
        raise FileNotFoundError(f"Agent config not found: {config_file}")

    try:
        with open(config_file, encoding="utf-8") as f:
            data: dict[str, Any] = yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        raise ValueError(f"Failed to parse agent config {config_file}: {e}") from e

    # Ensure name is set from directory name if not in file
    if "name" not in data:
        data["name"] = name

    # Strip unknown fields before passing to Pydantic (e.g. legacy prompt_file)
    known_fields = set(AgentConfig.model_fields.keys())
    data = {k: v for k, v in data.items() if k in known_fields}

    return AgentConfig(**data)


def load_agent_soul(agent_name: str | None, *, user_id: str | None = None) -> str | None:
    """Read the SOUL.md file for a custom agent, if it exists.

    SOUL.md defines the agent's personality, values, and behavioral guardrails.
    It is injected into the lead agent's system prompt as additional context.

    Args:
        agent_name: The name of the agent or None for the default agent.
        user_id: Owner of the agent. Defaults to the effective user from the
            current request context.

    Returns:
        The SOUL.md content as a string, or None if the file does not exist.
    """
    if agent_name:
        agent_dir = resolve_agent_dir(agent_name, user_id=user_id)
    else:
        agent_dir = get_paths().base_dir
    soul_path = agent_dir / SOUL_FILENAME
    if not soul_path.exists():
        return None
    content = soul_path.read_text(encoding="utf-8").strip()
    return content or None


def list_custom_agents(*, user_id: str | None = None) -> list[AgentConfig]:
    """Scan the current user's personal agents directory.

    Legacy shared agents are intentionally excluded from this list so the UI can
    keep "My Agents" personal-only. Legacy shared agents are listed by
    :func:`list_team_agents` for the agent square.

    Args:
        user_id: Owner whose agents to list. Defaults to the effective user
            from the current request context.

    Returns:
        List of AgentConfig for each valid agent directory found.
    """
    paths = get_paths()
    effective_user = user_id or get_effective_user_id()

    agents: list[AgentConfig] = []

    user_root = paths.user_agents_dir(effective_user)
    if not user_root.exists():
        return []

    for entry in sorted(user_root.iterdir()):
        if not entry.is_dir():
            continue
        config_file = entry / "config.yaml"
        if not config_file.exists():
            logger.debug(f"Skipping {entry.name}: no config.yaml")
            continue

        try:
            agent_cfg = load_agent_config(entry.name, user_id=effective_user)
            if agent_cfg is None:
                continue
            agents.append(agent_cfg)
        except Exception as e:
            logger.warning(f"Skipping agent '{entry.name}': {e}")

    agents.sort(key=lambda a: a.name)
    return agents


def list_team_agents() -> list[AgentConfig]:
    """Scan team-shared and legacy shared directories and return valid agents.

    Team-published agents shadow legacy shared agents with the same name.
    """
    paths = get_paths()

    seen: set[str] = set()
    agents: list[AgentConfig] = []

    for root in (paths.team_agents_dir, paths.agents_dir):
        if not root.exists():
            continue
        for entry in sorted(root.iterdir()):
            if not entry.is_dir():
                continue
            if entry.name in seen:
                continue
            config_file = entry / "config.yaml"
            if not config_file.exists():
                logger.debug(f"Skipping shared agent {entry.name}: no config.yaml")
                continue

            try:
                with open(config_file, encoding="utf-8") as f:
                    data: dict[str, Any] = yaml.safe_load(f) or {}
                if "name" not in data:
                    data["name"] = entry.name

                known_fields = set(AgentConfig.model_fields.keys())
                data = {k: v for k, v in data.items() if k in known_fields}
                agents.append(AgentConfig(**data))
                seen.add(entry.name)
            except Exception as e:
                logger.warning(f"Skipping shared agent '{entry.name}': {e}")

    agents.sort(key=lambda a: a.name)
    return agents
