"""Configuration and loaders for custom agents."""

import logging
import re
from urllib.parse import unquote
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from deerflow.config.paths import get_paths
from deerflow.config.user_context import resolve_user_id

logger = logging.getLogger(__name__)

SOUL_FILENAME = "SOUL.md"
AGENT_NAME_PATTERN = re.compile(r"^[\w\-\u3400-\u9fff]+$", re.UNICODE)


class AgentConfig(BaseModel):
    """Configuration for a custom agent."""

    name: str
    description: str = ""
    model: str | None = None
    tool_groups: list[str] | None = None

def _agent_dir(name: str, user_id: str | None) -> Path:
    paths = get_paths()
    if user_id:
        return paths.user_agent_dir(user_id, name)
    return paths.agent_dir(name)


def _agent_dir_candidates(name: str, user_id: str | None) -> list[Path]:
    """Return lookup order for agent directories.

    Order:
      1) User-scoped agent dir (if user_id is present)
      2) Global shared agent dir
    """
    paths = get_paths()
    candidates: list[Path] = []
    if user_id:
        candidates.append(paths.user_agent_dir(user_id, name))
    candidates.append(paths.agent_dir(name))
    return candidates


def _agents_dir(user_id: str | None) -> Path:
    paths = get_paths()
    if user_id:
        return paths.user_agents_dir(user_id)
    return paths.agents_dir


def _normalize_lookup_name(name: str) -> str:
    """Decode URL-encoded names defensively before validation/lookup."""
    if "%" in name:
        try:
            return unquote(name)
        except Exception:
            return name
    return name


def load_agent_config(name: str | None, *, user_id: str | None = None) -> AgentConfig | None:
    """Load the custom or default agent's config from its directory.

    Args:
        name: The agent name.

    Returns:
        AgentConfig instance.

    Raises:
        FileNotFoundError: If the agent directory or config.yaml does not exist.
        ValueError: If config.yaml cannot be parsed.
    """

    if name is None:
        return None
    name = _normalize_lookup_name(name)

    user_id = user_id or resolve_user_id()

    if not AGENT_NAME_PATTERN.match(name):
        raise ValueError(f"Invalid agent name '{name}'. Must match pattern: {AGENT_NAME_PATTERN.pattern}")
    errors: list[str] = []
    config_file: Path | None = None
    data: dict[str, Any] | None = None
    for agent_dir in _agent_dir_candidates(name, user_id):
        candidate_cfg = agent_dir / "config.yaml"
        if not agent_dir.exists():
            errors.append(f"Agent directory not found: {agent_dir}")
            continue
        if not candidate_cfg.exists():
            errors.append(f"Agent config not found: {candidate_cfg}")
            continue
        try:
            with open(candidate_cfg, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            config_file = candidate_cfg
            break
        except yaml.YAMLError as e:
            raise ValueError(f"Failed to parse agent config {candidate_cfg}: {e}") from e

    if data is None or config_file is None:
        raise FileNotFoundError("; ".join(errors) if errors else f"Agent '{name}' not found")

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

    Returns:
        The SOUL.md content as a string, or None if the file does not exist.
    """
    user_id = user_id or resolve_user_id()
    if agent_name:
        agent_name = _normalize_lookup_name(agent_name)
        for agent_dir in _agent_dir_candidates(agent_name, user_id):
            soul_path = agent_dir / SOUL_FILENAME
            if soul_path.exists():
                content = soul_path.read_text(encoding="utf-8").strip()
                if content:
                    return content
        return None
    agent_dir = get_paths().user_dir(user_id) if user_id else get_paths().base_dir
    soul_path = agent_dir / SOUL_FILENAME
    if not soul_path.exists():
        return None
    content = soul_path.read_text(encoding="utf-8").strip()
    return content or None


def list_custom_agents(*, user_id: str | None = None) -> list[AgentConfig]:
    """Scan the agents directory and return all valid custom agents.

    Returns:
        List of AgentConfig for each valid agent directory found.
    """
    user_id = user_id or resolve_user_id()
    dirs: list[Path] = [get_paths().agents_dir]
    user_agents_dir: Path | None = None
    if user_id:
        user_agents_dir = get_paths().user_agents_dir(user_id)
        dirs.append(user_agents_dir)

    merged: dict[str, AgentConfig] = {}
    for agents_dir in dirs:
        if not agents_dir.exists():
            continue
        for entry in sorted(agents_dir.iterdir()):
            if not entry.is_dir():
                continue
            config_file = entry / "config.yaml"
            if not config_file.exists():
                logger.debug(f"Skipping {entry.name}: no config.yaml")
                continue
            try:
                # Resolve with explicit source scope to avoid accidental fallback.
                scoped_user = user_id if (user_agents_dir is not None and agents_dir == user_agents_dir) else None
                agent_cfg = load_agent_config(entry.name, user_id=scoped_user)
                if agent_cfg is not None:
                    merged[agent_cfg.name.lower()] = agent_cfg
            except Exception as e:
                logger.warning(f"Skipping agent '{entry.name}': {e}")

    return [merged[k] for k in sorted(merged.keys())]
