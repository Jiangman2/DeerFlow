"""Configuration and loaders for custom agents.

Custom agents are stored per-user under ``{base_dir}/users/{user_id}/agents/{name}/``.
A legacy shared layout at ``{base_dir}/agents/{name}/`` is still readable so that
installations that pre-date user isolation continue to work until they run the
``scripts/migrate_user_isolation.py`` migration. New writes always target the
per-user layout.

Enterprise team agents are stored separately under ``{base_dir}/team_agents``.
They are readable by every user but remain distinct from personal agents and
are never writable through a viewer's agent chat.
"""

import logging
import re
import unicodedata
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from deerflow.config.agent_knowledge_config import AgentKnowledgeScope
from deerflow.config.paths import get_paths
from deerflow.runtime.user_context import get_effective_user_id

logger = logging.getLogger(__name__)

SOUL_FILENAME = "SOUL.md"
AGENT_NAME_PATTERN = re.compile(r"^[\w\-\u3400-\u9fff]+$", re.UNICODE)
MAX_AGENT_NAME_LENGTH = 64
MAX_AGENT_DISPLAY_NAME_LENGTH = 80
MAX_AGENT_OUTPUT_TOKENS = 200_000
MAX_AGENT_DESCRIPTION_LENGTH = 500
MAX_DIGITAL_EMPLOYEE_TAGS = 8
MAX_DIGITAL_EMPLOYEE_TAG_LENGTH = 20
AGENT_CATEGORY_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def validate_agent_display_name(value: str | None) -> str | None:
    """Validate a human-facing Unicode label without changing the stable id."""
    if value is None:
        return None
    if re.search(
        r"[\x00-\x1f\x7f-\x9f\u00ad\u061c\u200b\u200e-\u200f\u2028-\u202e\u2060-\u2069\ufeff]",
        value,
    ):
        raise ValueError("Display name must not contain control characters or invisible formatting controls")
    normalized = value.strip()
    if len(normalized) > MAX_AGENT_DISPLAY_NAME_LENGTH:
        raise ValueError(f"Display name must be {MAX_AGENT_DISPLAY_NAME_LENGTH} characters or fewer")
    if normalized and all(unicodedata.category(character)[0] in {"C", "M", "Z"} for character in normalized):
        raise ValueError("Display name must contain visible text")
    return normalized or None


def _blank_to_none(value: str | None) -> str | None:
    """Normalize a whitespace-only string to ``None``; leave real values untouched.

    A whitespace-only string (e.g. ``"   "``) is truthy in Python, so an
    unstripped ``value or fallback`` expression never falls through to the
    fallback. The ``require_mention`` precedence chain (``trigger.mention_login``
    -> ``github.bot_login`` -> ``channels.github.default_mention_login`` ->
    ``agent.name``, see AGENTS.md) relies on exactly that fallthrough, so both
    of the config-sourced links are normalized here, once, at the model layer
    — every reader downstream (today's and any future one) sees an honest
    "unset" instead of a literal whitespace string that can never match a
    real ``@mention``.
    """
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


class GitHubTriggerConfig(BaseModel):
    """Per-event trigger filter inside a :class:`GitHubBinding`."""

    # If set, only these GitHub action values fire the agent. None means "any
    # action allowed". Example: ["opened"] for pull_request restricts the agent
    # to only respond to brand-new PRs.
    actions: list[str] | None = None
    # If True, comment events only fire when the bot login is @-mentioned in
    # the comment body. Ignored on non-comment events.
    require_mention: bool = False
    # GitHub logins whose events bypass require_mention. Lets a repo owner
    # talk to the bot without typing the handle every time.
    allow_authors: list[str] = Field(default_factory=list)
    # Override the global default bot mention login for this trigger only.
    # Useful when one agent answers as @bot-a and another as @bot-b. A
    # whitespace-only value is normalized to None (see ``_blank_to_none``) so
    # it is treated as unset and falls through to ``github.bot_login`` instead
    # of being compared against literally.
    mention_login: str | None = None

    @field_validator("mention_login")
    @classmethod
    def _normalize_mention_login(cls, value: str | None) -> str | None:
        return _blank_to_none(value)


class GitHubBinding(BaseModel):
    """One (agent, repo) binding with per-event trigger overrides."""

    # GitHub "owner/name" string.
    repo: str
    # Event name → trigger override. Missing keys fall back to the dispatcher's
    # default trigger for that event.
    triggers: dict[str, GitHubTriggerConfig] = Field(default_factory=dict)


class GitHubAgentConfig(BaseModel):
    """Top-level ``github:`` block on a custom agent's ``config.yaml``."""

    # GitHub App installation id used to mint per-repo access tokens. The
    # ``ChannelManager`` mints a 1h installation token from this and injects it
    # into ``run_context["github_token"]``, which the ``bash`` tool exposes to
    # the agent's sandbox as ``GH_TOKEN`` / ``GITHUB_TOKEN``. The agent then
    # uses ``gh`` to read repo state, push branches, and post comments itself.
    # None means no token is minted: the agent still runs but cannot push or
    # post (effectively read-only via unauthenticated ``gh`` for public repos,
    # or fully blind for private ones).
    installation_id: int | None = None
    # GitHub App login this agent posts as (e.g. ``llm-gateway-ai`` for the
    # ``llm-gateway-ai[bot]`` App identity, without the ``[bot]`` suffix).
    # The dispatcher's self-event gate uses this to recognize webhook
    # deliveries triggered by this agent's own activity, regardless of what
    # ``mention_login`` the agent uses for trigger matching. None means
    # "fall back to mention_login / agent name", which is fine when those
    # match the bot identity, but should be set explicitly when they differ.
    # A whitespace-only value is normalized to None (see ``_blank_to_none``)
    # so it is treated as unset and falls through the rest of the chain.
    bot_login: str | None = None
    # Override the default github-channel ``recursion_limit`` (250). GitHub
    # runs are autonomous and long-running by nature — clone, explore, edit,
    # test, push, comment — but the right ceiling varies a lot by workload:
    # a review-only agent might be happy at 50, a multi-file refactor agent
    # might need 500+. Setting None means "use the channel default (250)".
    # Any positive integer is honored verbatim — including values below the
    # channel default and below the global 100-step floor — so an explicit
    # safety setting like ``recursion_limit: 50`` halts the agent at 50
    # super-steps as configured. Values <=0 are ignored (treated as None)
    # — a negative/zero limit would halt the agent before the first step.
    recursion_limit: int | None = None
    # Repos this agent is bound to. Empty list = bound to nothing = the agent
    # never fires from a webhook, even if it has a ``github:`` block.
    bindings: list[GitHubBinding] = Field(default_factory=list)

    @field_validator("bot_login")
    @classmethod
    def _normalize_bot_login(cls, value: str | None) -> str | None:
        return _blank_to_none(value)

    @model_validator(mode="after")
    def _unique_binding_repos(self) -> "GitHubAgentConfig":
        """Reject duplicate ``repo`` values across ``bindings``.

        At most one binding per repo is allowed. The per-event ``triggers``
        map on a single binding already expresses "this agent listens to N
        events on this repo", so multiple bindings for the same repo would
        either duplicate events (silent first-wins / double-registration —
        see PR feedback R3) or fragment them across rows for no benefit.
        Since this is the initial implementation and no existing operator
        config relies on duplicate-repo bindings, we fail loudly at config
        load instead of papering over the ambiguity at dispatch time.
        """
        seen: set[str] = set()
        dupes: set[str] = set()
        for binding in self.bindings:
            if binding.repo in seen:
                dupes.add(binding.repo)
            seen.add(binding.repo)
        if dupes:
            raise ValueError(f"Agent github.bindings has duplicate repos {sorted(dupes)}. Each repo must appear at most once — merge their `triggers:` maps into a single binding.")
        return self


def validate_agent_name(name: str | None) -> str | None:
    """Validate a custom agent name before using it in filesystem paths."""
    if name is None:
        return None
    if not isinstance(name, str):
        raise ValueError("Invalid agent name. Expected a string or None.")
    if len(name) > MAX_AGENT_NAME_LENGTH:
        raise ValueError(f"Invalid agent name '{name}'. Must be {MAX_AGENT_NAME_LENGTH} characters or fewer")
    is_safe = bool(name) and AGENT_NAME_PATTERN.fullmatch(name) and all(char == "-" or unicodedata.category(char)[0] in {"L", "N"} for char in name)
    if not is_safe:
        raise ValueError(f"Invalid agent name '{name}'. Must match pattern: {AGENT_NAME_PATTERN.pattern}")
    return name


def _normalize_category_id(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if not AGENT_CATEGORY_ID_PATTERN.fullmatch(normalized):
        raise ValueError("Category id must use lowercase letters, digits, and hyphens")
    return normalized


def _normalize_agent_tags(value: list[str]) -> list[str]:
    tags: list[str] = []
    seen: set[str] = set()
    for raw_tag in value:
        tag = raw_tag.strip()
        if not tag:
            continue
        if len(tag) > MAX_DIGITAL_EMPLOYEE_TAG_LENGTH:
            raise ValueError(f"Each tag must be {MAX_DIGITAL_EMPLOYEE_TAG_LENGTH} characters or fewer")
        folded = tag.casefold()
        if folded not in seen:
            seen.add(folded)
            tags.append(tag)
    if len(tags) > MAX_DIGITAL_EMPLOYEE_TAGS:
        raise ValueError(f"A digital employee can have at most {MAX_DIGITAL_EMPLOYEE_TAGS} tags")
    return tags


class AgentModelSettings(BaseModel):
    """Per-digital-employee sampling overrides layered over a model profile."""

    model_config = ConfigDict(extra="forbid")

    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_tokens: int | None = Field(
        default=None,
        ge=1,
        le=MAX_AGENT_OUTPUT_TOKENS,
    )


class AgentConfig(BaseModel):
    """Configuration for a custom agent."""

    name: str
    # ``name`` remains the stable filesystem/runtime identifier.  The
    # separately editable display name mirrors upstream DeerFlow's Unicode
    # display-name support without renaming an existing agent directory.
    display_name: str | None = None
    description: str = Field(default="", max_length=MAX_AGENT_DESCRIPTION_LENGTH)
    model: str | None = None
    tool_groups: list[str] | None = None
    # skills controls which skills are discoverable and may be activated by the
    # agent. It does not activate their allowed-tools policies at construction:
    # - None (or omitted): load all enabled skills (default fallback behavior)
    # - [] (explicit empty list): disable all skills
    # - ["skill1", "skill2"]: load only the specified skills
    skills: list[str] | None = None
    # Creator-owned selection, not authorization to publish knowledge to others.
    knowledge_scope: AgentKnowledgeScope | None = None
    # Presentation metadata for the enterprise Digital Employee directory.
    category_id: str | None = None
    tags: list[str] = Field(default_factory=list)
    avatar_id: str | None = None
    model_settings: AgentModelSettings | None = None
    thinking_enabled: bool | None = None
    reasoning_effort: Literal["low", "medium", "high"] | None = None
    # Optional binding to GitHub repositories so this agent can respond to
    # webhook events from the gateway dispatcher. None means "no GitHub
    # integration", which is the case for every existing agent.
    github: GitHubAgentConfig | None = None

    _validate_display_name = field_validator("display_name")(validate_agent_display_name)
    _validate_category_id = field_validator("category_id")(_normalize_category_id)
    _validate_tags = field_validator("tags")(_normalize_agent_tags)


# Fields explicitly managed by the agent-update surfaces (the
# ``update_agent`` harness tool and the HTTP ``PATCH /api/agents/{name}``
# route). Anything else declared on :class:`AgentConfig` — currently
# ``github``, and any future field — is preserved verbatim by
# :func:`preserve_non_managed_fields` so neither surface can silently
# drop hand-authored configuration. ``name`` is included because the
# updaters always re-emit it from the directory name (it must never come
# from the request body).
MANAGED_AGENT_CONFIG_FIELDS: frozenset[str] = frozenset(
    {
        "name",
        "display_name",
        "description",
        "model",
        "tool_groups",
        "skills",
        "category_id",
        "tags",
        "avatar_id",
        "model_settings",
        "thinking_enabled",
        "reasoning_effort",
    }
)


def preserve_non_managed_fields(existing_cfg: AgentConfig) -> dict[str, object]:
    """Return every top-level field on ``existing_cfg`` not in :data:`MANAGED_AGENT_CONFIG_FIELDS`.

    Used by the two surfaces that rewrite a custom agent's ``config.yaml``
    (the ``update_agent`` harness tool and the HTTP ``PATCH /api/agents/{name}``
    route) to carry forward any hand-authored field — currently ``github``,
    and any field added to :class:`AgentConfig` in the future — that the
    update API does not expose as an argument. Without this, operators who
    hand-author a ``github:`` block on a custom agent would silently lose
    it the next time the agent or a UI editor touched ``description`` /
    ``model`` / ``tool_groups`` / ``skills``.

    ``exclude_unset=True`` is recursive in Pydantic v2, so a sub-field the
    user did not write (and that defaulted to a Pydantic default) is not
    materialized into the dict — the file round-trips visually intact.
    """
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

    from deerflow.persistence.agents.publications import TeamPublicationStore

    publications = TeamPublicationStore(paths)
    if publications.has_record(name):
        return publications.read(name).directory

    legacy_path = paths.agent_dir(name)
    if legacy_path.exists() and (legacy_path / "config.yaml").exists():
        return legacy_path

    return user_path


def is_personal_agent(name: str, *, user_id: str | None = None) -> bool:
    """Return whether ``name`` has a real config owned by the current user.

    Directory existence is insufficient because per-agent conversation memory
    may create a user directory while the executable agent still resolves from
    the shared team or legacy layout. Mutation gates must require the personal
    ``config.yaml`` so a shared chat cannot silently create a same-name shadow.
    """
    validated_name = validate_agent_name(name)
    if not validated_name:
        return False
    from deerflow.config.app_config import get_app_config
    from deerflow.persistence.agents import get_agent_store

    try:
        database_backed = get_app_config().agent_storage.backend == "db"
    except Exception:
        database_backed = False
    if database_backed:
        return get_agent_store().exists(validated_name, user_id=user_id)
    paths = get_paths()
    effective_user = user_id or get_effective_user_id()
    return (paths.user_agent_dir(effective_user, validated_name) / "config.yaml").is_file()


AgentSource = Literal["personal", "team", "legacy"]


def validate_agent_source(source: Any) -> AgentSource | None:
    if source is None:
        return None
    if not isinstance(source, str) or source not in ("personal", "team", "legacy"):
        raise ValueError("Invalid agent source")
    return source


def shared_agent_source_dir(name: str, source: AgentSource) -> Path:
    """Resolve ONE shared namespace. Explicit selection never falls back."""
    name = validate_agent_name(name)
    source = validate_agent_source(source)
    if not name or source not in ("team", "legacy"):
        raise ValueError("Expected a named shared agent source")
    paths = get_paths()
    if source == "team":
        from deerflow.persistence.agents.publications import TeamPublicationStore

        return TeamPublicationStore(paths).read(name).directory
    directory = paths.agent_dir(name)
    if not (directory / "config.yaml").is_file():
        raise FileNotFoundError("Selected shared agent is no longer available")
    return directory


def load_agent_config(name: str | None, *, user_id: str | None = None, source: AgentSource | None = None) -> AgentConfig | None:
    """Load the custom or default agent's config from its directory.

    Reads from the per-user layout first; falls back to the legacy shared layout
    for installations that have not yet been migrated.

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

    source = validate_agent_source(source)
    if name is None:
        return None

    name = validate_agent_name(name)
    from deerflow.persistence.agents import get_agent_store, parse_agent_config

    if source in ("team", "legacy"):
        directory = shared_agent_source_dir(name, source)
        with (directory / "config.yaml").open(encoding="utf-8") as stream:
            document = yaml.safe_load(stream) or {}
        return parse_agent_config({**document, "name": name}, name)
    if source == "personal":
        return get_agent_store().get(name, user_id=user_id, allow_shared=False)
    return get_agent_store().get(name, user_id=user_id)


def load_agent_soul(agent_name: str | None, *, user_id: str | None = None, source: AgentSource | None = None) -> str | None:
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
    source = validate_agent_source(source)
    agent_name = validate_agent_name(agent_name)
    if not agent_name:
        agent_dir = get_paths().base_dir
        soul_path = agent_dir / SOUL_FILENAME
        if not soul_path.exists():
            return None
        content = soul_path.read_text(encoding="utf-8").strip()
        return content or None

    from deerflow.persistence.agents import get_agent_store

    if source in ("team", "legacy"):
        soul = shared_agent_source_dir(agent_name, source) / SOUL_FILENAME
        return (soul.read_text(encoding="utf-8").strip() or None) if soul.is_file() else None
    if source == "personal":
        return get_agent_store().get_soul(agent_name, user_id=user_id, allow_shared=False)
    return get_agent_store().get_soul(agent_name, user_id=user_id)


def list_custom_agents(*, user_id: str | None = None) -> list[AgentConfig]:
    """Scan the agents directory and return all valid custom agents.

    Returns the union of agents in the per-user layout and the legacy shared
    layout, so that pre-migration installations remain visible until they are
    migrated. Per-user entries shadow legacy entries with the same name.

    Args:
        user_id: Owner whose agents to list. Defaults to the effective user
            from the current request context.

    Returns:
        List of AgentConfig for each valid agent directory found.
    """
    from deerflow.persistence.agents import get_agent_store

    return get_agent_store().list(user_id=user_id)


def list_team_agents() -> list[AgentConfig]:
    """List enterprise team agents plus legacy shared agents.

    A current team agent shadows a legacy agent with the same name. Personal
    agents deliberately do not appear here: the agent square is a shared
    catalog, not another view of a user's private workspace.
    """
    paths = get_paths()
    seen: set[str] = set()
    agents: list[AgentConfig] = []

    for root in (paths.team_agents_dir, paths.agents_dir):
        if not root.exists():
            continue
        for entry in sorted(root.iterdir()):
            if not entry.is_dir() or entry.name in seen:
                continue
            if root == paths.team_agents_dir:
                from deerflow.persistence.agents.publications import TeamPublicationStore

                publications = TeamPublicationStore(paths)
                try:
                    if not publications.has_record(entry.name):
                        continue
                    # A withdrawn/corrupt team record must not reveal a legacy
                    # namesake. Keep it shadowed even if this read fails.
                    seen.add(entry.name)
                    data = publications.read(entry.name).config
                    known_fields = set(AgentConfig.model_fields.keys())
                    agents.append(AgentConfig(**{key: value for key, value in data.items() if key in known_fields}))
                except Exception as exc:
                    logger.warning("Skipping team agent '%s': %s", entry.name, exc)
                continue
            config_file = entry / "config.yaml"
            if not config_file.exists():
                continue
            try:
                with open(config_file, encoding="utf-8") as file:
                    data: dict[str, Any] = yaml.safe_load(file) or {}
                data.setdefault("name", entry.name)
                known_fields = set(AgentConfig.model_fields.keys())
                agents.append(AgentConfig(**{key: value for key, value in data.items() if key in known_fields}))
                seen.add(entry.name)
            except Exception as exc:
                logger.warning("Skipping team agent '%s': %s", entry.name, exc)

    agents.sort(key=lambda agent: agent.name)
    return agents
