"""Synthetic V4.1 verification with the operator-confirmed GLM credential.

No application startup, persistence, model-list edit, or credential export.
Run only through the packaged host tool, in a fresh interpreter.
"""
import asyncio
import base64
import contextlib
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
import re
import signal
import ssl
import stat
import struct
import sys
import warnings
import zlib

ENDPOINT = 'https://aiapi.cnooc.com.cn/DeepSeek-V4.1-Flash-1017/FNC-01'
MODEL = 'DeepSeek-V4.1-Flash-1017'
KEY_ENV = 'GLM_5_3_FLASH_API_KEY'
CONFIG_SHA = 'dbde51e71421be4ee4544684faa77baf18a522ecd87db663b2bcb4280589c36b'
ADAPTER_SHA = 'a79d5b0e8a117223ca8b04e23e8bf6b36f737b32f43ea62c747c9193629b5930'
MAX_REQUESTS = 7
CHECKS = ['native_document_request', 'adapter_nonstream_text', 'adapter_stream_text',
          'stream_tool_call', 'stream_tool_result', 'stream_thinking', 'stream_image_input']
TEXT = '这是连接测试，请只回复：连接正常。'


class Refused(RuntimeError):
    pass


def require(ok, code):
    if not ok:
        raise Refused(code)


class Sink:
    def write(self, value): return len(value)
    def flush(self): pass


def bounded(path, cap=2097152):
    with open(path, 'rb') as handle:
        before = os.fstat(handle.fileno())
        require(stat.S_ISREG(before.st_mode) and before.st_size <= cap, 'FILE_BOUND')
        raw = handle.read(cap + 1)
        after = os.fstat(handle.fileno())
    require(len(raw) == before.st_size and len(raw) <= cap and
            (before.st_ino, before.st_mtime_ns, before.st_size) ==
            (after.st_ino, after.st_mtime_ns, after.st_size), 'FILE_CHANGED')
    return raw


def credential_binding(raw, env):
    import yaml
    config = yaml.safe_load(raw)
    models = config.get('models')
    require(isinstance(models, list) and len(models) == 2 and
            all(isinstance(row, dict) for row in models), 'MODEL_LIST_CHANGED')
    require(models[0].get('name') == 'deepseek-v4-flash' and
            not any('v4.1' in str(row.get('name', '')).lower() for row in models),
            'DEFAULT_OR_V41_CHANGED')
    glm = [row for row in models if row.get('name') == 'glm-5.3-flash']
    require(len(glm) == 1 and glm[0].get('model') == 'GLM-5.3-Flash' and
            glm[0].get('api_key') == '$' + KEY_ENV, 'GLM_CREDENTIAL_REFERENCE_CHANGED')
    key = env.get(KEY_ENV)
    require(isinstance(key, str) and 8 <= len(key) <= 4096, 'GLM_CREDENTIAL_ABSENT')
    require(key == key.strip() and all(33 <= ord(c) <= 126 for c in key)
            and not key.lower().startswith('bearer'), 'GLM_CREDENTIAL_FORMAT')
    previous = env.get('DeepSeek_V4_API_KEY')
    return key, dict(credential_source=KEY_ENV, matches_configured_glm_reference=True,
                     previous_v4_key_present=bool(previous),
                     differs_from_previous_v4_key=(not hmac.compare_digest(key.encode(), previous.encode())) if previous else None,
                     default_model='deepseek-v4-flash', v41_enabled=False,
                     credential_values_exported=False)


def response_summary(raw, secret=None):
    """Export bounded categories and numeric business codes, never error prose."""
    row = {'response_bytes': len(raw), 'error_categories': [], 'business_codes': []}
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeError):
        row['response_format'] = 'NON_JSON'
        return row
    row['response_format'] = 'JSON'
    if not isinstance(data, dict): return row
    errors = [data]
    if isinstance(data.get('error'), dict): errors.append(data['error'])
    text = ' '.join(str(error.get(k, ''))[:2048] for error in errors for k in ('code', 'type', 'message', 'msg')).lower()
    for category, words in (
        ('payment_required', ('payment', '余额', '付费', '欠费', 'insufficient balance')),
        ('quota', ('quota', '额度', '配额', 'limit exceeded')),
        ('credential_or_permission', ('unauthorized', 'invalid api', 'invalid key', 'forbidden', 'permission', '鉴权', '权限', '未开通')),
        ('request_format', ('parameter', 'invalid request', '参数', '格式')),
        ('model_route', ('model not found', 'unknown model', '模型不存在')),
        ('rate_limit', ('rate limit', '限流')),
    ):
        if any(word in text for word in words): row['error_categories'].append(category)
    for error in errors:
        code = error.get('code')
        if secret is not None and str(code) == secret: continue
        if (isinstance(code, int) and not isinstance(code, bool) and abs(code) < 100000000) or (
            isinstance(code, str) and re.fullmatch(r'[0-9]{1,8}', code)):
            row['business_codes'].append(code)
    row['categories_are_hints_not_verified_root_cause'] = True
    return row


class Budget:
    def __init__(self, result, key):
        self.result, self.key, self.case = result, key, None

    def begin(self, name):
        self.case = {'name': name, 'provider_requests': 0, 'status': 'INCOMPLETE'}
        self.result['phase'] = name
        self.result['cases'].append(self.case)

    def request(self, request):
        require(self.case is not None and self.case['provider_requests'] == 0 and
                self.result['provider_requests'] < MAX_REQUESTS, 'REQUEST_LIMIT')
        require(str(request.url) == ENDPOINT and request.method == 'POST', 'REQUEST_ROUTE_CHANGED')
        require(hmac.compare_digest(request.headers.get('authorization', '').encode(),
                                    ('Bearer ' + self.key).encode()), 'REQUEST_CREDENTIAL_CHANGED')
        payload = json.loads(request.content)
        require(payload.get('model') == MODEL, 'REQUEST_MODEL_CHANGED')
        if self.case['name'] in CHECKS:
            require(bool(payload.get('stream')) == (self.case['name'] in CHECKS[2:]), 'REQUEST_STREAM_MODE_CHANGED')
        self.case.update(provider_requests=1, full_endpoint_verified=True, glm_credential_verified=True,
                         streaming=bool(payload.get('stream')), thinking_enabled=payload.get('thinking', {}).get('type') == 'enabled',
                         max_tokens=payload.get('max_tokens', payload.get('max_completion_tokens')))
        self.result['provider_requests'] += 1

    def accept(self):
        self.case['status'] = 'PASS'
        self.result['checks'].append(self.case['name'])


def synthetic_png():
    def chunk(kind, value):
        return struct.pack('!I', len(value)) + kind + value + struct.pack('!I', zlib.crc32(kind + value) & 0xffffffff)
    pixels = (b'\x00' + b'\x40\x80\xc0' * 64) * 64
    raw = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!IIBBBBB', 64, 64, 8, 2, 0, 0, 0))
    return 'data:image/png;base64,' + base64.b64encode(raw + chunk(b'IDAT', zlib.compress(pixels)) + chunk(b'IEND', b'')).decode()


async def verify(result, key, adapter, ca, transport=None):
    import httpx
    from langchain_core.messages import HumanMessage, ToolMessage
    budget = Budget(result, key)

    async def counted(request): budget.request(request)

    class LimitedStream(httpx.AsyncByteStream):
        def __init__(self, stream, row, error):
            self.stream, self.row, self.error = stream, row, error

        async def __aiter__(self):
            count = 0; error_body = bytearray()
            async for chunk in self.stream:
                count += len(chunk)
                require(count <= 1048576, 'RESPONSE_BOUND')
                if self.error:
                    require(count <= 65536, 'ERROR_RESPONSE_BOUND')
                    error_body.extend(chunk)
                yield chunk
            self.row['response_bytes'] = count
            if error_body: self.row.update(response_summary(bytes(error_body), key))

        async def aclose(self): await self.stream.aclose()

    async def response_hook(response):
        budget.case['http_status'] = response.status_code
        if response.is_stream_consumed:
            require(len(response.content) <= 1048576, 'RESPONSE_BOUND')
            if response.status_code >= 300: budget.case.update(response_summary(response.content[:65536], key))
        else:
            response.stream = LimitedStream(response.stream, budget.case, response.status_code >= 300)

    async def native():
        budget.begin(CHECKS[0])
        async with httpx.AsyncClient(verify=ssl.create_default_context(cafile=ca), trust_env=False,
                follow_redirects=False, timeout=35, transport=transport,
                event_hooks={'request': [counted], 'response': [response_hook]}) as client:
            async with client.stream('POST', ENDPOINT, headers={'Authorization': 'Bearer ' + key,
                    'Content-Type': 'application/json', 'Accept': 'application/json', 'Connection': 'keep-alive'},
                    json={'model': MODEL, 'messages': [{'role': 'user', 'content': TEXT}], 'max_tokens': 256,
                          'stream': False, 'temperature': 1.0, 'top_p': 0.95, 'thinking': {'type': 'disabled'}}) as response:
                raw = await response.aread()
                budget.case.update(response_summary(raw, key))
                require(response.status_code == 200, 'NATIVE_HTTP_NOT_ACCEPTED')
                data = json.loads(raw)
                choices = data.get('choices')
                require(isinstance(choices, list) and len(choices) == 1, 'NATIVE_RESPONSE_SHAPE')
                message = choices[0].get('message', {})
                budget.case['finish_reason'] = choices[0].get('finish_reason') if choices[0].get('finish_reason') in ('stop', 'length') else 'OTHER'
                require(isinstance(message.get('content'), str) and bool(message['content'].strip())
                        and choices[0].get('finish_reason') == 'stop', 'NATIVE_TEXT_INCOMPLETE')
                budget.accept()

    await asyncio.wait_for(native(), 45)
    model = adapter.CnoocDeepSeekV41Chat(model=MODEL, api_key=key, use_responses_api=False,
        max_retries=0, timeout=35, max_tokens=256, streaming=False, stream_usage=True,
        stream_chunk_timeout=35, temperature=1.0, top_p=0.95,
        extra_body={'thinking': {'type': 'disabled'}}, async_transport=transport)
    # Keep the installed exact-URL hook first; count only the final wire request.
    model.http_async_client.event_hooks['request'].append(counted)
    model.http_async_client.event_hooks['response'].append(response_hook)
    streaming_model = model.model_copy(update={'streaming': True})

    def text_ok(message):
        reason = message.response_metadata.get('finish_reason')
        budget.case['finish_reason'] = reason if reason in ('stop', 'length', 'tool_calls') else 'OTHER'
        return isinstance(message.content, str) and bool(message.content.strip()) and reason == 'stop'

    async def streamed(target, messages, **kwargs):
        message = None; count = 0
        async for chunk in target.astream(messages, config={'callbacks': []}, stream=True, **kwargs):
            count += 1
            require(count <= 8192, 'STREAM_CHUNK_BOUND')
            message = chunk if message is None else message + chunk
        require(message is not None, 'EMPTY_STREAM')
        budget.case['stream_chunks'] = count
        return message

    async def call(name, coro, accept):
        budget.begin(name)
        message = await asyncio.wait_for(coro, 45)
        require(accept(message), 'CAPABILITY_NOT_ACCEPTED')
        budget.accept()
        return message

    try:
        await call(CHECKS[1], model.ainvoke([HumanMessage(content=TEXT)], config={'callbacks': []}), text_ok)
        await call(CHECKS[2], streamed(streaming_model, [HumanMessage(content=TEXT)]), text_ok)
        tool = {'type': 'function', 'function': {'name': 'connection_probe', 'description': '无副作用连接测试',
                'parameters': {'type': 'object', 'properties': {'value': {'type': 'string'}},
                               'required': ['value'], 'additionalProperties': False}}}
        messages = [HumanMessage(content='请调用 connection_probe，value 填写 ok。')]
        def tool_ok(message):
            reason = message.response_metadata.get('finish_reason')
            calls = message.tool_calls
            valid = len(calls) == 1 and calls[0]['name'] == 'connection_probe' and calls[0]['args'] == {'value': 'ok'}
            budget.case.update(finish_reason=reason if reason in ('stop', 'length', 'tool_calls') else 'OTHER',
                               tool_call_count=len(calls), synthetic_tool_arguments_match=valid)
            return reason == 'tool_calls' and valid
        called = await call(CHECKS[3], streamed(streaming_model.bind_tools([tool], tool_choice='connection_probe'), messages),
                            tool_ok)
        await call(CHECKS[4], streamed(streaming_model.bind_tools([tool]), messages + [called,
            ToolMessage(content='测试成功，请只回复连接正常。', tool_call_id=called.tool_calls[0]['id'])]), text_ok)
        await call(CHECKS[5], streamed(streaming_model, [HumanMessage(content='1加1等于多少？用一句话回答。')],
            extra_body={'thinking': {'type': 'enabled'}}, reasoning_effort='low', max_tokens=2048), text_ok)
        await call(CHECKS[6], streamed(streaming_model, [HumanMessage(content=[
            {'type': 'text', 'text': '这是图片输入测试，请只回复已收到图片。'},
            {'type': 'image_url', 'image_url': {'url': synthetic_png(), 'detail': 'low'}}])]), text_ok)
    finally:
        await model.http_async_client.aclose()
        model.http_client.close()


def main():
    sys.path.insert(0, '/app/backend')
    sys.path.insert(0, '/app/backend/packages/harness')
    os.environ.update(LANGCHAIN_TRACING_V2='false', LANGSMITH_TRACING='false', OTEL_SDK_DISABLED='true')
    logging.disable(logging.CRITICAL); warnings.simplefilter('ignore')
    result = dict(schema='portal-v41-glm-provider.v1', status='INCOMPLETE', phase='BINDING',
        checks=[], cases=[], provider_requests=0, maximum_provider_requests=MAX_REQUESTS,
        credentials_exported=False, model_text_exported=False, raw_errors_exported=False,
        automatic_retry=False, application_started=False, database_access=False,
        files_modified=False, configuration_modified=False, v41_enabled=False,
        default_model_changed=False, strict_ca_and_hostname_verification=True)
    try:
        def deadline(_signal, _frame): raise Refused('PROCESS_DEADLINE')
        signal.signal(signal.SIGALRM, deadline); signal.alarm(350)
        with contextlib.redirect_stdout(Sink()), contextlib.redirect_stderr(Sink()):
            require(os.getenv('DEER_FLOW_CONFIG_PATH') in (None, '', '/app/backend/config.yaml'), 'CONFIG_PATH_CHANGED')
            raw = bounded('/app/backend/config.yaml')
            require(hashlib.sha256(raw).hexdigest() == CONFIG_SHA, 'CONFIG_CHANGED')
            key, result['credential_binding'] = credential_binding(raw, os.environ)
            path = Path('/app/backend/packages/harness/deerflow/models/cnooc_deepseek_v41.py')
            require(hashlib.sha256(bounded(path)).hexdigest() == ADAPTER_SHA, 'INSTALLED_ADAPTER_CHANGED')
            from deerflow.models import cnooc_deepseek_v41 as adapter
            require(Path(adapter.__file__).resolve() == path.resolve(), 'IMPORTED_ADAPTER_CHANGED')
            ca = os.getenv('REQUESTS_CA_BUNDLE') or os.getenv('SSL_CERT_FILE')
            require(bool(ca), 'CONFIGURED_CA_ABSENT')
            context = ssl.create_default_context(cafile=ca)
            require(context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED, 'CA_VERIFY_DISABLED')
            result['ca_sha256'] = hashlib.sha256(bounded(ca, 8388608)).hexdigest()
            result['installed_adapter_sha256'] = ADAPTER_SHA
            asyncio.run(asyncio.wait_for(verify(result, key, adapter, ca), 330))
            require(bounded('/app/backend/config.yaml') == raw, 'CONFIG_CHANGED_DURING_TEST')
            result.update(status='V41_GLM_CAPABILITIES_ACCEPTED_NOT_ENABLED', phase='COMPLETE')
    except Exception as exc:
        result.update(error_class=type(exc).__name__, error_code=str(exc) if isinstance(exc, Refused) else 'PROVIDER_CHECK_FAILED')
    finally:
        signal.alarm(0)
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__': main()
