"""Offline evidence collection through the real installed model factory."""

import importlib.util
import json
import os
from pathlib import Path

import httpx
import pytest

FOLDER = Path(__file__).parent
spec = importlib.util.spec_from_file_location('evidence', FOLDER/'deepseek-v41-transport-evidence.py')
e = importlib.util.module_from_spec(spec)
spec.loader.exec_module(e)


def packaged():
    n = {'__name__': 'test_diagnostic'}
    exec(compile(Path(os.environ['DIAG_SCRIPT']).read_bytes(), '<diagnostic>', 'exec'), n)
    app = {'__name__': 'test_app_probe'}
    exec(compile(n['PROBE_SOURCE'], '<app-probe>', 'exec'), app)
    return n, app


class Bytes(httpx.AsyncByteStream):
    def __init__(self, raw):
        self.raw = raw

    async def __aiter__(self):
        # Exercise UTF-8/SSE boundaries and split DONE markers.
        for i in range(0, len(self.raw), 7):
            yield self.raw[i:i+7]


def response(index, done=True):
    if index == 0:
        return httpx.Response(200, stream=Bytes(json.dumps(dict(choices=[dict(finish_reason='stop', message=dict(content='V41_PROBE_OK'))])).encode()), headers={'content-type': 'application/json'})
    delta = dict(role='assistant', content='RED BLUE' if index == 5 else 'V41_PROBE_OK')
    if index in (2, 5):
        delta['reasoning_content'] = 'private synthetic reasoning'
    if index == 3:
        delta.update(content='', tool_calls=[dict(index=0, id='test-call', type='function', function=dict(name='probe_echo', arguments='{"value":"V41_PROBE_OK"}'))])
    data = dict(id='test', model='DeepSeek-V4.1-Flash', choices=[dict(index=0, delta=delta, finish_reason='tool_calls' if index == 3 else 'stop')])
    raw = ('data: '+json.dumps(data)+'\n\n'+('data: [DONE]\n\n' if done else '')).encode()
    return httpx.Response(200, stream=Bytes(raw), headers={'content-type': 'text/event-stream','x-request-id':'upstream-123456789','server':'nginx'})


async def run(error_index=None, missing_done=None):
    n, app = packaged()
    requests = []
    recorder = e.Recorder('test-secret-private', 'test-op')

    def handler(request):
        index = len(requests)
        requests.append(request)
        if index == error_index:
            return httpx.Response(503, stream=Bytes(b'{"code":503,"data":"Service unavailable no fallback test-secret-private","message":"private"}'), headers={'content-type':'application/json','x-request-id':'upstream-abcdef0123456789'})
        return response(index, done=index != missing_done)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), event_hooks={'request':[recorder.request], 'response':[recorder.response]}) as client:
        result = await e.diagnose('test-secret-private', n['PROFILE'], app['run_probe'], client, recorder)
    return result, requests


@pytest.mark.asyncio
async def test_six_requests_actual_factory_wire_and_done():
    result, requests = await run()
    assert len(requests) == 6
    assert result['status'] == 'SAMPLE_ACCEPTED_NOT_DEPLOYED'
    assert result['wire_contract_accepted'] and result['stream_terminal_and_done']
    assert all(r['request_started_utc'] and r['response_headers_utc'] for r in result['requests'])
    text = json.dumps(result)
    assert 'test-secret-private' not in text and 'private synthetic reasoning' not in text
    assert result['requests'][4]['request_shape']['tool_result_ids_match']


@pytest.mark.asyncio
@pytest.mark.parametrize('index', [1, 4])
async def test_failure_has_correlation_and_body_features_without_retry(index):
    result, requests = await run(error_index=index)
    assert len(requests) == index + 1
    row = result['requests'][-1]
    assert row['http_status'] == 503
    assert row['headers']['x-request-id'] == 'upstream-abcdef0123456789'
    assert 'no_fallback' in row['error_body_features']['categories']
    assert 'test-secret-private' not in json.dumps(result)
    assert result['status'] == 'DIAGNOSIS_INCOMPLETE'


@pytest.mark.asyncio
async def test_raw_failure_does_not_masquerade_as_healthy_application():
    result, requests = await run(error_index=0)
    assert len(requests) == 6
    assert result['application']['status'] == 'PROVIDER_ACCEPTED_NOT_DEPLOYED'
    assert result['status'] == 'DIAGNOSIS_INCOMPLETE'


@pytest.mark.asyncio
async def test_missing_done_refuses_acceptance_even_with_stop():
    result, _ = await run(missing_done=1)
    assert result['status'] == 'DIAGNOSIS_INCOMPLETE'
    assert not result['stream_terminal_and_done']


def test_header_and_error_privacy():
    result = e.safe_headers(httpx.Headers({'authorization':'Bearer secret','set-cookie':'private','x-request-id':'secret','server':'nginx secret','via':'private'}), 'secret')
    assert 'secret' not in json.dumps(result) and 'private' not in json.dumps(result)
    value = e.error_features(b'{"code":503,"data":"private credential","message":"private"}')
    assert value['code'] == 503 and 'private' not in json.dumps(value)


@pytest.mark.asyncio
async def test_wrong_endpoint_and_seventh_request_rejected():
    recorder = e.Recorder('secret','test')
    with pytest.raises(ValueError):
        await recorder.request(httpx.Request('POST','https://example.com/',json={}))
    recorder.rows = [{}]*6
    with pytest.raises(ValueError):
        await recorder.request(httpx.Request('POST',e.ENDPOINT,json={}))


def test_diagnostic_host_requires_stable_rollout():
    n, _ = packaged()
    d=dict(metadata=dict(uid=n['GATEWAY_UID'],generation=382),spec=dict(replicas=70),status=dict(observedGeneration=382,replicas=70,updatedReplicas=70,readyReplicas=70,availableReplicas=70))
    n['stable'](d)
    d['status']['updatedReplicas']=69
    with pytest.raises(n['Refused'],match='rollout_in_progress'):
        n['stable'](d)


def test_original_r4_explicit_direct_transport():
    n, _ = packaged()
    assert 'trust_env=False' in n['DIAGNOSTIC_REMOTE']
    assert 'follow_redirects=False' in n['DIAGNOSTIC_REMOTE']
    assert n['PROFILE']['max_retries'] == 0
