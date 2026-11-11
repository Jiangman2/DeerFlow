"""Read-only SkillHub binary and active ingress inspection, fixed production scope.

Does not start applications, send business/API/provider requests, read Secrets,
connect to databases, write configuration or roll workloads. Configuration and
JAR bytes are read into bounded private memory; only hashes and auth facts leave.
"""
import argparse
import datetime
import hashlib
import io
import json
import os
import re
import subprocess
import tempfile
import zipfile

NAMESPACE = 'deer-flow'
DEPLOYMENTS = ('skillhub-server', 'skillhub-web', 'gateway', 'frontend', 'nginx-security-heai-20261012-r1')
SERVICES = ('skillhub-server', 'skillhub-web', 'nginx', 'nginx-nodeport')
ENTRY = 'nginx-security-heai-20261012-r1'
JAR_PATH = '/app/app.jar'
CLASS_PATHS = (
 'BOOT-INF/classes/com/iflytek/skillhub/integration/deerflow/DeerFlowRouteSecurityPolicyRegistry.class',
 'BOOT-INF/classes/com/iflytek/skillhub/compat/ClawHubRegistrySecurityConfig.class',
)
MARKERS = ('authorizationPolicies', 'isRegistryDataPath', '/api/web/skills',
           '/api/web/labels', 'ConditionalOnProperty', 'skillhub.auth.deerflow-session.enabled',
           'havingValue', 'false', 'publicLabelFilterChain', 'clawHubRegistryFilterChain')

class Refused(Exception):
    pass

def require(condition, code):
    if not condition:
        raise Refused(code)

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')

def identity(obj):
    m = obj['metadata']
    return {'name': m['name'], 'uid': m['uid'], 'generation': m.get('generation'),
            'spec_sha256': sha(canonical(obj.get('spec', {})))}

def safe_name(value):
    require(isinstance(value, str) and re.fullmatch(r'[a-z0-9][a-z0-9.-]{0,252}', value), 'INVALID_RESOURCE_NAME')
    return value

class Reader:
    def __init__(self):
        self.count = 0
        self.step = 'start'

    def command(self, args, step, limit=1048576, timeout=30):
        self.step = step
        self.count += 1
        argv = ['kubectl', '--kubeconfig=/etc/kubernetes/admin.conf', '--request-timeout=15s', '-n', NAMESPACE] + args
        # Anonymous OS temporary spools, cleaned on exit; no durable report/config writes.
        with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
            try:
                completed = subprocess.run(argv, stdout=output, stderr=errors, timeout=timeout, check=False)
            except subprocess.TimeoutExpired:
                raise Refused('COMMAND_TIMEOUT')
            require(completed.returncode == 0, 'COMMAND_FAILED')
            require(output.tell() <= limit, 'OUTPUT_BOUND')
            output.seek(0)
            return output.read(limit + 1)

    def get(self, kind, name):
        return json.loads(self.command(['get', kind, safe_name(name), '-o', 'json'], 'get:' + kind + ':' + name))

    def execute(self, pod, container, argv, step, limit=1048576):
        return self.command(['exec', safe_name(pod), '-c', safe_name(container), '--'] + argv, step, limit)

    def selected_pods(self, deployment):
        selector = deployment['spec']['selector']
        require(not selector.get('matchExpressions') and selector.get('matchLabels'), 'UNSUPPORTED_SELECTOR')
        labels = selector['matchLabels']
        for k, v in labels.items():
            require(re.fullmatch(r'[A-Za-z0-9_./-]+', k) and re.fullmatch(r'[A-Za-z0-9_.-]+', v), 'INVALID_SELECTOR')
        query = ','.join(k + '=' + v for k, v in sorted(labels.items()))
        raw = self.command(['get', 'pods', '-l', query, '-o', 'jsonpath={.items[*].metadata.name}'], 'pod_names:' + deployment['metadata']['name'], 8192)
        names = raw.decode('ascii').split()
        require(0 < len(names) <= 8, 'BOUNDED_POD_SET_REQUIRED')
        return [self.get('pod', name) for name in names]

    def endpoints(self, service):
        raw = self.command(['get', 'endpointslices', '-l', 'kubernetes.io/service-name=' + service,
                            '-o', 'json'], 'endpoints:' + service, 262144)
        rows = json.loads(raw).get('items', [])
        result = []
        for row in rows:
            for endpoint in row.get('endpoints', []):
                target = endpoint.get('targetRef', {})
                if endpoint.get('conditions', {}).get('ready') is True:
                    require(target.get('kind') == 'Pod' and target.get('namespace', NAMESPACE) == NAMESPACE, 'POD_ENDPOINT_REQUIRED')
                    result.append([safe_name(target['name']), target['uid']])
        return sorted(result)

def deployment_summary(d):
    out = identity(d)
    status = d.get('status', {})
    out.update(desired=d['spec'].get('replicas', 1), ready=status.get('readyReplicas', 0),
               updated=status.get('updatedReplicas', 0), total=status.get('replicas', 0),
               observed_generation=status.get('observedGeneration'), paused=d['spec'].get('paused', False),
               terminating=status.get('terminatingReplicas', 0))
    out['images'] = {c['name']: c['image'] for c in d['spec']['template']['spec']['containers']}
    return out

def sample_pod(reader, deployment):
    pods = reader.selected_pods(deployment)
    ready = [p for p in pods if not p['metadata'].get('deletionTimestamp') and
             p.get('status', {}).get('phase') == 'Running' and
             any(c.get('type') == 'Ready' and c.get('status') == 'True' for c in p.get('status', {}).get('conditions', []))]
    require(ready, 'NO_READY_POD')
    return sorted(ready, key=lambda p: p['metadata']['name'])[0]

def pod_summary(pod):
    out = identity(pod)
    out['node'] = pod['spec'].get('nodeName')
    out['containers'] = [{'name': c['name'], 'image_id': c.get('imageID'),
                          'restart_count': c.get('restartCount'), 'ready': c.get('ready')}
                         for c in pod.get('status', {}).get('containerStatuses', [])]
    return out

def jar_stat(reader, pod, container):
    raw = reader.execute(pod, container, ['sh', '-c',
        'test -f /app/app.jar && test ! -L /app/app.jar && stat -c "%s %Y %i" /app/app.jar && sha256sum /app/app.jar'],
        'jar_identity', 2048).decode('ascii').strip().splitlines()
    require(len(raw) == 2, 'JAR_IDENTITY_SHAPE')
    size, modified, inode = [int(x) for x in raw[0].split()]
    digest = raw[1].split()[0]
    require(0 < size <= 1073741824 and re.fullmatch(r'[0-9a-f]{64}', digest), 'JAR_IDENTITY_BOUND')
    return {'bytes': size, 'modified_epoch': modified, 'inode': inode, 'sha256': digest}

class RemoteJar(io.RawIOBase):
    """ZIP metadata/class reads through bounded block reads, no extraction/write."""
    def __init__(self, reader, pod, container, size):
        self.reader, self.pod, self.container, self.size = reader, pod, container, size
        self.position, self.transferred = 0, 0
        self.cache = {}

    def seekable(self):
        return True

    def tell(self):
        return self.position

    def seek(self, offset, whence=0):
        target = offset if whence == 0 else self.position + offset if whence == 1 else self.size + offset
        require(whence in (0, 1, 2) and 0 <= target <= self.size, 'JAR_SEEK_BOUND')
        self.position = target
        return target

    def read(self, size=-1):
        if size < 0:
            size = self.size - self.position
        size = min(size, self.size - self.position)
        require(0 <= size <= 4194304, 'JAR_READ_BOUND')
        if not size:
            return b''
        block = 65536
        start = self.position // block
        count = (self.position + size + block - 1) // block - start
        key = (start, count)
        if key not in self.cache:
            require(self.transferred + count * block <= 12582912, 'JAR_TOTAL_READ_BOUND')
            data = self.reader.execute(self.pod, self.container,
                ['dd', 'if=' + JAR_PATH, 'bs=65536', 'skip=' + str(start), 'count=' + str(count)],
                'jar_bounded_block', count * block)
            self.transferred += len(data)
            require(len(data) == min(count * block, self.size - start * block), 'JAR_SHORT_READ')
            self.cache[key] = data
        relative = self.position - start * block
        result = self.cache[key][relative:relative + size]
        self.position += len(result)
        return result

def inspect_jar(reader, pod, container):
    before = jar_stat(reader, pod, container)
    remote = RemoteJar(reader, pod, container, before['bytes'])
    result = {'identity': before, 'classes': {}, 'class_bytes_exported': False,
              'class_markers_are_not_runtime_authorization_proof': True}
    with zipfile.ZipFile(remote, 'r') as archive:
        require(len(archive.infolist()) <= 20000, 'JAR_ENTRY_BOUND')
        for name in CLASS_PATHS:
            infos = [info for info in archive.infolist() if info.filename == name]
            require(len(infos) <= 1, 'DUPLICATE_CLASS')
            if not infos:
                result['classes'][name.rsplit('/', 1)[-1]] = {'present': False}
                continue
            info = infos[0]
            require(0 < info.file_size <= 131072 and info.compress_size <= 131072, 'CLASS_SIZE_BOUND')
            body = archive.read(info)
            require(body[:4] == b'\xca\xfe\xba\xbe', 'INVALID_CLASS')
            result['classes'][name.rsplit('/', 1)[-1]] = {
                'present': True, 'bytes': len(body), 'sha256': sha(body),
                'markers': {word: word.encode('ascii') in body for word in MARKERS}}
    after = jar_stat(reader, pod, container)
    require(before == after, 'JAR_CHANGED_DURING_READ')
    result.update(stable_readback=True, bounded_bytes_transferred=remote.transferred)
    return result

def tokenize_config(text):
    # Preserve quoted values, comments and nested blocks without exporting values.
    pattern = r'''\s+|\#[^\n]*|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[{};]|(?:\\.|[^\s{};#"'])+'''
    tokens = []
    position = 0
    for match in re.finditer(pattern, text):
        require(match.start() == position, 'CONFIG_TOKEN_GAP')
        token = match.group(0)
        position = match.end()
        if token.isspace() or token.startswith('#'):
            continue
        if token.startswith(('"', "'")):
            token = token[1:-1]
        tokens.append(token)
    require(position == len(text) and len(tokens) <= 50000, 'CONFIG_TOKEN_BOUND')
    return tokens

def parse_config(text):
    tokens = tokenize_config(text)
    position = [0]
    def block(depth):
        require(depth <= 24, 'CONFIG_DEPTH_BOUND')
        nodes, words = [], []
        while position[0] < len(tokens):
            token = tokens[position[0]]
            position[0] += 1
            if token == '}':
                require(not words and depth > 0, 'CONFIG_CLOSE')
                return nodes
            if token == '{':
                require(words, 'CONFIG_BLOCK_HEADER')
                nodes.append((words, block(depth + 1)))
                words = []
            elif token == ';':
                require(words, 'CONFIG_EMPTY_DIRECTIVE')
                nodes.append((words, None))
                words = []
            else:
                words.append(token)
        require(not words and depth == 0, 'CONFIG_UNCLOSED')
        return nodes
    return block(0)

def summarize_config(raw):
    text = raw.decode('utf-8')
    tree = parse_config(text)
    routes, includes = [], []
    def visit(nodes):
        for words, children in nodes:
            if words[0] == 'include':
                includes.append(sha(canonical(words)))
            if words[0] == 'location' and any('skillhub' in w.lower() for w in words):
                require(children is not None, 'LOCATION_SHAPE')
                def directives(items):
                    found = []
                    for w, child in items:
                        found.append(w)
                        if child is not None:
                            found.extend(directives(child))
                    return found
                all_directives = directives(children)
                routes.append({'header': words, 'block_sha256': sha(canonical((words, children))),
                    'auth_request': [w[1:] for w in all_directives if w[0] == 'auth_request'],
                    'internal': any(w[0] == 'internal' for w in all_directives),
                    'return_statuses': [w[1] for w in all_directives if w[0] == 'return' and len(w) > 1 and re.fullmatch(r'[0-9]{3}', w[1])],
                    'directive_names': sorted(set(w[0] for w in all_directives)),
                    'proxy_pass_sha256': [sha(canonical(w)) for w in all_directives if w[0] == 'proxy_pass'],
                    'clears_forwarded': any(w == ['proxy_set_header', 'Forwarded', ''] for w in all_directives),
                    'clears_forwarded_port': any(w == ['proxy_set_header', 'X-Forwarded-Port', ''] for w in all_directives),
                    'sets_forwarded_host': any(w in (['proxy_set_header', 'X-Forwarded-Host', '$host'], ['proxy_set_header', 'X-Forwarded-Host', '$http_host']) for w in all_directives)})
            if children is not None:
                visit(children)
    visit(tree)
    require(len(routes) <= 30, 'ROUTE_BOUND')
    return {'sha256': sha(raw), 'bytes': len(raw), 'skillhub_routes': routes,
            'includes': len(includes), 'included_files_not_read': True,
            'raw_configuration_exported': False, 'on_disk_not_proof_of_last_reload': True}

def flag(containers):
    matches = [e for c in containers for e in c.get('env', []) if e.get('name') == 'SKILLHUB_AUTH_DEERFLOW_SESSION_ENABLED']
    require(len(matches) <= 1, 'DUPLICATE_FLAG')
    if not matches:
        return 'not_explicit'
    value = matches[0].get('value')
    return value.lower() if isinstance(value, str) and value.lower() in ('true', 'false') else 'indirect_or_nonboolean'

def inspect(reader, out):
    before = {name: reader.get('deployment', name) for name in DEPLOYMENTS}
    out['deployments'] = {name: deployment_summary(d) for name, d in before.items()}
    out['server_enterprise_auth_flag'] = flag(before['skillhub-server']['spec']['template']['spec']['containers'])
    services = {name: reader.get('service', name) for name in SERVICES}
    out['services'] = {name: dict(identity(s), service_type=s['spec']['type'], selector=s['spec'].get('selector'),
        ports=[{'port': p['port'], 'target_port': p['targetPort']} for p in s['spec']['ports']]) for name, s in services.items()}
    out['endpoints'] = {name: reader.endpoints(name) for name in SERVICES}
    for name in ('nginx', 'nginx-nodeport'):
        require(services[name]['spec'].get('selector') == before[ENTRY]['spec']['selector']['matchLabels'], 'ACTIVE_ENTRY_MISMATCH')
    server = sample_pod(reader, before['skillhub-server'])
    entry = sample_pod(reader, before[ENTRY])
    for service, pod in (('skillhub-server', server), ('nginx', entry), ('nginx-nodeport', entry)):
        require([pod['metadata']['name'], pod['metadata']['uid']] in out['endpoints'][service], 'SAMPLE_NOT_READY_ENDPOINT')
    out['samples'] = {'server': pod_summary(server), 'entry': pod_summary(entry)}
    server_containers = server['spec']['containers']
    require(len(server_containers) == 1, 'ONE_SERVER_CONTAINER_REQUIRED')
    out['server_pod_enterprise_auth_flag'] = flag(server_containers)
    jar_failed = False
    try:
        out['server_jar'] = inspect_jar(reader, server['metadata']['name'], server_containers[0]['name'])
    except Exception as exc:
        jar_failed = True
        out['server_jar'] = {'status': 'INCOMPLETE', 'step': reader.step, 'error_class': type(exc).__name__,
            'error_code': str(exc) if isinstance(exc, Refused) else 'BOUNDED_JAR_READ_FAILED', 'raw_errors_exported': False}
    containers = entry['spec']['containers']
    require(len(containers) == 1, 'ONE_ENTRY_CONTAINER_REQUIRED')
    container = containers[0]
    mounts = [m for m in container.get('volumeMounts', []) if m['mountPath'] == '/etc/nginx/nginx.conf.template']
    require(len(mounts) == 1, 'ENTRY_TEMPLATE_MOUNT_REQUIRED')
    volumes = [v for v in entry['spec']['volumes'] if v['name'] == mounts[0]['name']]
    require(len(volumes) == 1 and 'configMap' in volumes[0], 'ENTRY_CONFIGMAP_REQUIRED')
    cm_name = volumes[0]['configMap']['name']
    cm = reader.get('configmap', cm_name)
    key = mounts[0].get('subPath', 'nginx.conf')
    require(key in cm.get('data', {}), 'CONFIGMAP_KEY_REQUIRED')
    template = cm['data'][key].encode('utf-8')
    require(len(template) <= 262144, 'TEMPLATE_BOUND')
    out['entry_configmap'] = {'name': cm_name, 'uid': cm['metadata']['uid'],
        'data_sha256': sha(canonical(cm['data'])), 'key': key, 'configuration': summarize_config(template)}
    config_reads = {}
    for name, path in (('live', '/tmp/nginx.conf'), ('mounted_template', '/etc/nginx/nginx.conf.template')):
        raw = reader.execute(entry['metadata']['name'], container['name'], ['cat', path], 'entry_config:' + name, 262144)
        config_reads[name] = raw
        out.setdefault('nginx', {})[name] = summarize_config(raw)
    out['nginx']['mounted_matches_configmap'] = config_reads['mounted_template'] == template
    out['nginx']['startup_sha256'] = sha(canonical({'command': container.get('command', []), 'args': container.get('args', [])}))
    after = {name: reader.get('deployment', name) for name in DEPLOYMENTS}
    require(all(identity(before[n]) == identity(after[n]) for n in DEPLOYMENTS), 'DEPLOYMENT_CHANGED_DURING_READ')
    for original in (server, entry):
        fresh = reader.get('pod', original['metadata']['name'])
        require(pod_summary(original) == pod_summary(fresh) and not fresh['metadata'].get('deletionTimestamp'), 'SAMPLE_POD_CHANGED')
    for name, original in services.items():
        require(identity(original) == identity(reader.get('service', name)), 'SERVICE_CHANGED')
        require(out['endpoints'][name] == reader.endpoints(name), 'ENDPOINT_CHANGED')
    fresh_cm = reader.get('configmap', cm_name)
    require(fresh_cm['metadata']['uid'] == cm['metadata']['uid'] and fresh_cm.get('data') == cm.get('data'), 'CONFIGMAP_CHANGED')
    for name, path in (('live', '/tmp/nginx.conf'), ('mounted_template', '/etc/nginx/nginx.conf.template')):
        require(reader.execute(entry['metadata']['name'], container['name'], ['cat', path], 'stable_config:' + name, 262144) == config_reads[name], 'ENTRY_CONFIG_CHANGED')
    out.update(stable_readback=True, status='SKILLHUB_BASELINE_PARTIAL_JAR_UNAVAILABLE' if jar_failed else 'SKILLHUB_AUTH_BASELINE_OBSERVED_NO_CHANGE')

def run():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inspect-readonly', action='store_true', required=True)
    parser.parse_args()
    reader = Reader()
    out = {'schema': 'portal-skillhub-auth-baseline.v1', 'status': 'INCOMPLETE', 'automatic_retry': False,
           'configuration_modified': False, 'workload_modified': False, 'database_access': False,
           'secret_api_reads': 0, 'credentials_exported': False, 'business_requests': 0, 'provider_requests': 0,
           'persistent_files_modified': False, 'temporary_command_spools_cleaned': True,
           'raw_errors_exported': False, 'ready_for_activation': False}
    try:
        require(os.name == 'posix' and os.geteuid() == 0 and os.uname().nodename.split('.')[0] == 'datax-01', 'DATAX01_ROOT_REQUIRED')
        print(json.dumps({'phase': 'READ_SKILLHUB_JAR_AND_ACTIVE_NGINX_AUTH_CONFIGURATION'}), flush=True)
        inspect(reader, out)
    except Exception as exc:
        out.update(error_class=type(exc).__name__, error_code=str(exc) if isinstance(exc, Refused) else 'BOUNDED_INSPECTION_FAILED')
    out.update(last_step=reader.step, query_count=reader.count,
               observed_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
    print(json.dumps(out, sort_keys=True))
    return 0 if out['status'] == 'SKILLHUB_AUTH_BASELINE_OBSERVED_NO_CHANGE' else 1

if __name__ == '__main__':
    raise SystemExit(run())