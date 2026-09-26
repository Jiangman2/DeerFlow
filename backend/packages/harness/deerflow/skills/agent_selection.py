"""Creator-scoped capability selection for custom Agents.

An Agent whitelist is a selection, not a permission grant. Resolve against the
same user catalog as ordinary chat; never fall back to the publisher/global
custom catalog when a selected name is unavailable.
"""

from __future__ import annotations

from deerflow.skills.storage import get_or_new_user_skill_storage
from deerflow.skills.types import Skill

MAX_AGENT_SKILLS = 128


def selectable_agent_skills(user_id: str, *, app_config=None) -> list[Skill]:
    storage = get_or_new_user_skill_storage(user_id, app_config=app_config)
    # Preserve the storage's name-shadow resolution and live enabled state.
    return storage.load_skills(enabled_only=True)


def validate_agent_skills(skills: list[str] | None, *, user_id: str, app_config=None) -> list[str] | None:
    """Preserve official None/all and []/none semantics; reject invalid binds."""
    if skills is None:
        return None
    if not isinstance(skills, list):
        raise ValueError("Agent skills must be a list of canonical names or null")
    if len(skills) > MAX_AGENT_SKILLS:
        raise ValueError(f"An agent may select at most {MAX_AGENT_SKILLS} skills")
    selected: list[str] = []
    for name in skills:
        if not isinstance(name, str) or not name.strip() or name != name.strip():
            raise ValueError("Agent skill names must be non-empty canonical names")
        if name not in selected:
            selected.append(name)
    if not selected:
        return []
    available = {skill.name for skill in selectable_agent_skills(user_id, app_config=app_config)}
    if any(name not in available for name in selected):
        # Do not reveal whether a name exists in another user's private store.
        raise ValueError("Selected skills are unavailable or disabled for the current user; refresh the skill selection")
    return selected
