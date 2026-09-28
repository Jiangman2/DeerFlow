"""Repair user-visible WeKnora citations from current-turn tool metadata."""

from __future__ import annotations

import json
from typing import Any, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.runtime import Runtime

from deerflow.community.weknora.citations import extract_canonical_weknora_references, normalize_weknora_citations
from deerflow.runtime.weknora_context import WEKNORA_CITATION_TOOL_NAMES


def _tool_content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") in {"text", "output_text"}:
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "".join(parts)


def _references_from_tool_message(message: ToolMessage) -> list[dict[str, Any]]:
    if message.name == "task":
        return extract_canonical_weknora_references(_tool_content_text(message.content))
    if message.name not in WEKNORA_CITATION_TOOL_NAMES:
        return []
    try:
        payload = json.loads(_tool_content_text(message.content))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(payload, dict):
        return []

    raw = payload.get("results") if message.name == "weknora_search" else payload.get("references")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def _answer_images_from_tool_message(message: ToolMessage) -> list[dict[str, str]]:
    if message.name not in WEKNORA_CITATION_TOOL_NAMES:
        return []
    try:
        payload = json.loads(_tool_content_text(message.content))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(payload, dict):
        return []

    raw_images = payload.get("answer_images")
    if not isinstance(raw_images, list):
        raw_images = []

    # A direct WeKnora answer explicitly selects its answer_images. Search
    # results do not have that top-level field, so restore an image only when
    # the current search result contains one unique authenticated resource.
    # Multiple candidates remain model-visible but are never guessed here.
    if message.name == "weknora_search" and not raw_images:
        candidates: dict[str, dict[str, Any]] = {}
        results = payload.get("results")
        if isinstance(results, list):
            for result in results:
                if not isinstance(result, dict):
                    continue
                resource_images = result.get("resource_images")
                if not isinstance(resource_images, list):
                    continue
                for image in resource_images:
                    if not isinstance(image, dict):
                        continue
                    url = image.get("url")
                    if isinstance(url, str):
                        candidates.setdefault(url, image)
        if len(candidates) == 1:
            raw_images = list(candidates.values())

    images: list[dict[str, str]] = []
    for item in raw_images:
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        markdown = item.get("markdown")
        if isinstance(url, str) and url.startswith("/api/weknora/knowledge-bases/") and "/files?file_path=" in url and isinstance(markdown, str) and markdown.endswith(f"({url})"):
            images.append({"url": url, "markdown": markdown})
    return images


def _current_turn_metadata(
    messages: list[Any],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    latest_user_index = -1
    for index, message in enumerate(messages):
        if not isinstance(message, HumanMessage):
            continue
        if (message.additional_kwargs or {}).get("hide_from_ui"):
            continue
        latest_user_index = index
    if latest_user_index < 0:
        return [], []

    references: list[dict[str, Any]] = []
    answer_images: list[dict[str, str]] = []
    for message in messages[latest_user_index + 1 :]:
        if isinstance(message, ToolMessage):
            references.extend(_references_from_tool_message(message))
            answer_images.extend(_answer_images_from_tool_message(message))
    return references, answer_images


def _append_missing_images(
    content: Any,
    answer_images: list[dict[str, str]],
) -> Any:
    if not answer_images:
        return content

    text = _tool_content_text(content)
    missing = [image["markdown"] for image in answer_images if image["url"] not in text]
    if not missing:
        return content
    suffix = "\n\n" + "\n\n".join(missing)

    if isinstance(content, str):
        return content.rstrip() + suffix
    if not isinstance(content, list):
        return content

    blocks = list(content)
    for index in range(len(blocks) - 1, -1, -1):
        block = blocks[index]
        if isinstance(block, str):
            blocks[index] = block.rstrip() + suffix
            return blocks
        if isinstance(block, dict) and block.get("type") in {"text", "output_text"}:
            block_text = block.get("text")
            if isinstance(block_text, str):
                blocks[index] = {**block, "text": block_text.rstrip() + suffix}
                return blocks
    blocks.append({"type": "text", "text": suffix.lstrip()})
    return blocks


def _normalize_content(content: Any, references: list[dict[str, Any]]) -> Any:
    if isinstance(content, str):
        return normalize_weknora_citations(content, references)
    if not isinstance(content, list):
        return content

    changed = False
    blocks: list[Any] = []
    for block in content:
        if isinstance(block, str):
            normalized = normalize_weknora_citations(block, references)
            changed = changed or normalized != block
            blocks.append(normalized)
            continue
        if isinstance(block, dict) and block.get("type") in {"text", "output_text"}:
            text = block.get("text")
            if isinstance(text, str):
                normalized = normalize_weknora_citations(text, references)
                changed = changed or normalized != text
                blocks.append({**block, "text": normalized})
                continue
        blocks.append(block)
    return blocks if changed else content


class WeKnoraCitationMiddleware(AgentMiddleware[AgentState]):
    """Canonicalize citations in terminal AI messages using current-turn refs."""

    @staticmethod
    def _apply(state: AgentState) -> dict[str, Any] | None:
        messages = list(state.get("messages") or [])
        if not messages or not isinstance(messages[-1], AIMessage):
            return None

        final = messages[-1]
        if final.tool_calls or getattr(final, "invalid_tool_calls", None):
            return None

        references, answer_images = _current_turn_metadata(messages)
        if not references and not answer_images:
            return None
        normalized = _normalize_content(final.content, references)
        normalized = _append_missing_images(normalized, answer_images)
        if normalized == final.content:
            return None
        return {"messages": [final.model_copy(update={"content": normalized})]}

    @override
    def after_model(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        return self._apply(state)

    @override
    async def aafter_model(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        return self._apply(state)
