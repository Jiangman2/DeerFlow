"""Middleware to fix dangling tool calls and orphan tool results in message history.

A dangling tool call occurs when an AIMessage contains tool_calls but there are
no corresponding ToolMessages in the history (e.g., due to user interruption or
request cancellation). An orphan ToolMessage occurs when a tool result exists
without a matching AIMessage tool_call (e.g., after summarization/branching
dropped the upstream AIMessage). Both cause strict-provider rejections.

This middleware intercepts the model call to:
- Sanitize malformed tool-call names and arguments before provider serialization
- Insert synthetic ToolMessages with an error indicator for each dangling AIMessage
  tool_call, ensuring correct message ordering
- Drop orphan ToolMessages whose originating tool_call is no longer present in the
  request, preventing strict OpenAI-compatible backends from returning HTTP 400

Note: Uses wrap_model_call instead of before_model to ensure patches are inserted
at the correct positions (immediately after each dangling AIMessage), not appended
to the end of the message list as before_model + add_messages reducer would do.
"""

import json
import logging
import threading
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from typing import Any, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelCallResult, ModelRequest, ModelResponse, hook_config
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.runtime import Runtime

from deerflow.agents.middlewares._bounded_dict import BoundedDict

logger = logging.getLogger(__name__)

# Workaround for issue #2894: malformed write_file calls can carry huge Markdown
# payloads in invalid tool-call args. Keep recovery error details short so the
# synthetic ToolMessage does not echo large or malformed content back to the model.
_MAX_RECOVERY_ERROR_DETAIL_LEN = 500
_UNKNOWN_TOOL_NAME = "unknown_tool"
_EMPTY_TOOL_NAME_ERROR = "Tool call could not be executed because its name was missing or empty."
_INVALID_TOOL_CALL_FALLBACK = "模型连续两次生成了无法解析的工具参数，因此本次操作没有执行。请重试该步骤或切换模型。"


def _valid_tool_name(name: object) -> bool:
    return isinstance(name, str) and bool(name.strip())


def _normalize_tool_name(name: object) -> str:
    return name.strip() if _valid_tool_name(name) else _UNKNOWN_TOOL_NAME


def _has_invalid_tool_name(name: object) -> bool:
    return not _valid_tool_name(name)


def _parse_json_object(value: object) -> dict | None:
    """Parse a JSON-object string, returning None for other inputs."""
    if not isinstance(value, str):
        return None
    try:
        parsed = json.loads(value)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _normalize_tool_arguments(arguments: object) -> str:
    """Return a JSON-object string safe for OpenAI-compatible replay."""
    if isinstance(arguments, dict):
        try:
            return json.dumps(arguments, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError):
            return "{}"
    return arguments if _parse_json_object(arguments) is not None else "{}"


class DanglingToolCallMiddleware(AgentMiddleware[AgentState]):
    """Inserts placeholder ToolMessages for dangling tool calls and drops orphan
    ToolMessages (tool results whose originating AIMessage tool_call is gone).

    Scans the message history for:
    - AIMessages whose tool_calls lack corresponding ToolMessages, and injects
      synthetic error responses immediately after the offending AIMessage
    - ToolMessages with no matching AIMessage tool_call (orphans), and drops
      them so strict OpenAI-compatible backends do not reject the request
    """

    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.Lock()
        self._invalid_retry_counts: BoundedDict[tuple[str, str], int] = BoundedDict(1000)

    @staticmethod
    def _runtime_key(runtime: Runtime) -> tuple[str, str]:
        context = getattr(runtime, "context", None)
        if isinstance(context, dict):
            thread_id = str(context.get("thread_id") or "unknown-thread")
            run_id = str(context.get("run_id") or context.get("run_attempt_id") or id(runtime))
            return thread_id, run_id
        return "unknown-thread", str(id(runtime))

    def _clear_runtime(self, runtime: Runtime) -> None:
        with self._lock:
            self._invalid_retry_counts.pop(self._runtime_key(runtime), None)

    def _recover_invalid_tool_call(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        messages = list(state.get("messages") or [])
        if not messages or not isinstance(messages[-1], AIMessage):
            return None
        last = messages[-1]
        invalid_tool_calls = list(getattr(last, "invalid_tool_calls", None) or [])
        key = self._runtime_key(runtime)
        if not invalid_tool_calls:
            with self._lock:
                self._invalid_retry_counts.pop(key, None)
            return None

        with self._lock:
            retry_count = self._invalid_retry_counts.get(key, 0)
            if retry_count == 0:
                self._invalid_retry_counts[key] = 1

        if retry_count == 0:
            logger.warning(
                "DanglingToolCallMiddleware: retrying model after %d invalid tool call(s)",
                len(invalid_tool_calls),
            )
            return {"jump_to": "model"}

        additional_kwargs = dict(last.additional_kwargs or {})
        additional_kwargs.pop("tool_calls", None)
        additional_kwargs.pop("function_call", None)
        additional_kwargs.update(
            {
                "deerflow_error_fallback": True,
                "error_reason": "Model returned invalid tool-call arguments twice",
            }
        )
        response_metadata = dict(last.response_metadata or {})
        response_metadata["finish_reason"] = "stop"
        fallback = last.model_copy(
            update={
                "content": _INVALID_TOOL_CALL_FALLBACK,
                "tool_calls": [],
                "invalid_tool_calls": [],
                "additional_kwargs": additional_kwargs,
                "response_metadata": response_metadata,
            }
        )
        self._clear_runtime(runtime)
        return {"messages": [fallback]}

    @staticmethod
    def _message_tool_calls(msg) -> list[dict]:
        """Return normalized tool calls from structured fields or raw provider payloads.

        LangChain stores malformed provider function calls in ``invalid_tool_calls``.
        They do not execute, but provider adapters may still serialize enough of
        the call id/name back into the next request that strict OpenAI-compatible
        validators expect a matching ToolMessage. Treat them as dangling calls so
        the next model request stays well-formed and the model sees a recoverable
        tool error instead of another provider 400.
        """
        normalized: list[dict] = []

        tool_calls = getattr(msg, "tool_calls", None) or []
        for tool_call in tool_calls:
            if not isinstance(tool_call, dict):
                logger.debug("Skipping malformed non-dict tool_call in AIMessage: %r", tool_call)
                continue
            original_name = tool_call.get("name")
            normalized_call = dict(tool_call)
            normalized_call["name"] = _normalize_tool_name(original_name)
            if _has_invalid_tool_name(original_name):
                normalized_call["invalid_tool_name"] = True
            normalized.append(normalized_call)

        raw_tool_calls = (getattr(msg, "additional_kwargs", None) or {}).get("tool_calls") or []
        if not tool_calls:
            for raw_tc in raw_tool_calls:
                if not isinstance(raw_tc, dict):
                    continue

                function = raw_tc.get("function")
                name = raw_tc.get("name")
                if not name and isinstance(function, dict):
                    name = function.get("name")

                args = raw_tc.get("args", {})
                if not args and isinstance(function, dict):
                    parsed_args = _parse_json_object(function.get("arguments"))
                    args = parsed_args if parsed_args is not None else {}

                normalized_call = {
                    "id": raw_tc.get("id"),
                    "name": _normalize_tool_name(name),
                    "args": args if isinstance(args, dict) else {},
                }
                if _has_invalid_tool_name(name):
                    normalized_call["invalid_tool_name"] = True
                normalized.append(normalized_call)

        for invalid_tc in getattr(msg, "invalid_tool_calls", None) or []:
            if not isinstance(invalid_tc, dict):
                continue
            original_name = invalid_tc.get("name")
            normalized_call = {
                "id": invalid_tc.get("id"),
                "name": _normalize_tool_name(original_name),
                "args": {},
                "invalid": True,
                "error": invalid_tc.get("error"),
            }
            if _has_invalid_tool_name(original_name):
                normalized_call["invalid_tool_name"] = True
            normalized.append(normalized_call)

        return normalized

    @staticmethod
    def _synthetic_tool_message_content(tool_call: dict) -> str:
        if tool_call.get("invalid_tool_name"):
            return f"[{_EMPTY_TOOL_NAME_ERROR} Use one of the available tool names when retrying.]"
        if tool_call.get("invalid"):
            name = tool_call.get("name")
            error = tool_call.get("error")
            error_text = error[:_MAX_RECOVERY_ERROR_DETAIL_LEN] if isinstance(error, str) and error else ""
            # Workaround for issue #2894: malformed write_file calls can carry huge Markdown
            # payloads in invalid tool-call args. Keep recovery guidance actionable without
            # echoing large or malformed content back to the model.
            if name == "write_file":
                details = f" Parser error: {error_text}" if error_text else ""
                return (
                    "[write_file failed before execution: the tool-call arguments were not valid JSON, "
                    "so no file was written. This often happens when the model tries to write a very "
                    "large Markdown file in a single tool call, especially when `content` contains "
                    "unescaped quotes, inline JSON, backslashes, or code fences. Do not retry the same "
                    "large `write_file` payload for this artifact; provide the report/content directly "
                    "as normal assistant text in your next response. If a file write is still needed "
                    f"later, split the file into smaller sections instead of one large payload.{details}]"
                )
            if error_text:
                return f"[Tool call could not be executed because its arguments were invalid: {error_text}]"
            return "[Tool call could not be executed because its arguments were invalid.]"
        return "[Tool call was interrupted and did not return a result.]"

    @staticmethod
    def _sanitize_ai_message_tool_calls(msg):
        """Return an AIMessage with model-bound tool calls safe to serialize."""
        if getattr(msg, "type", None) != "ai":
            return msg

        changed = False
        update: dict = {}

        tool_calls = getattr(msg, "tool_calls", None)
        if tool_calls:
            structured_changed = False
            sanitized_tool_calls = []
            for tool_call in tool_calls:
                if not isinstance(tool_call, dict):
                    sanitized_tool_calls.append(tool_call)
                    continue
                name = tool_call.get("name")
                sanitized = dict(tool_call)
                normalized_name = _normalize_tool_name(name)
                if sanitized.get("name") != normalized_name:
                    sanitized["name"] = normalized_name
                    structured_changed = True
                sanitized_tool_calls.append(sanitized)
            if structured_changed:
                update["tool_calls"] = sanitized_tool_calls
                changed = True

        invalid_tool_calls = getattr(msg, "invalid_tool_calls", None)
        if invalid_tool_calls:
            invalid_changed = False
            sanitized_invalid_tool_calls = []
            for invalid_tool_call in invalid_tool_calls:
                if not isinstance(invalid_tool_call, dict):
                    sanitized_invalid_tool_calls.append(invalid_tool_call)
                    continue
                sanitized = dict(invalid_tool_call)
                normalized_name = _normalize_tool_name(sanitized.get("name"))
                normalized_arguments = _normalize_tool_arguments(sanitized.get("args"))
                if sanitized.get("name") != normalized_name:
                    sanitized["name"] = normalized_name
                    invalid_changed = True
                if sanitized.get("args") != normalized_arguments:
                    sanitized["args"] = normalized_arguments
                    invalid_changed = True
                sanitized_invalid_tool_calls.append(sanitized)
            if invalid_changed:
                update["invalid_tool_calls"] = sanitized_invalid_tool_calls
                changed = True

        additional_kwargs = dict(getattr(msg, "additional_kwargs", {}) or {})
        raw_tool_calls = additional_kwargs.get("tool_calls")
        if isinstance(raw_tool_calls, list):
            raw_changed = False
            sanitized_raw_tool_calls = []
            for raw_tool_call in raw_tool_calls:
                if not isinstance(raw_tool_call, dict):
                    sanitized_raw_tool_calls.append(raw_tool_call)
                    continue

                sanitized_raw = dict(raw_tool_call)
                function = sanitized_raw.get("function")
                if isinstance(function, dict):
                    sanitized_function = dict(function)
                    normalized_name = _normalize_tool_name(sanitized_function.get("name"))
                    normalized_arguments = _normalize_tool_arguments(sanitized_function.get("arguments"))
                    if sanitized_function.get("name") != normalized_name:
                        sanitized_function["name"] = normalized_name
                        raw_changed = True
                    if sanitized_function.get("arguments") != normalized_arguments:
                        sanitized_function["arguments"] = normalized_arguments
                        raw_changed = True
                    if sanitized_function != function:
                        sanitized_raw["function"] = sanitized_function
                else:
                    normalized_name = _normalize_tool_name(sanitized_raw.get("name"))
                    if sanitized_raw.get("name") != normalized_name:
                        sanitized_raw["name"] = normalized_name
                        raw_changed = True
                sanitized_raw_tool_calls.append(sanitized_raw)

            if raw_changed:
                additional_kwargs["tool_calls"] = sanitized_raw_tool_calls
                update["additional_kwargs"] = additional_kwargs
                changed = True

        if not changed:
            return msg
        return msg.model_copy(update=update)

    def _build_patched_messages(self, messages: list) -> list | None:
        """Return messages with tool results grouped after their tool-call AIMessage.

        This normalizes model-bound causal order before provider serialization while
        preserving already-valid transcripts unchanged.
        """
        tool_messages_by_id: dict[str, deque[ToolMessage]] = defaultdict(deque)
        for msg in messages:
            if isinstance(msg, ToolMessage):
                tool_messages_by_id[msg.tool_call_id].append(msg)

        tool_call_ids: set[str] = set()
        for msg in messages:
            if getattr(msg, "type", None) != "ai":
                continue
            for tc in self._message_tool_calls(msg):
                tc_id = tc.get("id")
                if tc_id:
                    tool_call_ids.add(tc_id)

        patched: list = []
        patch_count = 0
        drop_count = 0
        for msg in messages:
            if isinstance(msg, ToolMessage):
                if msg.tool_call_id in tool_call_ids:
                    continue  # Will be re-emitted after its AIMessage
                # Orphan: ToolMessage whose originating AIMessage tool_call is
                # no longer in the request (e.g. removed by summarization).
                # Drop it silently from the model request so strict providers
                # do not reject it with HTTP 400. Persisted state is untouched;
                # this only affects the single model call.
                drop_count += 1
                continue

            sanitized_msg = self._sanitize_ai_message_tool_calls(msg)
            patched.append(sanitized_msg)
            if getattr(msg, "type", None) != "ai":
                continue

            # Intentionally inspect the original message so empty names can be
            # classified before the sanitized message replaces them.
            for tc in self._message_tool_calls(msg):
                tc_id = tc.get("id")
                if not tc_id:
                    continue

                tool_msg_queue = tool_messages_by_id.get(tc_id)
                existing_tool_msg = tool_msg_queue.popleft() if tool_msg_queue else None
                if existing_tool_msg is not None:
                    if tc.get("invalid_tool_name") and _has_invalid_tool_name(existing_tool_msg.name):
                        existing_tool_msg = existing_tool_msg.model_copy(update={"name": tc["name"]})
                    patched.append(existing_tool_msg)
                else:
                    patched.append(
                        ToolMessage(
                            content=self._synthetic_tool_message_content(tc),
                            tool_call_id=tc_id,
                            name=tc.get("name", "unknown"),
                            status="error",
                        )
                    )
                    patch_count += 1

        if patched == messages and not drop_count:
            return None
        if drop_count or patch_count:
            logger.warning(
                "DanglingToolCallMiddleware: %d orphan(s) dropped, %d placeholder(s) injected",
                drop_count,
                patch_count,
            )
        return patched

    @override
    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelCallResult:
        patched = self._build_patched_messages(request.messages)
        if patched is not None:
            request = request.override(messages=patched)
        return handler(request)

    @override
    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelCallResult:
        patched = self._build_patched_messages(request.messages)
        if patched is not None:
            request = request.override(messages=patched)
        return await handler(request)

    @override
    def before_agent(self, state: AgentState, runtime: Runtime) -> dict | None:
        self._clear_runtime(runtime)
        return None

    @override
    async def abefore_agent(self, state: AgentState, runtime: Runtime) -> dict | None:
        self._clear_runtime(runtime)
        return None

    @hook_config(can_jump_to=["model"])
    @override
    def after_model(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        return self._recover_invalid_tool_call(state, runtime)

    @hook_config(can_jump_to=["model"])
    @override
    async def aafter_model(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        return self._recover_invalid_tool_call(state, runtime)

    @override
    def after_agent(self, state: AgentState, runtime: Runtime) -> dict | None:
        self._clear_runtime(runtime)
        return None

    @override
    async def aafter_agent(self, state: AgentState, runtime: Runtime) -> dict | None:
        self._clear_runtime(runtime)
        return None
