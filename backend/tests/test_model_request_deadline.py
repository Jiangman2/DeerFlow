"""Real deadline cancellation and the cross-provider terminal-error boundary."""

import asyncio
import logging
from types import SimpleNamespace

import httpx
import openai
import pytest
from langchain_core.messages import AIMessageChunk, HumanMessage
from langchain_core.outputs import ChatGenerationChunk

from deerflow.agents.middlewares.llm_error_handling_middleware import LLMErrorHandlingMiddleware
from deerflow.config.app_config import AppConfig
from deerflow.config.sandbox_config import SandboxConfig
from deerflow.models.enterprise_glm import EnterpriseGLMChat, GLMRequestDeadlineError
from deerflow.models.robust_openai import RobustChatOpenAI


def model():
    return EnterpriseGLMChat(model="GLM-5.3-Flash", api_key="synthetic-not-used", request_deadline_seconds=0.05, max_retries=0)


def middleware():
    return LLMErrorHandlingMiddleware(app_config=AppConfig(sandbox=SandboxConfig(use="test")))


def test_reasoning_and_empty_chunks_cannot_renew_total_deadline(monkeypatch, caplog):
    closed = []

    async def fake_stream(self, *args, **kwargs):
        try:
            yield ChatGenerationChunk(message=AIMessageChunk(content="private visible text"))
            while True:
                yield ChatGenerationChunk(message=AIMessageChunk(content="", additional_kwargs={"reasoning_content": "private reasoning"}))
                yield ChatGenerationChunk(message=AIMessageChunk(content=""))
                await asyncio.sleep(0.005)
        finally:
            closed.append(True)

    monkeypatch.setattr(RobustChatOpenAI, "_astream", fake_stream)

    async def run():
        with pytest.raises(GLMRequestDeadlineError) as exc:
            async for _ in model()._astream([HumanMessage("private prompt")]):
                pass
        return exc.value

    with caplog.at_level(logging.WARNING):
        error = asyncio.run(run())
    assert error.reason == "model_request_deadline"
    assert "visible_chunks=1" in str(error)
    assert "reasoning_chunks=0" not in str(error)
    assert "deadline_seconds=0.05" in str(error)
    assert closed == [True]
    assert "LLM_DEADLINE" in caplog.text
    assert "private" not in caplog.text + str(error)


def test_nonstream_deadline_has_specific_error(monkeypatch):
    async def pending(self, *args, **kwargs):
        await asyncio.sleep(10)

    monkeypatch.setattr(RobustChatOpenAI, "_agenerate", pending)
    with pytest.raises(GLMRequestDeadlineError, match="mode=nonstream"):
        asyncio.run(model()._agenerate([]))


def test_inner_timeout_is_not_mislabeled_total_deadline(monkeypatch):
    failure = TimeoutError("inner timeout")

    async def inner(self, *args, **kwargs):
        raise failure

    monkeypatch.setattr(RobustChatOpenAI, "_agenerate", inner)
    with pytest.raises(TimeoutError) as exc:
        asyncio.run(model()._agenerate([]))
    assert exc.value is failure


def test_user_cancellation_remains_cancellation_and_closes_stream(monkeypatch):
    started = asyncio.Event()
    closed = []

    async def pending(self, *args, **kwargs):
        try:
            started.set()
            await asyncio.sleep(10)
            yield ChatGenerationChunk(message=AIMessageChunk(content="never"))
        finally:
            closed.append(True)

    monkeypatch.setattr(RobustChatOpenAI, "_astream", pending)

    async def run():
        async def consume():
            async for _ in model()._astream([]):
                pass

        task = asyncio.create_task(consume())
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert closed == [True]


@pytest.mark.parametrize("error", [TimeoutError(), httpx.ReadTimeout("read expired"), httpx.ConnectTimeout("connect expired")])
def test_other_provider_timeouts_have_terminal_guidance_without_invented_retry(error):
    calls = []

    async def handler(request):
        calls.append(request)
        raise error

    result = asyncio.run(middleware().awrap_model_call(SimpleNamespace(), handler))
    assert len(calls) == 1
    assert "模型请求等待超时" in result.content
    assert result.additional_kwargs["error_reason"] == "timeout"
    assert result.additional_kwargs["deerflow_terminal_stop"] is True


def test_sdk_timeout_keeps_existing_retry_budget(monkeypatch):
    calls = []
    waits = []

    async def sleep(seconds):
        waits.append(seconds)

    async def handler(request):
        calls.append(request)
        raise openai.APITimeoutError(request=httpx.Request("POST", "https://synthetic.invalid"))

    monkeypatch.setattr(asyncio, "sleep", sleep)
    result = asyncio.run(middleware().awrap_model_call(SimpleNamespace(), handler))
    assert len(calls) == 3 and len(waits) == 2
    assert result.additional_kwargs["error_reason"] == "transient"
    assert result.additional_kwargs["deerflow_terminal_stop"] is True
    assert "模型请求等待超时" in result.content


def test_deadline_is_terminal_in_real_agent_no_recovery_loop(monkeypatch):
    from langchain.agents import create_agent
    from langchain_core.language_models.fake_chat_models import FakeListChatModel

    from deerflow.agents.middlewares.terminal_response_middleware import TerminalResponseMiddleware

    calls = []
    error = GLMRequestDeadlineError(seconds=600, elapsed_ms=600000, mode="stream", chunks=12, visible_chunks=0, reasoning_chunks=12, tool_chunks=0)

    async def fail(self, *args, **kwargs):
        calls.append(True)
        raise error

    monkeypatch.setattr(FakeListChatModel, "_agenerate", fail)
    agent = create_agent(FakeListChatModel(responses=["never"]), tools=[], middleware=[middleware(), TerminalResponseMiddleware()])
    result = asyncio.run(agent.ainvoke({"messages": [HumanMessage("continue")]}))
    assert calls == [True]
    final = result["messages"][-1]
    assert final.additional_kwargs["error_reason"] == "model_request_deadline"
    assert final.additional_kwargs["deerflow_terminal_stop"]
    assert "单次模型请求" in final.content
