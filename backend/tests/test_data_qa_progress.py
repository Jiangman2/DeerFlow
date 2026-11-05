"""Incremental SSE and real ToolNode custom-stream tests; no provider traffic."""

import asyncio
import json
import threading
from types import SimpleNamespace

import httpx
import pytest
from langchain_core.messages import AIMessage
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode

from deerflow.community.appforge import tools
from deerflow.community.appforge.data_qa_progress import DataQAProgress
from deerflow.community.appforge.data_qa_stream import collect_data_qa_stream


@pytest.fixture(autouse=True)
def synthetic_config(monkeypatch):
    monkeypatch.setattr(tools, "_tool_extra", lambda _: {})


def frame(value):
    return ("data: " + json.dumps(value) + "\n\n").encode()


def test_small_transport_events_are_not_buffered_and_private_prose_is_not_progress():
    observed = []

    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            yield frame({"event": "answer", "data": {"node_type": "unknown", "text": "SQL password=SECRET reasoning"}})
            assert observed == ["processing"]  # before next bytes, below 8 KB
            yield frame({"event": "answer", "data": {"node_type": "end", "text": "answer"}})
            assert observed == ["processing", "answering"]
            yield frame({"event": "workflow_finished", "data": {"status": "succeeded", "outputs": {"output": "answer"}}})

    response = httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Stream())
    try:
        result = collect_data_qa_stream(response, parse_knowledge=lambda _: [], on_progress=observed.append)
    finally:
        response.close()
    assert result["answer"] == "answer"
    assert "SECRET" not in json.dumps(result)


def runtime(events, **context):
    return SimpleNamespace(context={"thread_id": "thread", "run_id": "run", "user_id": "user", "datalake_enabled": True, **context}, tool_call_id="call", stream_writer=events.append)


def test_progress_is_closed_bounded_deduplicated_and_terminal():
    events = []
    p = DataQAProgress(runtime(events))
    for _ in range(100):
        p.emit("processing")
        p.emit("SQL secret")
    p.emit("completed")
    p.emit("waiting")
    assert [e["stage"] for e in events] == ["processing", "completed"]
    assert [e["sequence"] for e in events] == [1, 2]
    assert set(events[0]) == {"type", "thread_id", "run_id", "tool_call_id", "tool", "stage", "sequence"}


@pytest.mark.parametrize("result,stage", [({"status": "done", "answer": "ok"}, "completed"), ({"status": "empty"}, "empty"), ({"status": "interrupted", "interrupt": {"choices": ["x"]}}, "clarification")])
def test_result_contract_and_terminal(monkeypatch, result, stage):
    events = []
    monkeypatch.setattr(tools, "_api_key", lambda _: "synthetic")
    monkeypatch.setattr(tools, "_stream_invoke", lambda *a, **k: result)
    output = tools._invoke_tool("datalake_data_qa", "question", runtime(events))
    assert json.loads(output) == result
    assert [e["stage"] for e in events] == ["waiting", stage]


@pytest.mark.parametrize("error,stage", [(httpx.ReadTimeout("private"), "timeout"), (ValueError("private"), "failed"), (asyncio.CancelledError(), "cancelled")])
def test_failure_and_cancellation(monkeypatch, error, stage):
    events = []
    monkeypatch.setattr(tools, "_api_key", lambda _: "synthetic")

    def fail(*a, **k):
        raise error

    monkeypatch.setattr(tools, "_stream_invoke", fail)
    if stage == "cancelled":
        with pytest.raises(asyncio.CancelledError):
            tools._invoke_tool("datalake_data_qa", "question", runtime(events))
    else:
        assert "private" not in tools._invoke_tool("datalake_data_qa", "question", runtime(events))
    assert [e["stage"] for e in events] == ["waiting", stage]


def test_disabled_access_has_no_provider_or_progress(monkeypatch):
    events = []
    monkeypatch.setattr(tools, "_stream_invoke", lambda *a, **k: pytest.fail("unauthorized request"))
    assert json.loads(tools._invoke_tool("datalake_data_qa", "question", runtime(events, datalake_enabled=False)))["code"] == "datalake_disabled"
    assert events == []


def test_disconnected_observer_does_not_change_result():
    p = DataQAProgress(runtime([]))

    def closed(_):
        raise RuntimeError("private")

    p.writer = closed
    p.emit("waiting")
    p.emit("completed")
    assert p.finished


@pytest.mark.asyncio
async def test_real_async_toolnode_delivers_custom_events_before_tool_return(monkeypatch):
    release = threading.Event()
    returned = threading.Event()
    monkeypatch.setattr(tools, "_api_key", lambda _: "synthetic")

    def invoke(*args, on_progress):
        on_progress("processing")
        assert release.wait(10), "custom event did not arrive before completion"
        returned.set()
        return {"status": "done", "answer": "synthetic"}

    monkeypatch.setattr(tools, "_stream_invoke", invoke)
    graph = StateGraph(MessagesState, context_schema=dict)
    graph.add_node("tools", ToolNode([tools.datalake_data_qa_tool]))
    graph.add_edge(START, "tools")
    graph.add_edge("tools", END)
    seen = []
    try:
        async with asyncio.timeout(15):
            async for mode, event in graph.compile().astream(
                {"messages": [AIMessage(content="", tool_calls=[{"name": "datalake_data_qa", "id": "c", "args": {"query": "q"}}])]},
                context={"thread_id": "t", "run_id": "r", "user_id": "u", "datalake_enabled": True},
                stream_mode=["custom", "updates"],
            ):
                if mode == "custom":
                    seen.append(event)
                    if event["stage"] == "processing":
                        assert not returned.is_set()
                        release.set()
    finally:
        release.set()
    assert [e["stage"] for e in seen] == ["waiting", "processing", "completed"]
    assert all((e["thread_id"], e["run_id"], e["tool_call_id"]) == ("t", "r", "c") for e in seen)
