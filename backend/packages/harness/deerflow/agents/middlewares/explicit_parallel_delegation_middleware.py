"""Enforce the first delegation batch for explicit Ultra-style requests.

Prompt guidance alone is not reliable for models that see an executable-looking
skill path and choose a direct shell command instead of the supplied ``task``
tool.  This middleware narrows only the *initial* model call for a clearly
enumerated, explicitly parallel request.  Once a ``task`` call is present in
the conversation, all normal lead-agent tools are restored for synthesis.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelCallResult, ModelRequest, ModelResponse
from langchain_core.messages import HumanMessage

logger = logging.getLogger(__name__)

_NUMBERED_WORKSTREAM = re.compile(r"(?m)^\s*\d+\s*[.、)]")
_EXPLICIT_PARALLEL_MARKERS = ("ultra", "并行", "委派", "子任务", "subtask", "delegate")


class ExplicitParallelDelegationMiddleware(AgentMiddleware[AgentState]):
    """Require a ``task`` call before direct tools for explicit parallel work."""

    @staticmethod
    def requires_initial_delegation(messages: Sequence[Any]) -> bool:
        """Return whether the latest visible user request is explicitly parallel."""
        for message in reversed(messages):
            if not isinstance(message, HumanMessage):
                continue
            if getattr(message, "name", None) not in (None, "user-input"):
                continue
            content = message.content
            if not isinstance(content, str):
                continue
            normalized = content.casefold()
            if not any(marker in normalized for marker in _EXPLICIT_PARALLEL_MARKERS):
                return False
            return len(_NUMBERED_WORKSTREAM.findall(content)) >= 2
        return False

    @staticmethod
    def has_task_delegation(messages: Sequence[Any]) -> bool:
        """Return whether this turn has already issued a ``task`` tool call."""
        for message in messages:
            for tool_call in getattr(message, "tool_calls", ()) or ():
                if isinstance(tool_call, dict) and tool_call.get("name") == "task":
                    return True
        return False

    def _gate_initial_tools(self, request: ModelRequest) -> ModelRequest:
        if not self.requires_initial_delegation(request.messages) or self.has_task_delegation(request.messages):
            return request

        task_tools = [tool for tool in request.tools if getattr(tool, "name", None) == "task"]
        if not task_tools:
            return request

        # Qwen's thinking mode rejects OpenAI's ``tool_choice='required'``.
        # Expose only ``task`` instead: this preserves the delegation gate while
        # remaining compatible with thinking-enabled Ultra runs.
        logger.info("Explicit parallel request detected; exposing only task for the initial delegation batch")
        return request.override(tools=task_tools)

    @override
    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelCallResult:
        return handler(self._gate_initial_tools(request))

    @override
    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelCallResult:
        return await handler(self._gate_initial_tools(request))
