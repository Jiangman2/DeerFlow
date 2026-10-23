"""Display-only partial history at the explicit user-interrupt boundary.

No checkpoint mutation or per-token persistence. Complete model callbacks keep
their original path. Partial messages cannot become executable tool turns.
"""

from __future__ import annotations

import asyncio
import logging
import re
import threading
from collections.abc import Mapping
from typing import Any

from langchain_core.messages import AIMessage, AIMessageChunk

logger = logging.getLogger(__name__)
MAX_CHARS = 262144
MAX_PARTS = 8192
_THINK = re.compile(
    r"<(think|analysis|reasoning)(?:\s[^>]*)?>.*?(?:</\1\s*>|$)",
    re.IGNORECASE | re.DOTALL,
)


def visible_content(content: Any) -> str:
    """Only public text blocks; never arbitrary text keys in reasoning/tools."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "".join(
        block if isinstance(block, str) else block["text"]
        for block in content
        if isinstance(block, str)
        or (
            isinstance(block, Mapping)
            and block.get("type") in ("text", "output_text")
            and isinstance(block.get("text"), str)
        )
    )


def public_message(
    text: str, message_id: str, *, truncated: bool = False
) -> AIMessage | None:
    text = _THINK.sub("", text)
    # A cancelled chunk can end halfway through the opening hidden tag.
    tail = text.rfind("<")
    if tail >= 0 and (
        any(
            tag.startswith(text[tail:].lower())
            for tag in ("<think>", "<analysis>", "<reasoning>")
        )
        or re.match(r"<(think|analysis|reasoning)\b[^>]*$", text[tail:], re.IGNORECASE)
    ):
        text = text[:tail]
    if not text.strip():
        return None
    marker = {"deerflow_interrupted_partial": True}
    if truncated:
        marker["deerflow_partial_truncated"] = True
    return AIMessage(content=text, id=message_id, additional_kwargs=marker)


class InterruptedCapture:
    """Bounded run-private cache shared by callback and graph-consumer threads."""

    def __init__(self):
        self.lock = threading.Lock()
        self.callback: tuple[str, AIMessage] | None = None
        self.message_id: str | None = None
        self.parts: list[str] = []
        self.size = 0
        self.truncated = False
        self.completed_ids: set[str] = set()
        self.persisted_ids: set[str] = set()
        self.public_runs: set[str] = set()

    def clear_stream(self):
        self.message_id = None
        self.parts = []
        self.size = 0
        self.truncated = False


class InterruptedJournalMethods:
    """Narrow mixin for the hash-pinned production RunJournal."""

    _interrupted_history: InterruptedCapture

    @staticmethod
    def _public_caller(tags: Any, metadata: Any) -> bool:
        if any(
            isinstance(tag, str) and tag.startswith(("subagent:", "middleware:"))
            for tag in (tags or [])
        ):
            return False
        source = (metadata or {}).get("lc_source")
        return source in (None, "", "lead_agent")

    def _start_interrupted_capture(
        self, caller: str, rid: str, tags: Any, metadata: Any
    ) -> None:
        if caller == "lead_agent" and self._public_caller(tags, metadata):
            cache = self._interrupted_history
            with cache.lock:
                cache.public_runs.add(rid)
                cache.callback = None
                cache.clear_stream()

    def _capture_interrupted_error(
        self, error: BaseException, rid: str, response: Any, kwargs: dict
    ) -> None:
        caller = self._llm_callers_by_run_id.get(rid) or self._identify_caller(
            kwargs.get("tags"), kwargs.get("metadata")
        )
        if caller != "lead_agent" or not self._public_caller(
            kwargs.get("tags"), kwargs.get("metadata")
        ):
            return
        cache = self._interrupted_history
        with cache.lock:
            if rid not in cache.public_runs:
                return
            if not isinstance(error, asyncio.CancelledError):
                cache.callback = None
                cache.clear_stream()
                return
            if rid in self._counted_message_llm_run_ids:
                return
            for group in getattr(response, "generations", []) or []:
                for generation in group:
                    original = getattr(generation, "message", None)
                    if not isinstance(
                        original, AIMessage
                    ) or original.additional_kwargs.get("hide_from_ui"):
                        continue
                    text = visible_content(original.content)
                    identity = (
                        original.id
                        or cache.message_id
                        or f"df-interrupted:{self.run_id}:{rid}"
                    )
                    message = public_message(
                        text[:MAX_CHARS], identity, truncated=len(text) > MAX_CHARS
                    )
                    if message is not None and identity not in cache.completed_ids:
                        cache.callback = (rid, message)
                        return

    def _complete_interrupted_capture(self, rid: str, messages: list) -> None:
        cache = self._interrupted_history
        with cache.lock:
            cache.public_runs.discard(rid)
            if cache.callback is not None and cache.callback[0] == rid:
                cache.callback = None
            for message in messages:
                if isinstance(message, AIMessage) and message.id:
                    cache.completed_ids.add(message.id)
                    if message.id == cache.message_id:
                        cache.clear_stream()

    def observe_stream_message(self, chunk: Any) -> None:
        """Fallback at the existing graph-publication path, with no new callback.

        Required for cancellation paths whose provider error result is empty.
        Never serializes, writes SQL, publishes SSE, or copies tool arguments.
        """
        if not isinstance(chunk, (tuple, list)) or len(chunk) != 2:
            return
        original, metadata = chunk
        if (
            not isinstance(original, AIMessageChunk)
            or not original.id
            or not isinstance(metadata, Mapping)
        ):
            return
        if original.additional_kwargs.get("hide_from_ui"):
            return
        if self._identify_caller(
            metadata.get("tags"), dict(metadata)
        ) != "lead_agent" or not self._public_caller(metadata.get("tags"), metadata):
            return
        # Nested subgraphs are not the lead reply. Internal model calls bind
        # lc_source; this additional namespace check is deliberately restrictive.
        if metadata.get("langgraph_checkpoint_ns") and "|" in str(
            metadata["langgraph_checkpoint_ns"]
        ):
            return
        text = visible_content(original.content)
        if not text:
            return
        cache = self._interrupted_history
        with cache.lock:
            if original.id in cache.completed_ids:
                return
            if original.id != cache.message_id:
                cache.clear_stream()
                cache.message_id = original.id
            if cache.size >= MAX_CHARS:
                cache.truncated = True
                return
            if len(cache.parts) >= MAX_PARTS:
                cache.parts = [
                    "".join(cache.parts)
                ]  # bound object count, not visible text
            part = text[: MAX_CHARS - cache.size]
            cache.parts.append(part)
            cache.size += len(part)
            cache.truncated |= len(part) != len(text)

    def prepare_interrupted_message(
        self, *, status: str, action: str, requested: bool
    ) -> AIMessage | None:
        cache = self._interrupted_history
        with cache.lock:
            message = None
            if status == "interrupted" and action == "interrupt" and requested is True:
                if cache.callback is not None:
                    message = cache.callback[1]
                elif cache.message_id:
                    message = public_message(
                        "".join(cache.parts),
                        cache.message_id,
                        truncated=cache.truncated,
                    )
            cache.callback = None
            cache.clear_stream()
            if (
                message is not None
                and message.id not in cache.completed_ids
                and message.id not in cache.persisted_ids
            ):
                return message
            return None

    def persist_interrupted_message(self, message: AIMessage) -> None:
        cache = self._interrupted_history
        with cache.lock:
            if (
                not message.id
                or message.id in cache.persisted_ids
                or message.id in cache.completed_ids
            ):
                return
            cache.persisted_ids.add(message.id)
        self._put(
            event_type="llm.ai.response",
            category="message",
            content=message.model_dump(),
            metadata={"caller": "lead_agent", "interrupted_partial": True},
        )
        self._record_message_summary(message, caller="lead_agent")
        # Do not set provider-complete _last_lead_ai_message or guess token usage.


async def persist_interrupted_history(
    *, journal: Any, record: Any, checkpointer: Any, thread_id: str
) -> bool:
    """Called after graph-child drainage, before journal flush and stream end."""
    if journal is None:
        return False
    message = journal.prepare_interrupted_message(
        status=record.status.value,
        action=record.abort_action,
        requested=record.abort_event.is_set()
        and getattr(record, "abort_requested_by_client", False),
    )
    if message is None:
        return False
    try:
        from deerflow.agents.middlewares.artifact_delivery_middleware import (
            build_artifact_delivery_fallback,
            has_invalid_artifact_claim,
        )

        state = {}
        if checkpointer is not None:
            try:
                snapshot = await checkpointer.aget_tuple(
                    {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
                )
                checkpoint = (
                    getattr(snapshot, "checkpoint", {}) if snapshot is not None else {}
                )
                values = (
                    checkpoint.get("channel_values", {})
                    if isinstance(checkpoint, dict)
                    else {}
                )
                state = values if isinstance(values, dict) else {}
            except Exception:
                # Empty publication authority fails closed for all file claims.
                logger.warning(
                    "Partial-history checkpoint unavailable; validating without publication authority"
                )
        if await asyncio.to_thread(has_invalid_artifact_claim, message, state):
            message = build_artifact_delivery_fallback(
                message,
                reason="Interrupted before artifact delivery validation completed",
            )
        journal.persist_interrupted_message(message)
        logger.info(
            "Interrupted assistant display history prepared for run %s", journal.run_id
        )
        return True
    except Exception:
        logger.warning(
            "Failed to prepare interrupted display history for run %s",
            journal.run_id,
            exc_info=True,
        )
        return False
