from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from typing import Any

from deerflow.config.paths import get_paths


def _as_non_empty_str(value: Any) -> str | None:
    if isinstance(value, str):
        stripped = value.strip()
        if stripped:
            return stripped
    return None


def _mapping_user_id(mapping: Mapping[str, Any] | None) -> str | None:
    if not isinstance(mapping, Mapping):
        return None
    return _as_non_empty_str(mapping.get("user_id")) or _as_non_empty_str(mapping.get("x_df_user"))


def _mapping_thread_id(mapping: Mapping[str, Any] | None) -> str | None:
    if not isinstance(mapping, Mapping):
        return None
    return _as_non_empty_str(mapping.get("thread_id"))


def _langgraph_config() -> Mapping[str, Any]:
    try:
        from langgraph.config import get_config

        cfg = get_config()
        return cfg if isinstance(cfg, Mapping) else {}
    except Exception:
        return {}


def resolve_thread_owner(thread_id: str) -> str | None:
    try:
        conn = sqlite3.connect((get_paths().base_dir / "deerflow.db").as_posix())
        try:
            cur = conn.cursor()
            cur.execute("SELECT owner_user_id FROM threads WHERE thread_id = ?", (thread_id,))
            row = cur.fetchone()
            return _as_non_empty_str(row[0]) if row else None
        finally:
            conn.close()
    except Exception:
        return None


def resolve_user_id(
    context: Mapping[str, Any] | None = None,
    *,
    thread_id: str | None = None,
    config: Mapping[str, Any] | None = None,
) -> str | None:
    user_id = _mapping_user_id(context)
    if user_id:
        return user_id

    cfg = config or _langgraph_config()
    cfg_context = cfg.get("context", {}) if isinstance(cfg, Mapping) else {}
    cfg_configurable = cfg.get("configurable", {}) if isinstance(cfg, Mapping) else {}

    user_id = _mapping_user_id(cfg_context) or _mapping_user_id(cfg_configurable)
    if user_id:
        return user_id

    resolved_thread_id = (
        _as_non_empty_str(thread_id)
        or _mapping_thread_id(context)
        or _mapping_thread_id(cfg_context)
        or _mapping_thread_id(cfg_configurable)
    )
    if not resolved_thread_id:
        return None

    return resolve_thread_owner(resolved_thread_id)
