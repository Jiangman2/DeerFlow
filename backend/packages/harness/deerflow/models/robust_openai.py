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

import json
import warnings
from collections.abc import AsyncIterator, Iterator
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
from langchain_core.outputs import ChatGenerationChunk, ChatResult
from langchain_openai import ChatOpenAI
from langchain_openai.chat_models.base import (
    _astream_with_chunk_timeout,
    _handle_openai_api_error,
    _handle_openai_bad_request,
)

_MAX_PROVIDER_MESSAGE_CHARS = 1000


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
    ) -> None:
        self.code = _bounded_field(code, limit=200)
        self.error_code = self.code
        self.provider_message = _bounded_field(message)
        self.success = success

        parts = [reason]
        if self.code:
            parts.append(f"code={self.code}")
        if self.provider_message:
            parts.append(f"msg={self.provider_message}")
        if success is not None:
            parts.append(f"success={success!r}")
        super().__init__("; ".join(parts))


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
            text = response.decode("utf-8") if not isinstance(response, str) else response
            parsed = json.loads(text)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OpenAICompatibleResponseError(
                code="MALFORMED_RESPONSE",
                message=f"Expected a JSON object but received {type(response).__name__} data.",
                success=False,
                reason="OpenAI-compatible provider returned a malformed response",
            ) from exc
        if not isinstance(parsed, dict):
            raise OpenAICompatibleResponseError(
                code="MALFORMED_RESPONSE",
                message=f"Expected a JSON object but decoded {type(parsed).__name__} data.",
                success=False,
                reason="OpenAI-compatible provider returned a malformed response",
            )
        return parsed, parsed

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

    @staticmethod
    def _normalize_stream_chunk(chunk: Any) -> dict[str, Any]:
        chunk_dict, _ = _coerce_response_object(chunk)
        if chunk_dict.get("choices") is None and any(
            key in chunk_dict for key in ("code", "msg", "success")
        ):
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

        try:
            if "response_format" in payload:
                if self.include_response_headers:
                    warnings.warn(
                        "Cannot currently include response headers when "
                        "response_format is specified.",
                        stacklevel=2,
                    )
                payload.pop("stream")
                context_manager = self.root_client.beta.chat.completions.stream(
                    **payload
                )
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
                    chunk = self._normalize_stream_chunk(raw_chunk)
                    generation_chunk = self._convert_chunk_to_generation_chunk(
                        chunk,
                        default_chunk_class,
                        base_generation_info if is_first_chunk else {},
                    )
                    if generation_chunk is None:
                        continue
                    default_chunk_class = generation_chunk.message.__class__
                    logprobs = (generation_chunk.generation_info or {}).get(
                        "logprobs"
                    )
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
            generation_chunk = self._get_generation_chunk_from_completion(
                final_completion
            )
            if run_manager:
                run_manager.on_llm_new_token(
                    generation_chunk.text, chunk=generation_chunk
                )
            yield generation_chunk

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

        try:
            if "response_format" in payload:
                if self.include_response_headers:
                    warnings.warn(
                        "Cannot currently include response headers when "
                        "response_format is specified.",
                        stacklevel=2,
                    )
                payload.pop("stream")
                context_manager = (
                    self.root_async_client.beta.chat.completions.stream(**payload)
                )
            else:
                if self.include_response_headers:
                    raw_response = await self.async_client.with_raw_response.create(
                        **payload
                    )
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
                    chunk = self._normalize_stream_chunk(raw_chunk)
                    generation_chunk = self._convert_chunk_to_generation_chunk(
                        chunk,
                        default_chunk_class,
                        base_generation_info if is_first_chunk else {},
                    )
                    if generation_chunk is None:
                        continue
                    default_chunk_class = generation_chunk.message.__class__
                    logprobs = (generation_chunk.generation_info or {}).get(
                        "logprobs"
                    )
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
            generation_chunk = self._get_generation_chunk_from_completion(
                final_completion
            )
            if run_manager:
                await run_manager.on_llm_new_token(
                    generation_chunk.text, chunk=generation_chunk
                )
            yield generation_chunk

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
