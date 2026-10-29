import ast
import asyncio
import base64
import copy
import importlib.util
import json
from pathlib import Path
import types

import pytest
import yaml

ROOT = Path(__file__).resolve().parent


def module(filename):
    spec = importlib.util.spec_from_file_location(filename.replace('.', '_'), ROOT / filename)
    value = importlib.util.module_from_spec(spec); spec.loader.exec_module(value)
    return value


child = module('enable_child.py'); host = module('enable_host.py'); package = module('package_enable.py')
profile = yaml.safe_load((ROOT / 'model.yaml').read_text(encoding='utf-8'))


def source(indent=0, following=True, newline=True):
    rows = [{'name': 'deepseek-v4-flash', 'model': 'V4', 'use': 'langchain_openai:ChatOpenAI', 'api_key': 'synthetic-test-only'},
            {'name': 'glm-5.3-flash', 'model': 'GLM-5.3-Flash', 'use': 'langchain_openai:ChatOpenAI', 'api_key': '$GLM_5_3_FLASH_API_KEY'}]
    block = yaml.safe_dump(rows, sort_keys=False)
    raw = '# existing comment\nsandbox:\n  use: deerflow.sandbox.local:LocalSandboxProvider\nmodels:\n' + ''.join(' ' * indent + line + '\n' for line in block.splitlines())
    if following: raw += '# preserve this comment\ntoken_usage:\n  enabled: true\n'
    if not newline: raw = raw.rstrip('\n')
    return raw.encode()


@pytest.mark.parametrize('indent', [0, 2, 4])
@pytest.mark.parametrize('following,newline', [(True, True), (False, True), (False, False)])
def test_append_preserves_every_existing_setting_and_default(indent, following, newline):
    raw = source(indent, following, newline); new = child.append_profile(raw, profile)
    parsed = yaml.safe_load(new); old = yaml.safe_load(raw)
    assert parsed['models'] == old['models'] + [profile]
    parsed['models'].pop(); assert parsed == old
    assert b'# existing comment' in new
    if following: assert b'# preserve this comment\ntoken_usage:' in new


@pytest.mark.parametrize('bad', [b'models: []\nmodels: []\n', b'models: []', b'models: {}',
    source().replace(b'$GLM_5_3_FLASH_API_KEY', b'$DeepSeek_V4_API_KEY'),
    source().replace(b'deepseek-v4-flash', b'another-default')])
def test_refuses_changed_or_ambiguous_configuration(bad):
    with pytest.raises(child.Refused): child.append_profile(bad, profile)


def deployment():
    return {'apiVersion': 'apps/v1', 'kind': 'Deployment',
        'metadata': {'name': 'gateway', 'uid': 'gateway-uid', 'generation': 389, 'resourceVersion': '123'},
        'spec': {'replicas': 70, 'selector': {'matchLabels': {'app': 'gateway'}}, 'template': {'spec': {
            'containers': [{'name': 'gateway', 'image': 'unchanged', 'env': [{'name': 'X', 'value': 'preserve'}],
                'volumeMounts': [{'name': 'config', 'mountPath': '/app/backend/config.yaml', 'subPath': 'config.yaml', 'readOnly': True}]}],
            'volumes': [{'name': 'config', 'configMap': {'name': 'old-config'}}, {'name': 'secret', 'secret': {'secretName': 'unchanged'}}]}}},
        'status': dict(observedGeneration=389, replicas=70, updatedReplicas=70, readyReplicas=70, availableReplicas=70)}


def test_patch_changes_only_gateway_config_reference():
    old = deployment(); original = copy.deepcopy(old)
    expected, patch = host.plan(old, 'new-config')
    assert old == original
    assert len([p for p in patch if p['op'] != 'test']) == 1
    assert {p['path'] for p in patch if p['op'] == 'test'} == {
        '/apiVersion', '/kind', '/metadata/uid', '/metadata/resourceVersion', '/metadata/generation', '/spec'}
    changed = copy.deepcopy(old['spec']); changed['template']['spec']['volumes'][0]['configMap']['name'] = 'new-config'
    assert expected == changed


def test_config_clone_keeps_all_payload_and_old_object():
    old = {'metadata': {'name': 'old', 'uid': 'cm-uid', 'resourceVersion': '99', 'ownerReferences': ['not-copied']},
           'data': {'config.yaml': 'old', 'another': 'preserved'}, 'binaryData': {'binary': 'AQ=='}}
    original = copy.deepcopy(old); new = host.candidate_cm(old, b'new')
    assert old == original and new['data'] == {'config.yaml': 'new', 'another': 'preserved'}
    assert new['binaryData'] == old['binaryData'] and new['immutable'] is True
    assert 'resourceVersion' not in new['metadata'] and 'ownerReferences' not in new['metadata']


@pytest.mark.parametrize('failure', ['replicas', 'updatedReplicas', 'readyReplicas', 'availableReplicas', 'observedGeneration', 'terminatingReplicas'])
def test_rollout_requires_every_replica_and_no_terminating(failure):
    d = deployment(); assert host.stable(d, 389)
    d['status'][failure] = 1; assert not host.stable(d, 389)


@pytest.mark.parametrize('reply_lost', [False, True])
def test_uncertain_patch_is_read_back_without_reposting(monkeypatch, reply_lost):
    d = deployment(); expected, patch = host.plan(d, 'new-config')
    current = copy.deepcopy(d); current['spec'] = expected; current['metadata']['generation'] += 1
    monkeypatch.setitem(host.BASELINES, 'gateway', host.binding(d)); monkeypatch.setattr(host, 'save', lambda r: None)
    class Reader:
        calls = 0
        def read(self, *a, **k):
            self.calls += 1
            if reply_lost: raise TimeoutError()
            return b'deployment.apps/gateway'
        def get(self, *a): return current
    reader = Reader(); result = {}
    assert host.patch_once(reader, types.SimpleNamespace(BASE=[]), patch, expected, result) == current
    assert reader.calls == result['patch_request_count'] == 1 and result['patch_readback_accepted']


def test_uncertain_patch_without_matching_readback_fails(monkeypatch):
    d = deployment(); expected, patch = host.plan(d, 'new-config')
    monkeypatch.setitem(host.BASELINES, 'gateway', host.binding(d)); monkeypatch.setattr(host, 'save', lambda r: None)
    class Reader:
        calls = 0
        def read(self, *a, **k): self.calls += 1; raise TimeoutError()
        def get(self, *a): return d
    reader = Reader()
    with pytest.raises(host.Refused, match='PATCH_READBACK'): host.patch_once(reader, types.SimpleNamespace(BASE=[]), patch, expected, {})
    assert reader.calls == 1


@pytest.mark.parametrize('bad', ['missing', 'foreign', 'unready', 'terminating'])
def test_service_endpoint_set_must_match_all_accepted_pods(bad):
    ep = {'conditions': {'ready': True}, 'targetRef': {'kind': 'Pod', 'uid': 'p1'}}
    endpoints = [ep]
    if bad == 'missing': endpoints = []
    if bad == 'foreign': ep['targetRef']['uid'] = 'other'
    if bad == 'unready': ep['conditions']['ready'] = False
    if bad == 'terminating': ep['conditions']['terminating'] = True
    reader = types.SimpleNamespace(read=lambda *a, **k: json.dumps({'items': [{'endpoints': endpoints}]}).encode())
    with pytest.raises(host.Refused): host.verify_endpoints(reader, types.SimpleNamespace(BASE=[]), {'p1'})


def test_real_factory_constructs_both_modes_without_network(monkeypatch):
    import socket
    monkeypatch.setenv('GLM_5_3_FLASH_API_KEY', 'synthetic-glm-credential-only')
    monkeypatch.setenv('PYTHON_DOTENV_DISABLED', '1')
    connect = socket.socket.connect
    def forbidden(sock, address):
        if isinstance(address, tuple) and address[0] in ('127.0.0.1', '::1'): return connect(sock, address)
        raise AssertionError('NO_EXTERNAL_NETWORK_ALLOWED')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    raw = child.append_profile(source(), profile)
    result = asyncio.run(child.validate_model(raw, profile))
    assert result['factory_thinking_modes_verified'] and result['default_model'] == 'deepseek-v4-flash'


def test_packaged_entry_and_payload_parse_on_python37():
    raw = package.packaged(); ast.parse(raw, feature_version=(3, 7))
    ns = {'__name__': 'test_package'}; exec(compile(raw, 'packaged', 'exec'), ns)
    assert 'self.commands < 1600' in ns['LIBRARY'] and 'remaining = 7200 -' in ns['LIBRARY']
    assert 'def main(' not in ns['LIBRARY']
    ast.parse(ns['CHILD'], feature_version=(3, 7))
    childns = {'__name__': 'child_test'}; exec(ns['CHILD'], childns)
    assert yaml.safe_load(childns['PROFILE']) == profile


def test_wrong_host_does_not_claim_or_mutate(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(host.socket, 'gethostname', lambda: 'workstation')
    monkeypatch.setattr(host, 'CONTROL', str(tmp_path / 'once'))
    monkeypatch.setattr(__import__('sys'), 'argv', ['activation.py', '--enable-v41-optional-once'])
    assert host.main() == 1
    result = json.loads(capsys.readouterr().out)
    assert result['error_code'] == 'WRONG_HOST_OR_UID' and not result['patch_requested']
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('failure', [None, 'admission', 'last_pod'])
def test_full_seventy_replica_activation_simulation(monkeypatch, tmp_path, capsys, failure):
    lib = module('host_r2.py')
    old = deployment(); old['spec']['template']['spec']['containers'][0]['image'] = lib.IMAGE
    baselines = {n: host.binding(old) for n in host.BASELINES}
    monkeypatch.setattr(host, 'BASELINES', baselines)
    monkeypatch.setattr(host, 'CONTROL', str(tmp_path / 'claim'))
    monkeypatch.setattr(host, 'BASE_CONFIG_SHA', host.digest(source()))
    monkeypatch.setattr(host, 'read_receipt', lambda library: {'sha256': host.RECEIPT_SHA})
    monkeypatch.setattr(host, 'save', lambda result: None)
    candidate = child.append_profile(source(), profile)
    old_cm = {'metadata': {'name': 'old-config', 'uid': 'old-cm'}, 'data': {'config.yaml': source().decode()}}
    rs = {'metadata': {'uid': 'rs-uid', 'name': 'gateway-rs', 'ownerReferences': [
        {'kind': 'Deployment', 'uid': old['metadata']['uid'], 'controller': True}]}}
    mutations = []; runtime_probes = []
    class Reader:
        current = copy.deepcopy(old)
        config = None
        def get(self, kind, name):
            if kind == 'deployment': return copy.deepcopy(self.current if name == 'gateway' else old)
            if kind == 'replicaset': return rs
            if kind == 'pod':
                return {'metadata': {'name': name, 'uid': name + '-uid', 'ownerReferences': [
                    {'kind': 'ReplicaSet', 'name': 'gateway-rs', 'uid': 'rs-uid', 'controller': True}]},
                    'spec': dict(copy.deepcopy(self.current['spec']['template']['spec']), nodeName='node-a'),
                    'status': {'phase': 'Running', 'containerStatuses': [{'name': 'gateway', 'ready': True}]}}
            pytest.fail('unexpected kind')
        def pod_index(self, d): return [{'name': 'gateway-%03d' % i, 'uid': 'gateway-%03d-uid' % i} for i in range(70)]
        def resource(self, kind, name):
            if kind == 'configmap': return old_cm if name == 'old-config' else self.config
            if kind == 'service': return {'metadata': {'uid': name}, 'spec': {'selector': {'app': name}}}
            pytest.fail('unexpected resource')
        def read(self, args, **kwargs):
            if 'patch' in args:
                expected, _ = host.plan(old, self.new_name)
                value = copy.deepcopy(old); value['spec'] = expected
                value['metadata']['generation'] += 1; value['status']['observedGeneration'] += 1
                if '--dry-run=server' in args:
                    if failure == 'admission': value['spec']['replicas'] = 69
                    return json.dumps(value).encode()
                mutations.append('patch'); self.current = value; return b'deployment.apps/gateway'
            if 'create' in args:
                mutations.append('create'); self.config = json.loads((Path(host.CONTROL) / 'candidate.private.json').read_bytes())
                self.config['metadata']['uid'] = 'new-cm'; return b'configmap/new'
            if 'endpointslices' in args:
                return json.dumps({'items': [{'endpoints': [{'conditions': {'ready': True}, 'targetRef': {
                    'kind': 'Pod', 'uid': 'gateway-%03d-uid' % i}} for i in range(70)]}]}).encode()
            pytest.fail('unexpected command')
    reader = Reader(); reader.new_name = host.candidate_cm(old_cm, candidate)['metadata']['name']
    def probe(reader, lib, pod, mode, sha):
        if mode == 'runtime':
            runtime_probes.append(pod['metadata']['name'])
            if failure == 'last_pod' and len(runtime_probes) == 70: raise host.Refused('SYNTHETIC_UNHEALTHY')
        out = {'status': 'PASS', 'config_sha256': host.digest(candidate)}
        if mode == 'prepare': out['private_candidate_b64'] = base64.b64encode(candidate).decode()
        return out
    monkeypatch.setattr(host, 'probe', probe)
    result = {'once_claimed': False}
    if failure:
        with pytest.raises(host.Refused): host.execute(reader, lib, result)
        assert not result.get('runtime_accepted')
    else:
        host.execute(reader, lib, result)
        assert result['runtime_accepted'] and result['accepted_pod_count'] == 70 and result['v41_enabled']
    assert mutations == ([] if failure == 'admission' else ['create', 'patch'])
    assert len(runtime_probes) == (0 if failure == 'admission' else 70)
    assert 'private_candidate_b64' not in json.dumps(result)
    assert old_cm['data']['config.yaml'] == source().decode()
