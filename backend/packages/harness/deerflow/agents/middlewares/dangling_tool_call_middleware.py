"""Middleware to fix dangling tool calls in message history.

A dangling tool call occurs when an AIMessage contains tool_calls but there are
no corresponding ToolMessages in the history (e.g., due to user interruption or
request cancellation). This causes LLM errors due to incomplete message format.

This middleware intercepts the model call to detect and patch such gaps by
inserting synthetic ToolMessages with an error indicator immediately after the
AIMessage that made the tool calls, ensuring correct message ordering.

Note: Uses wrap_model_call instead of before_model to ensure patches are inserted
at the correct positions (immediately after each dangling AIMessage), not appended
to the end of the message list as before_model + add_messages reducer would do.
"""

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelCallResult, ModelRequest, ModelResponse
from langchain_core.messages import ToolMessage

logger = logging.getLogger(__name__)


class DanglingToolCallMiddleware(AgentMiddleware[AgentState]):
    """Inserts placeholder ToolMessages for dangling tool calls before model invocation.

    Scans the message history for AIMessages whose tool_calls lack corresponding
    ToolMessages, and injects synthetic error responses immediately after the
    offending AIMessage so the LLM receives a well-formed conversation.
    """

    @staticmethod
    def _extract_tool_calls(msg: Any) -> list[dict[str, Any]]:
        tool_calls: list[dict[str, Any]] = []
        for tc in getattr(msg, "tool_calls", None) or []:
            if isinstance(tc, dict):
                tool_calls.append(tc)

        additional_kwargs = getattr(msg, "additional_kwargs", None)
        if isinstance(additional_kwargs, dict):
            for tc in additional_kwargs.get("tool_calls") or []:
                if isinstance(tc, dict):
                    tool_calls.append(tc)

        return tool_calls

    def _build_patched_messages(self, messages: list) -> list | None:
        """Return a new message list with tool-call gaps and ordering fixed.

        OpenAI-compatible providers require that every assistant message with tool_calls
        is immediately followed by tool messages responding to each tool_call_id.

        This middleware:
        - Inserts placeholder ToolMessages for missing tool_call_id results.
        - Reorders existing ToolMessages (when present later in history) to appear
          immediately after the AIMessage that issued the tool call.
        """
        tool_call_ids_in_history: set[str] = set()
        for msg in messages:
            if isinstance(msg, ToolMessage) and isinstance(msg.tool_call_id, str):
                tool_call_ids_in_history.add(msg.tool_call_id)

        needs_patch = False
        for i, msg in enumerate(messages):
            if getattr(msg, "type", None) != "ai":
                continue
            tool_calls = self._extract_tool_calls(msg)
            if not tool_calls:
                continue
            ids: list[str] = []
            for tc in tool_calls:
                tc_id = tc.get("id")
                if isinstance(tc_id, str):
                    ids.append(tc_id)
            if not ids:
                continue

            following = messages[i + 1 : i + 1 + len(ids)]
            following_ids = [m.tool_call_id for m in following if isinstance(m, ToolMessage) and isinstance(m.tool_call_id, str)]
            if any(tc_id not in following_ids for tc_id in ids):
                needs_patch = True
                break

        if not needs_patch:
            return None

        patched: list = []
        removed_indexes: set[int] = set()
        placeholder_count = 0
        moved_count = 0

        i = 0
        while i < len(messages):
            if i in removed_indexes:
                i += 1
                continue

            msg = messages[i]
            patched.append(msg)

            if getattr(msg, "type", None) != "ai":
                i += 1
                continue

            tool_calls = self._extract_tool_calls(msg)
            ids: list[str] = []
            for tc in tool_calls:
                tc_id = tc.get("id")
                if isinstance(tc_id, str):
                    ids.append(tc_id)
            if not ids:
                i += 1
                continue

            calls_by_id: dict[str, dict[str, Any]] = {tc["id"]: tc for tc in tool_calls if isinstance(tc.get("id"), str)}

            found: dict[str, ToolMessage] = {}
            j = i + 1
            while j < len(messages) and len(found) < len(ids):
                if j in removed_indexes:
                    j += 1
                    continue
                m = messages[j]
                if isinstance(m, ToolMessage) and isinstance(m.tool_call_id, str) and m.tool_call_id in calls_by_id:
                    if m.tool_call_id not in found:
                        found[m.tool_call_id] = m
                        if j != i + 1 + (len(found) - 1):
                            moved_count += 1
                        removed_indexes.add(j)
                j += 1

            for tc_id in ids:
                if tc_id in found:
                    patched.append(found[tc_id])
                else:
                    tc = calls_by_id.get(tc_id, {})
                    patched.append(
                        ToolMessage(
                            content="[Tool call was interrupted and did not return a result.]",
                            tool_call_id=tc_id,
                            name=tc.get("name", "unknown"),
                            status="error",
                        )
                    )
                    placeholder_count += 1

            i += 1

        logger.warning(
            "Patched tool-call history: placeholders=%s moved=%s",
            placeholder_count,
            moved_count,
        )
        return patched

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelCallResult:
        patched = self._build_patched_messages(request.messages)
        if patched is not None:
            request = request.override(messages=patched)
        return handler(request)

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelCallResult:
        patched = self._build_patched_messages(request.messages)
        if patched is not None:
            request = request.override(messages=patched)
        return await handler(request)
