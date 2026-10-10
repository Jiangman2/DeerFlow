"""Title transport streams privately and only publishes a complete title."""

import asyncio
import json
from unittest.mock import MagicMock

import httpx
import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph

from deerflow.agents.middlewares import title_middleware as module
from deerflow.agents.middlewares.title_middleware import TitleMiddleware, TitleMiddlewareState
from deerflow.config.title_config import TitleConfig
from deerflow.models.robust_openai import RobustChatOpenAI


def state():
    return {"messages": [HumanMessage(content="请帮我写测试"), AIMessage(content="好的")]}


@pytest.mark.parametrize("failure", [None, RuntimeError("stream failed"), asyncio.CancelledError()])
def test_title_consumes_stream_and_closes_on_failure(monkeypatch, failure):
    closed = []

    async def chunks(*args, **kwargs):
        try:
            yield AIMessageChunk(content="<thi")
            yield AIMessageChunk(content='nk>私有推理</think>"流式')
            if failure is not None:
                raise failure
            yield AIMessageChunk(content='标题"')
            yield AIMessageChunk(content="", usage_metadata={"input_tokens": 5, "output_tokens": 4, "total_tokens": 9})
        finally:
            closed.append(True)

    model = MagicMock()
    model.astream.side_effect = chunks
    monkeypatch.setattr(module, "create_chat_model", MagicMock(return_value=model))
    middleware = TitleMiddleware(title_config=TitleConfig())
    if isinstance(failure, asyncio.CancelledError):
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(middleware._agenerate_title_result(state()))
    else:
        result = asyncio.run(middleware._agenerate_title_result(state()))
        assert result == {"title": "请帮我写测试" if failure else "流式标题"}
    assert closed == [True]
    model.ainvoke.assert_not_called()
    model.astream.assert_called_once()
    assert model.astream.call_args.kwargs["stream"] is True
    assert model.disable_streaming is False


@pytest.mark.parametrize(
    "contents, expected",
    [
        ([], "请帮我写测试"),
        (["", "  "], "请帮我写测试"),
        (["一二三四五六", "七八九十一二三四五六"], "一二三四五六七八九十"),
        ([[{"type": "text", "text": "分段", "index": 0}], [{"type": "text", "text": "标题", "index": 0}]], "分段标题"),
    ],
)
def test_title_empty_structured_and_truncated_stream(monkeypatch, contents, expected):
    async def chunks(*args, **kwargs):
        for content in contents:
            yield AIMessageChunk(content=content)

    model = MagicMock()
    model.astream.side_effect = chunks
    monkeypatch.setattr(module, "create_chat_model", lambda **kwargs: model)
    middleware = TitleMiddleware(title_config=TitleConfig(max_chars=10))
    assert asyncio.run(middleware._agenerate_title_result(state())) == {"title": expected}
    model.ainvoke.assert_not_called()


@pytest.mark.parametrize("model_class", [ChatOpenAI, RobustChatOpenAI])
def test_real_chat_transport_streams_but_graph_hides_title_tokens(monkeypatch, model_class):
    requests = []

    def upstream(request):
        payload = json.loads(request.content)
        requests.append(payload)
        assert payload["stream"] is True
        events = []
        for text, finish in [("流式", None), ("标题", None), ("", "stop")]:
            event = {
                "id": "title-test",
                "object": "chat.completion.chunk",
                "model": "title-test",
                "choices": [{"index": 0, "delta": {"role": "assistant", "content": text}, "finish_reason": finish}],
            }
            events.append("data: " + json.dumps(event, ensure_ascii=False) + "\n\n")
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=("".join(events) + "data: [DONE]\n\n").encode())

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
            # Even a normally non-streaming model must use streaming transport here.
            model = model_class(model="title-test", api_key="test-only", base_url="https://title.invalid/v1", http_async_client=client, streaming=False, disable_streaming=True, max_retries=0)
            monkeypatch.setattr(module, "create_chat_model", lambda **kwargs: model)
            middleware = TitleMiddleware(title_config=TitleConfig())

            async def title_node(state):
                return await middleware._agenerate_title_result(state)

            graph = StateGraph(TitleMiddlewareState)
            graph.add_node("title", title_node)
            graph.add_edge(START, "title")
            graph.add_edge("title", END)
            return [event async for event in graph.compile().astream(state(), stream_mode=["messages", "updates"])]

    events = asyncio.run(run())
    assert len(requests) == 1
    assert events == [("updates", {"title": {"title": "流式标题"}})]
