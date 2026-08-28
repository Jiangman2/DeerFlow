"""Run-scoped WeKnora authentication context."""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Any, Final

_current_weknora_access_token: Final[ContextVar[str | None]] = ContextVar("deerflow_weknora_access_token", default=None)
_current_weknora_scope: Final[ContextVar[dict[str, Any] | None]] = ContextVar(
    "deerflow_weknora_scope",
    default=None,
)
WEKNORA_TOOL_NAMES: Final[frozenset[str]] = frozenset(
    {
        "weknora_list_knowledge_bases",
        "weknora_get_knowledge_base",
        "weknora_list_documents",
        "weknora_get_document",
        "weknora_list_document_chunks",
        "weknora_read_document",
        "weknora_search",
        "weknora_ask",
        "weknora_list_agents",
        "weknora_invoke_agent",
    }
)
WEKNORA_CITATION_TOOL_NAMES: Final[frozenset[str]] = frozenset(
    {"weknora_search", "weknora_ask", "weknora_invoke_agent"}
)


def normalize_weknora_scope_ids(value: Any) -> list[str]:
    """Return unique, non-empty knowledge-base IDs from an explicit run scope."""
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(item.strip() for item in value if isinstance(item, str) and item.strip()))


def normalize_weknora_scope_items(value: Any) -> list[dict[str, str]]:
    """Return safe native mention-shaped KB/file/tag scope rows."""

    if not isinstance(value, list):
        return []
    rows: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for value_item in value:
        if not isinstance(value_item, dict):
            continue
        item_type = value_item.get("type")
        item_id = value_item.get("id")
        name = value_item.get("name")
        if item_type not in {"kb", "file", "tag"}:
            continue
        if not isinstance(item_id, str) or not item_id.strip():
            continue
        if not isinstance(name, str) or not name.strip():
            continue
        kb_id = value_item.get("kb_id")
        if item_type != "kb" and (not isinstance(kb_id, str) or not kb_id.strip()):
            continue
        key = (item_type, item_id.strip())
        if key in seen:
            continue
        seen.add(key)
        row = {
            "id": item_id.strip(),
            "name": name.strip(),
            "type": item_type,
        }
        for field in ("kb_id", "kb_name", "kb_type"):
            field_value = value_item.get(field)
            if isinstance(field_value, str) and field_value.strip():
                row[field] = field_value.strip()
        rows.append(row)
    return rows


def normalize_weknora_tag_scopes(value: Any) -> list[dict[str, Any]]:
    """Return deduplicated tag IDs grouped under their owning knowledge base."""

    if not isinstance(value, list):
        return []
    merged: dict[str, list[str]] = {}
    for item in value:
        if not isinstance(item, dict):
            continue
        kb_id = item.get("knowledge_base_id")
        if not isinstance(kb_id, str) or not kb_id.strip():
            continue
        tag_ids = normalize_weknora_scope_ids(item.get("tag_ids"))
        if not tag_ids:
            continue
        values = merged.setdefault(kb_id.strip(), [])
        values.extend(tag_id for tag_id in tag_ids if tag_id not in values)
    return [
        {"knowledge_base_id": kb_id, "tag_ids": tag_ids}
        for kb_id, tag_ids in merged.items()
    ]


def normalize_weknora_run_scope(context: Any) -> dict[str, Any]:
    """Normalize a browser-selected KB/file/tag scope from one run context."""

    if not isinstance(context, dict):
        return {}
    kb_ids = normalize_weknora_scope_ids(context.get("weknora_knowledge_base_ids"))
    knowledge_ids = normalize_weknora_scope_ids(context.get("weknora_knowledge_ids"))
    tag_scopes = normalize_weknora_tag_scopes(context.get("weknora_tag_scopes"))
    mentioned_items = normalize_weknora_scope_items(context.get("weknora_scope_items"))
    if not mentioned_items:
        names_by_id = {
            str(item.get("id")): str(item.get("name"))
            for item in context.get("weknora_knowledge_bases", [])
            if isinstance(item, dict) and item.get("id") and item.get("name")
        }
        mentioned_items = [
            {
                "id": kb_id,
                "name": names_by_id.get(kb_id, kb_id),
                "type": "kb",
            }
            for kb_id in kb_ids
        ]

    if not kb_ids and not knowledge_ids and not tag_scopes and not mentioned_items:
        return {}
    return {
        "knowledge_base_ids": kb_ids,
        "knowledge_ids": knowledge_ids,
        "tag_scopes": tag_scopes,
        "mentioned_items": mentioned_items,
    }


def has_weknora_scope(context: Any) -> bool:
    return bool(normalize_weknora_run_scope(context))


def gate_weknora_tools_for_scope(raw_tools, filtered_tools, *, scope_context: object):
    """Expose WeKnora tools only when this run explicitly selected a scope."""
    if not has_weknora_scope(scope_context):
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


def set_current_weknora_scope(context: Any) -> Token[dict[str, Any] | None]:
    return _current_weknora_scope.set(normalize_weknora_run_scope(context) or None)


def reset_current_weknora_scope(token: Token[dict[str, Any] | None]) -> None:
    _current_weknora_scope.reset(token)


def get_current_weknora_scope() -> dict[str, Any] | None:
    scope = _current_weknora_scope.get()
    return dict(scope) if isinstance(scope, dict) and scope else None
