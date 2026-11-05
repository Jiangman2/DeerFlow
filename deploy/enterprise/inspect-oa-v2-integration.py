"""Read OA configuration metadata and probe only a fixed local nonexistent job."""
import argparse
import copy
import hashlib
import json
import os
import socket
import subprocess
import sys

EXPECTED = {}
SOURCE_PINS = {}
CHILD = r'''
import contextlib, hashlib, importlib.util, io, json, os, pathlib, stat, sys
from urllib import request, error
from urllib.parse import urlsplit

def need(ok, code):
    if not ok:
        raise ValueError(code)

def read_pinned_sources(pins):
    contents = {}
    for name, expected in pins.items():
        path = pathlib.Path(name)
        need(name.startswith('/app/backend/') and path.resolve() == path, 'source_path_changed')
        fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, 'rb') as stream:
            before = os.fstat(stream.fileno())
            need(stat.S_ISREG(before.st_mode) and before.st_size == expected['bytes'] and before.st_size <= 262144, 'source_size_changed')
            raw = stream.read(262145)
            after = os.fstat(stream.fileno())
        need((before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) ==
             (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns), 'source_changed_during_read')
        need(hashlib.sha256(raw).hexdigest() == expected['sha256'], 'source_hash_changed')
        contents[name] = raw
    return contents

def describe(config):
    endpoint = None
    if config.callback_url:
        try:
            parsed = urlsplit(config.callback_url)
            endpoint = {'scheme': parsed.scheme, 'hostname': parsed.hostname,
                        'port': parsed.port or (443 if parsed.scheme == 'https' else 80),
                        'path_present': bool(parsed.path), 'query_present': bool(parsed.query),
                        'path_and_query_exported': False}
        except ValueError:
            endpoint = {'status': 'UNPARSEABLE'}
    return {'enabled': config.enabled, 'multi_result_enabled': config.multi_result_enabled,
            'callback_enabled': config.callback_enabled, 'callback_target': endpoint,
            'credential_presence': {'ingest_key': bool(config.ingest_key),
                'callback_bearer': bool(config.callback_bearer_token),
                'callback_signing_secret': bool(config.callback_signing_secret)},
            'owner_configured': bool(config.owner_user_id), 'agent_configured': bool(config.assistant_id),
            'callback_ca_configured': bool(config.callback_ca_bundle),
            'max_result_files': config.max_result_files, 'max_result_bytes': config.max_result_bytes,
            'max_total_result_bytes': config.max_total_result_bytes,
            'oa_recursion_limit': config.recursion_limit, 'run_timeout_seconds': config.run_timeout_seconds,
            'max_callback_attempts': config.max_callback_attempts}

class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

def local_probe(config, opener=None):
    if not config.enabled or not config.ingest_key:
        return {'status': 'NOT_REQUESTED', 'reason': 'OA_DISABLED_OR_KEY_MISSING', 'requests': 0}
    opener = opener or request.build_opener(request.ProxyHandler({}), NoRedirect())
    req = request.Request('http://127.0.0.1:8001/api/v1/integrations/oa/policy-flow/jobs/00000000-0000-0000-0000-000000000000',
        method='GET', headers={'Authorization': 'Bearer ' + config.ingest_key, 'Accept': 'application/json'})
    response = None
    try:
        try:
            response = opener.open(req, timeout=10)
        except error.HTTPError as exc:
            response = exc
        code = response.code
        raw = response.read(8193)
        if len(raw) > 8192:
            return {'status': 'INCOMPLETE', 'reason': 'RESPONSE_LIMIT', 'http_status': code, 'requests': 1}
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeError):
            payload = {}
        expected = code == 404 and isinstance(payload, dict) and payload.get('detail') == 'OA policy-flow job not found'
        return {'status': 'AUTHENTICATED_ROUTE_ACCEPTED' if expected else 'ROUTE_NOT_ACCEPTED',
                'http_status': code, 'expected_missing_job': expected, 'requests': 1,
                'credentials_sent_to_loopback_only': True, 'raw_response_exported': False}
    except Exception as exc:
        return {'status': 'INCOMPLETE', 'reason': 'LOCAL_TRANSPORT_ERROR', 'error_class': type(exc).__name__, 'requests': 1}
    finally:
        if response is not None:
            response.close()

def inspect(pins):
    raw = read_pinned_sources(pins)['/app/backend/app/gateway/oa_policy_flow_config.py']
    # Load only the hash-verified pure configuration module; no app startup.
    name = '_oa_inspection_config'
    module = type(sys)(name)
    sys.modules[name] = module
    exec(compile(raw, '/app/backend/app/gateway/oa_policy_flow_config.py', 'exec'), module.__dict__)
    config = module.load_oa_policy_flow_config()
    result = describe(config)
    result['local_route'] = local_probe(config)
    read_pinned_sources(pins)
    result.update(status='OA_CONFIGURATION_OBSERVED_NOT_BUSINESS_ACCEPTED', sources_verified=True,
        credentials_exported=False, configuration_modified=False, application_started=False,
        database_direct_access=False, provider_requests=0, callback_requests=0, job_submissions=0)
    return result

if __name__ == '__main__':
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            value = inspect(json.loads(sys.argv[1]))
        print(json.dumps(value, sort_keys=True))
    except Exception as exc:
        print(json.dumps({'status':'INCOMPLETE','error_class':type(exc).__name__,
            'raw_errors_exported':False,'credentials_exported':False,'job_submissions':0,
            'provider_requests':0,'callback_requests':0,'configuration_modified':False}))
        sys.exit(1)
'''


def require(ok, code):
    if not ok:
        raise ValueError(code)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()


def binding(deployment):
    return {'uid': deployment['metadata']['uid'], 'generation': deployment['metadata']['generation'],
            'spec_sha256': digest(deployment['spec'])}


def ready(deployment):
    n = deployment['spec'].get('replicas')
    s = deployment.get('status', {})
    return (type(n) is int and n > 0 and s.get('observedGeneration') == deployment['metadata']['generation']
            and all(s.get(k) == n for k in ('replicas', 'readyReplicas', 'updatedReplicas', 'availableReplicas'))
            and not s.get('unavailableReplicas') and not s.get('terminatingReplicas'))


def owner(value):
    refs = [r for r in value['metadata'].get('ownerReferences', []) if r.get('controller') is True]
    require(len(refs) == 1, 'owner_not_unique')
    return refs[0]


def identity(pod):
    rows = [c for c in pod['status'].get('containerStatuses', []) if c['name'] == 'gateway']
    require(len(rows) == 1 and rows[0].get('ready') is True and rows[0].get('state', {}).get('running') is not None,
            'gateway_container_not_ready')
    require(not pod['metadata'].get('deletionTimestamp') and pod['status'].get('phase') == 'Running', 'pod_not_running')
    containers = [c for c in pod['spec']['containers'] if c['name'] == 'gateway']
    require(len(containers) == 1 and containers[0]['image'] == EXPECTED['gateway']['image']
            and rows[0]['imageID'] == EXPECTED['gateway']['image'], 'pod_image_changed')
    return {'pod': pod['metadata']['name'], 'uid': pod['metadata']['uid'], 'node': pod['spec']['nodeName'],
            'container_id': rows[0]['containerID'], 'restart_count': rows[0]['restartCount']}


def select_pods(deployment, replicasets, pods):
    valid = set()
    for rs in replicasets:
        template = copy.deepcopy(rs['spec']['template'])
        template.get('metadata', {}).get('labels', {}).pop('pod-template-hash', None)
        ref = owner(rs)
        if (ref.get('kind') == 'Deployment' and ref.get('uid') == deployment['metadata']['uid']
                and template == deployment['spec']['template']):
            valid.add(rs['metadata']['uid'])
    result = {}
    for pod in sorted(pods, key=lambda p: p['metadata']['name']):
        ref = owner(pod)
        if pod['metadata'].get('deletionTimestamp') or ref.get('kind') != 'ReplicaSet' or ref.get('uid') not in valid:
            continue
        row = identity(pod)
        result.setdefault(row['node'], pod)
    require(len(result) >= 2, 'two_ready_gateway_nodes_required')
    return list(result.values())[:2]


def env_sources(deployment):
    container = next(c for c in deployment['spec']['template']['spec']['containers'] if c['name'] == 'gateway')
    rows = []
    for env in container.get('env', []):
        if not env['name'].startswith('OA_POLICY_FLOW_'):
            continue
        row = {'variable': env['name'], 'kind': 'literal' if 'value' in env else 'valueFrom'}
        for kind in ('secretKeyRef', 'configMapKeyRef'):
            if kind in env.get('valueFrom', {}):
                ref = env['valueFrom'][kind]
                row.update(kind=kind, name=ref.get('name'), key=ref.get('key'))
        rows.append(row)
    return {'explicit': rows, 'env_from': [
        {'kind': kind, 'name': item[kind].get('name'), 'prefix': item.get('prefix', '')}
        for item in container.get('envFrom', []) for kind in ('secretRef', 'configMapRef') if kind in item]}


def query(args, timeout=30, limit=8 * 1024**2):
    require(args and args[0] in ('get', 'exec'), 'command_forbidden')
    base = ['kubectl', '--kubeconfig=/etc/kubernetes/admin.conf', '--request-timeout=20s', '-n', 'deer-flow']
    p = subprocess.run(base + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    require(p.returncode == 0 and len(p.stdout) <= limit, 'readonly_query_failed')
    return json.loads(p.stdout)


def inspect_cluster(read=query):
    before = {name: read(['get', 'deployment', name, '-o', 'json']) for name in EXPECTED}
    for name, d in before.items():
        require(binding(d) == {k: EXPECTED[name][k] for k in ('uid', 'generation', 'spec_sha256')} and ready(d),
                'deployment_changed_or_unready_' + name)
    rs = read(['get', 'replicasets', '-l', 'app=gateway', '-o', 'json'])['items']
    pods = read(['get', 'pods', '-l', 'app=gateway', '-o', 'json'])['items']
    samples = []
    for pod in select_pods(before['gateway'], rs, pods):
        pin = identity(pod)
        require(identity(read(['get', 'pod', pin['pod'], '-o', 'json'])) == pin, 'pod_changed_before_probe')
        observation = read(['exec', pin['pod'], '-c', 'gateway', '--', '/app/backend/.venv/bin/python', '-I', '-B',
                            '-c', CHILD, json.dumps(SOURCE_PINS)], timeout=45, limit=32768)
        require(identity(read(['get', 'pod', pin['pod'], '-o', 'json'])) == pin, 'pod_changed_after_probe')
        require(observation.get('sources_verified') is True and observation.get('job_submissions') == 0
                and observation.get('provider_requests') == observation.get('callback_requests') == 0,
                'probe_incomplete')
        samples.append({'identity': pin, 'observation': observation})
    after = {name: read(['get', 'deployment', name, '-o', 'json']) for name in EXPECTED}
    require(all(binding(after[k]) == binding(before[k]) and ready(after[k]) for k in before), 'deployment_changed_after_probe')
    return {'schema': 'portal-oa-v2-integration-preflight.v1', 'status': 'OA_PREFLIGHT_OBSERVED_NOT_BUSINESS_ACCEPTED',
            'deployments': {k: binding(v) for k, v in before.items()}, 'oa_environment_sources': env_sources(before['gateway']),
            'samples': samples, 'scope': 'TWO_READY_GATEWAY_NODES_NOT_ALL_REPLICAS',
            'maximum_loopback_get_requests': 2, 'secret_api_reads': 0, 'credentials_exported': False,
            'job_submissions': 0, 'callback_requests': 0, 'provider_requests': 0,
            'configuration_modified': False, 'workload_modified': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inspect-readonly', action='store_true', required=True)
    parser.parse_args()
    try:
        require(os.name == 'posix' and os.geteuid() == 0 and socket.gethostname() == 'datax-01'
                and sys.flags.isolated and sys.flags.dont_write_bytecode, 'datax01_isolated_root_required')
        require(set(EXPECTED) == {'gateway', 'frontend', 'ppt-master-runner'} and len(SOURCE_PINS) == 4, 'package_incomplete')
        result = inspect_cluster()
        result['exitcode'] = 0
    except Exception as exc:
        result = {'status': 'INCOMPLETE', 'error_class': type(exc).__name__,
                  'error_code': str(exc) if isinstance(exc, ValueError) and str(exc).replace('_', '').replace('-', '').isalnum() else 'details_withheld',
                  'credentials_exported': False, 'workload_modified': False, 'job_submissions': 0,
                  'callback_requests': 0, 'provider_requests': 0, 'exitcode': 1}
    print(json.dumps(result, sort_keys=True), flush=True)
    return result['exitcode']


if __name__ == '__main__':
    raise SystemExit(main())
