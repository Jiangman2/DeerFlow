from __future__ import annotations

from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage

from deerflow.agents.middlewares.llm_error_handling_middleware import LLMErrorHandlingMiddleware
from deerflow.config.app_config import AppConfig
from deerflow.config.sandbox_config import SandboxConfig
from deerflow.models.output_safety import ModelOutputGuard, ModelOutputSafetyError, has_repetitive_tail
from deerflow.models.robust_openai import RobustChatOpenAI


@pytest.mark.parametrize("chunk_size", [1, 73, 512, 100000])
def test_repetition_is_detected_across_arbitrary_chunk_boundaries(chunk_size):
    text = "让我尝试搜索更多具体资料，然后再回答您的问题。\n" * 1000
    guard = ModelOutputGuard()
    with pytest.raises(ModelOutputSafetyError, match="repetitive_output"):
        for start in range(0, len(text), chunk_size):
            guard.observe(AIMessageChunk(content=text[start : start + chunk_size]))
    assert len(guard.tail) <= 8192


def test_normal_long_output_short_refrains_and_tool_arguments_are_not_loops():
    text = "\n".join(f"第 {index} 项：请检查本设备的运行参数与维护记录。" for index in range(3000))
    assert not has_repetitive_tail(text)
    assert not has_repetitive_tail("让我们一起努力。" * 10)
    assert not has_repetitive_tail("-" * 10000)
    ModelOutputGuard().observe(AIMessage(content="", tool_calls=[{"id": "write", "name": "write_file", "args": {"content": "valid repeated fixture" * 1000}}]))


def test_reasoning_budget_is_per_invocation_and_can_be_disabled():
    guard = ModelOutputGuard(max_reasoning_chars=20)
    guard.observe(AIMessageChunk(content="", additional_kwargs={"reasoning_content": "秘密" * 5}))
    with pytest.raises(ModelOutputSafetyError, match="reasoning_budget_exceeded") as error:
        guard.observe(AIMessageChunk(content="", additional_kwargs={"reasoning_content": "秘密" * 5}))
    assert "秘密" not in str(error.value)
    ModelOutputGuard(max_reasoning_chars=20).observe(AIMessageChunk(content="answer"))
    ModelOutputGuard(max_reasoning_chars=0, repetition_enabled=False).observe(AIMessage(content="repeat this text" * 1000, additional_kwargs={"reasoning_content": "x" * 100000}))


class FakeStream:
    def __init__(self, deltas):
        self.deltas = iter(deltas)
        self.closed = False
        self.count = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.closed = True

    def __iter__(self):
        return self

    def __next__(self):
        delta = next(self.deltas)
        self.count += 1
        return {
            "id": "mock-stream",
            "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
            "usage": {"prompt_tokens": 10, "completion_tokens": self.count, "total_tokens": 10 + self.count},
        }

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self)
        except StopIteration:
            raise StopAsyncIteration from None


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("kind", ["reasoning", "repetition"])
@pytest.mark.asyncio
async def test_real_adapter_closes_stream_on_safety_stop(mode, kind):
    delta = {"reasoning_content": "x" * 100} if kind == "reasoning" else {"content": "让我搜索更多的相关资料。" * 100}
    stream = FakeStream([delta] * 100)
    model = RobustChatOpenAI(model="mock", api_key="mock", max_reasoning_chars=200, cumulative_stream_usage=True)
    model.client = SimpleNamespace(create=lambda **kwargs: stream)

    async def create(**kwargs):
        return stream

    model.async_client = SimpleNamespace(create=create)
    with pytest.raises(ModelOutputSafetyError):
        if mode == "sync":
            list(model._stream([HumanMessage(content="test")]))
        else:
            async for _ in model._astream([HumanMessage(content="test")]):
                pass
    assert stream.closed
    assert stream.count < 10
    assert not model._cumulative_usage_by_completion


def test_completed_nonstream_answer_is_not_discarded_for_long_reasoning():
    model = RobustChatOpenAI(model="mock", api_key="mock", max_reasoning_chars=100)
    result = model._create_chat_result(
        {
            "choices": [{"message": {"role": "assistant", "content": "这是完整回答。", "reasoning_content": "x" * 500}, "finish_reason": "stop"}],
        }
    )
    assert result.generations[0].message.content == "这是完整回答。"


def test_local_guard_options_are_not_sent_to_provider():
    model = RobustChatOpenAI(model="mock", api_key="mock", max_reasoning_chars=123, repetition_guard_enabled=False)
    payload = model._get_request_payload([HumanMessage(content="test")])
    assert "max_reasoning_chars" not in payload
    assert "repetition_guard_enabled" not in payload


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.asyncio
async def test_safety_errors_are_not_retried_by_error_middleware(mode):
    middleware = LLMErrorHandlingMiddleware(app_config=AppConfig(sandbox=SandboxConfig(use="test")))
    calls = []

    def fail(request):
        calls.append(request)
        raise ModelOutputSafetyError("repetitive_output")

    async def afail(request):
        return fail(request)

    request = SimpleNamespace()
    result = middleware.wrap_model_call(request, fail) if mode == "sync" else await middleware.awrap_model_call(request, afail)
    assert len(calls) == 1
    assert result.additional_kwargs["deerflow_terminal_stop"]
    assert result.additional_kwargs["error_reason"] == "repetitive_output"
