"""Real adapter integration over MockTransport, with no external calls."""

import json
import logging

import httpx
import pytest
from langchain_core.messages import HumanMessage

from deerflow.models import llm_performance
from deerflow.models.cnooc_deepseek_v41 import CnoocDeepSeekV41Chat


def records(caplog):
    return [json.loads(r.getMessage().split("LLM_PERF ", 1)[1]) for r in caplog.records if r.name == llm_performance.__name__ and "LLM_PERF " in r.getMessage()]


def model(monkeypatch, caplog, *, streaming=True, fail=False):
    monkeypatch.setenv("DEERFLOW_LLM_PERFORMANCE_LOGS", "true")
    monkeypatch.delenv("DEERFLOW_LLM_TIMING_LOGS", raising=False)
    monkeypatch.delenv("DEERFLOW_LLM_STREAM_LIMIT_PER_PROCESS", raising=False)
    caplog.set_level(logging.INFO, logger=llm_performance.__name__)

    def serve(request):
        if fail:
            raise httpx.ReadTimeout("PRIVATE credential URL", request=request)
        if not streaming:
            return httpx.Response(
                200,
                json={
                    "id": "test",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "DeepSeek-V4.1-Flash-1017",
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": "PRIVATE ANSWER"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 6, "total_tokens": 16},
                },
            )
        chunks = [
            {"choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}], "usage": {"prompt_tokens": 10, "completion_tokens": 0, "total_tokens": 10}},
            {
                "choices": [{"index": 0, "delta": {"reasoning_content": "PRIVATE REASONING"}, "finish_reason": None}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12, "completion_tokens_details": {"reasoning_tokens": 2}},
            },
            {"choices": [{"index": 0, "delta": {"content": "PRIVATE ANSWER"}, "finish_reason": None}]},
            {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 10, "completion_tokens": 6, "total_tokens": 16, "completion_tokens_details": {"reasoning_tokens": 2}}},
        ]
        body = "".join("data: " + json.dumps(dict(id="test", object="chat.completion.chunk", created=0, model="DeepSeek-V4.1-Flash-1017", **chunk)) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n"
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    return CnoocDeepSeekV41Chat(model="DeepSeek-V4.1-Flash-1017", api_key="synthetic", transport=httpx.MockTransport(serve), async_transport=httpx.MockTransport(serve), streaming=streaming, max_retries=0)


def check(caplog, streaming=True):
    rows = records(caplog)
    assert len(rows) == 2
    assert rows[0]["event"] == "start" and rows[1]["event"] == "end"
    end = rows[-1]
    assert end["outcome"] == "completed"
    assert end["active"] == end["pending"] == 0
    assert end["input_tokens"] == 10 and end["output_tokens"] == 6
    assert "PRIVATE" not in json.dumps(rows)
    assert not llm_performance._counts
    if streaming:
        assert end["first_reasoning_ms"] is not None
        assert end["first_content_ms"] >= end["first_reasoning_ms"]
    else:
        assert end["first_effective_ms"] is None


def test_sync_stream(monkeypatch, caplog):
    m = model(monkeypatch, caplog)
    assert list(m._stream([HumanMessage(content="synthetic")]))
    check(caplog)


@pytest.mark.asyncio
async def test_async_stream(monkeypatch, caplog):
    m = model(monkeypatch, caplog)
    assert [chunk async for chunk in m._astream([HumanMessage(content="synthetic")])]
    check(caplog)


def test_sync_nonstream(monkeypatch, caplog):
    m = model(monkeypatch, caplog, streaming=False)
    assert m.invoke("synthetic").content
    check(caplog, streaming=False)


@pytest.mark.asyncio
async def test_async_nonstream(monkeypatch, caplog):
    m = model(monkeypatch, caplog, streaming=False)
    assert (await m.ainvoke("synthetic")).content
    check(caplog, streaming=False)


@pytest.mark.asyncio
async def test_real_adapter_timeout_closes_counter(monkeypatch, caplog):
    m = model(monkeypatch, caplog, fail=True)
    with pytest.raises(Exception):
        _ = [chunk async for chunk in m._astream([HumanMessage(content="synthetic")])]
    rows = records(caplog)
    assert len(rows) == 2
    assert rows[-1]["outcome"] == "error"
    assert rows[-1]["error_class"] == "APITimeoutError"
    assert rows[-1]["active"] == 0
    assert "PRIVATE" not in json.dumps(rows)


@pytest.mark.asyncio
async def test_disabled_has_no_performance_records(monkeypatch, caplog):
    m = model(monkeypatch, caplog)
    monkeypatch.setenv("DEERFLOW_LLM_PERFORMANCE_LOGS", "false")
    _ = [chunk async for chunk in m._astream([HumanMessage(content="synthetic")])]
    assert records(caplog) == []
    assert not llm_performance._counts


@pytest.mark.asyncio
async def test_real_stream_early_close_releases_active(monkeypatch, caplog):
    m = model(monkeypatch, caplog)
    stream = m._astream([HumanMessage(content="synthetic")])
    await anext(stream)
    assert list(llm_performance._counts.values()) == [[0, 1]]
    await stream.aclose()
    assert not llm_performance._counts
    rows = records(caplog)
    assert len(rows) == 2
    assert rows[-1]["outcome"] in {"closed", "cancelled"}
