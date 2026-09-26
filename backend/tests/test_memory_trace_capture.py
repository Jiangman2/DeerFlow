"""Memory queue trace capture must use the trusted run or current scope."""

from contextlib import nullcontext
from contextvars import Context
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from deerflow.agents.middlewares.memory_middleware import MemoryMiddleware
from deerflow.config.memory_config import MemoryConfig
from deerflow.trace_context import get_current_trace_id, request_trace_context


@pytest.mark.parametrize(
    "runtime_trace,ambient,expected",
    [
        ("trusted-run", "ambient", "trusted-run"),
        ("bad\ntrace", "ambient", "ambient"),
        (None, "ambient", "ambient"),
        (None, None, None),
    ],
)
def test_memory_capture_ignores_caller_metadata(runtime_trace, ambient, expected):
    def check():
        manager = MagicMock()
        middleware = MemoryMiddleware(memory_config=MemoryConfig(enabled=True))
        runtime = SimpleNamespace(context={"thread_id": "thread-memory", "deerflow_trace_id": runtime_trace})
        messages = [HumanMessage(content="My preference"), AIMessage(content="Remembered")]
        with (
            request_trace_context(ambient) if ambient else nullcontext(),
            patch("deerflow.agents.middlewares.memory_middleware.get_memory_manager", return_value=manager),
            patch("deerflow.agents.middlewares.memory_middleware.get_effective_user_id", return_value="user-memory"),
            patch("deerflow.agents.middlewares.memory_middleware.get_config", return_value={"metadata": {"deerflow_trace_id": "caller-forged"}}),
        ):
            middleware.after_agent({"messages": messages}, runtime)
            captured = manager.add.call_args.kwargs
            assert captured["user_id"] == "user-memory"
            assert captured["trace_id"] and captured["trace_id"] != "caller-forged"
            assert captured["trace_id"] == (expected or get_current_trace_id())

    Context().run(check)
