"""Real SDK + synthetic HTTP bytes: no live endpoint or credentials."""
import asyncio
import json
from contextlib import aclosing
from types import SimpleNamespace

import httpx
import pytest

from deerflow.agents.middlewares.llm_error_handling_middleware import LLMErrorHandlingMiddleware
from deerflow.config.app_config import AppConfig
from deerflow.config.sandbox_config import SandboxConfig
from deerflow.llm_timing_context import reset_current_llm_timing_context, set_current_llm_timing_context
from deerflow.models.cnooc_deepseek_v41 import CnoocDeepSeekV41Chat
from deerflow.models.cnooc_stream_guard import install_cnooc_stream_guard
from deerflow.models.robust_openai import OpenAICompatibleResponseError, RobustChatOpenAI


def frame(delta=None, finish=None, **extra):
    return {'id': 'synthetic', 'choices': [{'index': 0, 'delta': delta or {}, 'finish_reason': finish}], **extra}


def wire(frames, done=True):
    return ''.join('data: ' + json.dumps(f, ensure_ascii=False) + '\n\n' for f in frames) + ('data: [DONE]\n\n' if done else '')


def model(body, **kwargs):
    def serve(request):
        assert request.headers['authorization'] == 'Bearer synthetic-test-key'
        return httpx.Response(200, headers={'content-type': 'text/event-stream'}, content=body)
    transport = httpx.MockTransport(serve)
    return CnoocDeepSeekV41Chat(model='DeepSeek-V4.1-Flash-1017', api_key='synthetic-test-key',
                              transport=transport, async_transport=transport, **kwargs)


@pytest.mark.parametrize('delta', [{'role': 'assistant'}, {'reasoning_content': 'abcde'}, {'content': 'partial'},
                                  {'tool_calls': [{'index': 0, 'id': 'tc', 'type': 'function', 'function': {'name': 'bash', 'arguments': '{}'}}]}])
@pytest.mark.parametrize('done', [False, True])
def test_sync_rejects_incomplete_real_sdk_stream(delta, done, caplog):
    m = model(wire([frame(delta)], done))
    try:
        with pytest.raises(OpenAICompatibleResponseError) as err:
            list(m.stream('synthetic'))
        assert err.value.code == 'INCOMPLETE_STREAM'
        assert 'LLM_INCOMPLETE_STREAM' in caplog.text
    finally:
        m.http_client.close()
        asyncio.run(m.http_async_client.aclose())


@pytest.mark.asyncio
@pytest.mark.parametrize('done', [False, True])
@pytest.mark.parametrize('reasoning', ['', 'abcde'])
async def test_async_rejects_empty_or_reasoning_only_without_finish(done, reasoning, caplog):
    m = model(wire([frame({'role': 'assistant', 'reasoning_content': reasoning})], done))
    try:
        with pytest.raises(OpenAICompatibleResponseError):
            _ = [chunk async for chunk in m.astream('synthetic')]
        assert 'finish_present=False' in caplog.text
    finally:
        m.http_client.close()
        await m.http_async_client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize('finish', ['stop', 'tool_calls', 'length', 'content_filter'])
@pytest.mark.parametrize('done', [False, True])
async def test_completed_stream_retains_finish_and_does_not_require_usage(finish, done):
    m = model(wire([frame({'content': '正常内容'}, finish)], done))
    try:
        chunks = [chunk async for chunk in m.astream('synthetic')]
        assert any(c.response_metadata.get('finish_reason') == finish for c in chunks)
        assert ''.join(c.content for c in chunks) == '正常内容'
    finally:
        m.http_client.close()
        await m.http_async_client.aclose()


class SplitBytes(httpx.SyncByteStream, httpx.AsyncByteStream):
    def __init__(self, body):
        self.body = body
    def __iter__(self):
        for b in self.body:
            yield bytes([b])
    async def __aiter__(self):
        for b in self.body:
            yield bytes([b])


@pytest.mark.asyncio
async def test_split_utf8_crlf_comments_keep_sdk_parser_and_tool_arguments():
    tool = {'index': 0, 'id': 'tc', 'type': 'function', 'function': {'name': 'bash', 'arguments': '{"description":"中文"}'}}
    body = (': keepalive\n\n' + wire([frame({'tool_calls': [tool]}), frame(finish='tool_calls')])).replace('\n', '\r\n').encode()
    transport = httpx.MockTransport(lambda _: httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=SplitBytes(body)))
    m = CnoocDeepSeekV41Chat(api_key='synthetic-test-key', async_transport=transport)
    try:
        chunks = [chunk async for chunk in m.astream('synthetic')]
        combined = chunks[0]
        for c in chunks[1:]: combined += c
        assert combined.tool_calls[0]['args'] == {'description': '中文'}
        assert combined.response_metadata['finish_reason'] == 'tool_calls'
    finally:
        m.http_client.close()
        await m.http_async_client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize('recover', [False, True])
async def test_existing_provider_retry_is_bounded_and_recovers(recover, monkeypatch):
    attempts = []
    def serve(request):
        attempts.append(request)
        healthy = recover and len(attempts) == 2
        return httpx.Response(200, headers={'content-type': 'text/event-stream'},
                              content=wire([frame({'content': 'ok'} if healthy else {'reasoning_content': 'abc'}, 'stop' if healthy else None)]))
    m = CnoocDeepSeekV41Chat(api_key='synthetic-test-key', streaming=True, async_transport=httpx.MockTransport(serve))
    middleware = LLMErrorHandlingMiddleware(app_config=AppConfig(sandbox=SandboxConfig(use='test')))
    middleware.retry_base_delay_ms = 0
    monkeypatch.setattr('langgraph.config.get_stream_writer', lambda: lambda _: None)
    async def handler(_):
        return await m.ainvoke('synthetic')
    try:
        result = await middleware.awrap_model_call(SimpleNamespace(), handler)
        assert len(attempts) == 2
        if recover:
            assert result.content == 'ok'
            assert not result.additional_kwargs.get('deerflow_error_fallback')
        else:
            assert result.additional_kwargs['deerflow_error_fallback']
            assert 'INCOMPLETE_STREAM' in result.additional_kwargs['error_detail']
            assert result.tool_calls == []
    finally:
        m.http_client.close()
        await m.http_async_client.aclose()


@pytest.mark.asyncio
async def test_cancelled_consumer_is_not_reported_as_eof_failure(caplog):
    m = model(wire([frame({'content': 'partial'}), frame(finish='stop')]))
    try:
        async with aclosing(m.astream('synthetic')) as stream:
            await anext(stream)
        assert 'LLM_INCOMPLETE_STREAM' not in caplog.text
    finally:
        m.http_client.close()
        await m.http_async_client.aclose()


def test_warning_contains_context_and_counters_not_content(caplog):
    token = set_current_llm_timing_context(thread_id='thread-test', run_id='run-test')
    m = model(wire([frame({'content': 'PRIVATE-BODY', 'reasoning_content': 'PRIVATE-THOUGHT'})]))
    try:
        with pytest.raises(OpenAICompatibleResponseError): list(m.stream('PRIVATE-PROMPT'))
        assert 'thread_id=thread-test run_id=run-test' in caplog.text
        assert 'http_status=200' in caplog.text
        assert 'boundary=done' in caplog.text
        assert 'PRIVATE' not in caplog.text
        assert 'synthetic-test-key' not in caplog.text
    finally:
        reset_current_llm_timing_context(token)
        m.http_client.close()
        asyncio.run(m.http_async_client.aclose())


def test_guard_is_scoped_and_unknown_sdk_shapes_fail_visibly():
    assert RobustChatOpenAI._prepare_provider_stream(None, object()) is None
    with pytest.raises(RuntimeError, match='Unsupported V4.1 SDK stream shape'):
        install_cnooc_stream_guard(object())


@pytest.mark.asyncio
async def test_concurrent_responses_do_not_share_terminal_or_run_state(caplog):
    def serve(request):
        good = json.loads(request.content)['messages'][-1]['content'] == 'good'
        return httpx.Response(200, headers={'content-type': 'text/event-stream'},
                              content=wire([frame({'content': 'ok'} if good else {}, 'stop' if good else None)]))
    m = CnoocDeepSeekV41Chat(api_key='synthetic-test-key', async_transport=httpx.MockTransport(serve))
    async def execute(name):
        token = set_current_llm_timing_context(thread_id='thread-' + name, run_id='run-' + name)
        try:
            return [c async for c in m.astream(name)]
        finally:
            reset_current_llm_timing_context(token)
    try:
        good, bad = await asyncio.gather(execute('good'), execute('bad'), return_exceptions=True)
        assert isinstance(good, list)
        assert isinstance(bad, OpenAICompatibleResponseError)
        assert 'run_id=run-bad' in caplog.text
        assert 'run_id=run-good' not in caplog.text
    finally:
        m.http_client.close()
        await m.http_async_client.aclose()


@pytest.mark.asyncio
async def test_provider_business_error_keeps_original_category(caplog):
    m = model(wire([{'choices': None, 'code': '20009', 'success': False, 'msg': 'synthetic rejection'}]))
    try:
        with pytest.raises(OpenAICompatibleResponseError) as err:
            _ = [c async for c in m.astream('synthetic')]
        assert err.value.code == '20009'
        assert 'LLM_INCOMPLETE_STREAM' not in caplog.text
    finally:
        m.http_client.close()
        await m.http_async_client.aclose()
