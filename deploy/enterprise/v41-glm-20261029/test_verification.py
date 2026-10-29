import ast
import copy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import yaml

ROOT = Path(__file__).resolve().parent


def load(name):
    spec = importlib.util.spec_from_file_location('v41_glm_' + name, ROOT / (name + '.py'))
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


child = load('child')
host = load('host')
package = load('package')
KEY = 'synthetic-glm-credential'


def config():
    return {'models': [{'name': 'deepseek-v4-flash'},
        {'name': 'glm-5.3-flash', 'model': 'GLM-5.3-Flash', 'api_key': '$GLM_5_3_FLASH_API_KEY'}]}


def state():
    return dict(cases=[], checks=[], provider_requests=0)


def completion(content='连接正常。', finish='stop'):
    return {'id': 'synthetic-response', 'object': 'chat.completion', 'model': child.MODEL,
            'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': content}, 'finish_reason': finish}],
            'usage': {'prompt_tokens': 8, 'completion_tokens': 4, 'total_tokens': 12}}


def stream_frame(delta, finish=None):
    return {'id': 'synthetic-response', 'object': 'chat.completion.chunk', 'model': child.MODEL,
            'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish}]}


def sse(*frames):
    text = ''.join('data: ' + json.dumps(frame) + '\n\n' for frame in frames) + 'data: [DONE]\n\n'
    return httpx.Response(200, headers={'content-type': 'text/event-stream'}, text=text)


def test_binding_uses_glm_even_when_v4_is_present():
    key, observation = child.credential_binding(yaml.safe_dump(config()), {child.KEY_ENV: KEY, 'DeepSeek_V4_API_KEY': 'different-v4-key'})
    assert key == KEY and observation['differs_from_previous_v4_key'] is True
    assert KEY not in json.dumps(observation)
    key, observation = child.credential_binding(yaml.safe_dump(config()), {child.KEY_ENV: KEY})
    assert key == KEY and observation['previous_v4_key_present'] is False


@pytest.mark.parametrize('case', ['missing_glm_key', 'wrong_glm_reference', 'wrong_default', 'v41_already_enabled', 'whitespace', 'bearer'])
def test_binding_fails_before_provider_calls(case):
    data = config(); env = {child.KEY_ENV: KEY, 'DeepSeek_V4_API_KEY': 'unused-v4-key'}
    if case == 'missing_glm_key': env.pop(child.KEY_ENV)
    if case == 'wrong_glm_reference': data['models'][1]['api_key'] = '$DeepSeek_V4_API_KEY'
    if case == 'wrong_default': data['models'].reverse()
    if case == 'v41_already_enabled': data['models'].append({'name': 'deepseek-v4.1-flash-1017'})
    if case == 'whitespace': env[child.KEY_ENV] += '\n'
    if case == 'bearer': env[child.KEY_ENV] = 'Bearer ' + KEY
    with pytest.raises(child.Refused): child.credential_binding(yaml.safe_dump(data), env)


def test_error_categories_do_not_export_echoed_secret_or_prose():
    response = {'error': {'code': 20009, 'message': 'Payment required for Bearer ' + KEY}, 'authorization': KEY}
    row = child.response_summary(json.dumps(response).encode())
    assert row['business_codes'] == [20009] and row['error_categories'] == ['payment_required']
    assert KEY not in json.dumps(row) and 'Bearer' not in json.dumps(row)
    assert child.response_summary(b'{"code":"12345678"}', '12345678')['business_codes'] == []


@pytest.fixture
def adapter(monkeypatch):
    monkeypatch.delenv('REQUESTS_CA_BUNDLE', raising=False)
    monkeypatch.delenv('SSL_CERT_FILE', raising=False)
    from deerflow.models import cnooc_deepseek_v41
    return cnooc_deepseek_v41


@pytest.mark.asyncio
async def test_all_seven_checks_use_one_glm_key_and_exact_endpoint(adapter):
    requests = []
    def serve(request):
        requests.append(request)
        assert str(request.url) == child.ENDPOINT
        assert request.headers['authorization'] == 'Bearer ' + KEY
        p = json.loads(request.content)
        assert p['model'] == child.MODEL
        if not p.get('stream'): return httpx.Response(200, json=completion())
        if p.get('tool_choice'):
            return sse(stream_frame({'role': 'assistant', 'tool_calls': [{'index': 0, 'id': 'call_probe',
                        'type': 'function', 'function': {'name': 'connection_probe', 'arguments': '{"value":"ok"}'}}]}),
                       stream_frame({}, 'tool_calls'))
        if any(m['role'] == 'tool' for m in p['messages']):
            assert p['messages'][-1]['tool_call_id'] == 'call_probe'
        return sse(stream_frame({'role': 'assistant', 'content': '连接正常。'}), stream_frame({}, 'stop'))
    result = state()
    try:
        await child.verify(result, KEY, adapter, None, httpx.MockTransport(serve))
    except child.Refused:
        pytest.fail(json.dumps(result))
    assert result['checks'] == child.CHECKS and result['provider_requests'] == 7 and len(requests) == 7
    assert all(r['status'] == 'PASS' and r['glm_credential_verified'] for r in result['cases'])
    p = [json.loads(r.content) for r in requests]
    assert p[0]['messages'] == p[1]['messages'] and p[0]['max_tokens'] == p[1].get('max_tokens', p[1].get('max_completion_tokens')) == 256
    assert all(r['streaming'] for r in result['cases'][2:])
    assert p[5]['thinking'] == {'type': 'enabled'} and p[5]['reasoning_effort'] == 'low'
    assert p[6]['messages'][0]['content'][1]['image_url']['url'].startswith('data:image/png;base64,')
    assert KEY not in json.dumps(result)


@pytest.mark.asyncio
@pytest.mark.parametrize('code', [401, 402, 403, 429, 500, 302])
async def test_native_failure_stops_without_retry_or_redirect(adapter, code):
    seen = []
    def serve(request):
        seen.append(request)
        return httpx.Response(code, headers={'location': 'https://other.invalid'},
                              json={'error': {'code': '20009', 'message': 'Payment required: ' + KEY}})
    result = state()
    with pytest.raises(child.Refused, match='NATIVE_HTTP_NOT_ACCEPTED'):
        await child.verify(result, KEY, adapter, None, httpx.MockTransport(serve))
    assert len(seen) == 1 and result['provider_requests'] == 1 and result['checks'] == []
    assert result['cases'][0]['http_status'] == code
    assert KEY not in json.dumps(result)


@pytest.mark.asyncio
@pytest.mark.parametrize('body,error', [({'code': 20009, 'choices': None}, 'NATIVE_RESPONSE_SHAPE'),
                                     (completion(finish='length'), 'NATIVE_TEXT_INCOMPLETE')])
async def test_http_200_business_error_or_truncated_answer_not_accepted(adapter, body, error):
    result = state()
    with pytest.raises(child.Refused, match=error):
        await child.verify(result, KEY, adapter, None, httpx.MockTransport(lambda _: httpx.Response(200, json=body)))
    assert result['provider_requests'] == 1 and result['checks'] == []


@pytest.mark.asyncio
async def test_adapter_failure_is_not_retried(adapter):
    seen = []
    def serve(request):
        seen.append(request)
        return httpx.Response(200, json=completion()) if len(seen) == 1 else httpx.Response(402, json={'error': {'message': KEY}})
    result = state()
    with pytest.raises(Exception): await child.verify(result, KEY, adapter, None, httpx.MockTransport(serve))
    assert len(seen) == 2 and result['provider_requests'] == 2
    assert result['checks'] == [child.CHECKS[0]] and result['cases'][1]['http_status'] == 402
    assert KEY not in json.dumps(result)


@pytest.mark.asyncio
async def test_stream_is_size_bounded(adapter):
    class HugeStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            for _ in range(20): yield b'x' * 65536
    result = state()
    with pytest.raises(child.Refused, match='RESPONSE_BOUND'):
        await child.verify(result, KEY, adapter, None, httpx.MockTransport(lambda _: httpx.Response(200, stream=HugeStream())))
    assert result['provider_requests'] == 1


def test_request_guard_prevents_wrong_credentials_routes_and_extra_calls():
    result = state(); budget = child.Budget(result, KEY); budget.begin('synthetic')
    def request(key=KEY, url=child.ENDPOINT):
        return httpx.Request('POST', url, headers={'authorization': 'Bearer ' + key}, json={'model': child.MODEL})
    with pytest.raises(child.Refused, match='REQUEST_CREDENTIAL_CHANGED'): budget.request(request('v4-wrong-key'))
    with pytest.raises(child.Refused, match='REQUEST_ROUTE_CHANGED'): budget.request(request(url=child.ENDPOINT + '/chat/completions'))
    budget.request(request())
    with pytest.raises(child.Refused, match='REQUEST_LIMIT'): budget.request(request())
    for i in range(6): budget.begin(str(i)); budget.request(request())
    budget.begin('eighth')
    with pytest.raises(child.Refused, match='REQUEST_LIMIT'): budget.request(request())
    assert result['provider_requests'] == 7


@pytest.mark.parametrize('thinking,effort', [(False, 'high'), (True, 'low'), (True, None)])
def test_corrected_profile_factory_uses_glm_preserves_default(monkeypatch, adapter, thinking, effort):
    from deerflow.config.app_config import AppConfig
    from deerflow.config.model_config import ModelConfig
    from deerflow.models.factory import create_chat_model
    candidate = yaml.safe_load((ROOT / 'model.yaml').read_text(encoding='utf-8'))
    previous = yaml.safe_load((ROOT.parent / 'deepseek-v4.1-flash-1017.model.yaml').read_text(encoding='utf-8'))
    assert {**previous, 'api_key': '$GLM_5_3_FLASH_API_KEY'} == candidate
    monkeypatch.setenv(child.KEY_ENV, KEY); monkeypatch.setenv('DeepSeek_V4_API_KEY', 'v4-must-not-be-used')
    model_config = ModelConfig(**AppConfig.resolve_env_variables(candidate))
    app = SimpleNamespace(models=[SimpleNamespace(name='existing-default'), model_config], get_model_config=lambda _: model_config)
    model = create_chat_model(name=model_config.name, app_config=app, thinking_enabled=thinking, reasoning_effort=effort, attach_tracing=False)
    try:
        assert model.openai_api_key.get_secret_value() == KEY and app.models[0].name == 'existing-default'
        body = model._get_request_payload('hi')
        assert body['extra_body']['thinking']['type'] == ('enabled' if thinking else 'disabled')
        assert body.get('reasoning_effort') == ((effort or 'medium') if thinking else None)
    finally: model.http_client.close()


def test_package_is_self_contained_python37_syntax():
    raw = package.packaged()
    module = ast.parse(raw, feature_version=(3, 7))
    constants = {n.targets[0].id: ast.literal_eval(n.value) for n in module.body if isinstance(n, ast.Assign)
                 and isinstance(n.targets[0], ast.Name) and n.targets[0].id in ('CHILD', 'CHILD_SHA')}
    assert constants['CHILD'] == (ROOT / 'child.py').read_text(encoding='utf-8')
    assert child.hashlib.sha256(constants['CHILD'].encode()).hexdigest() == constants['CHILD_SHA']
    assert host.GATEWAY['generation'] == 389


def test_host_acceptance_requires_complete_verified_checks():
    observation = dict(status='V41_GLM_CAPABILITIES_ACCEPTED_NOT_ENABLED', checks=child.CHECKS,
        provider_requests=7, cases=[dict(name=name, provider_requests=1, status='PASS', http_status=200,
        full_endpoint_verified=True, glm_credential_verified=True) for name in child.CHECKS],
        credential_binding={'credential_source': child.KEY_ENV}, v41_enabled=False, configuration_modified=False)
    assert host.accepted(observation)
    bad = copy.deepcopy(observation); bad['cases'][-1]['http_status'] = 402
    assert not host.accepted(bad)
    bad = copy.deepcopy(observation); bad['checks'] = bad['checks'][:-1]
    assert not host.accepted(bad)


def test_source_template_wrong_host_has_no_side_effect(monkeypatch, capsys):
    monkeypatch.setattr(host.socket, 'gethostname', lambda: 'not-datax-01')
    monkeypatch.setattr(host.sys if hasattr(host, 'sys') else __import__('sys'), 'argv', ['host.py', '--verify-v41-with-glm-key-once'])
    monkeypatch.setattr(host.os, 'mkdir', lambda *a, **k: pytest.fail('must not create any files'))
    monkeypatch.setattr(host.subprocess, 'Popen', lambda *a, **k: pytest.fail('must not invoke kubectl'))
    assert host.main() == 1
    row = json.loads(capsys.readouterr().out)
    assert row['error_code'] == 'WRONG_HOST_OR_UID' and row['once_claimed'] is False


def test_existing_once_marker_is_never_removed_or_reexecuted(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(host.socket, 'gethostname', lambda: 'datax-01')
    monkeypatch.setattr(host.os, 'geteuid', lambda: 0, raising=False)
    monkeypatch.setattr(host, 'CHILD', 'synthetic')
    monkeypatch.setattr(host, 'CHILD_SHA', host.hashlib.sha256(b'synthetic').hexdigest())
    monkeypatch.setattr(host, 'CONTROL', str(tmp_path))
    monkeypatch.setattr(__import__('sys'), 'argv', ['host.py', '--verify-v41-with-glm-key-once'])
    monkeypatch.setattr(host.subprocess, 'Popen', lambda *a, **k: pytest.fail('must not invoke kubectl'))
    assert host.main() == 1
    assert json.loads(capsys.readouterr().out)['error_code'] == 'ONCE_ALREADY_CLAIMED'
    assert tmp_path.exists() and list(tmp_path.iterdir()) == []


def test_selects_only_current_owned_ready_pod():
    deployment = {'metadata': {'uid': 'gateway-deployment'}}
    sets = [{'metadata': {'uid': 'gateway-rs', 'ownerReferences': [
        {'controller': True, 'kind': 'Deployment', 'uid': 'gateway-deployment'}]}}]
    pod = {'metadata': {'name': 'gateway-valid', 'uid': 'pod-uid', 'ownerReferences': [
        {'controller': True, 'kind': 'ReplicaSet', 'uid': 'gateway-rs'}]},
        'spec': {'nodeName': 'node-a', 'containers': [{'name': 'gateway', 'image': host.IMAGE}]},
        'status': {'phase': 'Running', 'containerStatuses': [{'name': 'gateway', 'ready': True}]}}
    invalid = []
    for change in ('terminating', 'not_ready', 'wrong_image', 'foreign_controller', 'failed'):
        p = copy.deepcopy(pod); p['metadata']['name'] = change
        if change == 'terminating': p['metadata']['deletionTimestamp'] = 'now'
        if change == 'not_ready': p['status']['containerStatuses'][0]['ready'] = False
        if change == 'wrong_image': p['spec']['containers'][0]['image'] = 'unknown'
        if change == 'foreign_controller': p['metadata']['ownerReferences'][0]['uid'] = 'another-rs'
        if change == 'failed': p['status']['phase'] = 'Failed'
        invalid.append(p)
    assert host.owned_ready_pods(deployment, sets, [*invalid, pod]) == [pod]


@pytest.mark.parametrize('failure', [None, 'baseline', 'provider'])
def test_packaged_host_flow_claims_once_and_preserves_failed_receipt(monkeypatch, tmp_path, capsys, failure):
    deployment = {'metadata': {'uid': 'gateway-deployment', 'generation': 389},
                  'spec': {'replicas': 70}, 'status': {'observedGeneration': 389}}
    sets = [{'metadata': {'uid': 'gateway-rs', 'ownerReferences': [
        {'controller': True, 'kind': 'Deployment', 'uid': 'gateway-deployment'}]}}]
    pod = {'metadata': {'name': 'gateway-valid', 'uid': 'pod-uid', 'ownerReferences': [
        {'controller': True, 'kind': 'ReplicaSet', 'uid': 'gateway-rs'}]},
        'spec': {'nodeName': 'node-a', 'containers': [{'name': 'gateway', 'image': host.IMAGE}]},
        'status': {'phase': 'Running', 'containerStatuses': [{'name': 'gateway', 'ready': True}]}}
    observation = dict(status='V41_GLM_CAPABILITIES_ACCEPTED_NOT_ENABLED', checks=child.CHECKS,
        provider_requests=7, cases=[dict(name=name, provider_requests=1, status='PASS', http_status=200,
        full_endpoint_verified=True, glm_credential_verified=True) for name in child.CHECKS],
        credential_binding={'credential_source': child.KEY_ENV}, v41_enabled=False, configuration_modified=False)
    if failure == 'provider': observation.update(status='INCOMPLETE', provider_requests=1, checks=[])
    calls = []
    class FakeReader:
        def get(self, kind, name=None):
            if kind == 'deployment': return deployment
            if kind == 'replicaset': return {'items': sets}
            if kind == 'pods': return {'items': [pod]}
            if kind == 'pod': return pod
            pytest.fail('unexpected resource')
        def read(self, args, *a, **kw):
            assert 'exec' in args and '-I' in args and args[-1] == 'synthetic-child'
            calls.append(args)
            return json.dumps(observation).encode()
    monkeypatch.setattr(host, 'Reader', FakeReader)
    monkeypatch.setattr(host, 'GATEWAY', host.binding(deployment) if failure != 'baseline' else {})
    monkeypatch.setattr(host.socket, 'gethostname', lambda: 'datax-01')
    monkeypatch.setattr(host.os, 'geteuid', lambda: 0, raising=False)
    monkeypatch.setattr(host, 'CHILD', 'synthetic-child')
    monkeypatch.setattr(host, 'CHILD_SHA', host.hashlib.sha256(b'synthetic-child').hexdigest())
    monkeypatch.setattr(host, 'CONTROL', str(tmp_path / 'once'))
    monkeypatch.setattr(__import__('sys'), 'argv', ['host.py', '--verify-v41-with-glm-key-once'])
    assert host.main() == (0 if failure is None else 1)
    result = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert result['workload_modified'] is False and result['v41_enabled'] is False
    assert len(calls) == (0 if failure == 'baseline' else 1)
    if failure == 'baseline':
        assert result['error_code'] == 'GATEWAY_BASELINE_CHANGED' and not (tmp_path / 'once').exists()
    else:
        report = json.loads((tmp_path / 'once' / 'REPORT.json').read_text())
        assert report['once_claimed'] and report['protected_deployments_preserved']
        assert (report['status'] == 'V41_GLM_CAPABILITIES_ACCEPTED_NOT_ENABLED') == (failure is None)
