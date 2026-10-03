"""OpenAI Responses compatibility bridge for the enterprise DeepSeek endpoint.

The Codex CLI only speaks the Responses wire protocol.  The approved CNOOC
DeepSeek service speaks streamed Chat Completions.  This module keeps that
translation inside Gateway so a provisioned Sandbox receives only a scoped
relay credential, never the upstream model credential.
"""

from __future__ import annotations

import asyncio
import hashlib
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
from jsonschema import Draft202012Validator, SchemaError
from referencing import Registry
from referencing.exceptions import NoSuchResource, Unresolvable

from app.gateway.ppt_runtime_broker import dispatch_runtime, require_scope, persist_provider_receipt

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/codex/deepseek/v1", tags=["codex-responses"])

_DEFAULT_MODEL = "DeepSeek-V4-Flash"
_DEFAULT_UPSTREAM_URL = "https://aiapi.cnooc.com.cn/DeepSeek-V4-Flash/FEAT-511-001/v1/chat/completions"
_DEFAULT_MAX_REQUEST_BYTES = 32 * 1024 * 1024
_DEFAULT_MAX_OUTPUT_TOKENS = 65536
_MAX_TOOL_ARGUMENT_REPAIRS = 2
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


class ToolArgumentsError(DeepSeekUpstreamError):
    """Rejected before publication; a bounded model regeneration is safe."""

    def __init__(self, constraint: str, *, diagnostic: dict[str, int | str] | None = None) -> None:
        # Do not expose arguments, validator instance values, or provider secrets.
        super().__init__(
            "Enterprise DeepSeek tool arguments failed validation (" + constraint + "). No tools from this response were dispatched.",
            code="invalid_tool_arguments",
        )
        self.diagnostic = diagnostic or {}


@dataclass(frozen=True)
class ResponseToolTarget:
    kind: str
    name: str
    namespace: str | None = None
    parameters: dict[str, Any] | None = None


@dataclass(frozen=True)
class ConvertedRequest:
    payload: dict[str, Any]
    tool_targets: dict[str, ResponseToolTarget]
    ignored_hosted_tool_types: tuple[str, ...] = ()

    @property
    def tool_kinds(self) -> dict[str, str]:
        return {name: target.kind for name, target in self.tool_targets.items()}


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


def _find_upstream_tool_name(
    tool_targets: dict[str, ResponseToolTarget],
    *,
    name: str,
    namespace: str | None,
    kind: str,
    param: str,
) -> str:
    for upstream_name, target in tool_targets.items():
        if target.name == name and target.namespace == namespace and target.kind == kind:
            return upstream_name
    raise AdapterRequestError("Tool call references an unknown tool.", param=param)


def _convert_input_items(
    input_value: object,
    instructions: str,
    tool_targets: dict[str, ResponseToolTarget],
) -> list[dict[str, Any]]:
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
            namespace = item.get("namespace")
            if namespace is not None and not isinstance(namespace, str):
                raise AdapterRequestError("Tool call namespace must be text.", param=f"{param}.namespace")
            kind = "custom" if item_type == "custom_tool_call" else "function"
            upstream_name = _find_upstream_tool_name(
                tool_targets,
                name=name,
                namespace=namespace,
                kind=kind,
                param=f"{param}.name",
            )
            if item_type == "custom_tool_call":
                raw_input = item.get("input", "")
                if not isinstance(raw_input, str):
                    raise AdapterRequestError("Custom tool input must be text.", param=f"{param}.input")
                arguments = json.dumps({"input": raw_input}, ensure_ascii=False, separators=(",", ":"))
            else:
                raw_arguments = item.get("arguments", "{}")
                arguments = json.dumps(raw_arguments, ensure_ascii=False, separators=(",", ":")) if isinstance(raw_arguments, dict) else raw_arguments
                if not isinstance(arguments, str):
                    raise AdapterRequestError("Function arguments must be JSON text.", param=f"{param}.arguments")
            _append_assistant_tool_call(
                messages,
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": upstream_name, "arguments": arguments},
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


_MAX_CHAT_TOOL_NAME_LENGTH = 64
_UNSUPPORTED_PROVIDER_HOSTED_TOOL_TYPES = frozenset({"web_search", "web_search_preview"})


def _namespace_upstream_name(namespace: str, name: str) -> str:
    candidate = f"{namespace}__{name}"
    if len(candidate) <= _MAX_CHAT_TOOL_NAME_LENGTH:
        return candidate
    digest = hashlib.sha256(candidate.encode("utf-8")).hexdigest()[:12]
    prefix_length = _MAX_CHAT_TOOL_NAME_LENGTH - len(digest) - 1
    return f"{candidate[:prefix_length]}_{digest}"


def _chat_function_definition(tool: dict[str, Any], *, kind: str, name: str, description: str) -> dict[str, Any]:
    if kind == "custom":
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
    _check_tool_schema(parameters)
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": parameters,
        },
    }


def _deny_schema_retrieval(uri: str):
    # A tool schema must never become an outbound request or local-file reader.
    raise NoSuchResource(ref=uri)


def _check_tool_schema(parameters: dict[str, Any]) -> None:
    def check_refs(value: Any, depth: int = 0) -> None:
        if depth > 64:
            raise ValueError("schema too deep")
        if isinstance(value, dict):
            for key, child in value.items():
                if key in {"$ref", "$dynamicRef"} and (not isinstance(child, str) or not child.startswith("#")):
                    raise ValueError("non-local schema reference")
                # Only schema positions: properties or examples named "$ref"
                # are ordinary data and must not be mistaken for references.
                if key in {"properties", "patternProperties", "$defs", "definitions", "dependentSchemas", "dependencies"} and isinstance(child, dict):
                    for schema in child.values():
                        check_refs(schema, depth + 1)
                elif key in {"allOf", "anyOf", "oneOf", "prefixItems"} and isinstance(child, list):
                    for schema in child:
                        check_refs(schema, depth + 1)
                elif key in {"items", "additionalItems", "additionalProperties", "unevaluatedProperties", "unevaluatedItems", "propertyNames", "contains", "not", "if", "then", "else"}:
                    if isinstance(child, list):
                        for schema in child:
                            check_refs(schema, depth + 1)
                    else:
                        check_refs(child, depth + 1)

    try:
        check_refs(parameters)
        Draft202012Validator.check_schema(parameters)
    except (ValueError, SchemaError, RecursionError) as exc:
        raise AdapterRequestError("Tool parameter schema is invalid or requires an external reference.", param="tools") from exc


def _convert_tools(
    tools_value: object,
) -> tuple[list[dict[str, Any]], dict[str, ResponseToolTarget], tuple[str, ...]]:
    if tools_value is None:
        return [], {}, ()
    if not isinstance(tools_value, list):
        raise AdapterRequestError("Responses tools must be an array.", param="tools")
    tools: list[dict[str, Any]] = []
    tool_targets: dict[str, ResponseToolTarget] = {}
    ignored_hosted_tool_types: list[str] = []
    response_identities: set[tuple[str | None, str]] = set()
    for index, tool in enumerate(tools_value):
        param = f"tools[{index}]"
        if not isinstance(tool, dict):
            raise AdapterRequestError("Tool definition must be an object.", param=param)
        tool_type = tool.get("type")
        if tool_type in _UNSUPPORTED_PROVIDER_HOSTED_TOOL_TYPES:
            if tool_type not in ignored_hosted_tool_types:
                ignored_hosted_tool_types.append(str(tool_type))
            continue
        if tool_type not in {"function", "custom", "namespace"}:
            raise AdapterRequestError(
                f"Unsupported Responses tool type: {tool_type!r}.",
                code="unsupported_tool_type",
                param=f"{param}.type",
            )
        name = tool.get("name")
        if not isinstance(name, str) or not name:
            raise AdapterRequestError("Tool definition is missing name.", param=f"{param}.name")
        description = tool.get("description") if isinstance(tool.get("description"), str) else ""
        if tool_type != "namespace":
            identity = (None, name)
            if identity in response_identities or name in tool_targets:
                raise AdapterRequestError("Tool names must be unique.", param=f"{param}.name")
            response_identities.add(identity)
            definition = _chat_function_definition(tool, kind=tool_type, name=name, description=description)
            tool_targets[name] = ResponseToolTarget(kind=tool_type, name=name, parameters=definition["function"]["parameters"])
            tools.append(definition)
            continue

        namespace_tools = tool.get("tools")
        if not isinstance(namespace_tools, list):
            raise AdapterRequestError("Namespace tools must be an array.", param=f"{param}.tools")
        for child_index, child in enumerate(namespace_tools):
            child_param = f"{param}.tools[{child_index}]"
            if not isinstance(child, dict):
                raise AdapterRequestError("Namespace tool definition must be an object.", param=child_param)
            child_type = child.get("type")
            if child_type not in {"function", "custom"}:
                raise AdapterRequestError(
                    f"Unsupported namespace tool type: {child_type!r}.",
                    code="unsupported_tool_type",
                    param=f"{child_param}.type",
                )
            child_name = child.get("name")
            if not isinstance(child_name, str) or not child_name:
                raise AdapterRequestError("Namespace tool definition is missing name.", param=f"{child_param}.name")
            identity = (name, child_name)
            upstream_name = _namespace_upstream_name(name, child_name)
            if identity in response_identities or upstream_name in tool_targets:
                raise AdapterRequestError("Namespace tool names must be unique.", param=f"{child_param}.name")
            response_identities.add(identity)
            child_description = child.get("description") if isinstance(child.get("description"), str) else ""
            qualified_description = f"Namespace `{name}`. {description}\n{child_description}".strip()
            definition = _chat_function_definition(child, kind=child_type, name=upstream_name, description=qualified_description)
            tool_targets[upstream_name] = ResponseToolTarget(
                kind=child_type,
                name=child_name,
                namespace=name,
                parameters=definition["function"]["parameters"],
            )
            tools.append(definition)
    return tools, tool_targets, tuple(ignored_hosted_tool_types)


def _convert_tool_choice(value: object, tool_targets: dict[str, ResponseToolTarget]) -> object:
    if value is None:
        return "auto"
    if isinstance(value, str) and value in {"auto", "none", "required"}:
        return value
    if isinstance(value, dict) and value.get("type") in _UNSUPPORTED_PROVIDER_HOSTED_TOOL_TYPES:
        raise AdapterRequestError(
            "The enterprise DeepSeek provider cannot execute hosted web search.",
            code="unsupported_hosted_tool_choice",
            param="tool_choice.type",
        )
    if isinstance(value, dict) and value.get("type") in {"function", "custom"}:
        name = value.get("name")
        namespace = value.get("namespace")
        if namespace is not None and not isinstance(namespace, str):
            raise AdapterRequestError("tool_choice namespace must be text.", param="tool_choice.namespace")
        kind = str(value["type"])
        if not isinstance(name, str):
            raise AdapterRequestError("tool_choice references an unknown tool.", param="tool_choice.name")
        upstream_name = _find_upstream_tool_name(
            tool_targets,
            name=name,
            namespace=namespace,
            kind=kind,
            param="tool_choice.name",
        )
        return {"type": "function", "function": {"name": upstream_name}}
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
    tools, tool_targets, ignored_hosted_tool_types = _convert_tools(payload.get("tools"))
    messages = _convert_input_items(payload.get("input", []), instructions, tool_targets)
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
    tool_choice = _convert_tool_choice(payload.get("tool_choice"), tool_targets)
    if tools:
        upstream["tools"] = tools
        upstream["tool_choice"] = tool_choice
        upstream["parallel_tool_calls"] = bool(payload.get("parallel_tool_calls", True))
    if ignored_hosted_tool_types:
        logger.info(
            "Omitted provider-hosted tools unsupported by enterprise DeepSeek: %s.",
            ", ".join(ignored_hosted_tool_types),
        )
    return ConvertedRequest(
        payload=upstream,
        tool_targets=tool_targets,
        ignored_hosted_tool_types=ignored_hosted_tool_types,
    )


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
    if completion.finish_reason not in {"stop", "tool_calls"}:
        raise DeepSeekUpstreamError("Enterprise DeepSeek stream ended without a completed turn.", code="incomplete_response", retryable=True)
    if not completion.content and not completion.tool_calls:
        raise DeepSeekUpstreamError(
            "Enterprise DeepSeek completed without text or tool calls.",
            code="empty_response",
            retryable=True,
        )
    for tool_call in completion.tool_calls.values():
        if not tool_call.name or not tool_call.call_id:
            raise DeepSeekUpstreamError("Enterprise DeepSeek returned an incomplete tool call.", code="invalid_tool_call")
        _parse_tool_arguments(tool_call.arguments_text)


def _parse_tool_arguments(text: str) -> dict[str, Any]:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    def reject_constant(_):
        raise ValueError("non-finite constant")

    try:
        arguments = json.loads(text, object_pairs_hook=pairs, parse_constant=reject_constant)
    except json.JSONDecodeError as exc:
        # Keep positions, never the offending argument text/key/value.
        raise ToolArgumentsError("JSON syntax", diagnostic={"kind": "json_decode", "position": exc.pos, "line": exc.lineno, "column": exc.colno}) from exc
    except (ValueError, RecursionError) as exc:
        kind = "nesting_limit" if isinstance(exc, RecursionError) else "noncanonical_json"
        raise ToolArgumentsError("JSON syntax", diagnostic={"kind": kind}) from exc
    if not isinstance(arguments, dict):
        raise ToolArgumentsError("object required")
    return arguments


def _validate_tool_arguments(tool_call: ToolCallBuffer, target: ResponseToolTarget) -> dict[str, Any]:
    arguments = _parse_tool_arguments(tool_call.arguments_text)
    if target.parameters is not None:
        try:
            validator = Draft202012Validator(target.parameters, registry=Registry(retrieve=_deny_schema_retrieval))
            error = next(validator.iter_errors(arguments), None)
        except (Unresolvable, RecursionError) as exc:
            raise DeepSeekUpstreamError("Tool schema cannot be resolved locally.", code="invalid_tool_schema") from exc
        if error is not None:
            # error.message contains instance data. Log only the known validator keyword.
            constraint = str(error.validator)[:40]
            if error.validator == "required" and isinstance(error.instance, dict):
                missing = [name for name in error.validator_value if name not in error.instance]
                # Field names come from the caller's schema, never from argument values.
                constraint += "; missing fields=" + json.dumps([name[:80] for name in missing[:8]], ensure_ascii=True)
            raise ToolArgumentsError("schema " + constraint)
    return arguments


def _total_attempt_usage(attempts: list[dict[str, Any]]) -> dict[str, Any]:
    total = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    reasoning = 0
    for attempt in attempts:
        usage = _usage_payload(attempt)
        total["prompt_tokens"] += usage["input_tokens"]
        total["completion_tokens"] += usage["output_tokens"]
        total["total_tokens"] += usage["total_tokens"]
        reasoning += usage.get("output_tokens_details", {}).get("reasoning_tokens", 0)
    total["completion_tokens_details"] = {"reasoning_tokens": reasoning}
    return total


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


def _response_output(
    completion: DeepSeekCompletion,
    tool_targets: dict[str, ResponseToolTarget],
) -> list[dict[str, Any]]:
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
        target = tool_targets.get(tool_call.name)
        if target is None:
            raise DeepSeekUpstreamError(
                "Enterprise DeepSeek called an unknown tool.",
                code="unknown_tool_call",
            )
        arguments = _validate_tool_arguments(tool_call, target)
        namespace = {"namespace": target.namespace} if target.namespace is not None else {}
        if target.kind == "custom":
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
                    "name": target.name,
                    "input": raw_input,
                    **namespace,
                }
            )
        else:
            output.append(
                {
                    "id": f"fc_{uuid.uuid4().hex}",
                    "type": "function_call",
                    "status": "completed",
                    "call_id": tool_call.call_id,
                    "name": target.name,
                    "arguments": tool_call.arguments_text,
                    **namespace,
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
    tool_targets: dict[str, ResponseToolTarget],
    sequence_number: int,
) -> list[str]:
    output = _response_output(completion, tool_targets)
    events: list[str] = []
    sequence = sequence_number
    for output_index, item in enumerate(output):
        added = dict(item)
        added["status"] = "in_progress"
        if added["type"] == "message":
            added["content"] = []
        elif added["type"] == "function_call":
            added["arguments"] = ""
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
        if item["type"] == "function_call":
            for event_type, field_name in (
                ("response.function_call_arguments.delta", "delta"),
                ("response.function_call_arguments.done", "arguments"),
            ):
                events.append(
                    _sse(
                        {
                            "type": event_type,
                            "sequence_number": sequence,
                            "item_id": item["id"],
                            "output_index": output_index,
                            field_name: item["arguments"],
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


def _failed_event(response_id: str, model: str, error: DeepSeekUpstreamError, sequence_number: int, usage: dict[str, Any] | None = None) -> str:
    response = _base_response(response_id, model, status="failed", output=[])
    response["error"] = {
        "code": error.code,
        "message": str(error),
        "type": "server_error",
    }
    if usage is not None:
        response["usage"] = _usage_payload(usage)
    return _sse(
        {
            "type": "response.failed",
            "sequence_number": sequence_number,
            "response": response,
        }
    )


async def stream_deepseek_responses(converted: ConvertedRequest, *, receipt_request=None, receipt_scope=None):
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
    attempts = min(_int_env("PPT_MASTER_DEEPSEEK_ATTEMPTS", 2), 3)
    timeout = httpx.Timeout(
        connect=_float_env("PPT_MASTER_DEEPSEEK_CONNECT_TIMEOUT_SECONDS", 15.0),
        read=_float_env("PPT_MASTER_DEEPSEEK_READ_TIMEOUT_SECONDS", 900.0),
        write=_float_env("PPT_MASTER_DEEPSEEK_WRITE_TIMEOUT_SECONDS", 60.0),
        pool=_float_env("PPT_MASTER_DEEPSEEK_POOL_TIMEOUT_SECONDS", 15.0),
    )
    last_error: DeepSeekUpstreamError | None = None
    repairs = 0
    transient_failures = 0
    attempt_usage: list[dict[str, Any]] = []
    request_payload = converted.payload
    # Include repairs in one total budget; provider keepalives cannot extend it.
    deadline = time.monotonic() + min(_float_env("PPT_MASTER_DEEPSEEK_TURN_TIMEOUT_SECONDS", 540.0), 540.0)
    for attempt in range(1, attempts + _MAX_TOOL_ARGUMENT_REPAIRS + 1):
        completion = DeepSeekCompletion(model=model)
        usage_recorded = False
        provider_persisted = False
        last_progress = time.monotonic()
        turn_budget = asyncio.timeout(max(0.001, deadline - time.monotonic()))
        try:
            async with turn_budget, httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
                async with client.stream(
                    "POST",
                    upstream_url,
                    headers={
                        "Authorization": f"Bearer {_upstream_key()}",
                        "Content-Type": "application/json",
                        "Accept": "text/event-stream",
                        "Accept-Encoding": "identity",
                    },
                    json=request_payload,
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
            attempt_usage.append(completion.usage)
            usage_recorded = True
            if receipt_scope is not None:
                await persist_provider_receipt(receipt_request, receipt_scope, response_id, attempt, completion.usage, model=model)
                provider_persisted = True
            _validate_completion(completion)
            # Build the entire batch before publishing; a rejected sibling must
            # never leave an executed prefix.
            completion.usage = _total_attempt_usage(attempt_usage)
            for event in _completed_events(response_id, completion, converted.tool_targets, sequence):
                yield event
            return
        except ToolArgumentsError as exc:
            last_error = exc
            logger.warning(
                "Enterprise tool batch rejected response=%s diagnostic=%s calls=%s",
                response_id,
                exc.diagnostic,
                [
                    {
                        "tool_hash": hashlib.sha256(call.name.encode()).hexdigest()[:12],
                        "fragments": len(call.arguments),
                        "argument_bytes": len(call.arguments_text.encode("utf-8")),
                        "prefix_snapshots": sum(fragment.lstrip().startswith("{") for fragment in call.arguments),
                    }
                    for call in list(completion.tool_calls.values())[:16]
                ],
            )
            if not usage_recorded:
                attempt_usage.append(completion.usage)
            if repairs >= _MAX_TOOL_ARGUMENT_REPAIRS:
                break
            repairs += 1
            # Only regenerate an unpublished turn; preserve earlier receipts.
            feedback = (
                "Protocol validation rejected your previous response before dispatch. "
                "No tool from that response has executed. Regenerate the complete next response "
                "from the original conversation using only the supplied tools and their exact parameter schemas. "
                "Every function arguments value must be one complete JSON object with all required fields "
                "and correct field names and types. Do not put command text in a different field. "
                "Serialize nested quotes, backslashes and newlines using valid JSON string escaping; "
                "do not wrap arguments in Markdown or concatenate multiple JSON objects. "
                "Do not repeat tools whose outputs are already in the original conversation. " + str(exc)
            )
            request_payload = {**converted.payload, "messages": [*converted.payload["messages"], {"role": "system", "content": feedback}]}
            logger.warning("Enterprise DeepSeek tool arguments rejected before dispatch; correction=%s/%s.", repairs, _MAX_TOOL_ARGUMENT_REPAIRS)
            yield ": deepseek-tool-arguments-correction\n\n"
        except DeepSeekUpstreamError as exc:
            last_error = exc
            if not usage_recorded:
                attempt_usage.append(completion.usage)
            transient_failures += 1
            if not exc.retryable or transient_failures >= attempts:
                break
            logger.warning("Retrying enterprise DeepSeek Responses bridge after %s.", exc.code)
            yield ": deepseek-retry\n\n"
            await asyncio.sleep(min(2.0, float(attempt)))
        except (httpx.RequestError, TimeoutError):
            if not usage_recorded:
                attempt_usage.append(completion.usage)
            if turn_budget.expired() or time.monotonic() >= deadline:
                last_error = DeepSeekUpstreamError("Enterprise DeepSeek exceeded the total turn deadline.", code="upstream_deadline")
                break
            last_error = DeepSeekUpstreamError(
                "Enterprise DeepSeek is temporarily unavailable.",
                code="upstream_unavailable",
                retryable=True,
            )
            transient_failures += 1
            if transient_failures >= attempts:
                logger.warning("Enterprise DeepSeek Responses bridge failed.", exc_info=True)
                break
            yield ": deepseek-retry\n\n"
            await asyncio.sleep(min(2.0, float(attempt)))

        finally:
            if receipt_scope is not None and not provider_persisted:
                operation = asyncio.create_task(persist_provider_receipt(receipt_request, receipt_scope, response_id, attempt, completion.usage, model=model))
                while not operation.done():
                    try:
                        await asyncio.shield(operation)
                    except asyncio.CancelledError:
                        continue
                operation.result()

    assert last_error is not None
    yield _failed_event(response_id, model, last_error, sequence, _total_attempt_usage(attempt_usage))


@router.post("/responses")
async def create_response(request: Request):
    """Translate one authenticated Codex Responses turn to enterprise DeepSeek."""

    try:
        operation = request.headers.get("X-Ppt-Runtime-Operation")
        if operation is not None:
            return await dispatch_runtime(request, operation)
        _require_relay_auth(request)
        payload = await _read_request_json(request)
        receipt_scope = await require_scope(request) if request.headers.get("X-Ppt-Capability") else None
        converted = convert_responses_request(payload)
    except AdapterRequestError as exc:
        return _error_response(exc)
    return StreamingResponse(
        stream_deepseek_responses(converted, receipt_request=request, receipt_scope=receipt_scope),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
        },
    )
