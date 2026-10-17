#!/usr/bin/env python3
"""Read-only production baseline, Python 3.7+. No Secret GET, provider call or writes."""
import datetime
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
from urllib.parse import urlsplit, urlunsplit

NAMESPACE = 'deer-flow'
NUMERIC = {
    'MINERU_TIMEOUT_SECONDS', 'MINERU_POLL_INTERVAL_SECONDS', 'MINERU_TRANSIENT_POLL_RETRIES',
    'MINERU_SYNC_MAX_PAGES_PER_REQUEST', 'MINERU_SYNC_TRANSIENT_RETRIES',
    'MINERU_DISPATCH_MIN_BACKENDS', 'MINERU_DISPATCH_MAX_WAITING',
    'MINERU_DISPATCH_WAIT_TIMEOUT_SECONDS', 'MINERU_DISPATCH_REQUEST_TIMEOUT_SECONDS',
    'MINERU_DISPATCH_INTERACTIVE_REQUEST_TIMEOUT_SECONDS',
    'WEKNORA_ASYNQ_CORE_CONCURRENCY', 'WEKNORA_ASYNQ_SHARED_CONCURRENCY',
    'WEKNORA_ASYNQ_POSTPROCESS_CONCURRENCY', 'WEKNORA_ASYNQ_ENRICHMENT_CONCURRENCY',
    'WEKNORA_ASYNQ_MAINTENANCE_CONCURRENCY',
}
NAMED = {'MINERU_API_URL', 'MINERU_API_TOKEN', 'MINERU_REQUEST_MODE', 'MINERU_ADAPTER_MODE',
         'MINERU_TRUST_ENV', 'MINERU_CA_BUNDLE', 'REQUESTS_CA_BUNDLE', 'SSL_CERT_FILE',
         'MINERU_DISPATCH_BACKEND_HOST'}

PROBE = r'''
import ast, hashlib, json, os, re
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from urllib.request import build_opener, ProxyHandler, HTTPRedirectHandler, Request
class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None
def public_url(value):
    try:
        u = urlsplit(value)
        if u.scheme not in ('http', 'https') or not u.hostname or u.username or u.password:
            return {'present': bool(value), 'safe_to_display': False}
        path = '/'.join(x if len(x) <= 64 else '[REDACTED]' for x in u.path.split('/'))
        return {'url_without_query_or_fragment': urlunsplit((u.scheme, u.netloc, path, '', '')),
                'query_present': bool(u.query), 'fragment_present': bool(u.fragment)}
    except Exception:
        return {'present': bool(value), 'safe_to_display': False}
out = {'credentials_printed': False, 'files_modified': False, 'provider_requests': 0,
       'endpoint': public_url(os.getenv('MINERU_API_URL', '')),
       'token_present': bool(os.getenv('MINERU_API_TOKEN', '').strip())}
out['effective_settings'] = {}
for key in ('MINERU_REQUEST_MODE','MINERU_ADAPTER_MODE','MINERU_TRUST_ENV'):
    value = os.getenv(key)
    out['effective_settings'][key] = value if value in ('sync','async','worker','dispatcher','true','false','0','1') else ('UNSET' if value is None else 'UNRECOGNIZED')
for key in ('MINERU_TIMEOUT_SECONDS','MINERU_POLL_INTERVAL_SECONDS','MINERU_TRANSIENT_POLL_RETRIES',
            'MINERU_SYNC_MAX_PAGES_PER_REQUEST','MINERU_SYNC_TRANSIENT_RETRIES',
            'MINERU_DISPATCH_MIN_BACKENDS','MINERU_DISPATCH_MAX_WAITING',
            'MINERU_DISPATCH_WAIT_TIMEOUT_SECONDS','MINERU_DISPATCH_REQUEST_TIMEOUT_SECONDS',
            'MINERU_DISPATCH_INTERACTIVE_REQUEST_TIMEOUT_SECONDS'):
    value = os.getenv(key)
    out['effective_settings'][key] = value if value is not None and re.fullmatch(r'[0-9]{1,7}(?:\.[0-9]{1,4})?', value) else ('UNSET' if value is None else 'UNRECOGNIZED')
out['ca_files'] = []
for key in ('MINERU_CA_BUNDLE','REQUESTS_CA_BUNDLE','SSL_CERT_FILE'):
    value = os.getenv(key, '')
    row = {'variable': key, 'configured': bool(value)}
    if value:
        path = Path(value)
        row['exists'] = path.is_file()
        if path.is_file() and path.stat().st_size <= 4 * 1024 * 1024:
            row['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    out['ca_files'].append(row)
path = Path('/app/deerflow_mineru_adapter.py')
out['source'] = {'path': str(path), 'exists': path.is_file(), 'executed_or_imported': False}
if path.is_file() and path.stat().st_size <= 2 * 1024 * 1024:
    raw = path.read_bytes()
    text = raw.decode('utf-8')
    tree = ast.parse(text)
    classes = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'EnterpriseMinerUClient']
    env_names = sorted(set(re.findall(r'["\x27](MINERU_[A-Z0-9_]+)["\x27]', text)))
    out['source'].update(sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw), configurable_names=env_names)
    if len(classes) == 1:
        cls = classes[0]
        def attr(node, name):
            return isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == 'self' and node.attr == name
        def joined(node, first, literal, last=None):
            if not isinstance(node, ast.JoinedStr):
                return False
            v = node.values
            def match(n, expected):
                return isinstance(n, ast.FormattedValue) and (attr(n.value, expected[5:]) if expected.startswith('self.') else isinstance(n.value, ast.Name) and n.value.id == expected)
            def string(n):
                if isinstance(n, ast.Constant):
                    return n.value if isinstance(n.value, str) else None
                return n.s if isinstance(n, ast.Str) else None
            return (len(v) == (3 if last else 2) and match(v[0], first)
                    and string(v[1]) == literal and (not last or match(v[2], last)))
        assignments = [n for n in ast.walk(cls) if isinstance(n, ast.Assign)]
        tasks = any(any(attr(t, 'tasks_url') for t in n.targets) and joined(n.value, 'normalized_url', '/tasks') for n in assignments)
        status = any(any(isinstance(t, ast.Name) and t.id == 'status_url' for t in n.targets) and joined(n.value, 'self.tasks_url', '/', 'task_id') for n in assignments)
        result = any(any(isinstance(t, ast.Name) and t.id == 'result_url' for t in n.targets) and joined(n.value, 'status_url', '/result') for n in assignments)
        out['source']['same_base_url_pattern_present'] = tasks and status and result
        out['source']['separate_task_route_config_names'] = [n for n in env_names if re.search(r'(STATUS|RESULT|TASK|POLL).*(URL|PREFIX|PATH|BASE)', n)]
        out['source']['provider_dynamic_prefix_literal_present'] = '/dynamic/member1/mineru-for-zy-agent' in text
        out['source']['compatibility'] = 'STATIC_PATTERN_ONLY_NOT_PROVIDER_VALIDATION'
    out['source']['parse_coordinator_declared'] = any(isinstance(n, ast.ClassDef) and n.name == 'ParseCoordinator' for n in tree.body)
port = os.getenv('MINERU_LISTEN_PORT', '50052')
out['local_health'] = {'attempted': False}
if port.isdigit() and 1 <= int(port) <= 65535:
    try:
        opener = build_opener(ProxyHandler({}), NoRedirect())
        with opener.open(Request('http://127.0.0.1:' + port + '/health'), timeout=4) as response:
            raw = response.read(65537)
            if len(raw) > 65536:
                raise ValueError('bounded_health_body')
            data = json.loads(raw)
            result = {'attempted': True, 'http_status': response.status}
            for key in ('parser', 'dispatcher'):
                if isinstance(data.get(key), dict):
                    result[key] = {k: v for k, v in data[key].items()
                                   if k in ('active','waiting','rejected','ready','backends','active_backends',
                                            'waiting_interactive','waiting_knowledge','maximum_waiting','minimum_backends')
                                   and isinstance(v, (int, float, bool))}
            out['local_health'] = result
    except Exception as exc:
        out['local_health'] = {'attempted': True, 'error_type': type(exc).__name__, 'raw_error_printed': False}
print(json.dumps(out, sort_keys=True))
'''


class CheckFailure(RuntimeError):
    pass


def require(value, label):
    if not value:
        raise CheckFailure(label)


def emit(value):
    print(json.dumps(value, ensure_ascii=False, sort_keys=True), flush=True)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def capture(args, input_bytes=None, timeout=35):
    result = subprocess.run(['kubectl', '--request-timeout=20s', '-n', NAMESPACE] + args,
                            input=input_bytes, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            timeout=timeout, env=dict(os.environ, KUBECONFIG='/etc/kubernetes/admin.conf'))
    require(result.returncode == 0, 'kubernetes_read_failed')
    require(len(result.stdout) <= 8 * 1024 ** 2, 'bounded_kubernetes_output_exceeded')
    return result.stdout


def get(resource, name=None, selector=None):
    args = ['get', resource]
    if name:
        args.append(name)
    if selector:
        args.extend(['-l', selector])
    return json.loads(capture(args + ['--show-managed-fields=false', '-o', 'json']))


def display_value(key, value):
    if key == 'MINERU_API_TOKEN':
        return {'present': bool(value), 'redacted': True}
    if key in NUMERIC:
        return value if re.fullmatch(r'[0-9]{1,7}(?:\.[0-9]{1,4})?', value) else 'UNRECOGNIZED_REDACTED'
    if key == 'MINERU_API_URL':
        try:
            u = urlsplit(value)
            if u.scheme not in ('http', 'https') or not u.hostname or u.username or u.password:
                return 'REDACTED_NONCANONICAL_URL'
            path = '/'.join(p if len(p) <= 64 else '[REDACTED]' for p in u.path.split('/'))
            return urlunsplit((u.scheme, u.netloc, path, '', ''))
        except ValueError:
            return 'REDACTED_NONCANONICAL_URL'
    if key.endswith('CA_BUNDLE') or key == 'SSL_CERT_FILE':
        return {'configured': bool(value)}
    if key == 'MINERU_DISPATCH_BACKEND_HOST':
        return value if re.fullmatch(r'[A-Za-z0-9.-]{1,253}', value) else 'REDACTED'
    return value if value in ('sync', 'async', 'worker', 'dispatcher', 'true', 'false', '0', '1') else 'UNRECOGNIZED_REDACTED'


def env_summary(container):
    env = []
    for e in container.get('env', []):
        key = e.get('name', '')
        if key not in NUMERIC | NAMED:
            continue
        row = {'name': key}
        if 'value' in e:
            row['value'] = display_value(key, e['value'])
        else:
            refs = e.get('valueFrom', {})
            for kind in ('secretKeyRef', 'configMapKeyRef'):
                if kind in refs:
                    row[kind] = {k: refs[kind].get(k) for k in ('name', 'key')}
        env.append(row)
    return {'selected_env': env,
            'env_from_refs': [{k: e[k].get('name') for k in ('secretRef', 'configMapRef') if k in e}
                              for e in container.get('envFrom', [])]}


def selector_text(selector):
    parts = [k + '=' + v for k, v in sorted(selector.get('matchLabels', {}).items())]
    for expr in selector.get('matchExpressions', []):
        op, key, values = expr['operator'], expr['key'], expr.get('values', [])
        require(op in ('In', 'NotIn', 'Exists', 'DoesNotExist'), 'unsupported_selector')
        parts.append(key + (' in (' if op == 'In' else ' notin (') + ','.join(values) + ')'
                     if op in ('In', 'NotIn') else key if op == 'Exists' else '!' + key)
    require(bool(parts), 'empty_selector_refused')
    return ','.join(parts)


def controlled_by(obj, uid):
    return any(r.get('uid') == uid and r.get('controller') is True
               for r in obj.get('metadata', {}).get('ownerReferences', []))


def inspect_deployment(name):
    dep = get('deployment', name)
    selector = selector_text(dep['spec']['selector'])
    rs = get('replicasets', selector=selector)['items']
    rs_ids = {r['metadata']['uid'] for r in rs if controlled_by(r, dep['metadata']['uid'])}
    pods = [p for p in get('pods', selector=selector)['items']
            if any(controlled_by(p, uid) for uid in rs_ids)]
    containers = dep['spec']['template']['spec']['containers']
    emit({'deployment': name, 'uid': dep['metadata']['uid'], 'generation': dep['metadata']['generation'],
          'spec_sha256': digest(dep['spec']), 'desired': dep['spec'].get('replicas', 1),
          'ready': dep.get('status', {}).get('readyReplicas', 0), 'owned_pods': len(pods),
          'containers': [dict(name=c['name'], image=c['image'], resources=c.get('resources', {}),
                              **env_summary(c)) for c in containers]})
    if 'mineru' in name:
        require(len(pods) <= 24, 'too_many_adapter_pods_for_bounded_audit')
        for p in sorted(pods, key=lambda x: x['metadata']['name']):
            for c in containers:
                statuses = [x for x in p.get('status', {}).get('containerStatuses', []) if x.get('name') == c['name']]
                if c['name'] != 'adapter' and not any('/app/deerflow_mineru_adapter.py' in x
                                                     for x in c.get('command', []) + c.get('args', [])):
                    emit({'pod': p['metadata']['name'], 'container': c['name'], 'source_probe': 'UNKNOWN_CONTAINER_NOT_EXECUTED'})
                    continue
                row = {'pod': p['metadata']['name'], 'uid': p['metadata']['uid'], 'node': p['spec'].get('nodeName'),
                       'container': c['name'], 'image_id': statuses[0].get('imageID') if statuses else None}
                if p['metadata'].get('deletionTimestamp') or p.get('status', {}).get('phase') != 'Running' or not statuses or not statuses[0].get('ready'):
                    emit(dict(row, source_probe='POD_NOT_READY_NOT_EXECUTED'))
                    continue
                emit({'source_probe': 'RUNNING', 'pod': p['metadata']['name']})
                try:
                    raw = capture(['exec', '-i', p['metadata']['name'], '-c', c['name'], '--',
                                   '/app/.venv/bin/python', '-I', '-B', '-c',
                                   "import sys; exec(compile(sys.stdin.read(), '<mineru-readonly-audit>', 'exec'))"],
                                  PROBE.encode(), timeout=30)
                    require(len(raw) < 65536, 'probe_output_limit')
                    row['receipt'] = json.loads(raw)
                except Exception as exc:
                    row['source_probe'] = 'INCOMPLETE'
                    row['error_type'] = type(exc).__name__
                    row['raw_error_printed'] = False
                emit(row)
    # Recheck just the inspected Deployment, not unrelated Gateway/frontend data.
    after = get('deployment', name)
    emit({'deployment': name, 'stable_during_audit': dep['metadata']['uid'] == after['metadata']['uid']
          and dep['metadata']['generation'] == after['metadata']['generation']
          and digest(dep['spec']) == digest(after['spec'])})


def main():
    require(os.geteuid() == 0 and socket.gethostname().split('.')[0] == 'datax-01', 'run_as_root_on_datax01')
    names = capture(['get', 'deployments', '-o', 'custom-columns=NAME:.metadata.name', '--no-headers']).decode().split()
    names = sorted(n for n in names if 'mineru' in n or n in ('weknora-app', 'weknora-docreader'))
    require(0 < len(names) <= 12, 'unexpected_deployment_inventory_size')
    emit({'audit': 'START', 'utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
          'selected_deployments': names, 'secret_api_reads': 0, 'provider_requests': 0,
          'production_mutation': False, 'files_written_by_script': False})
    for name in names:
        require(re.fullmatch(r'[a-z0-9][a-z0-9.-]{0,252}', name), 'invalid_deployment_name')
        inspect_deployment(name)
    emit({'audit': 'COLLECTION_COMPLETE', 'production_mutation': False, 'credentials_printed': False,
          'provider_requests': 0, 'new_credentials_tested': False, 'ready_for_config_switch': False,
          'note': 'COLLECTION_COMPLETION_IS_NOT_PROVIDER_COMPATIBILITY_PASS'})


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        emit({'audit': 'INCOMPLETE', 'error_type': type(exc).__name__,
              'failed_check': str(exc) if isinstance(exc, CheckFailure) else 'REQUIRES_SAFE_DIAGNOSTIC',
              'raw_error_printed': False, 'production_mutation': False, 'automatic_retry': False})
        sys.exit(1)
