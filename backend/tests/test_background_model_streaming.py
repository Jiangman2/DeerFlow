"""Offline transport and failure contracts for private background model calls."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from langchain_openai import ChatOpenAI
from langgraph.constants import TAG_NOSTREAM
from langgraph.graph import END, START, MessagesState, StateGraph

from deerflow.agents.memory.backends.deermem.deermem.core.llm import stream_memory_response
from deerflow.models.robust_openai import RobustChatOpenAI
from deerflow.utils.streaming_llm import astream_response, stream_response


def sse(text='{"ok":true}', finish="stop"):
    events = []
    for part, reason in [(text[:4], None), (text[4:], None), ("", finish)]:
        event = {"id": "offline", "object": "chat.completion.chunk", "model": "offline", "choices": [{"index": 0, "delta": {"role": "assistant", "content": part}, "finish_reason": reason}]}
        events.append("data: " + json.dumps(event) + "\n\n")
    return ("".join(events) + "data: [DONE]\n\n").encode()


@pytest.mark.parametrize("caller", [stream_response, stream_memory_response])
@pytest.mark.parametrize("finish", ["stop", "length", "content_filter", None])
@pytest.mark.parametrize("model_class", [ChatOpenAI, RobustChatOpenAI])
def test_sync_actual_http_contract(caller, finish, model_class):
    requests = []

    def upstream(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=sse(finish=finish))

    with httpx.Client(transport=httpx.MockTransport(upstream)) as client:
        model = model_class(model="offline", api_key="test-only", base_url="https://offline.invalid/v1", http_client=client, streaming=False, disable_streaming=True, extra_body={"stream": False}, max_retries=0)
        bound = model.bind(stream=False, extra_body={"stream": False}).with_config(tags=["existing"])
        config = {"run_name": "memory_agent", "tags": ["caller"], "metadata": {"trace": "test"}}
        if finish == "stop":
            assert caller(bound, "test", config=config).content == '{"ok":true}'
        else:
            with pytest.raises(ValueError):
                caller(bound, "test", config=config)
        assert model.disable_streaming is True
        assert model.streaming is False
        assert model.extra_body == {"stream": False}
        assert config["tags"] == ["caller"]
    assert len(requests) == 1
    assert requests[0]["stream"] is True


@pytest.mark.parametrize("model_class", [ChatOpenAI, RobustChatOpenAI])
def test_async_actual_http_contract_and_graph_privacy(model_class):
    requests = []

    def upstream(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=sse())

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
            model = model_class(model="offline", api_key="test-only", base_url="https://offline.invalid/v1", http_async_client=client, streaming=False, disable_streaming=True, max_retries=0)

            async def node(state):
                result = await astream_response(model.with_config(tags=["existing"]), "test", config={"run_name": "private"})
                assert result.content == '{"ok":true}'
                return {"messages": [AIMessage(content="public result")]}

            graph = StateGraph(MessagesState)
            graph.add_node("background", node)
            graph.add_edge(START, "background")
            graph.add_edge("background", END)
            events = [e async for e in graph.compile().astream({"messages": [HumanMessage(content="test")]}, stream_mode=["messages", "updates"])]
            assert model.disable_streaming is True
            assert all(e[1][0].content == "public result" for e in events if e[0] == "messages")
            return events

    assert asyncio.run(run())
    assert requests[0]["stream"] is True


@pytest.mark.parametrize("caller", [stream_response, stream_memory_response])
def test_sync_midstream_failure_closes_without_partial_result(caller):
    closed = []

    def chunks(*args, **kwargs):
        assert kwargs["stream"] is True
        assert TAG_NOSTREAM in kwargs["config"]["tags"]
        try:
            yield AIMessageChunk(content='{"ok":true}')
            raise RuntimeError("injected disconnect")
        finally:
            closed.append(True)

    model = SimpleNamespace(stream=chunks)
    with pytest.raises(RuntimeError, match="disconnect"):
        caller(model, "test", config={})
    assert closed == [True]


@pytest.mark.parametrize("failure", [RuntimeError("disconnect"), asyncio.CancelledError()])
def test_async_failure_and_cancellation_close(failure):
    closed = []

    async def chunks(*args, **kwargs):
        try:
            yield AIMessageChunk(content='{"ok":true}')
            raise failure
        finally:
            closed.append(True)

    with pytest.raises(type(failure)):
        asyncio.run(astream_response(SimpleNamespace(astream=chunks), "test"))
    assert closed == [True]


@pytest.mark.parametrize("contents", [[], [""], [" ", " "]])
def test_empty_stream_is_error(contents):
    def chunks(*args, **kwargs):
        yield from (AIMessageChunk(content=c) for c in contents)

    with pytest.raises(ValueError):
        stream_response(SimpleNamespace(stream=chunks), "test")


def test_structured_chunks_and_usage_are_merged():
    def chunks(*args, **kwargs):
        yield AIMessageChunk(content=[{"type": "text", "text": "first", "index": 0}])
        yield AIMessageChunk(content=[{"type": "text", "text": "second", "index": 0}], usage_metadata={"input_tokens": 2, "output_tokens": 3, "total_tokens": 5})

    result = stream_response(SimpleNamespace(stream=chunks), "test")
    assert result.text == "firstsecond"
    assert result.usage_metadata["total_tokens"] == 5


def test_oneshot_and_goal_use_streams(monkeypatch):
    from deerflow.runtime import goal
    from deerflow.utils import oneshot_llm

    requests = []

    async def chunks(*args, **kwargs):
        requests.append(kwargs)
        yield AIMessageChunk(content='{"satisfied":true,"blocker":"none","reason":"done","evidence_summary":"done"}')

    model = SimpleNamespace(astream=chunks)
    monkeypatch.setattr(oneshot_llm, "create_chat_model", lambda **kwargs: model)
    monkeypatch.setattr(oneshot_llm, "inject_langfuse_metadata", lambda *args, **kwargs: None)
    monkeypatch.setattr(oneshot_llm, "get_effective_user_id", lambda: "test")

    async def run():
        assert await oneshot_llm.run_oneshot_llm(system_instruction="test", user_content="test", run_name="suggest_agent", app_config=object())
        result = await goal.evaluate_goal_completion(goal.build_goal_state("test"), [HumanMessage(content="test"), AIMessage(content="done")], model=model)
        assert result["satisfied"] is True

    asyncio.run(run())
    assert [r["config"]["run_name"] for r in requests] == ["suggest_agent", "goal_evaluator"]
    assert all(r["stream"] is True and TAG_NOSTREAM in r["config"]["tags"] for r in requests)


def test_security_scan_never_accepts_partial_allow(monkeypatch):
    from deerflow.skills import security_scanner as scanner

    async def chunks(*args, **kwargs):
        yield AIMessageChunk(content='{"decision":"allow","reason":"ok"}')
        raise RuntimeError("disconnect")

    monkeypatch.setattr(scanner, "create_chat_model", lambda **kwargs: SimpleNamespace(astream=chunks))
    monkeypatch.setattr(scanner, "_MODEL_RETRY_DELAYS_SECONDS", (0, 0, 0))
    config = SimpleNamespace(skill_evolution=SimpleNamespace(moderation_model_name=None))
    assert asyncio.run(scanner.scan_skill_content("test", app_config=config)).decision == "block"


def test_memory_does_not_finalize_partial_stream(monkeypatch):
    from deerflow.agents.memory.backends.deermem.deermem.config import DeerMemConfig
    from deerflow.agents.memory.backends.deermem.deermem.core.updater import MemoryUpdater

    def chunks(*args, **kwargs):
        yield AIMessageChunk(content='{"newFacts":[]}')
        raise RuntimeError("disconnect")

    updater = MemoryUpdater(config=DeerMemConfig(), storage=MagicMock(), llm=SimpleNamespace(stream=chunks))
    monkeypatch.setattr(updater, "_prepare_update_prompt", lambda **kwargs: ({}, "test"))
    finalize = MagicMock()
    monkeypatch.setattr(updater, "_finalize_update", finalize)
    assert updater._do_update_memory_sync_impl([]) is False
    finalize.assert_not_called()


def test_summary_keeps_old_state_on_disconnect(monkeypatch):
    from deerflow.agents.middlewares.summarization_middleware import DeerFlowSummarizationMiddleware

    model = MagicMock()
    model.with_config.return_value = model

    def chunks(*args, **kwargs):
        yield AIMessageChunk(content="partial summary")
        raise RuntimeError("disconnect")

    async def achunks(*args, **kwargs):
        for chunk in chunks(*args, **kwargs):
            yield chunk

    model.stream.side_effect = chunks
    model.astream.side_effect = achunks
    middleware = DeerFlowSummarizationMiddleware(model=model, token_counter=len, trigger=("messages", 4), keep=("messages", 2))
    monkeypatch.setattr(middleware, "_build_summary_prompt", lambda *args, **kwargs: "test")
    assert middleware._summarize_with([HumanMessage(content="test")]) is None
    assert asyncio.run(middleware._asummarize_with([HumanMessage(content="test")])) is None
    model.invoke.assert_not_called()
    model.ainvoke.assert_not_called()


@pytest.mark.parametrize("entry", ["memory", "summary_sync", "summary_async", "oneshot", "goal", "security"])
def test_each_entry_sends_stream_true(monkeypatch, entry):
    from deerflow.agents.memory.backends.deermem.deermem.config import DeerMemConfig
    from deerflow.agents.memory.backends.deermem.deermem.core.updater import MemoryUpdater
    from deerflow.agents.middlewares.summarization_middleware import DeerFlowSummarizationMiddleware
    from deerflow.runtime import goal
    from deerflow.skills import security_scanner
    from deerflow.utils import oneshot_llm

    requests = []
    payload = '{"satisfied":true,"blocker":"none","decision":"allow","reason":"test"}'

    def upstream(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=sse(text=payload))

    async def run():
        with httpx.Client(transport=httpx.MockTransport(upstream)) as sync_client:
            async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as async_client:
                model = RobustChatOpenAI(model="offline", api_key="test-only", base_url="https://offline.invalid/v1", http_client=sync_client, http_async_client=async_client, streaming=False, disable_streaming=True, max_retries=0)
                if entry == "memory":
                    updater = MemoryUpdater(config=DeerMemConfig(), storage=MagicMock(), llm=model)
                    monkeypatch.setattr(updater, "_prepare_update_prompt", lambda **kwargs: ({}, "test"))
                    finish = MagicMock(return_value=True)
                    monkeypatch.setattr(updater, "_finalize_update", finish)
                    assert await asyncio.to_thread(updater._do_update_memory_sync_impl, [])
                    assert finish.call_args.kwargs["response_content"] == payload
                elif entry.startswith("summary"):
                    summary = DeerFlowSummarizationMiddleware(model=model, token_counter=len, trigger=("messages", 4), keep=("messages", 2))
                    monkeypatch.setattr(summary, "_build_summary_prompt", lambda *args, **kwargs: "test")
                    messages = [HumanMessage(content="test")]
                    result = summary._summarize_with(messages) if entry == "summary_sync" else await summary._asummarize_with(messages)
                    assert result == payload
                elif entry == "oneshot":
                    monkeypatch.setattr(oneshot_llm, "create_chat_model", lambda **kwargs: model)
                    assert await oneshot_llm.run_oneshot_llm(system_instruction="test", user_content="test", run_name="input_polish", app_config=object()) == payload
                elif entry == "goal":
                    result = await goal.evaluate_goal_completion(goal.build_goal_state("test"), [HumanMessage(content="test"), AIMessage(content="done")], model=model)
                    assert result["satisfied"]
                else:
                    monkeypatch.setattr(security_scanner, "create_chat_model", lambda **kwargs: model)
                    config = SimpleNamespace(skill_evolution=SimpleNamespace(moderation_model_name=None))
                    assert (await security_scanner.scan_skill_content("test", app_config=config)).decision == "allow"
                assert model.disable_streaming is True and model.streaming is False

    asyncio.run(run())
    assert len(requests) == 1
    assert requests[0]["stream"] is True
