"""Real SDK/adapter contract against an offline HTTP transport, never a provider."""

import asyncio
import json

import httpx
import pytest
from langchain_core.messages import HumanMessage, ToolMessage

from deerflow.models.enterprise_glm import EnterpriseGLMChat


def chunk(delta, finish=None, usage=None):
    return dict(id="fixture", object="chat.completion.chunk", created=1, model="GLM-5.3-Flash", choices=[dict(index=0, delta=delta, finish_reason=finish)], usage=usage)


def sse(*frames):
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content="".join("data: " + json.dumps(frame) + "\n\n" for frame in frames) + "data: [DONE]\n\n")


def client(http, **kwargs):
    return EnterpriseGLMChat(model="GLM-5.3-Flash", api_key="offline-test-only", base_url="https://glm.invalid/v1", http_async_client=http, max_retries=0, **kwargs)


@pytest.mark.asyncio
async def test_real_sdk_stream_tool_roundtrip_and_last_content_chunk():
    requests = []

    def respond(request):
        assert str(request.url) == "https://glm.invalid/v1/chat/completions"
        body = json.loads(request.content)
        requests.append(body)
        assert body["thinking"] == {"type": "enabled"}
        assert body["model"] == "GLM-5.3-Flash" and body["stream"] is True
        assert body["stream_options"]["include_usage"] is True
        if len(requests) == 1:
            return sse(
                chunk({"role": "assistant", "reasoning_content": "synthetic reasoning"}),
                chunk({"tool_calls": [{"index": 0, "id": "call1", "type": "function", "function": {"name": "echo", "arguments": '{"value":'}}]}),
                chunk({"tool_calls": [{"index": 0, "function": {"arguments": '"safe"}'}}]}, "tool_calls"),
                dict(id="fixture", object="chat.completion.chunk", created=1, model="GLM-5.3-Flash", choices=[], usage=dict(prompt_tokens=10, completion_tokens=4, total_tokens=14)),
            )
        assert body["messages"][1]["reasoning_content"] == "synthetic reasoning"
        assert body["messages"][1]["tool_calls"][0]["id"] == "call1"
        assert body["messages"][2] == {"role": "tool", "content": "safe", "tool_call_id": "call1"}
        return sse(chunk({"role": "assistant", "content": "answer "}), chunk({"content": "tail"}, "stop"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        model = client(http, streaming=True, stream_usage=True).bind_tools(
            [{"type": "function", "function": {"name": "echo", "description": "test", "parameters": {"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"]}}}]
        )
        initial = [HumanMessage("fixture")]
        answer = await model.ainvoke(initial)
        assert answer.tool_calls == [dict(name="echo", args={"value": "safe"}, id="call1", type="tool_call")]
        assert answer.usage_metadata["total_tokens"] == 14
        final = await model.ainvoke(initial + [answer, ToolMessage("safe", tool_call_id="call1")])
        assert final.content == "answer tail"
    assert len(requests) == 2


@pytest.mark.asyncio
async def test_real_sdk_nonstream_image_is_first_and_reasoning_retained():
    def respond(request):
        body = json.loads(request.content)
        assert body["stream"] is False
        assert body["messages"][0]["content"][0]["type"] == "image_url"
        assert body["messages"][0]["content"][1]["type"] == "text"
        return httpx.Response(
            200, json=dict(id="fixture", object="chat.completion", created=1, model="GLM-5.3-Flash", choices=[dict(index=0, message=dict(role="assistant", content="red", reasoning_content="fixture"), finish_reason="stop")])
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        result = await client(http).ainvoke([HumanMessage([{"type": "text", "text": "color?"}, {"type": "image_url", "image_url": {"url": "data:image/png;base64,aGVsbG8="}}])])
    assert result.content == "red"
    assert result.additional_kwargs["reasoning_content"] == "fixture"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 500])
async def test_provider_errors_are_not_automatically_retried(status):
    count = 0

    def respond(request):
        nonlocal count
        count += 1
        return httpx.Response(status, json={"error": {"message": "synthetic failure", "type": "fixture"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        with pytest.raises(Exception):
            await client(http).ainvoke("fixture")
    assert count == 1


@pytest.mark.asyncio
async def test_closing_wrapper_closes_parent_generator_immediately(monkeypatch):
    from deerflow.models.robust_openai import RobustChatOpenAI

    closed = asyncio.Event()

    async def parent(*args, **kwargs):
        try:
            yield "fixture"
            await asyncio.Event().wait()
        finally:
            closed.set()

    monkeypatch.setattr(RobustChatOpenAI, "_astream", parent)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: None)) as http:
        stream = client(http)._astream([])
        assert await anext(stream) == "fixture"
        await stream.aclose()
        assert closed.is_set()


def test_responses_protocol_override_rejected_before_io():
    with pytest.raises(ValueError, match="Chat Completions"):
        EnterpriseGLMChat(model="GLM-5.3-Flash", api_key="offline", use_responses_api=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("effort", ["high", "max"])
@pytest.mark.parametrize("streaming", [False, True])
async def test_documented_effort_reaches_wire_with_fixed_thinking(effort, streaming):
    def respond(request):
        body = json.loads(request.content)
        assert body["thinking"] == {"type": "enabled"}
        assert body["reasoning_effort"] == effort
        assert body["model"] == "GLM-5.3-Flash"
        if streaming:
            return sse(chunk({"role": "assistant", "content": "ok"}, "stop"))
        return httpx.Response(200, json=dict(id="fixture", object="chat.completion", created=1, model="GLM-5.3-Flash",
                                            choices=[dict(index=0, message=dict(role="assistant", content="ok"), finish_reason="stop")]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        result = await client(http, streaming=streaming, extra_body={"thinking": {"type": "enabled"}, "reasoning_effort": effort}).ainvoke("fixture")
    assert result.content == "ok"


@pytest.mark.asyncio
async def test_documented_error_frame_is_not_a_successful_answer():
    import openai

    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: sse({"error": {"message": "synthetic failure", "type": "fixture"}}))) as http:
        with pytest.raises(openai.APIError):
            await client(http, streaming=True).ainvoke("fixture")
