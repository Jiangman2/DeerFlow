"""Real LangGraph -> RobustChatOpenAI -> SDK wire contract; no model/network calls."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from langgraph.graph import END, START, MessagesState, StateGraph
from openai import InternalServerError
from test_oa_policy_flow import _config, _job_values

from app.gateway import oa_policy_flow_service, services
from deerflow.models.robust_openai import RobustChatOpenAI


def _sse_payload():
    deltas = [
        {"role": "assistant", "tool_calls": [{"index": 0, "id": "call-1", "type": "function", "function": {"name": "write_json", "arguments": '{"name":'}}]},
        {"tool_calls": [{"index": 0, "function": {"arguments": '"流程.json"}'}}]},
    ]
    chunks = [{"id": "chat-1", "object": "chat.completion.chunk", "created": 1, "model": "test-model", "choices": [{"index": 0, "delta": delta, "finish_reason": None}]} for delta in deltas]
    chunks.append({"id": "chat-1", "object": "chat.completion.chunk", "created": 1, "model": "test-model", "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]})
    chunks.append({"id": "chat-1", "object": "chat.completion.chunk", "created": 1, "model": "test-model", "choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13}})
    return ("".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n").encode()


@pytest.mark.asyncio
@pytest.mark.parametrize("upstream_error", [False, True])
async def test_oa_launch_streams_sdk_request_and_assembles_tool_calls(monkeypatch, upstream_error):
    captured = {}

    def handle(request):
        captured["wire"] = json.loads(request.content)
        if upstream_error:
            return httpx.Response(504, text="upstream timed out")
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=_sse_payload())

    monkeypatch.setattr(services, "get_app_config", lambda: SimpleNamespace(scheduler=SimpleNamespace(recursion_limit=1000), max_recursion_limit=1000))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        model = RobustChatOpenAI(model="test-model", api_key="test-only", base_url="https://model.invalid/v1", http_async_client=client, max_retries=0, stream_usage=True)
        bound = model.bind_tools([{"type": "function", "function": {"name": "write_json", "description": "Write JSON", "parameters": {"type": "object", "properties": {"name": {"type": "string"}}}}}])

        async def node(state):
            return {"messages": [await bound.ainvoke(state["messages"])]}

        graph = StateGraph(MessagesState)
        graph.add_node("model", node)
        graph.add_edge(START, "model")
        graph.add_edge("model", END)
        compiled = graph.compile()

        async def start_run(body, thread_id, request):
            captured["body"] = body
            captured["events"] = []
            try:
                async for event in compiled.astream(body.input, stream_mode=body.stream_mode):
                    captured["events"].append(event)
            except InternalServerError:
                captured["error"] = True
            return SimpleNamespace(run_id="run-test", thread_id=thread_id)

        monkeypatch.setattr(services, "start_run", start_run)
        # Real OA orchestration + real shared launcher, only persistence is stubbed.
        app = SimpleNamespace(state=SimpleNamespace(run_store=SimpleNamespace(list_by_thread=AsyncMock(return_value=[]))))
        service = oa_policy_flow_service.OAPolicyFlowService(app=app, repository=SimpleNamespace(patch=AsyncMock(return_value=True)), config=_config())
        job = {**_job_values(), "prepared_at": "2026-10-16T00:00:00+00:00"}
        await service._prepare_and_launch(job)

    assert captured["body"].stream_mode == ["values", "messages"]
    assert captured["body"].context == {"non_interactive": True, "user_id": job["owner_user_id"]}
    assert captured["body"].on_disconnect == "continue"
    assert captured["wire"]["stream"] is True
    assert captured["wire"]["stream_options"] == {"include_usage": True}
    if upstream_error:
        assert captured["error"] is True
        assert not any(mode == "messages" for mode, _ in captured["events"])
    else:
        assert any(mode == "messages" for mode, _ in captured["events"])
        final = [value for mode, value in captured["events"] if mode == "values"][-1]["messages"][-1]
        assert final.tool_calls[0]["args"] == {"name": "流程.json"}
        assert final.tool_calls[0]["id"] == "call-1"
        assert final.usage_metadata["total_tokens"] == 13


@pytest.mark.asyncio
async def test_ordinary_scheduled_launch_keeps_default_stream_mode(monkeypatch):
    monkeypatch.setattr(services, "get_app_config", lambda: SimpleNamespace(scheduler=SimpleNamespace(recursion_limit=1000), max_recursion_limit=1000))
    start = AsyncMock(return_value=SimpleNamespace(run_id="run-1", thread_id="thread-1"))
    monkeypatch.setattr(services, "start_run", start)
    await services.launch_scheduled_thread_run(thread_id="thread-1", assistant_id="lead_agent", prompt="test", app=SimpleNamespace(), owner_user_id="owner-1")
    assert start.await_args.args[0].stream_mode is None
