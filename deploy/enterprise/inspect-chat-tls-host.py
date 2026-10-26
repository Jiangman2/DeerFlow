#!/usr/bin/env python3
"""Read current TLS deployment and public CA metadata; Python 3.7+ controller.

No Secret API reads, application startup, database writes, model calls, member
writes, rollout, certificate installation, or verification disabling. Up to six
Gateway node samples send three unauthenticated HEAD requests each. Reports are
written only to a new root-owned directory; no existing report is replaced.
One operator-supplied conversation is inspected in a read-only DB transaction.
"""
import argparse
import base64
import concurrent.futures
import datetime
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import zlib

CHILD = '__SEALED_CHILD__'
BASE = ['kubectl', '--kubeconfig=/etc/kubernetes/admin.conf', '--request-timeout=20s', '-n', 'deer-flow']
DEPLOYMENTS = ('gateway', 'ppt-master-runner', 'provisioner', 'frontend', 'nginx',
               'weknora-app', 'weknora-frontend', 'weknora-docreader',
               'deer-flow-mineru-adapter', 'deer-flow-mineru-dispatcher')
PYTHON_DEPLOYMENTS = ('gateway', 'ppt-master-runner', 'weknora-docreader',
                      'deer-flow-mineru-adapter', 'deer-flow-mineru-dispatcher')
ENV_NAMES = ('SSL_CERT_FILE', 'SSL_CERT_DIR', 'REQUESTS_CA_BUNDLE', 'CURL_CA_BUNDLE',
             'DEERFLOW_BROWSER_CA_BUNDLE', 'OA_POLICY_FLOW_CALLBACK_CA_BUNDLE',
             'GLM_5_3_FLASH_CA_BUNDLE', 'NODE_EXTRA_CA_CERTS', 'TAVILY_TLS_COMPAT',
             'WEKNORA_BAILIAN_TLS_COMPAT', 'OPENSSL_CONF',
             'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY',
             'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy')
PYTHON_COMMAND = '''for p in /app/backend/.venv/bin/python /app/.venv/bin/python /opt/venv/bin/python; do
 if test -x "$p"; then exec "$p" -I -B -; fi
done
if command -v python3 >/dev/null 2>&1; then exec python3 -I -B -; fi
printf '%s\n' '{"status":"PYTHON_UNAVAILABLE","runtime_certificates_not_inspected":true}'
'''


class Refused(Exception):
    pass


def require(value, code):
    if not value:
        raise Refused(code)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def query(args, body=None, timeout=30, limit=16 * 1024 * 1024):
    require(args[0] in ('get', 'exec'), 'READONLY_COMMAND_REQUIRED')
    if args[0] == 'get':
        require(args[1] in ('deployments', 'replicasets', 'pods', 'nodes', 'ingresses'), 'READONLY_RESOURCE_NOT_ALLOWED')
    cmd = list(BASE)
    if args[0] == 'exec':
        cmd[2] = '--request-timeout=70s'
    p = subprocess.run(cmd + args, input=body, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    require(p.returncode == 0, 'KUBERNETES_QUERY_FAILED')
    require(len(p.stdout) <= limit and len(p.stderr) <= 1024 * 1024, 'QUERY_OUTPUT_LIMIT')
    return json.loads(p.stdout)


def identity(d):
    return {'name': d['metadata']['name'], 'uid': d['metadata']['uid'],
            'generation': d['metadata'].get('generation'), 'spec_sha256': digest(d['spec'])}


def owned(item, uid):
    return any(x.get('controller') and x.get('uid') == uid for x in item['metadata'].get('ownerReferences', []))


def pod_identity(p, container):
    statuses = [c for c in p.get('status', {}).get('containerStatuses', []) if c['name'] == container]
    status = statuses[0] if len(statuses) == 1 else {}
    return {'pod': p['metadata']['name'], 'uid': p['metadata']['uid'], 'container': container,
            'node': p['spec'].get('nodeName'), 'container_id': status.get('containerID'),
            'image_id': status.get('imageID'), 'restart_count': status.get('restartCount'),
            'ready': bool(status.get('ready')),
            'running': p.get('status', {}).get('phase') == 'Running' and not p['metadata'].get('deletionTimestamp')}


def template_inventory(d, helpers):
    pod = d['spec']['template']['spec']
    containers = []
    for c in pod.get('containers', []) + pod.get('initContainers', []):
        env = []
        for e in c.get('env', []):
            name = e['name']
            if name not in ENV_NAMES:
                continue
            row = {'name': name}
            if 'valueFrom' in e:
                row['valueFrom'] = e['valueFrom']
            else:
                value = e.get('value', '')
                if name in helpers['CA_VARS'] or name in ('SSL_CERT_DIR', 'OPENSSL_CONF'):
                    row['configured_path'] = helpers['safe_path'](value)
                elif name in helpers['PROXY_VARS']:
                    row['endpoint'] = helpers['proxy_metadata'](value)
                elif name.lower() == 'no_proxy':
                    row.update(sha256=hashlib.sha256(value.encode()).hexdigest(), entry_count=len(value.split(',')) if value else 0)
                else:
                    row['value'] = value if value in ('true', 'false', '1', '0', '') else 'SET_OTHER'
            env.append(row)
        containers.append({'name': c['name'], 'image': c['image'], 'tls_environment': env,
            'env_from_sources': c.get('envFrom', []),
            'certificate_mounts': [m for m in c.get('volumeMounts', []) if any(word in (m.get('mountPath', '') + m['name']).lower() for word in ('cert', '-ca', '/ca', 'tls'))]})
    mounts = {m['name'] for c in containers for m in c['certificate_mounts']}
    volumes = [v for v in pod.get('volumes', []) if v['name'] in mounts]
    return dict(identity(d), desired=d['spec'].get('replicas'),
                observed_generation=d.get('status', {}).get('observedGeneration'),
                ready=d.get('status', {}).get('readyReplicas'), updated=d.get('status', {}).get('updatedReplicas'),
                containers=containers, certificate_volumes=volumes)


def runtime_exec(source, p, container, mode):
    entry = {'identity': pod_identity(p, container)}
    try:
        code = source + "\nprint(json.dumps(main(" + repr(mode) + "),sort_keys=True))\n"
        entry['observation'] = query(['exec', '-i', p['metadata']['name'], '-c', container, '--',
                                      '/bin/sh', '-ec', PYTHON_COMMAND], code.encode(), timeout=65, limit=1024 * 1024)
    except Exception as exc:
        entry['observation'] = {'status': 'INCOMPLETE', 'error_class': type(exc).__name__,
                                'error_code': str(exc) if isinstance(exc, Refused) else 'RUNTIME_INSPECTION_FAILED',
                                'raw_error_exported': False}
    return entry


def group_runtime(rows):
    groups, references = {}, []
    for row in rows:
        observation = row['observation']
        # Membership in a group is based on loaded settings, content and sources,
        # not Pod name, timestamps or the current number of active browsers.
        comparable = json.loads(json.dumps(observation))
        comparable.pop('sampled_at_utc', None)
        live = comparable.pop('live_browser_flags', None)
        for bundle in comparable.get('bundles', {}).values():
            bundle.pop('file_mtime_utc', None)
            # Kubernetes Secret projection version paths differ by Pod even
            # when the configured path and certificate contents are identical.
            bundle.pop('resolved_path', None)
        key = digest(comparable)
        if key not in groups:
            groups[key] = {'fingerprint': key, 'count': 0, 'nodes': [], 'observation': observation}
        g = groups[key]
        g['count'] += 1
        if row['identity']['node'] not in g['nodes']:
            g['nodes'].append(row['identity']['node'])
        references.append({'identity': row['identity'], 'group': key, 'live_browser_flags': live,
                           'workload': row.get('workload')})
    return list(groups.values()), references


def inspect(source, report, save):
    helpers = {'__name__': 'certificate_inventory_helpers'}
    exec(compile(source, '<sealed-certificate-inventory>', 'exec'), helpers)
    deployments = query(['get', 'deployments', '-o', 'json'])['items']
    require(len(deployments) <= 100, 'DEPLOYMENT_COUNT_LIMIT')
    chosen = {d['metadata']['name']: d for d in deployments if d['metadata']['name'] in DEPLOYMENTS}
    require('gateway' in chosen, 'GATEWAY_NOT_FOUND')
    report['deployments'] = [template_inventory(d, helpers) for d in chosen.values()]
    report['host_certificate_inventory'] = helpers['main']('inventory')
    report['host_scope'] = 'DATAX01_ONLY_NOT_ALL_NODE_HOST_FILESYSTEMS'
    nodes = query(['get', 'nodes', '-o', 'json'])['items']
    report['nodes'] = [{'name': n['metadata']['name'], 'uid': n['metadata']['uid'],
                        'conditions': {c['type']: c['status'] for c in n.get('status', {}).get('conditions', []) if c['type'] in ('Ready', 'DiskPressure')},
                        'container_runtime_version': n.get('status', {}).get('nodeInfo', {}).get('containerRuntimeVersion')}
                       for n in nodes]
    try:
        ingress = query(['get', 'ingresses', '-o', 'json'])['items']
        report['ingress_tls_references'] = [{'name': i['metadata']['name'], 'tls': i.get('spec', {}).get('tls', [])} for i in ingress]
    except Exception as exc:
        report['ingress_tls_references'] = {'status': 'UNAVAILABLE', 'error_class': type(exc).__name__}
    rs = query(['get', 'replicasets', '-o', 'json'])['items']
    pods = query(['get', 'pods', '-o', 'json'])['items']
    require(len(pods) <= 2000 and len(rs) <= 2000, 'CLUSTER_METADATA_LIMIT')
    work = []
    gateway_pods = []
    for name in PYTHON_DEPLOYMENTS:
        d = chosen.get(name)
        if d is None:
            continue
        owners = {r['metadata']['uid'] for r in rs if owned(r, d['metadata']['uid'])}
        owned_pods = [p for p in pods if any(owned(p, uid) for uid in owners)]
        for p in sorted(owned_pods, key=lambda x: x['metadata']['name']):
            candidates = [c for c in p['spec']['containers'] if c['name'] not in ('istio-proxy', 'linkerd-proxy')]
            require(len(candidates) <= 4, 'CONTAINER_COUNT_LIMIT')
            for c in candidates:
                info = pod_identity(p, c['name'])
                if not (info['running'] and info['ready']):
                    report.setdefault('uninspected_unready', []).append(dict(info, workload=name))
                    continue
                work.append((name, p, c['name']))
                if name == 'gateway' and c['name'] == 'gateway':
                    gateway_pods.append(p)
    require(len(work) <= 120 and 0 < len(gateway_pods) <= 100, 'RUNTIME_SAMPLE_LIMIT')
    report['gateway_expected_replicas'] = chosen['gateway']['spec'].get('replicas')
    report['gateway_observed_ready_pods'] = len(gateway_pods)
    # Current Sandbox deployment settings are metadata-only. Never start or
    # connect to a user's Sandbox browser or inspect their workspace files.
    sandbox = [p for p in pods if any('sandbox' in c['name'] or c['name'] == 'ppt-master-agent-host' for c in p['spec'].get('containers', []))]
    report['sandbox_metadata_count'] = len(sandbox)
    report['sandbox_templates'] = []
    seen = set()
    for p in sandbox:
        fake = {'metadata': p['metadata'], 'spec': {'template': {'spec': p['spec']}}}
        info = template_inventory(fake, helpers)
        key = digest(info['containers'])
        if key not in seen and len(seen) < 16:
            seen.add(key)
            report['sandbox_templates'].append({'container_configuration': info['containers'], 'certificate_volumes': info['certificate_volumes']})
    report['sandbox_template_limit'] = 16
    report['sandbox_runtime_files_inspected'] = False
    rows = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = {pool.submit(runtime_exec, source, p, c, 'inventory'): name for name, p, c in work}
        for future in concurrent.futures.as_completed(futures):
            row = future.result()
            row['workload'] = futures[future]
            rows.append(row)
            if len(rows) % 10 == 0 or len(rows) == len(work):
                print(json.dumps({'phase': 'READONLY_TLS_RUNTIME_INVENTORY', 'checked': len(rows), 'total': len(work)}), flush=True)
    groups, references = group_runtime(rows)
    report.update(runtime_groups=groups, runtime_samples=references)
    save()
    print(json.dumps({'phase': 'SCOPED_CONVERSATION_TLS_EVIDENCE', 'threads': 1}), flush=True)
    report['incident'] = runtime_exec(source, gateway_pods[0], 'gateway', 'incident')
    save()
    # At most one ready Gateway per node; no routing changes and no retries.
    samples = {}
    for p in gateway_pods:
        samples.setdefault(p['spec']['nodeName'], p)
    require(len(samples) <= 6, 'NETWORK_NODE_SAMPLE_LIMIT')
    report['network_probes'] = []
    report['maximum_head_requests'] = len(samples) * 3
    for node, p in sorted(samples.items()):
        print(json.dumps({'phase': 'UNAUTHENTICATED_TLS_HEAD_PROBE', 'node': node,
                          'checked': len(report['network_probes']), 'total': len(samples)}), flush=True)
        report['network_probes'].append(runtime_exec(source, p, 'gateway', 'probe'))
        save()
    after = query(['get', 'deployments', '-o', 'json'])['items']
    lookup = {d['metadata']['name']: d for d in after}
    report['changed_deployments'] = [name for name, d in chosen.items() if name not in lookup or identity(d) != identity(lookup[name])]
    endpods = {p['metadata']['name']: p for p in query(['get', 'pods', '-o', 'json'])['items']}
    report['changed_runtime_samples'] = []
    for row in rows:
        old = row['identity']
        p = endpods.get(old['pod'])
        if p is None or pod_identity(p, old['container']) != old:
            report['changed_runtime_samples'].append(old['pod'])
    gateway_rows = [r for r in rows if r['workload'] == 'gateway' and r['identity']['container'] == 'gateway']
    good = [r for r in gateway_rows if r['observation'].get('status') == 'OBSERVED']
    report['gateway_inventory_complete'] = len(good) == len(gateway_pods) == report['gateway_expected_replicas'] and not any(r['workload'] == 'gateway' for r in report.get('uninspected_unready', []))
    report['runtime_failures'] = [{'identity': r['identity'], 'status': r['observation'].get('status')} for r in rows if r['observation'].get('status') != 'OBSERVED']
    report['network_failure_count'] = sum(x.get('status') == 'FAILED' for n in report['network_probes'] for x in n['observation'].get('probes', []))
    report['probe_processes_complete'] = all(n['observation'].get('status') == 'PROBES_OBSERVED' for n in report['network_probes'])
    report['gateway_configuration_group_count'] = len({r['group'] for r in references if r['workload'] == 'gateway'})
    stable = not report['changed_deployments'] and not report['changed_runtime_samples']
    report['snapshot_stable'] = stable
    report['incident_process_complete'] = report['incident']['observation'].get('status') == 'SCOPED_INCIDENT_EVIDENCE_OBSERVED'
    complete = stable and report['gateway_inventory_complete'] and report['probe_processes_complete'] and report['incident_process_complete']
    report['status'] = 'TLS_CONFIGURATION_AND_TRANSPORT_OBSERVED_NOT_ROOT_CAUSE_CONFIRMED' if complete else 'PARTIAL_TLS_OBSERVATION_REVIEW_REQUIRED'
    report['exitcode'] = 0 if complete else 1
    report['scope_limits'] = ['ONE_SUPPLIED_THREAD_ONLY_NO_HISTORICAL_POD_ATTRIBUTION', 'NO_FRESH_CHROMIUM_NAVIGATION',
        'NON_PYTHON_CONSUMERS_DEPLOYMENT_METADATA_ONLY', 'SANDBOX_METADATA_ONLY',
        'FRESH_DIAGNOSTIC_PROCESS_NOT_INSPECTION_OF_CACHED_APPLICATION_SSL_CONTEXTS',
        'HEAD_ROOT_TLS_SUCCESS_DOES_NOT_PROVE_SEARCH_BUSINESS_SUCCESS',
        'NO_INSECURE_HANDSHAKE_TO_RECOVER_REJECTED_PEER_CERTIFICATE']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inspect-tls-readonly', action='store_true', required=True)
    parser.parse_args()
    report = {'schema': 'portal-chat-tls-diagnosis.v1', 'status': 'INCOMPLETE', 'exitcode': 1,
        'workload_modified': False, 'configuration_modified': False, 'secret_api_reads': 0,
        'database_access': 'ONE_SUPPLIED_THREAD_READONLY_TRANSACTION', 'database_modified': False,
        'provider_inference_requests': 0, 'credentials_exported': False, 'raw_logs_exported': False,
        'automatic_retry': False, 'certificate_validation_disabled': False, 'browser_launched': False,
        'report_files_written_only': True, 'started_utc': datetime.datetime.now(datetime.timezone.utc).isoformat()}
    started = time.monotonic()
    directory = None
    def save():
        if directory is None:
            return
        temporary = directory / 'REPORT.tmp'
        fd = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as stream:
            json.dump(report, stream, sort_keys=True, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(str(temporary), str(directory / 'REPORT.json'))
    try:
        require(os.name == 'posix' and os.geteuid() == 0 and socket.gethostname().split('.')[0] == 'datax-01', 'ROOT_ON_DATAX01_REQUIRED')
        require(sys.flags.isolated and sys.flags.dont_write_bytecode, 'PYTHON_I_B_REQUIRED')
        source = zlib.decompress(base64.b64decode(CHILD)).decode()
        directory = Path(tempfile.mkdtemp(prefix='portal-chat-tls-diagnosis-20261025-', dir='/var/lib'))
        inspect(source, report, save)
    except (Exception, KeyboardInterrupt) as exc:
        report.update(error_class=type(exc).__name__, error_code=str(exc) if isinstance(exc, Refused) else 'INSPECTION_FAILED_OR_INTERRUPTED')
    report['elapsed_seconds'] = round(time.monotonic() - started, 3)
    save()
    output = dict(report)
    if directory is not None:
        raw = (directory / 'REPORT.json').read_bytes()
        output.update(report_path=str(directory / 'REPORT.json'), report_bytes=len(raw), report_sha256=hashlib.sha256(raw).hexdigest())
    print(json.dumps(output, sort_keys=True), flush=True)
    return report['exitcode']


if __name__ == '__main__':
    raise SystemExit(main())
