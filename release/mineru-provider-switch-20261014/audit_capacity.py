#!/usr/bin/env python3
"""Read-only MinerU 64-worker capacity inventory, compatible with Python 3.7.

No provider calls, Secret GET, credential input, image imports or workload writes.
Request-based estimates are not scheduling or load-test acceptance.
"""
import datetime
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import stat
import sys
import tempfile
import time

import audit_mineru as a

WORKER = 'deer-flow-mineru-adapter'
DISPATCHER = 'deer-flow-mineru-dispatcher'
IMAGE = 'deerflow.local/deer-flow-mineru-adapter:20260921-enterprise-arm64-rc15-conversation-runtime4'
IMAGE_ID = 'sha256:bfbdd4d3309110be003549b2959d5080204b27bcd6440e33d4c67d80ee3775e9'
TARGET = 64
HEALTH = r'''
import hashlib,json,os
from pathlib import Path
from urllib.request import build_opener,ProxyHandler,HTTPRedirectHandler
from urllib.error import HTTPError
class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs): return None
p=Path('/app/deerflow_mineru_adapter.py')
out={'provider_requests':0,'files_modified':False}
if p.is_file() and p.stat().st_size < 2097152:
    out['source_sha256']=hashlib.sha256(p.read_bytes()).hexdigest()
port=os.getenv('MINERU_LISTEN_PORT','50052')
if not port.isdigit() or not 1<=int(port)<=65535: raise ValueError('invalid_health_port')
try:
    r=build_opener(ProxyHandler({}),NoRedirect()).open('http://127.0.0.1:'+port+'/health',timeout=12)
except HTTPError as e:
    r=e
with r:
    raw=r.read(65537)
    if len(raw)>65536: raise ValueError('health_body_too_large')
    out['http_status']=r.code
    data=json.loads(raw)
    for key in ('parser','dispatcher'):
        if isinstance(data.get(key),dict):
            out[key]={k:v for k,v in data[key].items() if k in (
              'active','waiting','rejected','discovered','minimum','busy','live',
              'quarantined','ready','capacity_target_met') and type(v) in (int,bool)}
print(json.dumps(out,sort_keys=True))
'''


def safe_label(value):
    return value if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,160}', value) else 'UNSET_OR_REDACTED'


def quantity(value):
    s = str(value or '0')
    units = {'n': Decimal('1e-9'), 'u': Decimal('1e-6'), 'm': Decimal('1e-3'),
             'k': Decimal(1000), 'K': Decimal(1000), 'M': Decimal(10) ** 6,
             'G': Decimal(10) ** 9, 'T': Decimal(10) ** 12, 'P': Decimal(10) ** 15,
             'E': Decimal(10) ** 18, 'Ki': Decimal(1024), 'Mi': Decimal(1024) ** 2,
             'Gi': Decimal(1024) ** 3, 'Ti': Decimal(1024) ** 4,
             'Pi': Decimal(1024) ** 5, 'Ei': Decimal(1024) ** 6, '': Decimal(1)}
    match = re.fullmatch(r'([0-9]+(?:\.[0-9]+)?)([a-zA-Z]*)', s)
    if match and match[2] in units:
        return Decimal(match[1]) * units[match[2]]
    a.require(bool(re.fullmatch(r'[0-9]+(?:\.[0-9]+)?[eE][+-]?[0-9]+', s)), 'unknown_resource_quantity')
    return Decimal(s)


def pod_requests(pod, resource):
    """Conservative scheduler-style accounting incl. init/sidecars/overhead."""
    spec = pod['spec']
    allocated = {c['name']: c.get('allocatedResources', {}) for c in
                 pod.get('status', {}).get('containerStatuses', []) + pod.get('status', {}).get('initContainerStatuses', [])}
    def req(c):
        return max(quantity(c.get('resources', {}).get('requests', {}).get(resource)),
                   quantity(allocated.get(c['name'], {}).get(resource)))
    regular = sum((req(c) for c in spec.get('containers', [])), Decimal(0))
    sidecars, init_max = Decimal(0), Decimal(0)
    for c in spec.get('initContainers', []):
        if c.get('restartPolicy') == 'Always':
            sidecars += req(c)
            init_max = max(init_max, sidecars)
        else:
            init_max = max(init_max, sidecars + req(c))
    computed = max(regular + sidecars, init_max,
                   quantity(spec.get('resources', {}).get('requests', {}).get(resource)))
    return computed + quantity(spec.get('overhead', {}).get(resource))


def pod_summary(pod):
    state = pod.get('status', {})
    containers = []
    for c in state.get('containerStatuses', []) + state.get('initContainerStatuses', []):
        entry = {'name': safe_label(c.get('name')), 'ready': bool(c.get('ready')),
                 'restarts': c.get('restartCount', 0)}
        for kind in ('state', 'lastState'):
            entry[kind] = {k: {f: (safe_label(v[f]) if f == 'reason' else v[f])
                               for f in ('reason', 'exitCode', 'signal', 'startedAt', 'finishedAt') if f in v}
                           for k, v in c.get(kind, {}).items() if k in ('waiting', 'running', 'terminated')}
        containers.append(entry)
    return {'pod': pod['metadata']['name'], 'uid': pod['metadata']['uid'],
            'node': pod['spec'].get('nodeName'), 'phase': safe_label(state.get('phase')),
            'reason': safe_label(state.get('reason')),
            'deleting': bool(pod['metadata'].get('deletionTimestamp')),
            'conditions': [{k: safe_label(c.get(k)) for k in ('type', 'status', 'reason')}
                           for c in state.get('conditions', [])], 'containers': containers}


def event_summary(items):
    reasons, flags = {}, set()
    patterns = {'insufficient_cpu': 'insufficient cpu', 'insufficient_memory': 'insufficient memory',
                'disk_pressure': 'diskpressure', 'image_pull': 'pull image',
                'image_not_preloaded': 'not present with pull policy of never',
                'node_affinity': "didn't match pod's node affinity",
                'untolerated_taint': 'untolerated taint', 'too_many_pods': 'too many pods',
                'unbound_volume': 'unbound immediate persistentvolumeclaims'}
    for e in items:
        reason = safe_label(e.get('reason'))
        reasons[reason] = reasons.get(reason, 0) + 1
        message = str(e.get('message', '')).lower()
        flags.update(k for k, v in patterns.items() if v in message)
    return {'event_reason_counts': reasons, 'message_categories': sorted(flags), 'raw_messages_printed': False}


def owned(dep):
    selector = a.selector_text(dep['spec']['selector'])
    owners = {r['metadata']['uid'] for r in a.get('replicasets', selector=selector)['items']
              if a.controlled_by(r, dep['metadata']['uid'])}
    pods = [p for p in a.get('pods', selector=selector)['items'] if any(a.controlled_by(p, u) for u in owners)]
    a.require(len(pods) <= 96, 'bounded_worker_inventory')
    return sorted(pods, key=lambda p: p['metadata']['name'])


def health(pod, name):
    rows = [c for c in pod.get('status', {}).get('containerStatuses', []) if c['name'] == name]
    image = next((c['image'] for c in pod['spec']['containers'] if c['name'] == name), None)
    if (pod['metadata'].get('deletionTimestamp') or pod.get('status', {}).get('phase') != 'Running'
            or len(rows) != 1 or not rows[0].get('ready')):
        return {'health': 'NOT_READY_NOT_EXECUTED'}
    if image != IMAGE or rows[0].get('imageID', '').split('://')[-1] != IMAGE_ID:
        return {'health': 'BASELINE_IMAGE_CHANGED_NOT_EXECUTED'}
    try:
        raw = a.capture(['exec', '-i', pod['metadata']['name'], '-c', name, '--',
                         '/app/.venv/bin/python', '-I', '-B', '-c',
                         "import sys;exec(compile(sys.stdin.read(),'<mineru-health-only>','exec'))"], HEALTH.encode(), timeout=25)
        a.require(len(raw) <= 65536, 'bounded_health_receipt')
        return json.loads(raw)
    except Exception:
        return {'health': 'INCOMPLETE', 'raw_error_printed': False}


def match_requirement(expr, values):
    key, op = expr['key'], expr['operator']
    exists, value = key in values, values.get(key)
    expected = expr.get('values', [])
    if op == 'In': return exists and value in expected
    if op == 'NotIn': return not exists or value not in expected
    if op == 'Exists': return exists
    if op == 'DoesNotExist': return not exists
    if op in ('Gt', 'Lt'):
        try:
            return exists and (int(value) > int(expected[0]) if op == 'Gt' else int(value) < int(expected[0]))
        except (ValueError, IndexError, TypeError):
            return False
    raise a.CheckFailure('unsupported_node_selector_operator')


def tolerates(taint, tolerations):
    for t in tolerations:
        if t.get('effect') and t['effect'] != taint.get('effect'): continue
        if t.get('operator', 'Equal') == 'Exists':
            if not t.get('key') or t['key'] == taint['key']: return True
        elif t.get('key') == taint['key'] and t.get('value', '') == taint.get('value', ''):
            return True
    return False


def static_eligible(node, pod_spec):
    labels = node['metadata'].get('labels', {})
    if any(labels.get(k) != v for k, v in pod_spec.get('nodeSelector', {}).items()): return False
    affinity = pod_spec.get('affinity', {}).get('nodeAffinity', {}).get('requiredDuringSchedulingIgnoredDuringExecution')
    if affinity is not None:
        def term_matches(term):
            if not term.get('matchExpressions') and not term.get('matchFields'): return False
            return (all(match_requirement(e, labels) for e in term.get('matchExpressions', []))
                    and all(match_requirement(e, {'metadata.name': node['metadata']['name']}) for e in term.get('matchFields', [])))
        if not any(term_matches(t) for t in affinity.get('nodeSelectorTerms', [])): return False
    taints = list(node.get('spec', {}).get('taints', []))
    if node.get('spec', {}).get('unschedulable'):
        taints.append({'key': 'node.kubernetes.io/unschedulable', 'effect': 'NoSchedule'})
    if any(t.get('effect') in ('NoSchedule', 'NoExecute') and not tolerates(t, pod_spec.get('tolerations', [])) for t in taints):
        return False
    conditions = {c['type']: c.get('status') for c in node.get('status', {}).get('conditions', [])}
    return (conditions.get('Ready') == 'True' and all(conditions.get(c) == 'False'
            for c in ('MemoryPressure', 'DiskPressure', 'PIDPressure')))


def node_budget(node, pods, worker_uids, worker_spec):
    active = [p for p in pods if p.get('status', {}).get('phase') not in ('Succeeded', 'Failed')]
    other = [p for p in active if p['metadata']['uid'] not in worker_uids]
    alloc = node['status']['allocatable']
    desired = {'spec': worker_spec}
    available_slots = []
    summary = {'node': node['metadata']['name'], 'architecture': node['status'].get('nodeInfo', {}).get('architecture'),
               'static_node_rules_match': static_eligible(node, worker_spec),
               'conditions': {c['type']: c.get('status') for c in node['status'].get('conditions', [])
                              if c['type'] in ('Ready', 'DiskPressure', 'MemoryPressure', 'PIDPressure')},
               'active_bound_pods': len(active), 'existing_worker_pods': len(active) - len(other)}
    for key in ('cpu', 'memory'):
        total = quantity(alloc.get(key))
        reserved = sum((pod_requests(p, key) for p in active), Decimal(0))
        other_reserved = sum((pod_requests(p, key) for p in other), Decimal(0))
        per_worker = pod_requests(desired, key)
        a.require(per_worker > 0, 'worker_requires_positive_cpu_memory_requests')
        available_slots.append(max(0, int((total - other_reserved) // per_worker)))
        scale = Decimal(1024) ** 3 if key == 'memory' else Decimal(1)
        summary[key] = {'unit': 'GiB' if key == 'memory' else 'cores',
                        'allocatable': float(total / scale), 'reserved_requests': float(reserved / scale),
                        'per_worker_request': float(per_worker / scale)}
    available_slots.append(max(0, int(quantity(alloc.get('pods'))) - len(other)))
    summary['steady_state_worker_slots_upper_bound'] = min(available_slots) if summary['static_node_rules_match'] else 0
    summary['estimate_only'] = True
    return summary


def save_private(value):
    for path in (Path('/'), Path('/var'), Path('/var/lib')):
        s = path.lstat()
        a.require(stat.S_ISDIR(s.st_mode) and s.st_uid == 0 and not s.st_mode & 0o022, 'unsafe_private_report_parent')
    root = Path(tempfile.mkdtemp(prefix='mineru-capacity-', dir='/var/lib'))
    path = root / 'RESULT.json'
    raw = (json.dumps(value, sort_keys=True, indent=2) + '\n').encode()
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
    return str(path), hashlib.sha256(raw).hexdigest()


def main():
    a.require(os.geteuid() == 0 and socket.gethostname().split('.')[0] == 'datax-01', 'root_datax01_required')
    os.umask(0o077)
    deadline = time.monotonic() + 900
    report = {'utc': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'target_concurrency': TARGET,
              'provider_requests': 0, 'secret_api_reads': 0, 'production_workloads_mutated': False,
              'ready_for_rollout': False, 'deployments': {}, 'nodes': []}
    baselines = {}
    workers = []
    for name in (WORKER, DISPATCHER, 'weknora-app', 'weknora-docreader'):
        a.require(time.monotonic() < deadline, 'audit_deadline_exceeded')
        a.emit({'deployment_inventory': 'RUNNING', 'deployment': name})
        dep = a.get('deployment', name)
        baselines[name] = (dep['metadata']['uid'], a.digest(dep['spec']))
        pods = owned(dep)
        template = dep['spec']['template']['spec']
        row = {'uid': dep['metadata']['uid'], 'generation': dep['metadata']['generation'],
               'spec_sha256': a.digest(dep['spec']), 'desired': dep['spec'].get('replicas', 1),
               'ready': dep.get('status', {}).get('readyReplicas', 0), 'strategy': dep['spec'].get('strategy'),
               'containers': [dict(name=c['name'], image=c['image'], resources=c.get('resources', {}),
                                   **a.env_summary(c)) for c in template['containers']],
               'scheduling': {k: template[k] for k in ('nodeSelector', 'affinity', 'tolerations', 'topologySpreadConstraints', 'priorityClassName') if k in template},
               'pods': []}
        for p in pods:
            a.require(time.monotonic() < deadline, 'audit_deadline_exceeded')
            summary = pod_summary(p)
            if name in (WORKER, DISPATCHER):
                a.emit({'pod_health_inventory': 'RUNNING', 'pod': p['metadata']['name']})
                summary['health_receipt'] = health(p, 'adapter' if name == WORKER else 'dispatcher')
                if summary['health_receipt'].get('health') == 'NOT_READY_NOT_EXECUTED':
                    try:
                        ev = json.loads(a.capture(['get', 'events', '--field-selector',
                                                   'involvedObject.uid=' + p['metadata']['uid'], '-o', 'json']))
                        summary.update(event_summary(ev.get('items', [])))
                    except Exception:
                        summary['events'] = 'UNAVAILABLE'
            row['pods'].append(summary)
        report['deployments'][name] = row
        a.emit(dict(deployment=name, **row))
        if name == WORKER:
            workers, worker_spec = pods, template
    nodes = a.get('nodes')['items']
    a.require(0 < len(nodes) <= 64, 'bounded_node_inventory')
    for node in sorted(nodes, key=lambda n: n['metadata']['name']):
        a.require(time.monotonic() < deadline, 'audit_deadline_exceeded')
        name = node['metadata']['name']
        a.emit({'node_inventory': 'RUNNING', 'node': name})
        # Per-node query bounds output. Never print pod env, command, logs or bodies.
        pods = json.loads(a.capture(['get', 'pods', '--all-namespaces', '--field-selector',
                                    'spec.nodeName=' + name, '--show-managed-fields=false', '-o', 'json']))['items']
        row = node_budget(node, pods, {p['metadata']['uid'] for p in workers}, worker_spec)
        report['nodes'].append(row)
        a.emit(row)
    stability = {}
    for name, old in baselines.items():
        after = a.get('deployment', name)
        stability[name] = (after['metadata']['uid'], a.digest(after['spec'])) == old
    report['baseline_stable_during_audit'] = all(stability.values())
    report['deployment_stability'] = stability
    report['steady_state_slots_upper_bound_requests_only'] = sum(r['steady_state_worker_slots_upper_bound'] for r in report['nodes'])
    report['not_proven'] = ['actual_CPU_RAM_peak', 'disk_free_image_preload', 'image_architecture_eligibility', 'hard_pod_affinity_and_topology',
                            'rolling_surge_headroom', 'provider_64_acceptance', 'WeKnora_64_acceptance',
                            'Redis_backlog_empty', 'metrics_time_series']
    report['inventory'] = 'COMPLETE'
    path, sha = save_private(report)
    a.emit({'inventory': 'COMPLETE', 'report': path, 'report_sha256': sha,
            'baseline_stable_during_audit': report['baseline_stable_during_audit'],
            'target_concurrency': TARGET, 'ready_for_rollout': False,
            'production_workloads_mutated': False, 'provider_requests': 0,
            'note': 'RESOURCE_REQUEST_ESTIMATE_IS_NOT_CAPACITY_ACCEPTANCE'})


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        a.emit({'inventory': 'INCOMPLETE', 'error_type': type(exc).__name__,
                'failed_check': str(exc) if isinstance(exc, a.CheckFailure) else 'SAFE_DIAGNOSTIC_REQUIRED',
                'raw_error_printed': False, 'production_workloads_mutated': False,
                'provider_requests': 0, 'automatic_retry': False})
        sys.exit(1)
