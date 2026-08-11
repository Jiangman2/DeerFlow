"""OpenAI Responses compatibility bridge for the enterprise DeepSeek endpoint.

The Codex CLI only speaks the Responses wire protocol.  The approved CNOOC
DeepSeek service speaks streamed Chat Completions.  This module keeps that
translation inside Gateway so a provisioned Sandbox receives only a scoped
relay credential, never the upstream model credential.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/codex/deepseek/v1", tags=["codex-responses"])

_DEFAULT_MODEL = "DeepSeek-V4-Flash"
_DEFAULT_UPSTREAM_URL = (
    "http://model.ai.cnooc/member1/deepseek-v4-flash-291b-1m/v1/chat/completions"
)
_DEFAULT_MAX_REQUEST_BYTES = 32 * 1024 * 1024
_DEFAULT_MAX_OUTPUT_TOKENS = 65536
_RETRYABLE_HTTP_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})
_RETRYABLE_BUSINESS_CODES = frozenset(
    {
        "10020",
        "MODEL_BUSY",
        "RATE_LIMITED",
        "SERVICE_BUSY",
        "TEMPORARILY_UNAVAILABLE",
    }
)


class AdapterRequestError(ValueError):
    """A bounded, client-safe Responses request validation error."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "invalid_request",
        param: str | None = None,
        status_code: int = 400,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.param = param
        self.status_code = status_code


class DeepSeekUpstreamError(RuntimeError):
    """A sanitized upstream failure suitable for a Responses failed event."""

    def __init__(self, message: str, *, code: str, retryable: bool = False) -> None:
        super().__init__(message[:1000])
        self.code = str(code)[:128]
        self.retryable = retryable


@dataclass(frozen=True)
class ConvertedRequest:
    payload: dict[str, Any]
    tool_kinds: dict[str, str]


@dataclass
class ToolCallBuffer:
    call_id: str = ""
    name: str = ""
    arguments: list[str] = field(default_factory=list)

    def append(self, payload: dict[str, Any]) -> None:
        call_id = payload.get("id")
        if isinstance(call_id, str) and call_id:
            self.call_id = call_id
        function = payload.get("function")
        if not isinstance(function, dict):
            return
        name = function.get("name")
        if isinstance(name, str) and name:
            self.name = name
        arguments = function.get("arguments")
        if isinstance(arguments, str):
            self.arguments.append(arguments)
        elif isinstance(arguments, dict):
            self.arguments.append(json.dumps(arguments, ensure_ascii=False, separators=(",", ":")))

    @property
    def arguments_text(self) -> str:
        return "".join(self.arguments) or "{}"


@dataclass
class DeepSeekCompletion:
    model: str
    content: list[str] = field(default_factory=list)
    reasoning_content: list[str] = field(default_factory=list)
    tool_calls: dict[int, ToolCallBuffer] = field(default_factory=dict)
    finish_reason: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)

    def consume(self, payload: dict[str, Any]) -> None:
        if _is_business_error(payload):
            code = str(payload.get("code") or "UPSTREAM_ERROR")
            message = str(payload.get("msg") or payload.get("message") or "DeepSeek returned an error.")
            raise DeepSeekUpstreamError(
                message,
                code=code,
                retryable=code.upper() in _RETRYABLE_BUSINESS_CODES,
            )

        model = payload.get("model")
        if isinstance(model, str) and model:
            self.model = model
        usage = payload.get("usage")
        if isinstance(usage, dict):
            self.usage = usage

        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            return
        choice = choices[0]
        if not isinstance(choice, dict):
            return
        finish_reason = choice.get("finish_reason")
        if isinstance(finish_reason, str) and finish_reason:
            self.finish_reason = finish_reason

        message = choice.get("delta")
        if not isinstance(message, dict):
            message = choice.get("message")
        if not isinstance(message, dict):
            return

        content = message.get("content")
        if isinstance(content, str) and content:
            self.content.append(content)
        reasoning = message.get("reasoning_content")
        if isinstance(reasoning, str) and reasoning:
            self.reasoning_content.append(reasoning)

        tool_calls = message.get("tool_calls")
        if not isinstance(tool_calls, list):
            return
        for fallback_index, tool_call in enumerate(tool_calls):
            if not isinstance(tool_call, dict):
                continue
            raw_index = tool_call.get("index", fallback_index)
            index = raw_index if isinstance(raw_index, int) and raw_index >= 0 else fallback_index
            self.tool_calls.setdefault(index, ToolCallBuffer()).append(tool_call)


def _int_env(name: str, default: int, *, minimum: int = 1) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return max(minimum, int(raw))
    except ValueError:
        logger.warning("Ignoring invalid integer environment value for %s.", name)
        return default


def _float_env(name: str, default: float, *, minimum: float = 0.1) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return max(minimum, float(raw))
    except ValueError:
        logger.warning("Ignoring invalid floating-point environment value for %s.", name)
        return default


def _configured_model() -> str:
    return os.getenv("PPT_MASTER_DEEPSEEK_MODEL", _DEFAULT_MODEL).strip() or _DEFAULT_MODEL


def _relay_key() -> str:
    value = os.getenv("PPT_MASTER_RESPONSES_RELAY_KEY", "").strip()
    if not value:
        raise AdapterRequestError(
            "PPT Master Responses relay is not configured.",
            code="relay_not_configured",
            status_code=503,
        )
    return value


def _upstream_key() -> str:
    value = os.getenv("DeepSeek_V4_API_KEY", "").strip()
    if not value:
        raise DeepSeekUpstreamError(
            "Enterprise DeepSeek credentials are not configured.",
            code="upstream_not_configured",
        )
    return value


def _require_relay_auth(request: Request) -> None:
    expected = _relay_key()
    scheme, _, provided = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not provided or not hmac.compare_digest(provided.strip(), expected):
        raise AdapterRequestError(
            "Invalid PPT Master Responses relay credentials.",
            code="invalid_api_key",
            status_code=401,
        )


def _error_response(error: AdapterRequestError) -> JSONResponse:
    return JSONResponse(
        status_code=error.status_code,
        content={
            "error": {
                "message": str(error),
                "type": "invalid_request_error" if error.status_code < 500 else "server_error",
                "param": error.param,
                "code": error.code,
            }
        },
    )


async def _read_request_json(request: Request) -> dict[str, Any]:
    max_bytes = _int_env(
        "PPT_MASTER_RESPONSES_MAX_REQUEST_BYTES",
        _DEFAULT_MAX_REQUEST_BYTES,
    )
    content_length = request.headers.get("content-length", "").strip()
    if content_length:
        try:
            if int(content_length) > max_bytes:
                raise AdapterRequestError(
                    f"Responses request exceeds the {max_bytes}-byte limit.",
                    code="request_too_large",
                    status_code=413,
                )
        except ValueError:
            pass
    body = await request.body()
    if not body:
        raise AdapterRequestError("Responses request body is empty.", param="body")
    if len(body) > max_bytes:
        raise AdapterRequestError(
            f"Responses request exceeds the {max_bytes}-byte limit.",
            code="request_too_large",
            status_code=413,
        )
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AdapterRequestError("Responses request body must be valid JSON.", param="body") from exc
    if not isinstance(payload, dict):
        raise AdapterRequestError("Responses request body must be a JSON object.", param="body")
    return payload


def _content_text(content: object, *, param: str) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        raise AdapterRequestError("Message content must be text or a text block array.", param=param)
    parts: list[str] = []
    for index, block in enumerate(content):
        if isinstance(block, str):
            parts.append(block)
            continue
        if not isinstance(block, dict):
            raise AdapterRequestError("Message content block must be an object.", param=f"{param}[{index}]")
        block_type = block.get("type")
        if block_type not in {"input_text", "output_text", "text"}:
            raise AdapterRequestError(
                f"Unsupported Responses content block type: {block_type!r}.",
                code="unsupported_content_type",
                param=f"{param}[{index}].type",
            )
        text = block.get("text")
        if not isinstance(text, str):
            raise AdapterRequestError("Text content block is missing text.", param=f"{param}[{index}].text")
        parts.append(text)
    return "".join(parts)


def _append_assistant_tool_call(messages: list[dict[str, Any]], tool_call: dict[str, Any]) -> None:
    if messages and messages[-1].get("role") == "assistant":
        assistant = messages[-1]
    else:
        assistant = {"role": "assistant", "content": ""}
        messages.append(assistant)
    assistant.setdefault("tool_calls", []).append(tool_call)


def _convert_input_items(input_value: object, instructions: str) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    if instructions:
        messages.append({"role": "system", "content": instructions})
    if isinstance(input_value, str):
        messages.append({"role": "user", "content": input_value})
        return messages
    if not isinstance(input_value, list):
        raise AdapterRequestError("Responses input must be text or an item array.", param="input")

    for index, item in enumerate(input_value):
        param = f"input[{index}]"
        if not isinstance(item, dict):
            raise AdapterRequestError("Responses input item must be an object.", param=param)
        item_type = item.get("type")
        if item_type in {None, "message"} and isinstance(item.get("role"), str):
            role = str(item["role"])
            if role in {"developer", "system"}:
                role = "system"
            if role not in {"system", "user", "assistant"}:
                raise AdapterRequestError(f"Unsupported message role: {role!r}.", param=f"{param}.role")
            messages.append(
                {
                    "role": role,
                    "content": _content_text(item.get("content", ""), param=f"{param}.content"),
                }
            )
            continue
        if item_type in {"reasoning", "item_reference"}:
            # This bridge never creates opaque reasoning or remote item references.
            # Ignoring replay-only metadata is safer than exposing chain-of-thought.
            continue
        if item_type in {"function_call", "custom_tool_call"}:
            name = item.get("name")
            call_id = item.get("call_id")
            if not isinstance(name, str) or not name or not isinstance(call_id, str) or not call_id:
                raise AdapterRequestError("Tool call input is missing name or call_id.", param=param)
            if item_type == "custom_tool_call":
                raw_input = item.get("input", "")
                if not isinstance(raw_input, str):
                    raise AdapterRequestError("Custom tool input must be text.", param=f"{param}.input")
                arguments = json.dumps({"input": raw_input}, ensure_ascii=False, separators=(",", ":"))
            else:
                raw_arguments = item.get("arguments", "{}")
                arguments = (
                    json.dumps(raw_arguments, ensure_ascii=False, separators=(",", ":"))
                    if isinstance(raw_arguments, dict)
                    else raw_arguments
                )
                if not isinstance(arguments, str):
                    raise AdapterRequestError("Function arguments must be JSON text.", param=f"{param}.arguments")
            _append_assistant_tool_call(
                messages,
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": arguments},
                },
            )
            continue
        if item_type in {"function_call_output", "custom_tool_call_output"}:
            call_id = item.get("call_id")
            if not isinstance(call_id, str) or not call_id:
                raise AdapterRequestError("Tool output is missing call_id.", param=f"{param}.call_id")
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": _content_text(item.get("output", ""), param=f"{param}.output"),
                }
            )
            continue
        raise AdapterRequestError(
            f"Unsupported Responses input item type: {item_type!r}.",
            code="unsupported_input_item",
            param=f"{param}.type",
        )
    return messages


def _convert_tools(tools_value: object) -> tuple[list[dict[str, Any]], dict[str, str]]:
    if tools_value is None:
        return [], {}
    if not isinstance(tools_value, list):
        raise AdapterRequestError("Responses tools must be an array.", param="tools")
    tools: list[dict[str, Any]] = []
    tool_kinds: dict[str, str] = {}
    for index, tool in enumerate(tools_value):
        param = f"tools[{index}]"
        if not isinstance(tool, dict):
            raise AdapterRequestError("Tool definition must be an object.", param=param)
        tool_type = tool.get("type")
        if tool_type not in {"function", "custom"}:
            raise AdapterRequestError(
                f"Unsupported Responses tool type: {tool_type!r}.",
                code="unsupported_tool_type",
                param=f"{param}.type",
            )
        name = tool.get("name")
        if not isinstance(name, str) or not name:
            raise AdapterRequestError("Tool definition is missing name.", param=f"{param}.name")
        if name in tool_kinds:
            raise AdapterRequestError("Tool names must be unique.", param=f"{param}.name")
        description = tool.get("description")
        if not isinstance(description, str):
            description = ""
        if tool_type == "custom":
            parameters = {
                "type": "object",
                "properties": {"input": {"type": "string", "description": "Raw custom tool input."}},
                "required": ["input"],
                "additionalProperties": False,
            }
            description = f"{description}\nReturn the complete raw tool input in the JSON string field `input`.".strip()
        else:
            parameters = tool.get("parameters")
            if not isinstance(parameters, dict):
                parameters = {"type": "object", "properties": {}}
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": parameters,
                },
            }
        )
        tool_kinds[name] = tool_type
    return tools, tool_kinds


def _convert_tool_choice(value: object, tool_kinds: dict[str, str]) -> object:
    if value is None:
        return "auto"
    if isinstance(value, str) and value in {"auto", "none", "required"}:
        return value
    if isinstance(value, dict) and value.get("type") in {"function", "custom"}:
        name = value.get("name")
        if not isinstance(name, str) or name not in tool_kinds:
            raise AdapterRequestError("tool_choice references an unknown tool.", param="tool_choice.name")
        return {"type": "function", "function": {"name": name}}
    raise AdapterRequestError("Unsupported Responses tool_choice.", param="tool_choice")


def convert_responses_request(payload: dict[str, Any]) -> ConvertedRequest:
    if payload.get("stream") is not True:
        raise AdapterRequestError(
            "The enterprise DeepSeek bridge requires stream=true.",
            code="streaming_required",
            param="stream",
        )
    model = payload.get("model")
    configured_model = _configured_model()
    if model != configured_model:
        raise AdapterRequestError(
            f"Unsupported model {model!r}; expected {configured_model!r}.",
            code="model_not_found",
            param="model",
        )
    if payload.get("previous_response_id"):
        raise AdapterRequestError(
            "previous_response_id is unsupported; Codex must replay durable input items.",
            code="unsupported_previous_response_id",
            param="previous_response_id",
        )

    instructions = payload.get("instructions", "")
    if not isinstance(instructions, str):
        raise AdapterRequestError("Responses instructions must be text.", param="instructions")
    tools, tool_kinds = _convert_tools(payload.get("tools"))
    messages = _convert_input_items(payload.get("input", []), instructions)
    if not messages:
        raise AdapterRequestError("Responses input produced no model messages.", param="input")

    requested_max = payload.get("max_output_tokens", _DEFAULT_MAX_OUTPUT_TOKENS)
    if not isinstance(requested_max, int) or isinstance(requested_max, bool) or requested_max < 1:
        raise AdapterRequestError("max_output_tokens must be a positive integer.", param="max_output_tokens")
    max_output = min(
        requested_max,
        _int_env("PPT_MASTER_DEEPSEEK_MAX_OUTPUT_TOKENS", _DEFAULT_MAX_OUTPUT_TOKENS),
    )
    reasoning = payload.get("reasoning")
    effort = "high"
    if isinstance(reasoning, dict) and isinstance(reasoning.get("effort"), str):
        requested_effort = reasoning["effort"]
        if requested_effort in {"none", "minimal", "low"}:
            effort = "low"
        elif requested_effort in {"medium", "high"}:
            effort = "high"
        elif requested_effort in {"xhigh", "max"}:
            effort = "max"

    upstream: dict[str, Any] = {
        "model": configured_model,
        "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": True},
        "max_tokens": max_output,
        "temperature": 0.0,
        "enable_thinking": effort != "low",
        "reasoning_effort": effort,
    }
    if tools:
        upstream["tools"] = tools
        upstream["tool_choice"] = _convert_tool_choice(payload.get("tool_choice"), tool_kinds)
        upstream["parallel_tool_calls"] = bool(payload.get("parallel_tool_calls", True))
    return ConvertedRequest(payload=upstream, tool_kinds=tool_kinds)


def _is_business_error(payload: dict[str, Any]) -> bool:
    if payload.get("choices") is not None:
        return False
    return any(key in payload for key in ("code", "msg", "success", "error"))


def _parse_upstream_line(line: str) -> dict[str, Any] | None:
    raw = line.strip()
    if not raw or raw.startswith((":", "event:", "id:")):
        return None
    if raw.startswith("data:"):
        raw = raw[5:].strip()
    if not raw or raw == "[DONE]":
        return None
    try:
        payload: object = json.loads(raw)
        if isinstance(payload, str):
            payload = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise DeepSeekUpstreamError(
            "Enterprise DeepSeek returned malformed stream data.",
            code="malformed_upstream_response",
            retryable=True,
        ) from exc
    if not isinstance(payload, dict):
        raise DeepSeekUpstreamError(
            "Enterprise DeepSeek returned a non-object stream item.",
            code="malformed_upstream_response",
            retryable=True,
        )
    return payload


def _validate_completion(completion: DeepSeekCompletion) -> None:
    if completion.finish_reason == "content_filter":
        raise DeepSeekUpstreamError("Enterprise DeepSeek blocked the response.", code="content_filter")
    if completion.finish_reason == "length":
        raise DeepSeekUpstreamError(
            "Enterprise DeepSeek exhausted max_tokens before completing the turn.",
            code="max_output_tokens",
        )
    if not completion.content and not completion.tool_calls:
        raise DeepSeekUpstreamError(
            "Enterprise DeepSeek completed without text or tool calls.",
            code="empty_response",
            retryable=True,
        )
    for tool_call in completion.tool_calls.values():
        if not tool_call.name or not tool_call.call_id:
            raise DeepSeekUpstreamError("Enterprise DeepSeek returned an incomplete tool call.", code="invalid_tool_call")
        try:
            arguments = json.loads(tool_call.arguments_text)
        except json.JSONDecodeError as exc:
            raise DeepSeekUpstreamError(
                "Enterprise DeepSeek returned malformed tool arguments.",
                code="invalid_tool_arguments",
            ) from exc
        if not isinstance(arguments, dict):
            raise DeepSeekUpstreamError(
                "Enterprise DeepSeek tool arguments must be a JSON object.",
                code="invalid_tool_arguments",
            )


def _usage_payload(usage: dict[str, Any]) -> dict[str, Any]:
    input_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
    result: dict[str, Any] = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": int(usage.get("total_tokens") or input_tokens + output_tokens),
        "input_tokens_details": {"cached_tokens": 0},
    }
    details = usage.get("completion_tokens_details")
    if isinstance(details, dict) and isinstance(details.get("reasoning_tokens"), int):
        result["output_tokens_details"] = {"reasoning_tokens": details["reasoning_tokens"]}
    return result


def _response_output(completion: DeepSeekCompletion, tool_kinds: dict[str, str]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    text = "".join(completion.content)
    if text:
        output.append(
            {
                "id": f"msg_{uuid.uuid4().hex}",
                "type": "message",
                "status": "completed",
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "text": text,
                        "annotations": [],
                    }
                ],
            }
        )
    for index in sorted(completion.tool_calls):
        tool_call = completion.tool_calls[index]
        kind = tool_kinds.get(tool_call.name, "function")
        if kind == "custom":
            arguments = json.loads(tool_call.arguments_text)
            raw_input = arguments.get("input")
            if not isinstance(raw_input, str):
                raise DeepSeekUpstreamError(
                    "Enterprise DeepSeek custom tool call is missing the input string.",
                    code="invalid_custom_tool_input",
                )
            output.append(
                {
                    "id": f"ctc_{uuid.uuid4().hex}",
                    "type": "custom_tool_call",
                    "status": "completed",
                    "call_id": tool_call.call_id,
                    "name": tool_call.name,
                    "input": raw_input,
                }
            )
        else:
            output.append(
                {
                    "id": f"fc_{uuid.uuid4().hex}",
                    "type": "function_call",
                    "status": "completed",
                    "call_id": tool_call.call_id,
                    "name": tool_call.name,
                    "arguments": tool_call.arguments_text,
                }
            )
    return output


def _base_response(response_id: str, model: str, *, status: str, output: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": response_id,
        "object": "response",
        "created_at": int(time.time()),
        "status": status,
        "model": model,
        "output": output,
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": [],
    }


def _sse(event: dict[str, Any]) -> str:
    event_type = str(event.get("type") or "message")
    return f"event: {event_type}\ndata: {json.dumps(event, ensure_ascii=False, separators=(',', ':'))}\n\n"


def _completed_events(
    response_id: str,
    completion: DeepSeekCompletion,
    tool_kinds: dict[str, str],
    sequence_number: int,
) -> list[str]:
    output = _response_output(completion, tool_kinds)
    events: list[str] = []
    sequence = sequence_number
    for output_index, item in enumerate(output):
        added = dict(item)
        added["status"] = "in_progress"
        if added["type"] == "message":
            added["content"] = []
        events.append(
            _sse(
                {
                    "type": "response.output_item.added",
                    "sequence_number": sequence,
                    "output_index": output_index,
                    "item": added,
                }
            )
        )
        sequence += 1
        events.append(
            _sse(
                {
                    "type": "response.output_item.done",
                    "sequence_number": sequence,
                    "output_index": output_index,
                    "item": item,
                }
            )
        )
        sequence += 1
    response = _base_response(response_id, completion.model, status="completed", output=output)
    response["completed_at"] = int(time.time())
    response["usage"] = _usage_payload(completion.usage)
    events.append(
        _sse(
            {
                "type": "response.completed",
                "sequence_number": sequence,
                "response": response,
            }
        )
    )
    return events


def _failed_event(response_id: str, model: str, error: DeepSeekUpstreamError, sequence_number: int) -> str:
    response = _base_response(response_id, model, status="failed", output=[])
    response["error"] = {
        "code": error.code,
        "message": str(error),
        "type": "server_error",
    }
    return _sse(
        {
            "type": "response.failed",
            "sequence_number": sequence_number,
            "response": response,
        }
    )


async def stream_deepseek_responses(converted: ConvertedRequest):
    """Yield one Responses SSE turn while consuming DeepSeek's streamed chat turn."""

    response_id = f"resp_{uuid.uuid4().hex}"
    model = str(converted.payload["model"])
    sequence = 0
    created = _base_response(response_id, model, status="in_progress", output=[])
    yield _sse({"type": "response.created", "sequence_number": sequence, "response": created})
    sequence += 1
    yield _sse({"type": "response.in_progress", "sequence_number": sequence, "response": created})
    sequence += 1

    upstream_url = os.getenv("PPT_MASTER_DEEPSEEK_UPSTREAM_URL", _DEFAULT_UPSTREAM_URL).strip()
    attempts = _int_env("PPT_MASTER_DEEPSEEK_ATTEMPTS", 2)
    timeout = httpx.Timeout(
        connect=_float_env("PPT_MASTER_DEEPSEEK_CONNECT_TIMEOUT_SECONDS", 15.0),
        read=_float_env("PPT_MASTER_DEEPSEEK_READ_TIMEOUT_SECONDS", 900.0),
        write=_float_env("PPT_MASTER_DEEPSEEK_WRITE_TIMEOUT_SECONDS", 60.0),
        pool=_float_env("PPT_MASTER_DEEPSEEK_POOL_TIMEOUT_SECONDS", 15.0),
    )
    last_error: DeepSeekUpstreamError | None = None
    for attempt in range(1, attempts + 1):
        completion = DeepSeekCompletion(model=model)
        last_progress = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
                async with client.stream(
                    "POST",
                    upstream_url,
                    headers={
                        "Authorization": f"Bearer {_upstream_key()}",
                        "Content-Type": "application/json",
                        "Accept": "text/event-stream",
                        "Accept-Encoding": "identity",
                    },
                    json=converted.payload,
                ) as response:
                    if response.status_code >= 400:
                        await response.aread()
                        raise DeepSeekUpstreamError(
                            f"Enterprise DeepSeek returned HTTP {response.status_code}.",
                            code=f"http_{response.status_code}",
                            retryable=response.status_code in _RETRYABLE_HTTP_STATUS,
                        )
                    async for line in response.aiter_lines():
                        parsed = _parse_upstream_line(line)
                        if parsed is not None:
                            completion.consume(parsed)
                        now = time.monotonic()
                        if now - last_progress >= 15.0:
                            yield ": deepseek-progress\n\n"
                            last_progress = now
            _validate_completion(completion)
            for event in _completed_events(response_id, completion, converted.tool_kinds, sequence):
                yield event
            return
        except DeepSeekUpstreamError as exc:
            last_error = exc
            if not exc.retryable or attempt >= attempts:
                break
            logger.warning("Retrying enterprise DeepSeek Responses bridge after %s.", exc.code)
            yield ": deepseek-retry\n\n"
            await asyncio.sleep(min(2.0, float(attempt)))
        except (httpx.RequestError, TimeoutError):
            last_error = DeepSeekUpstreamError(
                "Enterprise DeepSeek is temporarily unavailable.",
                code="upstream_unavailable",
                retryable=True,
            )
            if attempt >= attempts:
                logger.warning("Enterprise DeepSeek Responses bridge failed.", exc_info=True)
                break
            yield ": deepseek-retry\n\n"
            await asyncio.sleep(min(2.0, float(attempt)))

    assert last_error is not None
    yield _failed_event(response_id, model, last_error, sequence)


@router.post("/responses")
async def create_response(request: Request):
    """Translate one authenticated Codex Responses turn to enterprise DeepSeek."""

    try:
        _require_relay_auth(request)
        payload = await _read_request_json(request)
        converted = convert_responses_request(payload)
    except AdapterRequestError as exc:
        return _error_response(exc)
    return StreamingResponse(
        stream_deepseek_responses(converted),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
        },
    )
