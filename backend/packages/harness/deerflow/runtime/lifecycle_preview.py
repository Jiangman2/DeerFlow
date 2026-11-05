"""Bound the redundant run.end diagnostic snapshot before serialization.

This is not a checkpoint or model-context transform. Real message events and
the graph state retain their existing persistence paths.
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import BaseMessage

MAX_NODES = 512
MAX_CHARS = 32_768
MAX_VALUE_CHARS = 4096
MAX_DEPTH = 8
_OMITTED = "[diagnostic preview omitted]"


def lifecycle_preview(value: Any) -> tuple[Any, bool]:
    """Copy a bounded JSON preview without dumping/repr-ing the original state."""
    nodes_left = MAX_NODES
    chars_left = MAX_CHARS
    truncated = False
    ancestors: set[int] = set()

    def text(raw: str, maximum: int = MAX_VALUE_CHARS) -> str:
        nonlocal chars_left, truncated
        keep = min(len(raw), maximum, chars_left)
        chars_left -= keep
        if keep < len(raw):
            truncated = True
            return raw[:keep] + _OMITTED
        return raw

    def visit(item: Any, depth: int) -> Any:
        nonlocal nodes_left, truncated
        if nodes_left <= 0 or depth > MAX_DEPTH:
            truncated = True
            return _OMITTED
        nodes_left -= 1
        if isinstance(item, str):
            return text(item)
        if item is None or isinstance(item, (bool, float)):
            return item
        if isinstance(item, int):
            if item.bit_length() <= 256:
                return item
            truncated = True
            return _OMITTED
        if isinstance(item, BaseMessage):
            # model_dump()/str(message) would copy the complete embedded image
            # or tool payload before any limit could be applied.
            truncated = True
            item = vars(item)
        if not isinstance(item, (dict, list, tuple)) or id(item) in ancestors:
            truncated = True
            return _OMITTED
        ancestors.add(id(item))
        try:
            if isinstance(item, dict):
                result = {}
                for key, child in item.items():
                    if nodes_left <= 0 or chars_left <= 0:
                        truncated = True
                        break
                    if not isinstance(key, str):
                        truncated = True
                        continue
                    result[text(key, 128)] = visit(child, depth + 1)
                return result
            result = []
            for child in item:
                if nodes_left <= 0 or chars_left <= 0:
                    truncated = True
                    break
                result.append(visit(child, depth + 1))
            return result
        finally:
            ancestors.remove(id(item))

    return_value = visit(value, 0)
    return return_value, truncated
