"""Run event capture via LangChain callbacks.

RunJournal sits between LangChain's callback mechanism and the pluggable
RunEventStore. It standardizes callback data into RunEvent records and
handles token usage accumulation.

Key design decisions:
- on_llm_new_token is NOT implemented -- only complete messages via on_llm_end
- on_chat_model_start captures structured prompts as llm.human.input and
  extracts the first human message for run.input, because it is more reliable than
  on_chain_start (fires on every node) — messages here are fully structured.
- on_chain_start with parent_run_id=None emits a run.start trace marking root invocation.
- on_llm_end emits llm.ai.response using AIMessage.model_dump()
- Token usage accumulated in memory, written to RunRow on run completion
- Caller identification via tags injection (lead_agent / subagent:{name} / middleware:{name})
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage, AnyMessage, BaseMessage, HumanMessage, ToolMessage
from langgraph.types import Command

from deerflow.agents.human_input import read_human_input_response
from deerflow.runtime.events.catalog import (
    LLM_AI_RESPONSE_EVENT,
    LLM_ERROR_EVENT,
    LLM_HUMAN_INPUT_EVENT,
    LLM_TOOL_RESULT_EVENT,
    MEMORY_CONTEXT_EVENT,
    MIDDLEWARE_EVENT_PATTERN,
    RUN_DELIVERY_EVENT,
    RUN_END_EVENT,
    RUN_ERROR_EVENT,
    RUN_START_EVENT,
)
from deerflow.utils.messages import message_to_text, restore_original_human_message

if TYPE_CHECKING:
    from deerflow.runtime.events.store.base import RunEventStore

logger = logging.getLogger(__name__)

_LEGACY_SUMMARY_MESSAGE_NAME = "summary"
_RECONCILED_TOOL_MESSAGE_NAMES = frozenset({"ask_clarification"})
_PERSISTED_HIDDEN_HUMAN_INPUT_RESPONSE_SOURCES = frozenset({"ask_clarification"})


@dataclass
class _PendingLlmResponse:
    llm_run_id: str
    events: list[dict]
    message_count: int
    last_ai_message: str | None


def _should_persist_human_input_message(message: BaseMessage) -> bool:
    if not isinstance(message, HumanMessage):
        return False
    if message.name == _LEGACY_SUMMARY_MESSAGE_NAME:
        return False
    if message.additional_kwargs.get("hide_from_ui") is not True:
        return True
    response = read_human_input_response(message.additional_kwargs)
    return response is not None and response["source"] in _PERSISTED_HIDDEN_HUMAN_INPUT_RESPONSE_SOURCES


class RunJournal(BaseCallbackHandler):
    """LangChain callback handler that captures events to RunEventStore."""

    # Store handles and scheduled writes belong to the parent run loop. Child
    # executors filter this callback, retaining framework streaming callbacks.
    deerflow_loop_bound = True

    # Async LangChain dispatch must mutate one journal on the event-loop thread,
    # not concurrently in executor threads. Durable I/O remains asynchronous.
    run_inline = True

    def __init__(
        self,
        run_id: str,
        thread_id: str,
        event_store: RunEventStore,
        *,
        track_token_usage: bool = True,
        flush_threshold: int = 20,
        progress_reporter: Callable[[dict], Awaitable[None]] | None = None,
        progress_flush_interval: float = 5.0,
    ):
        super().__init__()
        self.run_id = run_id
        self.thread_id = thread_id
        self._store: RunEventStore | None = event_store
        self._closed = False
        self._track_tokens = track_token_usage
        self._flush_threshold = flush_threshold
        self._progress_reporter = progress_reporter
        self._progress_flush_interval = progress_flush_interval
        try:
            self._owner_loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
        except RuntimeError:
            self._owner_loop = None

        # Write buffer
        self._buffer: list[dict] = []
        self._pending_llm_response: _PendingLlmResponse | None = None
        self._pending_flush_tasks: set[asyncio.Task[None]] = set()
        self._explicit_flush_in_progress = False
        self._pending_progress_task: asyncio.Task[None] | None = None
        self._pending_progress_delayed = False
        self._progress_dirty = False
        self._last_progress_flush = 0.0

        # Token accumulators
        self._total_input_tokens = 0
        self._total_output_tokens = 0
        self._total_tokens = 0
        self._llm_call_count = 0

        # Caller-bucketed token accumulators
        self._lead_agent_tokens = 0
        self._subagent_tokens = 0
        self._middleware_tokens = 0

        # Per-model token accumulator
        self._tokens_by_model: dict[str, dict[str, int]] = {}

        # Dedup: LangChain may fire on_llm_end multiple times for the same run_id
        self._counted_llm_run_ids: set[str] = set()
        self._counted_external_source_ids: set[str] = set()
        self._counted_message_llm_run_ids: set[str] = set()
        self._llm_response_callers: dict[str, str] = {}
        self._memory_context_recorded = False
        self._tool_promotion_claim_lock = threading.Lock()
        self._claimed_tool_promotions: set[str] = set()

        # Convenience fields
        self._last_ai_msg: str | None = None
        self._last_lead_ai_message: AIMessage | None = None
        self._first_human_msg: str | None = None
        self._msg_count = 0
        self._had_llm_error_fallback = False
        self._llm_error_fallback_message: str | None = None

        # Latency tracking
        self._llm_start_times: dict[str, float] = {}  # langchain run_id -> start time

        # LLM request/response tracking
        self._llm_call_index = 0
        self._seen_llm_starts: set[str] = set()  # langchain run_ids that fired on_chat_model_start
        # Some OpenAI-compatible providers drop Runnable tags between
        # ``on_chat_model_start`` and ``on_llm_end``.  Preserve the caller
        # resolved at start time so internal summarization/title completions
        # cannot be journaled as user-visible lead-agent messages.
        self._llm_callers_by_run_id: dict[str, str] = {}
        self._current_run_tool_call_names: dict[str, str] = {}
        self._persisted_tool_message_identities: set[str] = set()
        self._active_tool_names: dict[str, str] = {}
        self._produced_artifacts: list[tuple[str, str | None]] = []
        self._produced_artifact_keys: set[tuple[str, str | None]] = set()
        self._external_artifact_paths: list[str] = []

    # -- Lifecycle callbacks --

    @staticmethod
    def _message_text(message: BaseMessage) -> str:
        """Extract displayable text from a message's mixed content shape."""
        return message_to_text(message, text_attribute_fallback=True)

    def _message_summary_text(self, message: BaseMessage, *, caller: str | None = None) -> str | None:
        """Return the bounded user-facing AI summary text for one message."""
        is_ai_message = isinstance(message, AIMessage) or getattr(message, "type", None) == "ai"
        if not is_ai_message or (caller is not None and caller != "lead_agent"):
            return None
        text = self._message_text(message).strip()
        return text[:2000] if text else None

    def _record_message_summary(self, message: BaseMessage, *, caller: str | None = None) -> None:
        """Update run-level convenience fields for persisted run rows."""
        self._msg_count += 1

        # ``last_ai_message`` should represent the lead agent's user-facing
        # answer. Middleware/subagent model calls and empty tool-call-only
        # AI messages must not overwrite the last useful assistant text.
        summary_text = self._message_summary_text(message, caller=caller)
        if summary_text is not None:
            self._last_ai_msg = summary_text

    def on_chain_start(
        self,
        serialized: dict[str, Any],
        inputs: dict[str, Any],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        caller = self._identify_caller(tags)
        if parent_run_id is None:
            # Root graph invocation — emit a single trace event for the run start.
            chain_name = (serialized or {}).get("name", "unknown")
            self._put(
                event_type=RUN_START_EVENT.event_type,
                category=RUN_START_EVENT.category,
                content={"chain": chain_name},
                metadata={"caller": caller, **(metadata or {})},
            )

    def on_chain_end(
        self,
        outputs: Any,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        # Nested chain ends fire for internal graph nodes; only the root chain
        # represents the user-visible run lifecycle.
        if parent_run_id is not None:
            return
        self._reconcile_final_error_fallback(outputs)
        self._reconcile_final_tool_messages(outputs)
        self._put(event_type=RUN_END_EVENT.event_type, category=RUN_END_EVENT.category, content=outputs, metadata={"status": "success"})
        self._flush_sync()

    def on_chain_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        self._put(
            event_type=RUN_ERROR_EVENT.event_type,
            category=RUN_ERROR_EVENT.category,
            content=str(error),
            metadata={"error_type": type(error).__name__},
        )
        self._flush_sync()

    # -- LLM callbacks --

    def on_chat_model_start(
        self,
        serialized: dict,
        messages: list[list[BaseMessage]],
        *,
        run_id: UUID,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        """Capture structured prompt messages for llm_request event.

        This is also the canonical place to extract the first human message:
        messages are fully structured here, it fires only on real LLM calls,
        and the content is never compressed by checkpoint trimming.
        """
        rid = str(run_id)
        self._llm_start_times[rid] = time.monotonic()
        self._llm_call_index += 1
        self._seen_llm_starts.add(rid)

        logger.debug(
            "on_chat_model_start %s: tags=%s num_batches=%d message_counts=%s",
            run_id,
            tags,
            len(messages),
            [len(batch) for batch in messages],
        )

        # Capture the first user message sent to the lead agent in this run.
        caller = self._identify_caller(tags, metadata)
        self._llm_callers_by_run_id[rid] = caller
        if caller == "lead_agent" and not self._first_human_msg and messages:
            for batch in reversed(messages):
                for m in reversed(batch):
                    if _should_persist_human_input_message(m):
                        persisted_message = restore_original_human_message(m)
                        self.set_first_human_message(self._message_text(persisted_message))
                        self._put(
                            event_type=LLM_HUMAN_INPUT_EVENT.event_type,
                            category=LLM_HUMAN_INPUT_EVENT.category,
                            content=persisted_message.model_dump(),
                            metadata={"caller": caller},
                        )
                        self._record_message_summary(persisted_message, caller=caller)
                        break
                if self._first_human_msg:
                    break

    def on_llm_start(self, serialized: dict, prompts: list[str], *, run_id: UUID, parent_run_id: UUID | None = None, tags: list[str] | None = None, metadata: dict[str, Any] | None = None, **kwargs: Any) -> None:
        # Fallback: on_chat_model_start is preferred. This just tracks latency.
        rid = str(run_id)
        self._llm_start_times[rid] = time.monotonic()
        self._llm_callers_by_run_id.setdefault(rid, self._identify_caller(tags, metadata))

    def on_llm_end(
        self,
        response: Any,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        if self._closed:
            return

        messages: list[AnyMessage] = []
        response_events: list[dict] = []
        should_schedule_progress = False
        logger.debug("on_llm_end %s: tags=%s", run_id, tags)
        rid = str(run_id)
        callback_caller = self._llm_callers_by_run_id.pop(rid, None) or self._identify_caller(tags, kwargs.get("metadata"))
        is_canonical_callback = rid not in self._counted_message_llm_run_ids
        caller = self._llm_response_callers.get(rid, callback_caller)
        for generation in response.generations:
            for gen in generation:
                if hasattr(gen, "message"):
                    messages.append(gen.message)
                else:
                    logger.warning(f"on_llm_end {run_id}: generation has no message attribute: {gen}")

        for message in messages:
            if is_canonical_callback:
                self._remember_current_run_tool_calls(message, caller=caller)
                if caller == "lead_agent" and isinstance(message, AIMessage):
                    # The enterprise interrupted-delivery guard must inspect the
                    # canonical response, not a provider-mutated/replayed object.
                    self._last_lead_ai_message = message.model_copy(deep=True)

            # Latency
            start = self._llm_start_times.pop(rid, None)
            latency_ms = int((time.monotonic() - start) * 1000) if start else None

            # Token usage from message
            usage = getattr(message, "usage_metadata", None)
            usage_dict = deepcopy(dict(usage)) if usage else {}
            additional_kwargs = getattr(message, "additional_kwargs", None) or {}
            if is_canonical_callback and isinstance(additional_kwargs, dict) and additional_kwargs.get("deerflow_error_fallback"):
                self._had_llm_error_fallback = True
                detail = additional_kwargs.get("error_detail")
                reason = additional_kwargs.get("error_reason")
                fallback_text = self._message_text(message).strip()
                if isinstance(detail, str) and detail.strip():
                    self._llm_error_fallback_message = detail.strip()
                elif isinstance(reason, str) and reason.strip():
                    self._llm_error_fallback_message = reason.strip()
                elif fallback_text:
                    self._llm_error_fallback_message = fallback_text[:2000]

            # Resolve call index
            call_index = self._llm_call_index
            if rid not in self._seen_llm_starts:
                # Fallback: on_chat_model_start was not called
                self._llm_call_index += 1
                call_index = self._llm_call_index
                self._seen_llm_starts.add(rid)

            response_events.append(
                self._make_event(
                    event_type=LLM_AI_RESPONSE_EVENT.event_type,
                    category=LLM_AI_RESPONSE_EVENT.category,
                    content=message.model_dump(),
                    metadata={
                        "caller": caller,
                        "usage": usage_dict,
                        "latency_ms": latency_ms,
                        "llm_call_index": call_index,
                    },
                )
            )

            # Token accumulation (dedup by langchain run_id to avoid double-counting
            # when the callback fires more than once for the same response)
            if self._track_tokens:
                input_tk = usage_dict.get("input_tokens", 0) or 0
                output_tk = usage_dict.get("output_tokens", 0) or 0
                total_tk = usage_dict.get("total_tokens", 0) or 0
                if total_tk == 0:
                    total_tk = input_tk + output_tk
                if total_tk > 0 and rid not in self._counted_llm_run_ids:
                    self._counted_llm_run_ids.add(rid)
                    self._total_input_tokens += input_tk
                    self._total_output_tokens += output_tk
                    self._total_tokens += total_tk
                    self._llm_call_count += 1

                    if caller.startswith("subagent:"):
                        self._subagent_tokens += total_tk
                    elif caller.startswith("middleware:"):
                        self._middleware_tokens += total_tk
                    else:
                        self._lead_agent_tokens += total_tk

                    # Per-model bucket
                    response_metadata = getattr(message, "response_metadata", None) or {}
                    per_call_model: str | None = None
                    if isinstance(response_metadata, Mapping):
                        per_call_model = response_metadata.get("model_name") or response_metadata.get("model")
                    self._record_model_usage(per_call_model, input_tk, output_tk, total_tk, self._extract_cache_read(usage_dict))

                    should_schedule_progress = True

        if messages:
            self._queue_llm_response_events(rid, response_events, messages, caller=caller)

        if should_schedule_progress:
            self._schedule_progress_flush()

    def on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        self._llm_start_times.pop(str(run_id), None)
        self._put(event_type=LLM_ERROR_EVENT.event_type, category=LLM_ERROR_EVENT.category, content=str(error))

    def on_tool_start(self, serialized, input_str, *, run_id, parent_run_id=None, tags=None, metadata=None, inputs=None, **kwargs):
        """Cache actual tool attribution, including subagent-only callbacks."""
        tool_run_id = str(run_id)
        tool_name = serialized.get("name") if isinstance(serialized, Mapping) else None
        if isinstance(tool_name, str) and tool_name:
            self._active_tool_names[tool_run_id] = tool_name
        logger.debug("Tool start for node %s, tool_run_id=%s, tags=%s", run_id, tool_run_id, tags)

    def on_tool_end(self, output, *, run_id, parent_run_id=None, **kwargs):
        """Handle tool end event, append message and clear node data"""
        active_tool_name = self._active_tool_names.pop(str(run_id), None)
        try:
            if isinstance(output, ToolMessage):
                msg = cast(ToolMessage, output)
                self._persist_tool_result_message(msg)
            elif isinstance(output, Command):
                cmd = cast(Command, output)
                # A Command may only navigate, with no state update at all.
                update = cmd.update if isinstance(cmd.update, Mapping) else {}
                messages = update.get("messages", [])
                artifacts = update.get("artifacts")
                artifact_tool_names: set[str] = set()
                for message in messages:
                    if isinstance(message, BaseMessage):
                        self._persist_tool_result_message(message)
                        if artifacts and isinstance(message, ToolMessage):
                            tool_name = self._current_run_tool_call_names.get(message.tool_call_id)
                            if tool_name:
                                artifact_tool_names.add(tool_name)
                    else:
                        logger.warning(f"on_tool_end {run_id}: command update message is not BaseMessage: {type(message)}")
                if artifacts:
                    artifact_tool_name = active_tool_name
                    if artifact_tool_name is None and len(artifact_tool_names) == 1:
                        artifact_tool_name = next(iter(artifact_tool_names))
                    self._record_produced_artifacts(artifacts, artifact_tool_name)
            else:
                logger.warning(f"on_tool_end {run_id}: output is not ToolMessage: {type(output)}")
        finally:
            logger.debug("Tool end for node %s", run_id)

    def on_tool_error(self, error, *, run_id, **kwargs):
        self._active_tool_names.pop(str(run_id), None)

    # -- Internal methods --

    @staticmethod
    def _message_identity(message: BaseMessage) -> str | None:
        tool_call_id = getattr(message, "tool_call_id", None)
        if isinstance(tool_call_id, str) and tool_call_id:
            return f"tool:{tool_call_id}"
        message_id = getattr(message, "id", None)
        if isinstance(message_id, str) and message_id:
            return f"message:{message_id}"
        return None

    @staticmethod
    def _tool_call_value(tool_call: Any, key: str) -> Any:
        if isinstance(tool_call, Mapping):
            return tool_call.get(key)
        return getattr(tool_call, key, None)

    def _remember_current_run_tool_calls(self, message: AnyMessage, *, caller: str) -> None:
        if caller != "lead_agent":
            return
        is_ai_message = isinstance(message, AIMessage) or getattr(message, "type", None) == "ai"
        if not is_ai_message:
            return
        tool_calls = getattr(message, "tool_calls", None) or []
        if not isinstance(tool_calls, list):
            return
        for tool_call in tool_calls:
            tool_call_id = self._tool_call_value(tool_call, "id")
            if not isinstance(tool_call_id, str) or not tool_call_id:
                continue
            name = self._tool_call_value(tool_call, "name")
            self._current_run_tool_call_names[tool_call_id] = str(name or "")

    def _persist_tool_result_message(self, message: BaseMessage) -> None:
        self._put(event_type=LLM_TOOL_RESULT_EVENT.event_type, category=LLM_TOOL_RESULT_EVENT.category, content=message.model_dump())
        identity = self._message_identity(message)
        if identity:
            self._persisted_tool_message_identities.add(identity)
        self._record_message_summary(message)

    def _final_output_messages(self, outputs: Any) -> list[Any]:
        if isinstance(outputs, Mapping):
            messages = outputs.get("messages", [])
            return messages if isinstance(messages, list) else []
        return []

    def _should_reconcile_tool_message(self, message: ToolMessage) -> bool:
        if message.additional_kwargs.get("hide_from_ui") is True:
            return False
        tool_call_id = getattr(message, "tool_call_id", None)
        if not isinstance(tool_call_id, str) or not tool_call_id:
            return False
        tool_call_name = self._current_run_tool_call_names.get(tool_call_id)
        if tool_call_name is None:
            return False
        message_name = getattr(message, "name", None)
        if message_name not in _RECONCILED_TOOL_MESSAGE_NAMES and tool_call_name not in _RECONCILED_TOOL_MESSAGE_NAMES:
            return False
        identity = self._message_identity(message)
        return identity is not None and identity not in self._persisted_tool_message_identities

    def _reconcile_final_tool_messages(self, outputs: Any) -> None:
        for message in self._final_output_messages(outputs):
            if not isinstance(message, ToolMessage):
                continue
            if self._should_reconcile_tool_message(message):
                self._persist_tool_result_message(message)

    def _reconcile_final_error_fallback(self, outputs: Any) -> None:
        """Persist an after-model replacement that callbacks could not observe.

        ``on_llm_end`` sees the provider's original AI message before agent
        middleware runs. ArtifactDeliveryMiddleware may replace that message in
        graph state with a visible failure carrying the same id. Recording the
        final version lets message pagination apply normal last-write-wins
        semantics instead of resurrecting the fabricated download link.
        """
        for message in reversed(self._final_output_messages(outputs)):
            if not isinstance(message, AIMessage):
                continue
            if (message.additional_kwargs or {}).get("deerflow_error_fallback") is not True:
                continue
            self._put(
                event_type=LLM_AI_RESPONSE_EVENT.event_type,
                category=LLM_AI_RESPONSE_EVENT.category,
                content=message.model_dump(),
                metadata={"caller": "lead_agent", "reconciled": True},
            )
            return

    def get_last_lead_ai_message(self) -> AIMessage | None:
        """Return the latest provider-complete lead response seen this run."""
        return self._last_lead_ai_message

    def reconcile_ai_message(self, message: AIMessage, *, reason: str) -> None:
        """Persist a post-callback replacement with last-write-wins identity."""
        self._put(
            event_type=LLM_AI_RESPONSE_EVENT.event_type,
            category=LLM_AI_RESPONSE_EVENT.category,
            content=message.model_dump(),
            metadata={"caller": "lead_agent", "reconciled": True, "reason": reason},
        )
        self._last_lead_ai_message = message
        text = self._message_text(message).strip()
        if text:
            self._last_ai_msg = text[:2000]

    def _make_event(self, *, event_type: str, category: str, content: str | dict = "", metadata: dict | None = None) -> dict:
        return {
            "thread_id": self.thread_id,
            "run_id": self.run_id,
            "event_type": event_type,
            "category": category,
            "content": content,
            "metadata": metadata or {},
            "created_at": datetime.now(UTC).isoformat(),
        }

    def _commit_pending_llm_response(self) -> None:
        pending = self._pending_llm_response
        if pending is None:
            return
        self._pending_llm_response = None
        self._buffer.extend(pending.events)
        self._msg_count += pending.message_count
        if pending.last_ai_message is not None:
            self._last_ai_msg = pending.last_ai_message

    def _snapshot_message_summary(self, messages: Sequence[AnyMessage], *, caller: str) -> tuple[int, str | None]:
        """Freeze summary fields before a provider can mutate replayed messages."""
        last_ai_message: str | None = None
        for message in messages:
            summary_text = self._message_summary_text(message, caller=caller)
            if summary_text is not None:
                last_ai_message = summary_text
        return len(messages), last_ai_message

    @staticmethod
    def _has_positive_usage(events: list[dict]) -> bool:
        for event in events:
            usage = event["metadata"].get("usage")
            if not isinstance(usage, Mapping):
                continue
            for key in ("input_tokens", "output_tokens", "total_tokens"):
                try:
                    if int(usage.get(key) or 0) > 0:
                        return True
                except (TypeError, ValueError):
                    continue
        return False

    @staticmethod
    def _merge_response_event_usage(canonical_events: list[dict], replay_events: list[dict]) -> None:
        """Enrich canonical generation events with only replayed usage fields."""
        for canonical, replay in zip(canonical_events, replay_events, strict=False):
            replay_metadata = replay.get("metadata")
            if isinstance(replay_metadata, Mapping):
                replay_usage = replay_metadata.get("usage")
                if isinstance(replay_usage, Mapping):
                    canonical["metadata"]["usage"] = deepcopy(dict(replay_usage))

            canonical_content = canonical.get("content")
            replay_content = replay.get("content")
            if isinstance(canonical_content, dict) and isinstance(replay_content, Mapping) and "usage_metadata" in replay_content:
                replay_content_usage = replay_content.get("usage_metadata")
                canonical_content["usage_metadata"] = deepcopy(dict(replay_content_usage)) if isinstance(replay_content_usage, Mapping) else replay_content_usage

    def _flush_if_threshold_reached(self) -> None:
        pending_count = len(self._pending_llm_response.events) if self._pending_llm_response is not None else 0
        if len(self._buffer) + pending_count >= self._flush_threshold:
            self._flush_sync()

    def _queue_llm_response_events(self, llm_run_id: str, events: list[dict], messages: list[AnyMessage], *, caller: str) -> None:
        """Queue one logical response and merge usage into its canonical callback."""
        if self._closed:
            return
        has_usage = self._has_positive_usage(events)
        pending = self._pending_llm_response
        if pending is not None and pending.llm_run_id == llm_run_id:
            if has_usage:
                # Only usage can enrich the first callback, never its generation
                # set, immutable summary, caller or non-usage payload.
                self._merge_response_event_usage(pending.events, events)
                self._commit_pending_llm_response()
                self._flush_if_threshold_reached()
            return
        if llm_run_id in self._counted_message_llm_run_ids:
            return

        # A different event is the ordering boundary for a no-usage callback.
        self._commit_pending_llm_response()
        self._flush_if_threshold_reached()
        message_count, last_ai_message = self._snapshot_message_summary(messages, caller=caller)
        self._pending_llm_response = _PendingLlmResponse(llm_run_id, events, message_count, last_ai_message)
        self._counted_message_llm_run_ids.add(llm_run_id)
        self._llm_response_callers[llm_run_id] = caller
        if has_usage:
            self._commit_pending_llm_response()
        self._flush_if_threshold_reached()
        # Defer an incomplete copy only until the next event or flush, allowing
        # providers to immediately re-fire on_llm_end with usage filled in.

    def _put(self, *, event_type: str, category: str, content: str | dict = "", metadata: dict | None = None) -> None:
        if self._closed:
            return
        self._commit_pending_llm_response()
        self._buffer.append(self._make_event(event_type=event_type, category=category, content=content, metadata=metadata))
        if len(self._buffer) >= self._flush_threshold:
            self._flush_sync()

    def _flush_sync(self) -> None:
        """Best-effort flush of buffer to RunEventStore.

        BaseCallbackHandler methods are synchronous.  If an event loop is
        running we schedule an async ``put_batch``; otherwise the events
        stay in the buffer and are flushed later by the async ``flush()``
        call in the worker's ``finally`` block.
        """
        self._commit_pending_llm_response()
        if not self._buffer:
            return
        # Skip if a flush is already in flight — avoids concurrent writes
        # to the same SQLite file from multiple fire-and-forget tasks.
        if self._pending_flush_tasks or self._explicit_flush_in_progress:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No event loop — keep events in buffer for later async flush.
            return
        batch = self._buffer.copy()
        self._buffer.clear()
        task = loop.create_task(self._flush_async(batch))
        self._pending_flush_tasks.add(task)
        task.add_done_callback(self._on_flush_done)

    async def _flush_async(self, batch: list[dict]) -> None:
        try:
            store = self._store
            if store is None:
                return
            await store.put_batch(batch)
        except Exception:
            logger.warning(
                "Failed to flush %d events for run %s — returning to buffer",
                len(batch),
                self.run_id,
                exc_info=True,
            )
            # Return failed events to buffer for retry on next flush
            if not self._closed:
                self._buffer = batch + self._buffer

    def _on_flush_done(self, task: asyncio.Task) -> None:
        self._pending_flush_tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            logger.warning("Journal flush task failed: %s", exc)

    def _identify_caller(self, tags: list[str] | None, metadata: dict[str, Any] | None = None) -> str:
        _tags = tags or []
        for tag in _tags:
            if isinstance(tag, str) and (tag.startswith("subagent:") or tag.startswith("middleware:") or tag == "lead_agent"):
                return tag
        # ``lc_source`` is bound directly on internal model invocations and is
        # more reliable than Runnable tags for several OpenAI-compatible
        # providers.  Use it only for known internal sources.
        source = (metadata or {}).get("lc_source")
        if source in {"summarization", "title"}:
            return f"middleware:{source}"
        # Default to lead_agent: the main agent graph does not inject
        # callback tags, while subagents and middleware explicitly tag
        # themselves.
        return "lead_agent"

    def _record_model_usage(
        self,
        model_name: str | None,
        input_tokens: int,
        output_tokens: int,
        total_tokens: int,
        cache_read_tokens: int = 0,
    ) -> None:
        """Add a single LLM call's token usage to the per-model accumulator.

        Missing / empty ``model_name`` collapses into a shared ``"unknown"``
        bucket so the breakdown stays usable when a provider doesn't surface
        ``response_metadata.model_name``.

        ``cache_read_tokens`` (prompt-cache hits, from
        ``usage_metadata.input_token_details.cache_read``) is stored as a
        sparse bucket key — only written when non-zero — so buckets from
        providers without cache reporting keep their historical shape.
        """
        if total_tokens <= 0:
            return
        bucket = self._tokens_by_model.setdefault(
            model_name or "unknown",
            {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        )
        bucket["input_tokens"] += int(input_tokens or 0)
        bucket["output_tokens"] += int(output_tokens or 0)
        bucket["total_tokens"] += int(total_tokens)
        if cache_read_tokens > 0:
            bucket["cache_read_tokens"] = bucket.get("cache_read_tokens", 0) + int(cache_read_tokens)

    @staticmethod
    def _extract_cache_read(usage_dict: dict) -> int:
        """Prompt-cache-hit input tokens from LangChain's normalized usage."""
        details = usage_dict.get("input_token_details") or {}
        if not isinstance(details, Mapping):
            return 0
        try:
            return max(int(details.get("cache_read") or 0), 0)
        except (TypeError, ValueError):
            return 0

    # -- Public methods (called by worker) --

    def record_external_llm_usage_records(
        self,
        records: list[dict[str, int | str | None]],
    ) -> None:
        """Record token usage from external sources (e.g., subagents).

        Each record should contain:
            source_run_id: Unique identifier to prevent double-counting
            caller: Caller tag (e.g. "subagent:general-purpose")
            model_name: Real per-call model name (str or None; falls back to
                ``"unknown"`` bucket when missing)
            input_tokens: Input token count
            output_tokens: Output token count
            total_tokens: Total token count (computed from input+output if 0/missing)
            cache_read_tokens: Optional prompt-cache-hit input tokens
            llm_call_count: Optional number of external model calls represented
                by the record (defaults to 1)
        """
        if not self._track_tokens:
            return
        for record in records:
            source_id = str(record.get("source_run_id", ""))
            if not source_id:
                continue
            if source_id in self._counted_external_source_ids:
                continue

            total_tk = record.get("total_tokens", 0) or 0
            if total_tk <= 0:
                input_tk = record.get("input_tokens", 0) or 0
                output_tk = record.get("output_tokens", 0) or 0
                total_tk = input_tk + output_tk
            if total_tk <= 0:
                continue

            input_tk = record.get("input_tokens", 0) or 0
            output_tk = record.get("output_tokens", 0) or 0

            self._counted_external_source_ids.add(source_id)
            self._total_input_tokens += input_tk
            self._total_output_tokens += output_tk
            self._total_tokens += total_tk
            try:
                external_call_count = max(int(record.get("llm_call_count", 1) or 1), 1)
            except (TypeError, ValueError):
                external_call_count = 1
            self._llm_call_count += external_call_count

            caller = str(record.get("caller", ""))
            if caller.startswith("subagent:"):
                self._subagent_tokens += total_tk
            elif caller.startswith("middleware:"):
                self._middleware_tokens += total_tk
            else:
                self._lead_agent_tokens += total_tk

            cache_read_tk = record.get("cache_read_tokens", 0) or 0
            self._record_model_usage(record.get("model_name"), input_tk, output_tk, total_tk, int(cache_read_tk))

            self._schedule_progress_flush()

    def set_first_human_message(self, content: str) -> None:
        """Record the first human message for convenience fields."""
        self._first_human_msg = content[:2000] if content else None

    def claim_tool_promotions(self, tool_names: Iterable[str]) -> list[str]:
        """Deduplicate one lead run's discoveries even across parallel Sends."""
        with self._tool_promotion_claim_lock:
            if self._closed:
                return []
            names = sorted(set(tool_names) - self._claimed_tool_promotions)
            self._claimed_tool_promotions.update(names)
            return names

    def record_middleware(self, tag: str, *, name: str, hook: str, action: str, changes: dict) -> None:
        """Record a middleware state-change event.

        Called by middleware implementations when they perform a meaningful
        state change (e.g., title generation, summarization, HITL approval).
        Pure-observation middleware should not call this.

        Args:
            tag: Short identifier for the middleware (e.g., "title", "summarize",
                 "guardrail"). Used to form event_type="middleware:{tag}".
            name: Full middleware class name.
            hook: Lifecycle hook that triggered the action (e.g., "after_model").
            action: Specific action performed (e.g., "generate_title").
            changes: Dict describing the state changes made.
        """
        if self._closed:
            return
        event_type = MIDDLEWARE_EVENT_PATTERN.event_type(tag)
        owner_loop = self._owner_loop
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None
        if owner_loop is not None and running_loop is not owner_loop:
            if owner_loop.is_closed() or not owner_loop.is_running():
                logger.warning("Dropping cross-thread middleware event after run loop shutdown")
                return
            try:
                owner_loop.call_soon_threadsafe(
                    partial(
                        self._put,
                        event_type=event_type,
                        category=MIDDLEWARE_EVENT_PATTERN.category,
                        content={"name": name, "hook": hook, "action": action, "changes": dict(changes)},
                    )
                )
            except RuntimeError:
                logger.warning("Dropping cross-thread middleware event after run loop shutdown")
            return

        self._put(
            event_type=event_type,
            category=MIDDLEWARE_EVENT_PATTERN.category,
            content={"name": name, "hook": hook, "action": action, "changes": changes},
        )

    def record_memory_context(self, *, content_sha256: str) -> None:
        """Record the effective hidden memory block for this run.

        The full block already lives in checkpoint state and may contain user
        data, so the event stores only its exact SHA-256 identity. Operators
        consume it through the existing run-events debug API to compare the
        effective memory used by different runs without copying that content.
        """
        if self._memory_context_recorded:
            return
        self._put(
            event_type=MEMORY_CONTEXT_EVENT.event_type,
            category=MEMORY_CONTEXT_EVENT.category,
            content={"content_sha256": content_sha256},
        )
        self._memory_context_recorded = True

    def _record_produced_artifacts(self, artifacts: Any, tool_name: str | None) -> None:
        """Accumulate tool-returned artifact paths, deduped by (path, tool)."""
        if not isinstance(artifacts, list):
            return
        for path in artifacts:
            if not isinstance(path, str) or not path:
                continue
            key = (path, tool_name)
            if key not in self._produced_artifact_keys:
                self._produced_artifact_keys.add(key)
                self._produced_artifacts.append(key)

    def record_external_artifact_delivery(self, paths: list[str], *, run_id: str, thread_id: str) -> None:
        """Host-only PPT handoff, never a model/tool state projection."""
        from deerflow.runtime.runs.delivery import is_output_path

        if self._closed:
            raise RuntimeError("External delivery journal is closed")
        if run_id != self.run_id or thread_id != self.thread_id:
            raise RuntimeError("External delivery identity does not match the active journal")
        if not isinstance(paths, list) or any(not is_output_path(path) for path in paths):
            raise RuntimeError("External delivery contains an invalid artifact path")
        for path in paths:
            if path not in self._external_artifact_paths:
                self._external_artifact_paths.append(path)

    def get_delivery_content(self) -> dict[str, Any]:
        """Return a fact record, not proof that an artifact passed acceptance."""
        by_tool: dict[str, list[str]] = {}
        paths: list[str] = []
        for path, tool_name in self._produced_artifacts:
            paths.append(path)
            if tool_name:
                by_tool.setdefault(tool_name, []).append(path)
        content = {"presented": len(paths), "paths": paths, "by_tool": by_tool}
        if self._external_artifact_paths:
            from deerflow.runtime.runs.delivery import PPT_AGENT_PUBLISHER

            external = list(self._external_artifact_paths)
            content["by_publisher"] = {PPT_AGENT_PUBLISHER: external}
            content["paths"] = list(dict.fromkeys(paths + external))
            content["presented"] = len(content["paths"])
        return content

    def record_delivery(self) -> None:
        """Buffer a fact for direct journal users; workers use singleton writes."""
        self._put(event_type=RUN_DELIVERY_EVENT.event_type, category=RUN_DELIVERY_EVENT.category, content=self.get_delivery_content())

    async def flush(self) -> None:
        """Force flush remaining buffer. Called in worker's finally block."""
        if self._closed:
            return
        self._explicit_flush_in_progress = True
        try:
            self._commit_pending_llm_response()
            if self._pending_flush_tasks:
                await asyncio.gather(*tuple(self._pending_flush_tasks), return_exceptions=True)
            while self._pending_progress_task is not None:
                pending_progress_task = self._pending_progress_task
                if pending_progress_task.done():
                    if self._pending_progress_task is pending_progress_task:
                        self._pending_progress_task = None
                    break
                if self._pending_progress_delayed:
                    pending_progress_task.cancel()
                    await asyncio.gather(pending_progress_task, return_exceptions=True)
                    if self._pending_progress_task is pending_progress_task:
                        self._pending_progress_task = None
                    self._progress_dirty = False
                    self._pending_progress_delayed = False
                    break
                await asyncio.gather(pending_progress_task, return_exceptions=True)
                if self._pending_progress_task is pending_progress_task:
                    self._pending_progress_task = None

            while self._buffer:
                batch = self._buffer[: self._flush_threshold]
                del self._buffer[: self._flush_threshold]
                try:
                    store = self._store
                    if store is None:
                        return
                    await store.put_batch(batch)
                except Exception:
                    self._buffer = batch + self._buffer
                    raise
        finally:
            self._explicit_flush_in_progress = False

    def _detach_runtime_dependencies(self) -> None:
        """Release store/reporter and run-scoped caches, including enterprise data."""
        self._closed = True
        self._store = None
        self._progress_reporter = None
        self._buffer.clear()
        self._pending_llm_response = None
        self._pending_flush_tasks.clear()
        self._explicit_flush_in_progress = False
        self._pending_progress_task = None
        self._pending_progress_delayed = False
        self._progress_dirty = False
        self._tokens_by_model.clear()
        self._counted_llm_run_ids.clear()
        self._counted_external_source_ids.clear()
        self._counted_message_llm_run_ids.clear()
        self._llm_response_callers.clear()
        self._llm_start_times.clear()
        self._seen_llm_starts.clear()
        self._llm_callers_by_run_id.clear()
        self._current_run_tool_call_names.clear()
        self._persisted_tool_message_identities.clear()
        self._active_tool_names.clear()
        self._produced_artifacts.clear()
        self._produced_artifact_keys.clear()
        self._external_artifact_paths.clear()
        self._last_ai_msg = None
        self._last_lead_ai_message = None
        self._first_human_msg = None
        self._llm_error_fallback_message = None

    async def close(self, *, flush: bool = True) -> None:
        """Flush normally; a fenced owner discards instead of starting writes."""
        if self._closed:
            return
        if flush:
            # Retain failed batches/store so a later close can retry.
            await self.flush()
            self._detach_runtime_dependencies()
            return

        self._closed = True
        self._store = None
        self._progress_reporter = None
        try:
            pending_flush_tasks = tuple(self._pending_flush_tasks)
            for task in pending_flush_tasks:
                task.cancel()
            if pending_flush_tasks:
                await asyncio.gather(*pending_flush_tasks, return_exceptions=True)
            pending_progress_task = self._pending_progress_task
            if pending_progress_task is not None:
                pending_progress_task.cancel()
                await asyncio.gather(pending_progress_task, return_exceptions=True)
        finally:
            self._detach_runtime_dependencies()

    def _schedule_progress_flush(self) -> None:
        """Best-effort throttled progress snapshot for active run visibility."""
        if self._progress_reporter is None:
            return
        now = time.monotonic()
        elapsed = now - self._last_progress_flush
        if elapsed < self._progress_flush_interval:
            self._progress_dirty = True
            self._schedule_delayed_progress_flush(self._progress_flush_interval - elapsed)
            return
        if self._pending_progress_task is not None and not self._pending_progress_task.done():
            self._progress_dirty = True
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._progress_dirty = False
        self._pending_progress_task = loop.create_task(self._flush_progress_async(snapshot=self.get_completion_data()))

    def _schedule_delayed_progress_flush(self, delay: float) -> None:
        if self._pending_progress_task is not None and not self._pending_progress_task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        delay = max(0.0, delay)
        self._pending_progress_delayed = delay > 0
        self._pending_progress_task = loop.create_task(self._flush_progress_async(delay=delay))

    async def _flush_progress_async(self, *, snapshot: dict | None = None, delay: float = 0.0) -> None:
        if self._progress_reporter is None:
            return
        if delay > 0:
            self._pending_progress_delayed = True
            await asyncio.sleep(delay)
            self._pending_progress_delayed = False
        dirty_before_write = self._progress_dirty
        self._progress_dirty = False
        snapshot_to_write = snapshot or self.get_completion_data()
        try:
            await self._progress_reporter(snapshot_to_write)
            self._last_progress_flush = time.monotonic()
        except Exception:
            logger.warning("Failed to persist progress snapshot for run %s", self.run_id, exc_info=True)
        if dirty_before_write or self._progress_dirty:
            self._progress_dirty = False
            self._pending_progress_task = None
            self._schedule_delayed_progress_flush(self._progress_flush_interval)

    def get_completion_data(self) -> dict:
        """Return accumulated token and message data for run completion."""
        return {
            "total_input_tokens": self._total_input_tokens,
            "total_output_tokens": self._total_output_tokens,
            "total_tokens": self._total_tokens,
            "llm_call_count": self._llm_call_count,
            "lead_agent_tokens": self._lead_agent_tokens,
            "subagent_tokens": self._subagent_tokens,
            "middleware_tokens": self._middleware_tokens,
            "token_usage_by_model": {model: dict(usage) for model, usage in self._tokens_by_model.items()},
            "message_count": self._msg_count,
            "last_ai_message": self._last_ai_msg,
            "first_human_message": self._first_human_msg,
        }

    @property
    def had_llm_error_fallback(self) -> bool:
        return self._had_llm_error_fallback

    @property
    def llm_error_fallback_message(self) -> str | None:
        return self._llm_error_fallback_message
