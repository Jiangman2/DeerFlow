"""Reader regressions: large namespace, scoped real kubectl output, one-use guard."""
import ast
import copy
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import threading
from urllib.parse import parse_qs, urlparse

import pytest

ROOT = Path(__file__).resolve().parent


def load(name):
    spec = importlib.util.spec_from_file_location('v41_glm_' + name, ROOT / (name + '.py'))
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


host = load('host_r2')
old = load('host')
package = load('package_r2')
UID = '12345678-1234-1234-1234-123456789012'


def deployment():
    return {'metadata': {'uid': 'gateway-deployment', 'generation': 389},
            'spec': {'replicas': 70, 'selector': {'matchLabels': {'app': 'gateway'}}},
            'status': {'observedGeneration': 389}}


def fixture():
    pod = {'metadata': {'name': 'gateway-valid', 'uid': UID, 'ownerReferences': [
        {'controller': True, 'kind': 'ReplicaSet', 'name': 'gateway-rs', 'uid': 'rs-uid'}]},
        'spec': {'nodeName': 'node-a', 'containers': [{'name': 'gateway', 'image': host.IMAGE}]},
        'status': {'phase': 'Running', 'containerStatuses': [{'name': 'gateway', 'ready': True}]}}
    rs = {'metadata': {'name': 'gateway-rs', 'uid': 'rs-uid', 'ownerReferences': [
        {'controller': True, 'kind': 'Deployment', 'uid': 'gateway-deployment'}]}}
    return pod, rs


@pytest.mark.parametrize('stream,code', [('stdout', 'STDOUT_LIMIT_EXCEEDED'), ('stderr', 'STDERR_LIMIT_EXCEEDED')])
def test_limits_have_step_counts_without_exporting_content(stream, code):
    reader = host.Reader()
    with pytest.raises(host.Refused, match=code):
        reader.read([sys.executable, '-c', 'import sys;sys.' + stream + '.write("sensitive-marker"*1000)'],
                    cap=1024, step='synthetic-output-bound')
    row = reader.observations[-1]
    assert row['step'] == 'synthetic-output-bound' and row[stream + '_bytes'] > 1024
    assert row['error_code'] == code and 'sensitive-marker' not in json.dumps(row)


def test_reader_timeout_and_return_code_are_distinct():
    reader = host.Reader()
    with pytest.raises(host.Refused, match='COMMAND_TIMEOUT'):
        reader.read([sys.executable, '-c', 'import time;time.sleep(30)'], timeout=0.1, step='timeout-test')
    assert reader.observations[-1]['status'] == 'FAILED'
    with pytest.raises(host.Refused, match='READ_COMMAND_FAILED'):
        reader.read([sys.executable, '-c', 'import sys;sys.stderr.write("private error");sys.exit(7)'], step='exit-test')
    assert reader.observations[-1]['returncode'] == 7
    assert 'private error' not in json.dumps(reader.observations)


def test_reader_success_with_stdin_and_bounded_stderr():
    reader = host.Reader()
    raw = reader.read([sys.executable, '-c', 'import sys;sys.stdout.buffer.write(sys.stdin.buffer.read());sys.stderr.write("hidden")'],
                      data=b'{"synthetic":true}', cap=1024, step='echo-test')
    assert json.loads(raw)['synthetic'] and reader.observations[-1]['status'] == 'PASS'
    assert reader.observations[-1]['stderr_bytes'] == 6


def test_r1_namespace_limit_is_reproduced_without_increasing_r2_cap():
    command = [sys.executable, '-c', 'import sys;sys.stdout.write("x"*(9*1024*1024))']
    with pytest.raises(old.Refused, match='OUTPUT_BOUND'): old.Reader().read(command)
    reader = host.Reader()
    with pytest.raises(host.Refused, match='STDOUT_LIMIT_EXCEEDED'): reader.read(command, step='same-cap-regression')
    assert reader.observations[-1]['byte_limit_per_stream'] == 8 * 1024 * 1024


def test_index_has_selector_projection_and_small_bound(monkeypatch):
    reader = host.Reader(); seen = []
    def read(args, **kwargs):
        seen.append((args, kwargs)); return ('gateway-valid\t' + UID + '\n').encode()
    monkeypatch.setattr(reader, 'read', read)
    assert reader.pod_index(deployment()) == [{'name': 'gateway-valid', 'uid': UID}]
    args, kwargs = seen[0]
    assert args[args.index('-l') + 1] == 'app=gateway' and '--field-selector=status.phase=Running' in args
    assert args[-1] == r'jsonpath={range .items[*]}{.metadata.name}{"\t"}{.metadata.uid}{"\n"}{end}'
    assert kwargs['cap'] == 131072
    assert '\n' not in args[-1] and '\t' not in args[-1]


@pytest.mark.parametrize('kind,name', [('replicaset', None), ('pods', None), ('secret', 'anything'), ('pod', 'bad/name')])
def test_unfiltered_or_secret_get_is_not_allowed(kind, name, monkeypatch):
    reader = host.Reader()
    monkeypatch.setattr(reader, 'read', lambda *a, **kw: pytest.fail('must refuse before kubectl'))
    with pytest.raises(host.Refused, match='NAMED_RESOURCE_REQUIRED'): reader.get(kind, name)


@pytest.mark.parametrize('text', ['malformed', 'gateway-valid\twrong-uid\n', 'gateway-valid\t' + UID + '\ngateway-valid\t' + UID + '\n'])
def test_invalid_name_uid_index_fails_closed(text, monkeypatch):
    reader = host.Reader(); monkeypatch.setattr(reader, 'read', lambda *a, **k: text.encode())
    with pytest.raises(host.Refused): reader.pod_index(deployment())


@pytest.mark.parametrize('change', ['pod_uid', 'rs_uid', 'foreign_deployment', 'wrong_image'])
def test_owner_identity_and_image_still_required(change):
    pod, rs = fixture()
    if change == 'pod_uid': pod['metadata']['uid'] = 'replaced-pod'
    if change == 'rs_uid': rs['metadata']['uid'] = 'replaced-rs'
    if change == 'foreign_deployment': rs['metadata']['ownerReferences'][0]['uid'] = 'another-deployment'
    if change == 'wrong_image': pod['spec']['containers'][0]['image'] = 'another-image'
    class Reader:
        def pod_index(self, d): return [{'name': 'gateway-valid', 'uid': UID}]
        def get(self, kind, name): return pod if kind == 'pod' else rs
    with pytest.raises(host.Refused): host.select_pod(Reader(), deployment())


def test_r2_keeps_existing_once_claim_and_child_identical(monkeypatch, tmp_path, capsys):
    raw = package.packaged()
    syntax = ast.parse(raw, feature_version=(3, 7))
    constants = {n.targets[0].id: ast.literal_eval(n.value) for n in syntax.body if isinstance(n, ast.Assign)
                 and isinstance(n.targets[0], ast.Name) and n.targets[0].id in ('CHILD', 'CHILD_SHA', 'CONTROL')}
    assert constants['CONTROL'] == old.CONTROL
    assert constants['CHILD_SHA'] == '0fc6e5192f7c8e4553ad6ba1077a464babf0589f250e1debe993ef42db24f02e'
    assert hashlib.sha256(constants['CHILD'].encode()).hexdigest() == constants['CHILD_SHA']
    monkeypatch.setattr(host.socket, 'gethostname', lambda: 'datax-01')
    monkeypatch.setattr(host.os, 'geteuid', lambda: 0, raising=False)
    monkeypatch.setattr(host, 'CHILD', constants['CHILD']); monkeypatch.setattr(host, 'CHILD_SHA', constants['CHILD_SHA'])
    monkeypatch.setattr(host, 'CONTROL', str(tmp_path))
    monkeypatch.setattr(sys, 'argv', ['host.py', '--verify-v41-with-glm-key-once'])
    monkeypatch.setattr(host.subprocess, 'Popen', lambda *a, **k: pytest.fail('no command allowed'))
    assert host.main() == 1
    assert json.loads(capsys.readouterr().out)['error_code'] == 'ONCE_ALREADY_CLAIMED'
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('failure', [None, 'baseline', 'provider'])
def test_r2_host_reads_only_named_resources_before_one_execution(monkeypatch, tmp_path, capsys, failure):
    dep = deployment(); pod, rs = fixture(); calls = []
    observation = dict(status='V41_GLM_CAPABILITIES_ACCEPTED_NOT_ENABLED', checks=host.CHECKS,
        provider_requests=7, cases=[dict(name=name, provider_requests=1, status='PASS', http_status=200,
        full_endpoint_verified=True, glm_credential_verified=True) for name in host.CHECKS],
        credential_binding={'credential_source': 'GLM_5_3_FLASH_API_KEY'}, v41_enabled=False, configuration_modified=False)
    if failure == 'provider': observation.update(status='INCOMPLETE', provider_requests=1, checks=[])
    class Reader:
        def __init__(self): self.observations = []
        def get(self, kind, name):
            assert name and kind in ('deployment', 'pod', 'replicaset')
            self.observations.append({'step': kind + ':' + name})
            return {'deployment': dep, 'pod': pod, 'replicaset': rs}[kind]
        def pod_index(self, d): return [{'name': 'gateway-valid', 'uid': UID}]
        def read(self, args, *a, **kw):
            assert 'exec' in args and '-I' in args and args[-1] == 'synthetic-child'
            calls.append(args); return json.dumps(observation).encode()
    monkeypatch.setattr(host, 'Reader', Reader)
    monkeypatch.setattr(host, 'GATEWAY', host.binding(dep) if failure != 'baseline' else {})
    monkeypatch.setattr(host.socket, 'gethostname', lambda: 'datax-01')
    monkeypatch.setattr(host.os, 'geteuid', lambda: 0, raising=False)
    monkeypatch.setattr(host, 'CHILD', 'synthetic-child')
    monkeypatch.setattr(host, 'CHILD_SHA', hashlib.sha256(b'synthetic-child').hexdigest())
    monkeypatch.setattr(host, 'CONTROL', str(tmp_path / 'once'))
    monkeypatch.setattr(sys, 'argv', ['host.py', '--verify-v41-with-glm-key-once'])
    assert host.main() == (0 if failure is None else 1)
    result = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert result['workload_modified'] is False and result['v41_enabled'] is False and result['read_observations']
    assert len(calls) == (0 if failure == 'baseline' else 1)
    if failure == 'baseline':
        assert result['error_code'] == 'GATEWAY_BASELINE_CHANGED' and not (tmp_path / 'once').exists()
    else:
        report = json.loads((tmp_path / 'once' / 'REPORT.json').read_text())
        assert report['once_claimed'] and report['protected_deployments_preserved']


def test_real_kubectl_projects_large_synthetic_pod_list(monkeypatch, tmp_path):
    """Loopback fake API only; empty kubeconfig, no real cluster or auth access."""
    kubectl = shutil.which('kubectl')
    if not kubectl: pytest.skip('kubectl not installed')
    rows = [{'apiVersion': 'v1', 'kind': 'Pod', 'metadata': {'name': 'gateway-%03d' % i,
              'uid': '%08x-1234-1234-1234-123456789012' % i, 'labels': {'app': 'gateway'},
              'annotations': {'synthetic-padding': 'x' * 140000}},
             'spec': {'containers': [{'name': 'gateway', 'image': 'synthetic'}]},
             'status': {'phase': 'Running'}} for i in range(70)]
    body = json.dumps({'apiVersion': 'v1', 'kind': 'PodList', 'metadata': {}, 'items': rows}).encode()
    assert len(body) > 8388608
    seen = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            url = urlparse(self.path); seen.append((url.path, parse_qs(url.query)))
            data = None
            if url.path == '/api': data = {'apiVersion': 'v1', 'kind': 'APIVersions', 'versions': ['v1']}
            if url.path == '/apis': data = {'apiVersion': 'v1', 'kind': 'APIGroupList', 'groups': []}
            if url.path == '/api/v1': data = {'apiVersion': 'v1', 'kind': 'APIResourceList', 'groupVersion': 'v1', 'resources': [
                {'name': 'pods', 'singularName': 'pod', 'namespaced': True, 'kind': 'Pod', 'verbs': ['get', 'list']}]}
            if url.path == '/api/v1/namespaces/deer-flow/pods': payload = body
            elif data is not None: payload = json.dumps(data).encode()
            else: self.send_error(404); return
            self.send_response(200); self.send_header('Content-Type', 'application/json'); self.send_header('Content-Length', str(len(payload)))
            self.end_headers(); self.wfile.write(payload)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    kubeconfig = tmp_path / 'empty-kubeconfig.json'
    kubeconfig.write_text(json.dumps({'apiVersion': 'v1', 'kind': 'Config', 'clusters': [], 'contexts': [], 'users': [], 'current-context': ''}))
    monkeypatch.setattr(host, 'BASE', [kubectl, '--kubeconfig=' + str(kubeconfig), '--server=http://127.0.0.1:' + str(server.server_port),
        '--request-timeout=10s', '--cache-dir=' + str(tmp_path / 'kubectl-cache'), '-n', 'deer-flow'])
    try:
        reader = host.Reader(); index = reader.pod_index(deployment())
        assert len(index) == 70 and index[0]['name'] == 'gateway-000'
        assert reader.observations[-1]['stdout_bytes'] < 5000
        queries = [q for path, q in seen if path.endswith('/pods')]
        assert queries and all(q.get('labelSelector') == ['app=gateway'] and q.get('fieldSelector') == ['status.phase=Running'] for q in queries)
    finally: server.shutdown(); server.server_close(); thread.join(timeout=5)
