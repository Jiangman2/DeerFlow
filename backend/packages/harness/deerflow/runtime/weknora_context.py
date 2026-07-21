"""Run-scoped WeKnora authentication context."""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Any, Final

_current_weknora_access_token: Final[ContextVar[str | None]] = ContextVar("deerflow_weknora_access_token", default=None)
WEKNORA_TOOL_NAMES: Final[frozenset[str]] = frozenset({"weknora_search", "weknora_ask"})


def normalize_weknora_scope_ids(value: Any) -> list[str]:
    """Return unique, non-empty knowledge-base IDs from an explicit run scope."""
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(item.strip() for item in value if isinstance(item, str) and item.strip()))


def gate_weknora_tools_for_scope(raw_tools, filtered_tools, *, selected_ids: object):
    """Expose WeKnora tools only when this run explicitly selected a KB scope."""
    if not normalize_weknora_scope_ids(selected_ids):
        return [tool for tool in filtered_tools if getattr(tool, "name", None) not in WEKNORA_TOOL_NAMES]

    existing_names = {getattr(tool, "name", None) for tool in filtered_tools}
    restored = [tool for tool in raw_tools if getattr(tool, "name", None) in WEKNORA_TOOL_NAMES and getattr(tool, "name", None) not in existing_names]
    return [*filtered_tools, *restored]


def set_current_weknora_access_token(token: str) -> Token[str | None]:
    return _current_weknora_access_token.set(token)


def reset_current_weknora_access_token(token: Token[str | None]) -> None:
    _current_weknora_access_token.reset(token)


def get_current_weknora_access_token() -> str | None:
    token = _current_weknora_access_token.get()
    return token.strip() if isinstance(token, str) and token.strip() else None
