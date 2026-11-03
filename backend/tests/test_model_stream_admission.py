from __future__ import annotations

import asyncio

import pytest
from langchain_core.messages import AIMessageChunk, HumanMessage
from langchain_core.outputs import ChatGenerationChunk

from deerflow.models.robust_openai import RobustChatOpenAI
from deerflow.models.stream_admission import admit_model_stream, validate_stream_admission_config


@pytest.mark.asyncio
async def test_stream_limit_is_disabled_by_default(monkeypatch) -> None:
    monkeypatch.delenv("DEERFLOW_LLM_STREAM_LIMIT_MODEL", raising=False)
    monkeypatch.delenv("DEERFLOW_LLM_STREAM_LIMIT_PER_PROCESS", raising=False)

    async with admit_model_stream("DeepSeek-V4-Flash"):
        pass


@pytest.mark.asyncio
async def test_stream_limit_is_shared_by_model_instances_in_one_worker(monkeypatch) -> None:
    monkeypatch.setenv("DEERFLOW_LLM_STREAM_LIMIT_MODEL", "DeepSeek-V4-Flash")
    monkeypatch.setenv("DEERFLOW_LLM_STREAM_LIMIT_PER_PROCESS", "2")
    active = 0
    peak = 0

    async def fake_admitted(self, messages, **kwargs):
        nonlocal active, peak
        del self, messages, kwargs
        active += 1
        peak = max(peak, active)
        try:
            await asyncio.sleep(0.02)
            yield ChatGenerationChunk(message=AIMessageChunk(content="done"))
        finally:
            active -= 1

    monkeypatch.setattr(RobustChatOpenAI, "_astream_admitted", fake_admitted)
    models = [RobustChatOpenAI(model="DeepSeek-V4-Flash", api_key="test-key") for _ in range(6)]

    async def consume(model):
        return [chunk async for chunk in model._astream([HumanMessage(content="test")])]

    results = await asyncio.gather(*(consume(model) for model in models))

    assert peak == 2
    assert all(len(chunks) == 1 for chunks in results)


@pytest.mark.asyncio
async def test_other_model_is_not_queued_behind_target_model(monkeypatch) -> None:
    monkeypatch.setenv("DEERFLOW_LLM_STREAM_LIMIT_MODEL", "DeepSeek-V4-Flash")
    monkeypatch.setenv("DEERFLOW_LLM_STREAM_LIMIT_PER_PROCESS", "1")
    entered = asyncio.Event()
    release = asyncio.Event()

    async def hold_target():
        async with admit_model_stream("DeepSeek-V4-Flash"):
            entered.set()
            await release.wait()

    task = asyncio.create_task(hold_target())
    try:
        await entered.wait()
        async with asyncio.timeout(1.0):
            async with admit_model_stream("qwen3.7-plus"):
                pass
    finally:
        release.set()
        await task


@pytest.mark.asyncio
async def test_early_stream_close_releases_slot(monkeypatch) -> None:
    monkeypatch.setenv("DEERFLOW_LLM_STREAM_LIMIT_MODEL", "DeepSeek-V4-Flash")
    monkeypatch.setenv("DEERFLOW_LLM_STREAM_LIMIT_PER_PROCESS", "1")
    closed = asyncio.Event()

    async def fake_admitted(self, messages, **kwargs):
        del self, messages, kwargs
        try:
            yield ChatGenerationChunk(message=AIMessageChunk(content="first"))
            await asyncio.sleep(3600)
        finally:
            closed.set()

    monkeypatch.setattr(RobustChatOpenAI, "_astream_admitted", fake_admitted)
    model = RobustChatOpenAI(model="DeepSeek-V4-Flash", api_key="test-key")
    stream = model._astream([HumanMessage(content="test")])
    await anext(stream)
    await stream.aclose()

    assert closed.is_set()
    async with asyncio.timeout(1.0):
        async with admit_model_stream("DeepSeek-V4-Flash"):
            pass


@pytest.mark.asyncio
async def test_cancelled_waiter_does_not_consume_a_slot(monkeypatch) -> None:
    monkeypatch.setenv("DEERFLOW_LLM_STREAM_LIMIT_MODEL", "DeepSeek-V4-Flash")
    monkeypatch.setenv("DEERFLOW_LLM_STREAM_LIMIT_PER_PROCESS", "1")

    async with admit_model_stream("DeepSeek-V4-Flash"):
        waiter = asyncio.create_task(_wait_for_slot())
        await asyncio.sleep(0)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter

    async with asyncio.timeout(1.0):
        async with admit_model_stream("DeepSeek-V4-Flash"):
            pass


@pytest.mark.asyncio
async def test_queue_and_release_are_logged(monkeypatch, caplog) -> None:
    monkeypatch.setenv("DEERFLOW_LLM_STREAM_LIMIT_MODEL", "DeepSeek-V4-Flash")
    monkeypatch.setenv("DEERFLOW_LLM_STREAM_LIMIT_PER_PROCESS", "1")
    caplog.set_level("INFO", logger="deerflow.models.stream_admission")

    async with admit_model_stream("DeepSeek-V4-Flash"):
        waiter = asyncio.create_task(_wait_for_slot())
        await asyncio.sleep(0)
        assert "LLM_ADMISSION event=queued" in caplog.text

    await waiter
    assert "LLM_ADMISSION event=acquired" in caplog.text
    assert "LLM_ADMISSION event=released" in caplog.text


async def _wait_for_slot() -> None:
    async with admit_model_stream("DeepSeek-V4-Flash"):
        pass


@pytest.mark.asyncio
async def test_invalid_enabled_limit_fails_explicitly(monkeypatch) -> None:
    monkeypatch.setenv("DEERFLOW_LLM_STREAM_LIMIT_MODEL", "DeepSeek-V4-Flash")
    monkeypatch.setenv("DEERFLOW_LLM_STREAM_LIMIT_PER_PROCESS", "invalid")

    with pytest.raises(ValueError, match="positive integer"):
        async with admit_model_stream("DeepSeek-V4-Flash"):
            pass


def test_startup_validation_requires_target_model(monkeypatch) -> None:
    monkeypatch.delenv("DEERFLOW_LLM_STREAM_LIMIT_MODEL", raising=False)
    monkeypatch.setenv("DEERFLOW_LLM_STREAM_LIMIT_PER_PROCESS", "6")

    with pytest.raises(ValueError, match="DEERFLOW_LLM_STREAM_LIMIT_MODEL"):
        validate_stream_admission_config()


def test_startup_validation_accepts_thirty_per_process(monkeypatch) -> None:
    monkeypatch.setenv("DEERFLOW_LLM_STREAM_LIMIT_MODEL", "DeepSeek-V4-Flash")
    monkeypatch.setenv("DEERFLOW_LLM_STREAM_LIMIT_PER_PROCESS", "30")

    assert validate_stream_admission_config() == ("DeepSeek-V4-Flash", 30)
