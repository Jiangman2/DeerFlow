"""OpenAI-compatible chat adapter with business-envelope diagnostics.

Some enterprise OpenAI-compatible gateways report application failures as
``200 OK`` responses shaped like this::

    {"choices": null, "success": false, "code": "...", "msg": "..."}

``langchain-openai`` correctly rejects the null ``choices`` value, but its
generic ``TypeError`` only lists the response keys. That discards the fields
needed to distinguish a transient provider failure from quota, authentication,
or context-limit errors. This adapter preserves the bounded business error
fields in a typed exception before delegating valid responses to the standard
``ChatOpenAI`` implementation.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import threading
import time
import uuid
import warnings
from collections import OrderedDict
from collections.abc import AsyncIterator, Callable, Iterator, Mapping
from contextlib import aclosing, asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

import openai
from langchain_core.callbacks import (
    AsyncCallbackManagerForLLMRun,
    CallbackManagerForLLMRun,
)
from langchain_core.language_models import LanguageModelInput
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    BaseMessageChunk,
)
from langchain_core.messages.ai import UsageMetadata, subtract_usage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_openai import ChatOpenAI
from langchain_openai.chat_models.base import (
    _astream_with_chunk_timeout,
    _handle_openai_api_error,
    _handle_openai_bad_request,
)
from pydantic import Field, PrivateAttr

from deerflow.llm_timing_context import get_current_llm_timing_context
from deerflow.models.assistant_payload_replay import (
    restore_assistant_payloads,
    restore_reasoning_content,
)
from deerflow.models.stream_admission import admit_model_stream

_MAX_PROVIDER_MESSAGE_CHARS = 1000
_MAX_JSON_STRING_UNWRAP_DEPTH = 3
_CUMULATIVE_USAGE_TRACKER_CAPACITY = 1024
_CUMULATIVE_USAGE_TRACKER_IDLE_SECONDS = 60 * 60
_LLM_TIMING_LOGS_ENV = "DEERFLOW_LLM_TIMING_LOGS"
_TIMING_THREAD_METADATA_KEY = "deerflow_timing_thread_id"
_TIMING_RUN_METADATA_KEY = "deerflow_timing_run_id"

logger = logging.getLogger(__name__)

_MISSING = object()


def _extract_reasoning_content(value: Any) -> str | object:
    """Return DeepSeek-style reasoning content from a mapping or SDK object."""

    if isinstance(value, Mapping):
        reasoning = value.get("reasoning_content", _MISSING)
        return reasoning if isinstance(reasoning, str) else _MISSING

    reasoning = getattr(value, "reasoning_content", _MISSING)
    if isinstance(reasoning, str):
        return reasoning

    model_extra = getattr(value, "model_extra", None)
    if isinstance(model_extra, Mapping):
        reasoning = model_extra.get("reasoning_content", _MISSING)
        if isinstance(reasoning, str):
            return reasoning

    return _MISSING


def _with_reasoning_content(
    message: AIMessage | AIMessageChunk,
    reasoning_content: str,
) -> AIMessage | AIMessageChunk:
    """Attach a provider reasoning delta in DeerFlow's standard message field."""

    additional_kwargs = dict(message.additional_kwargs)
    additional_kwargs["reasoning_content"] = reasoning_content
    return message.model_copy(update={"additional_kwargs": additional_kwargs})


def _llm_timing_logs_enabled() -> bool:
    """Return whether opt-in, content-free model timing logs are enabled."""

    return os.getenv(_LLM_TIMING_LOGS_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def _safe_timing_identifier(value: Any) -> str:
    """Return a bounded, single-token identifier safe for operational logs."""

    if not isinstance(value, str):
        return "-"
    identifier = value.strip()
    if not identifier or len(identifier) > 128 or any(ch.isspace() or ord(ch) < 32 or ord(ch) > 126 for ch in identifier):
        return "-"
    return identifier


def _timing_context_from_run_manager(
    run_manager: CallbackManagerForLLMRun | AsyncCallbackManagerForLLMRun | None,
) -> tuple[str, str]:
    """Resolve DeerFlow IDs from task-local state, then LangChain metadata.

    LangGraph may execute a model callback in a child task or callback context
    that does not retain the worker's ``ContextVar``.  The worker also writes
    these IDs to inheritable LangChain metadata, which survives that boundary.
    """

    timing_context = get_current_llm_timing_context()
    if timing_context is not None:
        return timing_context.thread_id, timing_context.run_id

    for metadata_name in ("metadata", "inheritable_metadata"):
        metadata = getattr(run_manager, metadata_name, None)
        if isinstance(metadata, Mapping):
            thread_id = _safe_timing_identifier(metadata.get(_TIMING_THREAD_METADATA_KEY))
            run_id = _safe_timing_identifier(metadata.get(_TIMING_RUN_METADATA_KEY))
            if thread_id != "-" or run_id != "-":
                return thread_id, run_id
    return "-", "-"


def _safe_endpoint_for_log(value: Any) -> str:
    """Format a provider endpoint without query parameters or user credentials."""

    raw = str(value or "").strip()
    if not raw:
        return "-"
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return "<invalid-endpoint>"
    if not parsed.scheme or not parsed.netloc:
        return "<configured-endpoint>"
    host = parsed.hostname or ""
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    return urlunsplit((parsed.scheme, host, parsed.path, "", ""))


@dataclass(slots=True)
class _ModelStreamTiming:
    """Emit opt-in timing records without retaining prompt or response content."""

    enabled: bool
    model: str
    endpoint: str
    langchain_run_id: str
    mode: str
    deerflow_thread_id: str = "-"
    deerflow_run_id: str = "-"
    llm_call_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    started_at: float = field(default_factory=time.monotonic)
    first_chunk_at: float | None = None
    chunk_count: int = 0
    token_usage: dict[str, int | float] = field(default_factory=dict)
    finished: bool = False
    upstream_next_count: int = 0
    upstream_next_total_s: float = 0.0
    upstream_next_max_s: float = 0.0
    upstream_next_over_1s: int = 0
    upstream_error_wait_s: float | None = None
    callback_count: int = 0
    callback_total_s: float = 0.0
    callback_max_s: float = 0.0
    callback_over_100ms: int = 0
    downstream_resume_count: int = 0
    downstream_resume_total_s: float = 0.0
    downstream_resume_max_s: float = 0.0
    downstream_resume_over_100ms: int = 0

    @classmethod
    def for_invocation(
        cls,
        model: Any,
        run_manager: CallbackManagerForLLMRun | AsyncCallbackManagerForLLMRun | None,
        *,
        mode: str,
    ) -> _ModelStreamTiming:
        deerflow_thread_id, deerflow_run_id = _timing_context_from_run_manager(run_manager)
        return cls(
            enabled=_llm_timing_logs_enabled(),
            model=str(getattr(model, "model_name", None) or getattr(model, "model", None) or "unknown"),
            endpoint=_safe_endpoint_for_log(getattr(model, "openai_api_base", None)),
            langchain_run_id=str(getattr(run_manager, "run_id", None) or "-"),
            mode=mode,
            deerflow_thread_id=deerflow_thread_id,
            deerflow_run_id=deerflow_run_id,
        )

    def start(self) -> None:
        if self.enabled:
            logger.info(
                "LLM_TIMING start llm_call_id=%s mode=%s model=%s endpoint=%s langchain_run_id=%s deerflow_thread_id=%s deerflow_run_id=%s",
                self.llm_call_id,
                self.mode,
                self.model,
                self.endpoint,
                self.langchain_run_id,
                self.deerflow_thread_id,
                self.deerflow_run_id,
            )

    def observe_chunk(self, generation_chunk: ChatGenerationChunk) -> None:
        self.chunk_count += 1
        self._mark_first_chunk()

        usage = getattr(generation_chunk.message, "usage_metadata", None)
        if not isinstance(usage, Mapping):
            return
        self.token_usage = {key: value for key in ("input_tokens", "output_tokens", "total_tokens") if isinstance((value := usage.get(key)), (int, float))}

    def observe_result(self, result: ChatResult) -> None:
        """Record one non-stream result using the same content-free fields.

        ``ChatOpenAI.ainvoke`` bypasses ``_astream`` when ``streaming`` is
        false.  Goal evaluation intentionally uses that path, so without this
        method its model wait was indistinguishable from unattributed run time.
        A completed non-stream response has no real first chunk; the existing
        ``first_chunk`` field therefore records response completion latency.
        """

        self.chunk_count += 1
        self._mark_first_chunk()
        for generation in reversed(result.generations):
            usage = getattr(getattr(generation, "message", None), "usage_metadata", None)
            if not isinstance(usage, Mapping):
                continue
            self.token_usage = {key: value for key in ("input_tokens", "output_tokens", "total_tokens") if isinstance((value := usage.get(key)), (int, float))}
            return

    def _mark_first_chunk(self) -> None:
        """Record the first observable response unit exactly once."""

        if self.first_chunk_at is not None:
            return
        self.first_chunk_at = time.monotonic()
        if self.enabled:
            logger.info(
                "LLM_TIMING first_chunk llm_call_id=%s mode=%s model=%s langchain_run_id=%s deerflow_thread_id=%s deerflow_run_id=%s wait_ms=%d",
                self.llm_call_id,
                self.mode,
                self.model,
                self.langchain_run_id,
                self.deerflow_thread_id,
                self.deerflow_run_id,
                round((self.first_chunk_at - self.started_at) * 1000),
            )

    def observe_upstream_next(self, elapsed_s: float, *, failed: bool = False) -> None:
        """Measure only the await for the next raw provider chunk, not local work."""

        if not self.enabled:
            return
        self.upstream_next_count += 1
        self.upstream_next_total_s += elapsed_s
        self.upstream_next_max_s = max(self.upstream_next_max_s, elapsed_s)
        self.upstream_next_over_1s += elapsed_s >= 1.0
        if failed:
            self.upstream_error_wait_s = elapsed_s

    def observe_callback(self, elapsed_s: float) -> None:
        """Measure the awaited LangChain callback for one delivered chunk."""

        if not self.enabled:
            return
        self.callback_count += 1
        self.callback_total_s += elapsed_s
        self.callback_max_s = max(self.callback_max_s, elapsed_s)
        self.callback_over_100ms += elapsed_s >= 0.1

    def observe_downstream_resume(self, elapsed_s: float) -> None:
        """Measure time suspended at yield while the downstream consumer runs."""

        if not self.enabled:
            return
        self.downstream_resume_count += 1
        self.downstream_resume_total_s += elapsed_s
        self.downstream_resume_max_s = max(self.downstream_resume_max_s, elapsed_s)
        self.downstream_resume_over_100ms += elapsed_s >= 0.1

    def finish(self, *, outcome: str, error: BaseException | None = None) -> None:
        if self.finished:
            return
        self.finished = True
        if not self.enabled:
            return

        elapsed_ms = round((time.monotonic() - self.started_at) * 1000)
        first_chunk_ms = "-" if self.first_chunk_at is None else round((self.first_chunk_at - self.started_at) * 1000)
        error_type = "-" if error is None else type(error).__name__
        status_code = getattr(error, "status_code", "-") if error is not None else "-"
        logger.info(
            "LLM_TIMING end llm_call_id=%s mode=%s model=%s langchain_run_id=%s deerflow_thread_id=%s deerflow_run_id=%s outcome=%s total_ms=%s first_chunk_ms=%s chunks=%d status_code=%s error=%s usage=%s",
            self.llm_call_id,
            self.mode,
            self.model,
            self.langchain_run_id,
            self.deerflow_thread_id,
            self.deerflow_run_id,
            outcome,
            elapsed_ms,
            first_chunk_ms,
            self.chunk_count,
            status_code,
            error_type,
            self.token_usage or "-",
        )
        if self.mode == "async":
            logger.info(
                "LLM_STREAM_PATH event=end llm_call_id=%s model=%s "
                "deerflow_thread_id=%s deerflow_run_id=%s outcome=%s "
                "upstream_next_count=%d upstream_next_total_ms=%d upstream_next_max_ms=%d "
                "upstream_next_over_1s=%d upstream_error_wait_ms=%s "
                "callback_count=%d callback_total_ms=%d callback_max_ms=%d callback_over_100ms=%d "
                "downstream_resume_count=%d downstream_resume_total_ms=%d "
                "downstream_resume_max_ms=%d downstream_resume_over_100ms=%d",
                self.llm_call_id,
                self.model,
                self.deerflow_thread_id,
                self.deerflow_run_id,
                outcome,
                self.upstream_next_count,
                round(self.upstream_next_total_s * 1000),
                round(self.upstream_next_max_s * 1000),
                self.upstream_next_over_1s,
                "-" if self.upstream_error_wait_s is None else round(self.upstream_error_wait_s * 1000),
                self.callback_count,
                round(self.callback_total_s * 1000),
                round(self.callback_max_s * 1000),
                self.callback_over_100ms,
                self.downstream_resume_count,
                round(self.downstream_resume_total_s * 1000),
                round(self.downstream_resume_max_s * 1000),
                self.downstream_resume_over_100ms,
            )


@asynccontextmanager
async def _closing_async_iterator(iterator: AsyncIterator[Any], *, close_on_exit: bool = True) -> AsyncIterator[AsyncIterator[Any]]:
    """Close async generators without requiring every iterator to expose aclose."""

    try:
        yield iterator
    finally:
        if close_on_exit:
            close = getattr(iterator, "aclose", None)
            if callable(close):
                await close()


async def _timed_async_stream_chunks(
    chunks: AsyncIterator[Any],
    timing: _ModelStreamTiming,
    *,
    clock: Callable[[], float] = time.monotonic,
) -> AsyncIterator[Any]:
    """Keep provider await time separate from callback and consumer backpressure."""

    async with _closing_async_iterator(chunks) as source:
        while True:
            wait_started = clock() if timing.enabled else None
            try:
                chunk = await anext(source)
            except StopAsyncIteration:
                return
            except BaseException:
                if wait_started is not None:
                    timing.observe_upstream_next(clock() - wait_started, failed=True)
                raise
            if wait_started is not None:
                timing.observe_upstream_next(clock() - wait_started)
            yield chunk


def _bounded_field(value: Any, *, limit: int = _MAX_PROVIDER_MESSAGE_CHARS) -> str:
    """Return a single-line bounded representation suitable for logs."""

    if value in (None, ""):
        return ""
    normalized = " ".join(str(value).split())
    return normalized[:limit]


class OpenAICompatibleResponseError(RuntimeError):
    """Business error returned inside an HTTP-success response."""

    def __init__(
        self,
        *,
        code: Any = None,
        message: Any = None,
        success: Any = None,
        reason: str = "OpenAI-compatible provider returned choices=null",
        diagnostic: Any = None,
    ) -> None:
        self.code = _bounded_field(code, limit=200)
        self.error_code = self.code
        self.provider_message = _bounded_field(message)
        self.success = success
        self.diagnostic = _bounded_field(diagnostic, limit=300)

        parts = [reason]
        if self.code:
            parts.append(f"code={self.code}")
        if self.provider_message:
            parts.append(f"msg={self.provider_message}")
        if success is not None:
            parts.append(f"success={success!r}")
        if self.diagnostic:
            parts.append(f"diagnostic={self.diagnostic}")
        super().__init__("; ".join(parts))


def _scalar_diagnostic(response: Any) -> str:
    """Return a bounded, content-free fingerprint for a malformed scalar."""

    if isinstance(response, str):
        payload = response.encode("utf-8", errors="replace")
    elif isinstance(response, (bytes, bytearray)):
        payload = bytes(response)
    else:
        payload = repr(type(response).__name__).encode("ascii")
    return f"type={type(response).__name__},length={len(payload)},sha256={hashlib.sha256(payload).hexdigest()[:16]}"


def _malformed_response_error(response: Any, *, detail: str) -> OpenAICompatibleResponseError:
    return OpenAICompatibleResponseError(
        code="MALFORMED_RESPONSE",
        message=detail,
        success=False,
        reason="OpenAI-compatible provider returned a malformed response",
        diagnostic=_scalar_diagnostic(response),
    )


def _coerce_response_object(response: Any) -> tuple[dict[str, Any], Any]:
    """Normalize OpenAI-compatible responses without leaking malformed bodies.

    The OpenAI SDK normally returns either a mapping or a Pydantic model. A few
    enterprise gateways occasionally return a JSON-encoded object as a string,
    especially when a streamed request is internally converted to a business
    error response. Accept that recoverable shape and turn every other scalar
    response into the same typed, bounded provider error used by the retry
    middleware.
    """

    if isinstance(response, dict):
        return response, response

    if isinstance(response, (str, bytes, bytearray)):
        try:
            original_text = response.decode("utf-8") if not isinstance(response, str) else response
        except UnicodeDecodeError as exc:
            raise _malformed_response_error(
                response,
                detail=(f"Expected UTF-8 JSON data but received {type(response).__name__} data."),
            ) from exc

        text = original_text.strip()
        parsed: Any = None
        for _ in range(_MAX_JSON_STRING_UNWRAP_DEPTH):
            if text.startswith("data:"):
                text = text.removeprefix("data:").lstrip()
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError as exc:
                raise _malformed_response_error(
                    response,
                    detail=(f"Expected a JSON object but received {type(response).__name__} data."),
                ) from exc
            if isinstance(parsed, dict):
                return parsed, parsed
            if not isinstance(parsed, str):
                raise _malformed_response_error(
                    response,
                    detail=(f"Expected a JSON object but decoded {type(parsed).__name__} data."),
                )
            text = parsed.strip()

        raise _malformed_response_error(
            response,
            detail=("Expected a JSON object but exceeded the bounded string unwrap depth."),
        )

    model_dump = getattr(response, "model_dump", None)
    if not callable(model_dump):
        raise OpenAICompatibleResponseError(
            code="MALFORMED_RESPONSE",
            message=f"Expected a mapping or model object but received {type(response).__name__} data.",
            success=False,
            reason="OpenAI-compatible provider returned a malformed response",
        )

    response_dict = model_dump()
    if not isinstance(response_dict, dict):
        raise OpenAICompatibleResponseError(
            code="MALFORMED_RESPONSE",
            message=f"Provider model_dump() returned {type(response_dict).__name__}, not a mapping.",
            success=False,
            reason="OpenAI-compatible provider returned a malformed response",
        )
    return response_dict, response


def _has_terminal_finish(chunk: dict[str, Any]) -> bool:
    choices = chunk.get("choices")
    if not isinstance(choices, list):
        return False
    return any(isinstance(choice, dict) and choice.get("finish_reason") is not None for choice in choices)


def _get_completion_id(chunk: Mapping[str, Any]) -> str | None:
    """Return a stable completion id when the provider supplied one."""

    candidates = [chunk.get("id")]
    nested_chunk = chunk.get("chunk")
    if isinstance(nested_chunk, Mapping):
        candidates.append(nested_chunk.get("id"))

    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate
    return None


class _StreamFrameGuard:
    """Quarantine malformed scalar frames until a legal stream end is observed.

    A malformed scalar is never converted into assistant content or a tool call.
    The stream is accepted only when a later standard OpenAI chunk carries a
    terminal ``finish_reason``. Otherwise the original typed error is raised at
    end-of-stream so the normal provider retry path remains authoritative.
    """

    def __init__(self, normalizer: Callable[[Any], dict[str, Any]]) -> None:
        self._normalizer = normalizer
        self._malformed_count = 0
        self._last_malformed: OpenAICompatibleResponseError | None = None
        self._terminal_seen = False

    def normalize(self, raw_chunk: Any) -> dict[str, Any] | None:
        try:
            chunk = self._normalizer(raw_chunk)
        except OpenAICompatibleResponseError as exc:
            if exc.code != "MALFORMED_RESPONSE":
                raise
            self._malformed_count += 1
            self._last_malformed = exc
            logger.warning(
                "Quarantined malformed OpenAI-compatible stream frame pending a legal terminal chunk: count=%d %s",
                self._malformed_count,
                exc.diagnostic,
            )
            return None

        if _has_terminal_finish(chunk):
            self._terminal_seen = True
        return chunk

    def mark_terminal(self) -> None:
        self._terminal_seen = True

    def raise_if_incomplete(self) -> None:
        if self._last_malformed is None or self._terminal_seen:
            return
        raise self._last_malformed


class RobustChatOpenAI(ChatOpenAI):
    """``ChatOpenAI`` hardened for non-standard enterprise response envelopes.

    The audited internal endpoint requires streaming for OpenAI tool calls:
    non-streaming tool responses are deterministically rejected by its safety
    gateway with business code ``20009``. Streaming therefore remains enabled.
    A small compatibility layer normalizes the occasional JSON-encoded scalar
    SSE chunk before LangChain sees it, avoiding the historical
    ``str.model_dump()`` crash without forcing all requests down the broken
    non-streaming tool path.
    """

    disable_streaming: bool | Literal["tool_calling"] = False
    cumulative_stream_usage: bool = Field(
        default=False,
        description=("Treat streaming usage snapshots from an OpenAI-compatible provider as cumulative per completion and convert them to per-chunk deltas."),
    )
    _cumulative_usage_by_completion: OrderedDict[str, tuple[UsageMetadata, float]] = PrivateAttr(default_factory=OrderedDict)
    _cumulative_usage_lock: Any = PrivateAttr(default_factory=threading.Lock)

    def _get_request_payload(
        self,
        input_: LanguageModelInput,
        *,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> dict:
        """Replay DeepSeek reasoning content for a following tool-call request.

        The updated DeepSeek endpoint emits ``reasoning_content`` separately from
        normal assistant content. LangChain retains it on ``AIMessage`` but does
        not serialize that provider extension back into the next request. Tool
        calling is multi-turn, so preserve it when the provider requires the
        complete preceding assistant turn.
        """

        original_messages = self._convert_input(input_).to_messages()
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        restore_assistant_payloads(
            payload.get("messages", []),
            original_messages,
            restore_reasoning_content,
        )
        return payload

    def _usage_delta(self, completion_id: str, usage: UsageMetadata, *, terminal: bool) -> UsageMetadata:
        """Convert a completion's cumulative usage snapshot into a delta."""

        with self._cumulative_usage_lock:
            previous_snapshot = self._cumulative_usage_by_completion.get(completion_id)
            previous = previous_snapshot[0] if previous_snapshot is not None else None
            delta = subtract_usage(usage, previous)
            if terminal:
                self._cumulative_usage_by_completion.pop(completion_id, None)
            else:
                now = time.monotonic()
                self._cumulative_usage_by_completion[completion_id] = (usage, now)
                self._cumulative_usage_by_completion.move_to_end(completion_id)
                while len(self._cumulative_usage_by_completion) > _CUMULATIVE_USAGE_TRACKER_CAPACITY:
                    oldest_completion_id, (_, updated_at) = next(iter(self._cumulative_usage_by_completion.items()))
                    if now - updated_at < _CUMULATIVE_USAGE_TRACKER_IDLE_SECONDS:
                        break
                    self._cumulative_usage_by_completion.pop(oldest_completion_id, None)
            return delta

    def _clear_usage_snapshot(self, completion_id: str) -> None:
        """Forget a completed stream even when its terminal frame has no usage."""

        with self._cumulative_usage_lock:
            self._cumulative_usage_by_completion.pop(completion_id, None)

    def _convert_chunk_to_generation_chunk(
        self,
        chunk: dict[str, Any],
        default_chunk_class: type[BaseMessageChunk],
        base_generation_info: dict[str, Any],
    ) -> ChatGenerationChunk | None:
        """Normalize opt-in cumulative usage after standard chunk conversion."""

        generation_chunk = super()._convert_chunk_to_generation_chunk(
            chunk,
            default_chunk_class,
            base_generation_info,
        )
        if generation_chunk is None:
            return generation_chunk

        if self.cumulative_stream_usage:
            completion_id = _get_completion_id(chunk)
            if completion_id is not None:
                choices = chunk.get("choices", []) or chunk.get("chunk", {}).get("choices", [])
                terminal = len(choices) == 0
                usage_metadata = getattr(generation_chunk.message, "usage_metadata", None)
                if usage_metadata is not None and isinstance(generation_chunk.message, AIMessageChunk):
                    generation_chunk.message.usage_metadata = self._usage_delta(
                        completion_id,
                        usage_metadata,
                        terminal=terminal,
                    )
                elif terminal:
                    self._clear_usage_snapshot(completion_id)

        choices = chunk.get("choices", []) or chunk.get("chunk", {}).get("choices", [])
        if choices and isinstance(generation_chunk.message, AIMessageChunk):
            first_choice = choices[0] if isinstance(choices[0], Mapping) else {}
            delta = first_choice.get("delta") if isinstance(first_choice, Mapping) else None
            reasoning_content = _extract_reasoning_content(delta)
            if reasoning_content is not _MISSING:
                generation_chunk = ChatGenerationChunk(
                    message=_with_reasoning_content(generation_chunk.message, reasoning_content),
                    generation_info=generation_chunk.generation_info,
                )
        return generation_chunk

    def _time_non_stream_invocation(self, kwargs: Mapping[str, Any]) -> bool:
        """Return whether this call bypasses this adapter's stream methods.

        The normal agent graph streams through ``_stream``/``_astream`` and is
        already timed there.  Explicit or configured non-stream requests use
        ``_generate``/``_agenerate`` instead and need their own wrapper.
        """

        return not bool(kwargs.get("stream")) and not bool(self.streaming)

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        """Time synchronous non-stream model invocations without content logs."""

        if not self._time_non_stream_invocation(kwargs):
            return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
        timing = _ModelStreamTiming.for_invocation(self, run_manager, mode="sync_nonstream")
        timing.start()
        try:
            result = super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
        except asyncio.CancelledError as exc:
            timing.finish(outcome="cancelled", error=exc)
            raise
        except BaseException as exc:
            timing.finish(outcome="error", error=exc)
            raise
        timing.observe_result(result)
        timing.finish(outcome="completed")
        return result

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        """Time asynchronous non-stream model invocations without content logs."""

        if not self._time_non_stream_invocation(kwargs):
            return await super()._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs)
        timing = _ModelStreamTiming.for_invocation(self, run_manager, mode="async_nonstream")
        timing.start()
        try:
            result = await super()._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs)
        except asyncio.CancelledError as exc:
            timing.finish(outcome="cancelled", error=exc)
            raise
        except BaseException as exc:
            timing.finish(outcome="error", error=exc)
            raise
        timing.observe_result(result)
        timing.finish(outcome="completed")
        return result

    @staticmethod
    def _normalize_stream_chunk(chunk: Any) -> dict[str, Any]:
        chunk_dict, _ = _coerce_response_object(chunk)
        if chunk_dict.get("choices") is None and any(key in chunk_dict for key in ("code", "msg", "success")):
            raise OpenAICompatibleResponseError(
                code=chunk_dict.get("code"),
                message=chunk_dict.get("msg"),
                success=chunk_dict.get("success"),
            )
        return chunk_dict

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        *,
        stream_usage: bool | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        """Stream while normalizing non-standard scalar SSE chunks."""

        self._ensure_sync_client_available()
        kwargs["stream"] = True
        stream_usage = self._should_stream_usage(stream_usage, **kwargs)
        if stream_usage:
            kwargs["stream_options"] = {"include_usage": stream_usage}
        payload = self._get_request_payload(messages, stop=stop, **kwargs)
        default_chunk_class: type[BaseMessageChunk] = AIMessageChunk
        base_generation_info: dict[str, Any] = {}
        response: Any = None
        frame_guard = _StreamFrameGuard(self._normalize_stream_chunk)
        timing = _ModelStreamTiming.for_invocation(self, run_manager, mode="sync")
        timing.start()

        try:
            if "response_format" in payload:
                if self.include_response_headers:
                    warnings.warn(
                        "Cannot currently include response headers when response_format is specified.",
                        stacklevel=2,
                    )
                payload.pop("stream")
                context_manager = self.root_client.beta.chat.completions.stream(**payload)
            else:
                if self.include_response_headers:
                    raw_response = self.client.with_raw_response.create(**payload)
                    response = raw_response.parse()
                    base_generation_info = {"headers": dict(raw_response.headers)}
                else:
                    response = self.client.create(**payload)
                context_manager = response
            with context_manager as response:
                is_first_chunk = True
                for raw_chunk in response:
                    chunk = frame_guard.normalize(raw_chunk)
                    if chunk is None:
                        continue
                    generation_chunk = self._convert_chunk_to_generation_chunk(
                        chunk,
                        default_chunk_class,
                        base_generation_info if is_first_chunk else {},
                    )
                    if generation_chunk is None:
                        continue
                    default_chunk_class = generation_chunk.message.__class__
                    timing.observe_chunk(generation_chunk)
                    logprobs = (generation_chunk.generation_info or {}).get("logprobs")
                    if run_manager:
                        run_manager.on_llm_new_token(
                            generation_chunk.text,
                            chunk=generation_chunk,
                            logprobs=logprobs,
                        )
                    is_first_chunk = False
                    yield generation_chunk
        except openai.BadRequestError as exc:
            timing.finish(outcome="error", error=exc)
            _handle_openai_bad_request(exc)
        except openai.APIError as exc:
            timing.finish(outcome="error", error=exc)
            _handle_openai_api_error(exc)
        except asyncio.CancelledError as exc:
            timing.finish(outcome="cancelled", error=exc)
            raise
        except BaseException as exc:
            timing.finish(outcome="error", error=exc)
            raise

        try:
            if hasattr(response, "get_final_completion") and "response_format" in payload:
                final_completion = response.get_final_completion()
                frame_guard.mark_terminal()
                generation_chunk = self._get_generation_chunk_from_completion(final_completion)
                timing.observe_chunk(generation_chunk)
                if run_manager:
                    run_manager.on_llm_new_token(generation_chunk.text, chunk=generation_chunk)
                yield generation_chunk
            frame_guard.raise_if_incomplete()
        except asyncio.CancelledError as exc:
            timing.finish(outcome="cancelled", error=exc)
            raise
        except BaseException as exc:
            timing.finish(outcome="error", error=exc)
            raise
        else:
            timing.finish(outcome="completed")

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        *,
        stream_usage: bool | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        """Queue only configured models before opening an outbound stream."""

        async with admit_model_stream(self.model_name):
            async with aclosing(
                self._astream_admitted(
                    messages,
                    stop=stop,
                    run_manager=run_manager,
                    stream_usage=stream_usage,
                    **kwargs,
                )
            ) as stream:
                async for chunk in stream:
                    yield chunk

    async def _astream_admitted(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        *,
        stream_usage: bool | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        """Async stream while normalizing non-standard scalar SSE chunks."""

        kwargs["stream"] = True
        stream_usage = self._should_stream_usage(stream_usage, **kwargs)
        if stream_usage:
            kwargs["stream_options"] = {"include_usage": stream_usage}
        payload = self._get_request_payload(messages, stop=stop, **kwargs)
        default_chunk_class: type[BaseMessageChunk] = AIMessageChunk
        base_generation_info: dict[str, Any] = {}
        response: Any = None
        frame_guard = _StreamFrameGuard(self._normalize_stream_chunk)
        timing = _ModelStreamTiming.for_invocation(self, run_manager, mode="async")
        timing.start()

        try:
            if "response_format" in payload:
                if self.include_response_headers:
                    warnings.warn(
                        "Cannot currently include response headers when response_format is specified.",
                        stacklevel=2,
                    )
                payload.pop("stream")
                context_manager = self.root_async_client.beta.chat.completions.stream(**payload)
            else:
                if self.include_response_headers:
                    raw_response = await self.async_client.with_raw_response.create(**payload)
                    response = raw_response.parse()
                    base_generation_info = {"headers": dict(raw_response.headers)}
                else:
                    response = await self.async_client.create(**payload)
                context_manager = response
            async with context_manager as response:
                is_first_chunk = True
                source_chunks = _astream_with_chunk_timeout(
                    response,
                    self.stream_chunk_timeout,
                    model_name=self.model_name,
                )
                if timing.enabled:
                    source_chunks = _timed_async_stream_chunks(source_chunks, timing)
                async with _closing_async_iterator(source_chunks, close_on_exit=timing.enabled) as raw_chunks:
                    async for raw_chunk in raw_chunks:
                        chunk = frame_guard.normalize(raw_chunk)
                        if chunk is None:
                            continue
                        generation_chunk = self._convert_chunk_to_generation_chunk(
                            chunk,
                            default_chunk_class,
                            base_generation_info if is_first_chunk else {},
                        )
                        if generation_chunk is None:
                            continue
                        default_chunk_class = generation_chunk.message.__class__
                        timing.observe_chunk(generation_chunk)
                        logprobs = (generation_chunk.generation_info or {}).get("logprobs")
                        if run_manager:
                            callback_started = time.monotonic() if timing.enabled else None
                            try:
                                await run_manager.on_llm_new_token(
                                    generation_chunk.text,
                                    chunk=generation_chunk,
                                    logprobs=logprobs,
                                )
                            finally:
                                if callback_started is not None:
                                    timing.observe_callback(time.monotonic() - callback_started)
                        is_first_chunk = False
                        resume_started = time.monotonic() if timing.enabled else None
                        try:
                            yield generation_chunk
                        finally:
                            if resume_started is not None:
                                timing.observe_downstream_resume(time.monotonic() - resume_started)
        except openai.BadRequestError as exc:
            timing.finish(outcome="error", error=exc)
            _handle_openai_bad_request(exc)
        except openai.APIError as exc:
            timing.finish(outcome="error", error=exc)
            _handle_openai_api_error(exc)
        except asyncio.CancelledError as exc:
            timing.finish(outcome="cancelled", error=exc)
            raise
        except BaseException as exc:
            timing.finish(outcome="error", error=exc)
            raise

        try:
            if hasattr(response, "get_final_completion") and "response_format" in payload:
                final_completion = await response.get_final_completion()
                frame_guard.mark_terminal()
                generation_chunk = self._get_generation_chunk_from_completion(final_completion)
                timing.observe_chunk(generation_chunk)
                if run_manager:
                    callback_started = time.monotonic() if timing.enabled else None
                    try:
                        await run_manager.on_llm_new_token(generation_chunk.text, chunk=generation_chunk)
                    finally:
                        if callback_started is not None:
                            timing.observe_callback(time.monotonic() - callback_started)
                resume_started = time.monotonic() if timing.enabled else None
                try:
                    yield generation_chunk
                finally:
                    if resume_started is not None:
                        timing.observe_downstream_resume(time.monotonic() - resume_started)
            frame_guard.raise_if_incomplete()
        except asyncio.CancelledError as exc:
            timing.finish(outcome="cancelled", error=exc)
            raise
        except BaseException as exc:
            timing.finish(outcome="error", error=exc)
            raise
        else:
            timing.finish(outcome="completed")

    def _create_chat_result(
        self,
        response: dict | Any,
        generation_info: dict | None = None,
    ) -> ChatResult:
        response_dict, normalized_response = _coerce_response_object(response)
        if response_dict.get("choices") is None and any(key in response_dict for key in ("code", "msg", "success")):
            raise OpenAICompatibleResponseError(
                code=response_dict.get("code"),
                message=response_dict.get("msg"),
                success=response_dict.get("success"),
            )
        result = super()._create_chat_result(normalized_response, generation_info)
        choices = response_dict.get("choices", [])
        if not isinstance(choices, list):
            return result

        patched_generations: list[ChatGeneration] | None = None
        for index, generation in enumerate(result.generations):
            choice = choices[index] if index < len(choices) else {}
            message_payload = choice.get("message") if isinstance(choice, Mapping) else None
            reasoning_content = _extract_reasoning_content(message_payload)
            if reasoning_content is _MISSING or not isinstance(generation.message, AIMessage):
                continue
            if patched_generations is None:
                patched_generations = list(result.generations)
            patched_generations[index] = ChatGeneration(
                message=_with_reasoning_content(generation.message, reasoning_content),
                generation_info=generation.generation_info,
            )

        return ChatResult(
            generations=patched_generations or result.generations,
            llm_output=result.llm_output,
        )
