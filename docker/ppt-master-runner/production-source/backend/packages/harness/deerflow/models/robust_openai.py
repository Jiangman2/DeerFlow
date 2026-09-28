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

import hashlib
import json
import logging
import threading
import time
import warnings
from collections import OrderedDict
from collections.abc import AsyncIterator, Callable, Iterator, Mapping
from typing import Any, Literal

import openai
from langchain_core.callbacks import (
    AsyncCallbackManagerForLLMRun,
    CallbackManagerForLLMRun,
)
from langchain_core.messages import (
    AIMessageChunk,
    BaseMessage,
    BaseMessageChunk,
)
from langchain_core.messages.ai import UsageMetadata, subtract_usage
from langchain_core.outputs import ChatGenerationChunk, ChatResult
from langchain_openai import ChatOpenAI
from langchain_openai.chat_models.base import (
    _astream_with_chunk_timeout,
    _handle_openai_api_error,
    _handle_openai_bad_request,
)
from pydantic import Field, PrivateAttr

_MAX_PROVIDER_MESSAGE_CHARS = 1000
_MAX_JSON_STRING_UNWRAP_DEPTH = 3
_CUMULATIVE_USAGE_TRACKER_CAPACITY = 1024
_CUMULATIVE_USAGE_TRACKER_IDLE_SECONDS = 60 * 60

logger = logging.getLogger(__name__)


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
        if generation_chunk is None or not self.cumulative_stream_usage:
            return generation_chunk

        completion_id = _get_completion_id(chunk)
        if completion_id is None:
            return generation_chunk

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
        return generation_chunk

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
            _handle_openai_bad_request(exc)
        except openai.APIError as exc:
            _handle_openai_api_error(exc)
        if hasattr(response, "get_final_completion") and "response_format" in payload:
            final_completion = response.get_final_completion()
            frame_guard.mark_terminal()
            generation_chunk = self._get_generation_chunk_from_completion(final_completion)
            if run_manager:
                run_manager.on_llm_new_token(generation_chunk.text, chunk=generation_chunk)
            yield generation_chunk
        frame_guard.raise_if_incomplete()

    async def _astream(
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
                async for raw_chunk in _astream_with_chunk_timeout(
                    response,
                    self.stream_chunk_timeout,
                    model_name=self.model_name,
                ):
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
                    logprobs = (generation_chunk.generation_info or {}).get("logprobs")
                    if run_manager:
                        await run_manager.on_llm_new_token(
                            generation_chunk.text,
                            chunk=generation_chunk,
                            logprobs=logprobs,
                        )
                    is_first_chunk = False
                    yield generation_chunk
        except openai.BadRequestError as exc:
            _handle_openai_bad_request(exc)
        except openai.APIError as exc:
            _handle_openai_api_error(exc)
        if hasattr(response, "get_final_completion") and "response_format" in payload:
            final_completion = await response.get_final_completion()
            frame_guard.mark_terminal()
            generation_chunk = self._get_generation_chunk_from_completion(final_completion)
            if run_manager:
                await run_manager.on_llm_new_token(generation_chunk.text, chunk=generation_chunk)
            yield generation_chunk
        frame_guard.raise_if_incomplete()

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
        return super()._create_chat_result(normalized_response, generation_info)
