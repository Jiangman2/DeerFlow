"""Real SDK SSE termination tests, with no network or credentials."""

import asyncio
import json

import httpx
import openai
import pytest

from deerflow.models.enterprise_glm import EnterpriseGLMChat
from deerflow.models.output_safety import ModelOutputSafetyError
from deerflow.models.robust_openai import RobustChatOpenAI


def frame(delta, finish=None):
    return {"id": "fixture", "object": "chat.completion.chunk", "created": 1, "model": "GLM-5.3-Flash", "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}


def event(value):
    return ("data: " + json.dumps(value, ensure_ascii=False) + "\r\n\r\n").encode()


DONE = b"data: [DONE]\r\n\r\n"
FINAL = event(frame({"role": "assistant", "content": "正常末尾"}, "stop"))
USAGE = event({"id": "fixture", "object": "chat.completion.chunk", "created": 1, "model": "GLM-5.3-Flash", "choices": [], "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}})


class Bytes(httpx.SyncByteStream, httpx.AsyncByteStream):
    def __init__(self, body):
        self.body = body
        self.closed = False

    def __iter__(self):
        for byte in self.body:
            yield bytes([byte])

    async def __aiter__(self):
        for byte in self.body:
            yield bytes([byte])

    def close(self):
        self.closed = True

    async def aclose(self):
        self.closed = True


def response(body):
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=body)


def model(http, asynchronous, cls=EnterpriseGLMChat, **kw):
    return cls(model="GLM-5.3-Flash", api_key="offline-only", base_url="https://fixture.invalid/v1", max_retries=0, streaming=True, stream_usage=True, **{"http_async_client" if asynchronous else "http_client": http}, **kw)


@pytest.mark.asyncio
@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("headers", [False, True])
async def test_complete_fragmented_utf8_crlf_comments_usage(asynchronous, headers):
    stream = Bytes(b": keepalive\r\n\r\n" + FINAL + USAGE + DONE)
    transport = httpx.MockTransport(lambda _: response(stream))
    if asynchronous:
        async with httpx.AsyncClient(transport=transport) as http:
            result = await model(http, True, include_response_headers=headers).ainvoke("fixture")
    else:
        with httpx.Client(transport=transport) as http:
            result = model(http, False, include_response_headers=headers).invoke("fixture")
    assert result.content == "正常末尾"
    assert result.usage_metadata["total_tokens"] == 5
    assert stream.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize(
    "body",
    [
        FINAL,
        event(frame({"role": "assistant", "content": "partial"})),
        DONE,
        FINAL + b"data: [DONE]junk\n\n",
        FINAL + b"data: [DONE]",
        event(frame({"role": "assistant", "content": "[DONE]"}, "stop")),
        event(frame({"role": "assistant", "tool_calls": [{"index": 0, "id": "call1", "type": "function", "function": {"name": "echo", "arguments": '{"x":1}'}}]}, "tool_calls")),
        b"",
    ],
)
async def test_incomplete_stream_never_returns_completed_message(asynchronous, body):
    stream = Bytes(body)
    calls = []

    def handler(request):
        calls.append(request)
        return response(stream)

    transport = httpx.MockTransport(handler)
    with pytest.raises(ModelOutputSafetyError, match="incomplete_glm_stream"):
        if asynchronous:
            async with httpx.AsyncClient(transport=transport) as http:
                await model(http, True).ainvoke("fixture")
        else:
            with httpx.Client(transport=transport) as http:
                model(http, False).invoke("fixture")
    assert len(calls) == 1
    assert stream.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("asynchronous", [False, True])
async def test_error_frame_preserves_sdk_exception(asynchronous):
    stream = Bytes(event({"error": {"message": "fixture", "type": "fixture"}}))
    transport = httpx.MockTransport(lambda _: response(stream))
    with pytest.raises(openai.APIError):
        if asynchronous:
            async with httpx.AsyncClient(transport=transport) as http:
                await model(http, True).ainvoke("fixture")
        else:
            with httpx.Client(transport=transport) as http:
                model(http, False).invoke("fixture")
    assert stream.closed


@pytest.mark.asyncio
async def test_concurrent_stream_state_is_not_shared():
    def handler(request):
        complete = json.loads(request.content)["messages"][0]["content"] == "complete"
        return response(Bytes(FINAL + (DONE if complete else b"")))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        llm = model(http, True)
        good, bad = await asyncio.gather(llm.ainvoke("complete"), llm.ainvoke("partial"), return_exceptions=True)
    assert good.content == "正常末尾"
    assert isinstance(bad, ModelOutputSafetyError)


@pytest.mark.asyncio
async def test_other_provider_not_changed():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: response(Bytes(FINAL)))) as http:
        result = await model(http, True, cls=RobustChatOpenAI).ainvoke("fixture")
    assert result.content == "正常末尾"


@pytest.mark.asyncio
async def test_cancel_closes_transport_without_masking_cancellation():
    waiting = asyncio.Event()

    class Waiting(Bytes):
        async def __aiter__(self):
            yield event(frame({"role": "assistant", "content": "partial"}))
            waiting.set()
            await asyncio.Event().wait()

    stream = Waiting(b"")
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: response(stream))) as http:
        task = asyncio.create_task(model(http, True).ainvoke("fixture"))
        await asyncio.wait_for(waiting.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert stream.closed


def test_unknown_sdk_shape_fails_closed():
    from deerflow.models.glm_stream_guard import install_glm_stream_guard

    with pytest.raises(ModelOutputSafetyError):
        install_glm_stream_guard(object())


@pytest.mark.asyncio
@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("complete", [False, True])
async def test_structured_sdk_wrapper_also_requires_done(asynchronous, complete):
    from pydantic import BaseModel

    class Answer(BaseModel):
        value: str

    stream = Bytes(event(frame({"role": "assistant", "content": '{"value":"ok"}'}, "stop")) + (DONE if complete else b""))
    transport = httpx.MockTransport(lambda _: response(stream))

    async def run():
        if asynchronous:
            async with httpx.AsyncClient(transport=transport) as http:
                return await model(http, True).with_structured_output(Answer, method="json_schema").ainvoke("fixture")
        with httpx.Client(transport=transport) as http:
            return model(http, False).with_structured_output(Answer, method="json_schema").invoke("fixture")

    if complete:
        assert (await run()).value == "ok"
    else:
        with pytest.raises(ModelOutputSafetyError):
            await run()
    assert stream.closed
