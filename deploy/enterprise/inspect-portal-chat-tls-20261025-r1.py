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

CHILD = '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'
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
