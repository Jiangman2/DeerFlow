"""update_agent tool — propose owner-reviewed changes to private SOUL/config.

Bound to the lead agent only when ``runtime.context['agent_name']`` identifies
a personal custom agent owned by the current user. The default and team-shared
agents do not see this tool, and the bootstrap flow continues to use
``setup_agent`` for the initial creation handshake.

The tool stores an owner-only proposal. The authenticated confirmation route
revalidates resources and commits through the configured file/SQL draft CAS;
a model response cannot confirm, apply or publish its own proposal.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from langchain_core.messages import ToolMessage
from langchain_core.tools import tool
from langgraph.types import Command
from pydantic import BeforeValidator

from deerflow.config.agents_config import (
    MAX_AGENT_DESCRIPTION_LENGTH,
    is_personal_agent,
    validate_agent_name,
)
from deerflow.config.app_config import get_app_config
from deerflow.config.paths import get_paths
from deerflow.persistence.agents import get_agent_store
from deerflow.persistence.agents.base import parse_agent_config
from deerflow.persistence.agents.proposals import EmployeeProposalStore
from deerflow.runtime.user_context import resolve_runtime_user_id
from deerflow.skills.agent_selection import validate_agent_skills
from deerflow.tools.types import Runtime

logger = logging.getLogger(__name__)

_NULLISH_STRINGS = frozenset({"null", "none", "undefined"})

# Channels whose inbound messages come from untrusted external commenters
# (anyone on a GitHub repo, etc.). The lead-agent factory already drops
# this tool for runs on these channels (see ``_WEBHOOK_CHANNELS`` in
# ``deerflow.agents.lead_agent.agent``); this set is the in-tool mirror
# so a custom factory that re-attaches ``update_agent`` cannot silently
# expose self-mutation over a webhook.
_UNTRUSTED_CHANNELS: frozenset[str] = frozenset({"github"})


def _is_nullish_string(value: object) -> bool:
    return isinstance(value, str) and value.strip().lower() in _NULLISH_STRINGS


def _normalize_nullish_string(value: object) -> object:
    return None if _is_nullish_string(value) else value


OptionalText = Annotated[str | None, BeforeValidator(_normalize_nullish_string)]
OptionalStringList = Annotated[list[str] | None, BeforeValidator(_normalize_nullish_string)]


@tool(parse_docstring=True)
def update_agent(
    runtime: Runtime,
    soul: OptionalText = None,
    description: OptionalText = None,
    skills: OptionalStringList = None,
    tool_groups: OptionalStringList = None,
    model: OptionalText = None,
) -> Command:
    """Propose updates to this employee for its creator's explicit review.

    Use this when the user asks to refine the agent's identity, description,
    skill whitelist, tool-group whitelist, or default model. Only the fields
    you explicitly pass are updated; omitted fields keep their existing values.

    Pass ``soul`` as the FULL replacement SOUL.md content — there is no patch
    semantics, so always start from the current SOUL and apply your edits.

    Pass ``skills=[]`` to disable all skills for this agent. Omit ``skills``
    entirely to keep the existing whitelist. Do not pass literal strings like
    ``"null"`` / ``"none"`` / ``"undefined"`` for unchanged fields; omit those
    fields instead.

    Args:
        soul: Optional full replacement SOUL.md content.
        description: Optional new one-line description.
        skills: Optional skill whitelist. ``[]`` = no skills, omit = unchanged.
        tool_groups: Optional tool-group whitelist. ``[]`` = empty, omit = unchanged.
        model: Optional model override (must match a configured model name).

    Returns:
        Command containing a review link. No employee definition is changed
        until the creator confirms in that authenticated page. Always show the
        review link and say the proposal is not yet applied or published.
    """
    tool_call_id = runtime.tool_call_id
    agent_name_raw: str | None = runtime.context.get("agent_name") if runtime.context else None
    channel_name: str | None = runtime.context.get("channel_name") if runtime.context else None

    def _err(message: str) -> Command:
        return Command(update={"messages": [ToolMessage(content=f"Error: {message}", tool_call_id=tool_call_id, status="error")]})

    # Defence in depth — the lead-agent factory already withholds this
    # tool from webhook-channel runs (see ``_WEBHOOK_CHANNELS`` in
    # ``deerflow.agents.lead_agent.agent``). The same channel set is
    # mirrored here so a future code path that re-attaches the tool
    # without going through ``_make_lead_agent`` (custom factories,
    # tests, etc.) does not silently accept untrusted self-mutation
    # requests routed from a webhook.
    if channel_name in _UNTRUSTED_CHANNELS:
        return _err(f"update_agent is disabled on the {channel_name!r} channel. Self-mutation requests must come from an operator-trusted surface (chat UI or the HTTP API), not a webhook fan-out.")

    if soul is None and description is None and skills is None and tool_groups is None and model is None:
        return _err('No fields provided. Pass at least one of: soul, description, skills, tool_groups, model. Omit unchanged fields instead of passing null-like strings such as "null", "none", or "undefined".')

    if soul is not None and not soul.strip():
        return _err("soul content is empty; refusing to update the digital employee with an empty SOUL.md. Omit the soul field if you do not want to change it.")

    if description is not None and len(description) > MAX_AGENT_DESCRIPTION_LENGTH:
        return _err(f"description must be {MAX_AGENT_DESCRIPTION_LENGTH} characters or fewer")

    try:
        agent_name = validate_agent_name(agent_name_raw)
    except ValueError as e:
        return _err(str(e))

    if not agent_name:
        return _err("update_agent is only available inside a custom agent's chat. There is no agent_name in the current runtime context, so there is nothing to update. If you are inside the bootstrap flow, use setup_agent instead.")

    # Resolve the active user so that updates only affect this user's agent.
    # ``resolve_runtime_user_id`` prefers ``runtime.context["user_id"]`` (set by
    # the gateway from the auth-validated request) and falls back to the
    # contextvar, then DEFAULT_USER_ID. This matches setup_agent so a user
    # creating an agent and later refining it always touches the same files,
    # even if the contextvar gets lost across an async/thread boundary
    # (issue #2782 / #2862 class of bugs).
    user_id = resolve_runtime_user_id(runtime)
    if runtime.context and runtime.context.get("agent_source") not in (None, "personal"):
        return _err("Shared agent selection is read-only; it cannot update a same-name personal agent")
    try:
        skills = validate_agent_skills(skills, user_id=user_id)
    except ValueError as error:
        return _err(str(error))

    # Reject an unknown ``model`` *before* touching the filesystem. Otherwise
    # ``_resolve_model_name`` silently falls back to the default at runtime
    # and the user sees confusing repeated warnings on every later turn.
    if model is not None and get_app_config().get_model_config(model) is None:
        return _err(f"Unknown model '{model}'. Pass a model name that exists in config.yaml's models section.")

    paths = get_paths()
    team_dir = paths.team_agent_dir(agent_name)
    legacy_dir = paths.agent_dir(agent_name)
    # Require a personal config.yaml, not bare directory existence. A shared
    # agent chat may create only per-user memory in this directory; treating
    # that as ownership would let load_agent_config resolve the team/legacy
    # snapshot and then silently fork personal config/SOUL files here.
    if not is_personal_agent(agent_name, user_id=user_id):
        if (team_dir / "config.yaml").is_file():
            return _err(f"Agent '{agent_name}' is a read-only published snapshot in the team square and is not scoped to the current user. Create a personal copy before updating it.")
        if (legacy_dir / "config.yaml").is_file():
            return _err(f"Agent '{agent_name}' only exists in the legacy shared layout and is not scoped to a user. Run scripts/migrate_user_isolation.py to move legacy agents into the per-user layout before updating.")
        return _err(f"Agent '{agent_name}' does not exist for the current user. Use setup_agent to create a new agent first.")

    try:
        store = get_agent_store()
        before = store.read_definition(agent_name, user_id=user_id)
        existing_cfg = parse_agent_config(before.config, agent_name)
    except FileNotFoundError:
        return _err(f"Agent '{agent_name}' does not exist for the current user. Use setup_agent to create a new agent first.")
    except ValueError as e:
        return _err(f"Agent '{agent_name}' has an unreadable config: {e}")

    if existing_cfg is None:
        return _err(f"Agent '{agent_name}' could not be loaded.")

    # Recheck ownership after loading so a concurrent unpublish/delete cannot
    # turn the shared-read fallback into a personal write target.
    if not is_personal_agent(agent_name, user_id=user_id):
        return _err(f"Agent '{agent_name}' is no longer a personal agent for the current user. Reload the agent before updating it.")

    updated_fields: list[str] = []

    # Force the on-disk ``name`` to match the directory we are writing into,
    # even if ``existing_cfg.name`` had drifted (e.g. from manual yaml edits).
    config_data: dict[str, Any] = {"name": agent_name}
    new_description = description if description is not None else existing_cfg.description
    config_data["description"] = new_description
    if description is not None and description != existing_cfg.description:
        updated_fields.append("description")

    new_model = model if model is not None else existing_cfg.model
    if new_model is not None:
        config_data["model"] = new_model
    if model is not None and model != existing_cfg.model:
        updated_fields.append("model")

    new_tool_groups = tool_groups if tool_groups is not None else existing_cfg.tool_groups
    if new_tool_groups is not None:
        config_data["tool_groups"] = new_tool_groups
    if tool_groups is not None and tool_groups != existing_cfg.tool_groups:
        updated_fields.append("tool_groups")

    new_skills = skills if skills is not None else existing_cfg.skills
    if new_skills is not None:
        config_data["skills"] = new_skills
    if skills is not None and skills != existing_cfg.skills:
        updated_fields.append("skills")

    config_changed = bool({"description", "model", "tool_groups", "skills"} & set(updated_fields))

    if soul is not None:
        updated_fields.append("soul")

    if config_changed or soul is not None:
        try:
            # Only explicit changes cross the store boundary. Model defaults
            # must never erase fields unknown to this version of the tool.
            proposal = EmployeeProposalStore(paths).create(
                agent_name,
                user_id,
                before,
                {key: config_data[key] for key in updated_fields if key != "soul"},
                soul,
            )
        except Exception as e:
            logger.error(
                "[update_agent] Failed to update agent '%s' (user=%s): %s",
                agent_name,
                user_id,
                e,
                exc_info=True,
            )
            return _err(f"Failed to update agent '{agent_name}': {e}")

    if not updated_fields:
        return Command(update={"messages": [ToolMessage(content=f"No changes applied to agent '{agent_name}'. The provided values matched the existing config.", tool_call_id=tool_call_id)]})

    logger.info("[update_agent] Proposed changes to agent '%s' (user=%s) fields: %s", agent_name, user_id, updated_fields)
    from urllib.parse import quote

    review_url = f"/workspace/agents/manage/{quote(agent_name, safe='')}/proposals/{proposal['id']}"
    return Command(
        update={
            "messages": [
                ToolMessage(
                    content=(
                        f"Employee change proposal created; NOTHING has been applied or published. Proposed fields: {', '.join(updated_fields)}. Ask the creator to [review and confirm the changes]({review_url}). Do not claim the employee has already changed."
                    ),
                    tool_call_id=tool_call_id,
                )
            ]
        }
    )
