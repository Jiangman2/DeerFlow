#!/usr/bin/env python3
"""Fixed, read-only Kubernetes preflight. Python 3.7+. No exec/SSH/CRI actions."""
import argparse
import decimal
import hashlib
import ipaddress
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time

NAMESPACE = 'deer-flow'
TARGET = 'datax-02'
NODE_UID = '3148d4c1-6a97-42ed-a78c-950dcea5cbec'
NODE_IP = '192.168.111.37'
DEPLOYMENTS = ('gateway', 'frontend', 'ppt-master-runner', 'provisioner')
PG_REFERENCE = 'm.daocloud.io/docker.io/library/postgres@sha256:33f923b05f64ca54ac4401c01126a6b92afe839a0aa0a52bc5aeb5cc958e5f20'
MAX_BYTES = 16 * 1024 * 1024
MAX_PODS = 1024
TOTAL_SECONDS = 360
COMMAND_SECONDS = 25
RESOURCE_KEYS = ('cpu', 'memory', 'ephemeral-storage')
NAME = re.compile(r'[a-z0-9][a-z0-9.-]{0,252}\Z')
TOKEN = re.compile(r'[A-Za-z0-9_./:@+*=,-]{1,512}\Z')


class Failure(Exception):
    def __init__(self, code, evidence=None):
        self.code, self.evidence = code, evidence or {}
        Exception.__init__(self, code)


def require(ok, code):
    if not ok:
        raise Failure(code)


def canonical(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':')).encode('utf-8')


def sha(value):
    return hashlib.sha256(value).hexdigest()


def stamp(value):
    return sha(canonical(value))


def safe_token(value):
    if value is None:
        return 'UNSET'
    return value if isinstance(value, str) and TOKEN.fullmatch(value) else 'REDACTED_INVALID_VALUE'


def name(value):
    require(isinstance(value, str) and NAME.fullmatch(value), 'invalid_resource_name')
    return value


def safe_path(value):
    if value is None:
        return 'UNSET'
    if isinstance(value, str) and value.startswith('/') and len(value) <= 512 and not re.search(r'[\x00-\x20?#]', value):
        return value
    return 'REDACTED_UNEXPECTED_PATH'


def image(value):
    pattern = r'(?:[A-Za-z0-9][A-Za-z0-9._-]*(?::[0-9]{1,5})?/)?[A-Za-z0-9][A-Za-z0-9._/-]*(?::[A-Za-z0-9_.-]+)?(?:@sha256:[0-9a-f]{64})?'
    return value if isinstance(value, str) and len(value) <= 512 and re.fullmatch(pattern, value) else 'REDACTED_INVALID_IMAGE'


def image_digest(value):
    if not isinstance(value, str):
        return 'UNRECOGNIZED'
    match = re.fullmatch(r'(?:(?:containerd|docker)://)?(sha256:[0-9a-f]{64})', value)
    match = match or re.fullmatch(r'(?:(?:docker-pullable|containerd)://)?[A-Za-z0-9][A-Za-z0-9._:/-]{0,511}@(sha256:[0-9a-f]{64})', value)
    return match.group(1) if match else 'UNRECOGNIZED'


def bounded_json(raw):
    require(len(raw) <= MAX_BYTES, 'json_output_limit')
    def pairs(rows):
        result = {}
        for key, value in rows:
            require(key not in result, 'duplicate_json_key')
            result[key] = value
        return result
    try:
        return json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(Failure('nonfinite_json')))
    except Failure:
        raise
    except Exception:
        raise Failure('invalid_json')


def bounded_run(argv, timeout=COMMAND_SECONDS, limit=MAX_BYTES):
    """Retain stdout only; drain and discard stderr; clean only our process group."""
    output = bytearray()
    counts = {'stdout_bytes': 0, 'stderr_bytes': 0}
    exceeded, pipe_failed = threading.Event(), threading.Event()
    proc, threads = None, []
    started = time.monotonic()
    env = {'PATH': '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
           'HOME': '/root', 'LANG': 'C.UTF-8'}
    def drain(stream, key):
        try:
            while True:
                part = stream.read(65536)
                if not part:
                    break
                counts[key] += len(part)
                if key == 'stdout_bytes' and len(output) < limit:
                    output.extend(part[:limit - len(output)])
                if counts[key] > limit:
                    exceeded.set()
        except Exception:
            pipe_failed.set()
        finally:
            stream.close()
    def stop():
        if proc is None:
            return
        try:
            if os.name == 'posix':
                os.killpg(proc.pid, signal.SIGKILL)
            elif proc.poll() is None:
                proc.kill()
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
    try:
        try:
            proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, env=env, start_new_session=(os.name == 'posix'))
        except Exception:
            raise Failure('command_start_failed')
        for stream, key in ((proc.stdout, 'stdout_bytes'), (proc.stderr, 'stderr_bytes')):
            thread = threading.Thread(target=drain, args=(stream, key), daemon=True)
            threads.append(thread)
            thread.start()
        while proc.poll() is None:
            require(not exceeded.is_set(), 'command_output_limit')
            require(time.monotonic() - started < timeout, 'command_timeout')
            time.sleep(0.02)
        for thread in threads:
            thread.join(timeout=0.5)
        require(not any(t.is_alive() for t in threads), 'command_pipe_cleanup_incomplete')
        require(not exceeded.is_set(), 'command_output_limit')
        require(not pipe_failed.is_set(), 'command_pipe_failed')
        if proc.returncode:
            raise Failure('command_failed', {'returncode': proc.returncode})
        return bytes(output), dict(counts, elapsed_ms=int((time.monotonic() - started) * 1000))
    finally:
        if proc is not None:
            # Also closes inherited-pipe descendants after an exited parent.
            stop()
        for thread in threads:
            if thread.ident is not None:
                thread.join(timeout=0.5)


def selector(spec):
    result = []
    labels = spec.get('matchLabels', {})
    expressions = spec.get('matchExpressions', [])
    require(isinstance(labels, dict) and isinstance(expressions, list) and 0 < len(labels) + len(expressions) <= 32, 'invalid_selector')
    def key(value):
        require(isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_.][A-Za-z0-9_./-]{0,316}', value), 'invalid_selector_key')
        return value
    def val(value):
        require(isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_.-]{0,63}', value), 'invalid_selector_value')
        return value
    for k, v in sorted(labels.items()):
        result.append(key(k) + '=' + val(v))
    for row in expressions:
        op, values = row.get('operator'), row.get('values', [])
        require(isinstance(values, list) and len(values) <= 32, 'invalid_selector_values')
        k = key(row.get('key'))
        if op in ('In', 'NotIn'):
            require(values, 'empty_selector_values')
            result.append(k + (' in (' if op == 'In' else ' notin (') + ','.join(val(v) for v in values) + ')')
        else:
            require(op in ('Exists', 'DoesNotExist') and not values, 'invalid_selector_operator')
            result.append(('!' if op == 'DoesNotExist' else '') + k)
    text = ','.join(result)
    require(len(text) <= 4096, 'selector_too_large')
    return text


def resource_map(values):
    require(isinstance(values, dict) and len(values) <= 64, 'invalid_resource_map')
    return {safe_token(k): safe_token(v) for k, v in sorted(values.items())}


def scheduling(spec):
    """Only Kubernetes scheduling fields; no arbitrary annotations/Env/reference names."""
    allowed = ('nodeName', 'nodeSelector', 'affinity', 'tolerations', 'topologySpreadConstraints',
               'schedulerName', 'priorityClassName', 'runtimeClassName', 'preemptionPolicy')
    def project(value, depth=0):
        require(depth < 14, 'scheduling_depth_limit')
        if value is None or type(value) in (bool, int):
            return value
        if isinstance(value, str):
            return '' if value == '' else safe_token(value)
        if isinstance(value, list):
            require(len(value) <= 128, 'scheduling_count_limit')
            return [project(v, depth + 1) for v in value]
        require(isinstance(value, dict) and len(value) <= 128, 'invalid_scheduling_shape')
        return {safe_token(k): project(v, depth + 1) for k, v in sorted(value.items())}
    return {k: project(spec[k]) for k in allowed if k in spec}


def security(value):
    output = {}
    for key in ('runAsUser', 'runAsGroup', 'fsGroup', 'runAsNonRoot', 'privileged', 'allowPrivilegeEscalation', 'readOnlyRootFilesystem'):
        if type(value.get(key)) in (bool, int):
            output[key] = value[key]
    if isinstance(value.get('seccompProfile'), dict):
        output['seccomp_type'] = safe_token(value['seccompProfile'].get('type'))
    for key in ('capabilities', 'supplementalGroups', 'sysctls', 'seLinuxOptions', 'windowsOptions'):
        if key in value:
            output[key + '_sha256'] = stamp(value[key])
    return output


def container_shape(container, volumes):
    command, args = container.get('command', []), container.get('args', [])
    require(isinstance(command, list) and isinstance(args, list), 'invalid_command_shape')
    launcher = 'CUSTOM_REDACTED'
    combined = command + args
    if not combined:
        launcher = 'IMAGE_DEFAULT_UNRESOLVED'
    elif combined in (['node', 'server.js'], ['/usr/local/bin/node', 'server.js']):
        launcher = 'NODE_STANDALONE'
    elif combined in (['pnpm', 'start'], ['pnpm', 'run', 'start'], ['sh', '-c', 'cd /app/frontend && pnpm start']):
        launcher = 'PNPM_START'
    elif command == ['sh', '-c'] and args == ['cd backend && PYTHONPATH=. uv run --no-sync uvicorn app.gateway.app:app --host 0.0.0.0 --port 8001 --workers 1']:
        launcher = 'KNOWN_GATEWAY_UVICORN'
    probes = {}
    for key in ('livenessProbe', 'readinessProbe', 'startupProbe'):
        if key not in container:
            continue
        probe = container[key]
        methods = [k for k in ('httpGet', 'tcpSocket', 'exec', 'grpc') if k in probe]
        probes[key] = {'sha256': stamp(probe), 'kind': methods[0] if len(methods) == 1 else 'OTHER',
                       'timing': {k: probe[k] for k in ('initialDelaySeconds', 'periodSeconds', 'timeoutSeconds', 'successThreshold', 'failureThreshold') if type(probe.get(k)) is int}}
        if methods == ['httpGet']:
            probes[key]['header_count'] = len(probe['httpGet'].get('httpHeaders', []))
    resources = container.get('resources', {})
    return {'name': name(container['name']), 'image': image(container.get('image')),
            'image_pull_policy': safe_token(container.get('imagePullPolicy')),
            'command_args_sha256': stamp([command, args]), 'launcher': launcher,
            'working_directory': safe_path(container.get('workingDir')),
            'restart_policy': safe_token(container.get('restartPolicy')),
            'requests': resource_map(resources.get('requests', {})), 'limits': resource_map(resources.get('limits', {})),
            'resource_claims_present': bool(resources.get('claims')), 'resources_sha256': stamp(resources),
            'security': security(container.get('securityContext', {})),
            'ports': [{'container_port': p['containerPort'], 'host_port_present': bool(p.get('hostPort')), 'protocol': safe_token(p.get('protocol', 'TCP'))} for p in container.get('ports', []) if type(p.get('containerPort')) is int and 0 < p['containerPort'] < 65536],
            'mounts': [{'path': safe_path(m.get('mountPath')), 'read_only': m.get('readOnly') is True,
                        'sub_path_present': bool(m.get('subPath') or m.get('subPathExpr')),
                        'reference_type': volumes.get(m.get('name'), 'UNKNOWN')} for m in container.get('volumeMounts', [])],
            'probes': probes, 'environment_entries': len(container.get('env', [])),
            'environment_from_entries': len(container.get('envFrom', [])), 'environment_values': 'NOT_EXPORTED'}


def workload(row, kind, expected_name):
    require(row.get('kind') == kind and row['metadata']['name'] == expected_name and row['metadata']['namespace'] == NAMESPACE, 'workload_identity_mismatch')
    meta, spec, status = row['metadata'], row['spec'], row.get('status', {})
    pod = spec['template']['spec']
    volumes = {}
    kinds = ('secret', 'configMap', 'persistentVolumeClaim', 'hostPath', 'emptyDir', 'projected', 'nfs', 'csi', 'downwardAPI', 'ephemeral')
    for v in pod.get('volumes', []):
        matches = [k for k in kinds if k in v]
        volumes[v['name']] = matches[0] if len(matches) == 1 else 'OTHER'
    counts = {k: status.get(k, 0) for k in ('replicas', 'readyReplicas', 'updatedReplicas', 'availableReplicas')}
    require(all(type(v) is int and 0 <= v <= MAX_PODS for v in counts.values()), 'invalid_workload_counts')
    desired = spec.get('replicas', 1)
    require(type(desired) is int and 0 <= desired <= MAX_PODS, 'invalid_replica_count')
    return {'kind': kind, 'name': expected_name, 'uid_sha256': stamp(meta['uid']), 'generation': meta.get('generation'),
            'spec_sha256': stamp(spec), 'deleting': bool(meta.get('deletionTimestamp')), 'desired': desired,
            'observed_generation': status.get('observedGeneration'), 'counts': counts,
            'converged': bool(not meta.get('deletionTimestamp') and desired > 0 and status.get('observedGeneration') == meta.get('generation') and all(v == desired for v in counts.values())),
            'scheduling': scheduling(pod), 'selector_sha256': stamp(spec.get('selector')),
            'host_network': pod.get('hostNetwork') is True, 'host_pid': pod.get('hostPID') is True,
            'host_ipc': pod.get('hostIPC') is True, 'automount_service_account_token': pod.get('automountServiceAccountToken', 'UNSET'),
            'pod_security': security(pod.get('securityContext', {})), 'pod_resources_present': bool(pod.get('resources')),
            'containers': [container_shape(c, volumes) for c in pod['containers']],
            'init_containers': [container_shape(c, volumes) for c in pod.get('initContainers', [])]}


def node(row, expected_name, label_keys=()):
    require(row.get('kind') == 'Node' and row['metadata']['name'] == expected_name, 'node_identity_mismatch')
    meta, status, spec = row['metadata'], row.get('status', {}), row.get('spec', {})
    info = status.get('nodeInfo', {})
    conditions = {c['type']: c.get('status') for c in status.get('conditions', [])}
    wanted = ('Ready', 'DiskPressure', 'MemoryPressure', 'PIDPressure', 'NetworkUnavailable')
    ips = []
    for address in status.get('addresses', []):
        if address.get('type') == 'InternalIP':
            try:
                ips.append(str(ipaddress.ip_address(address['address'])))
            except Exception:
                raise Failure('invalid_node_ip')
    keep_labels = set(label_keys) | {'kubernetes.io/hostname', 'kubernetes.io/arch', 'kubernetes.io/os'}
    return {'name': expected_name, 'uid': safe_token(meta['uid']), 'internal_ips': sorted(set(ips)),
            'historical_identity_matches': (meta['uid'] == NODE_UID and sorted(set(ips)) == [NODE_IP]) if expected_name == TARGET else 'NO_HISTORICAL_PIN',
            'architecture': info.get('architecture') if info.get('architecture') in ('arm64', 'amd64') else 'OTHER',
            'operating_system': info.get('operatingSystem') if info.get('operatingSystem') in ('linux', 'windows') else 'OTHER',
            'runtime_version': safe_token(info.get('containerRuntimeVersion')), 'kubelet_version': safe_token(info.get('kubeletVersion')),
            'conditions': {k: conditions.get(k) if conditions.get(k) in ('True', 'False', 'Unknown') else 'NOT_REPORTED' for k in wanted},
            'unschedulable': spec.get('unschedulable') is True,
            'taints': [{'key': safe_token(t.get('key')), 'value': safe_token(t.get('value', '')), 'effect': safe_token(t.get('effect'))} for t in spec.get('taints', [])],
            'labels': {safe_token(k): safe_token(v) for k, v in meta.get('labels', {}).items() if k in keep_labels},
            'capacity': resource_map({k: v for k, v in status.get('capacity', {}).items() if k in RESOURCE_KEYS + ('pods',)}),
            'allocatable': resource_map({k: v for k, v in status.get('allocatable', {}).items() if k in RESOURCE_KEYS + ('pods',)})}


def pg_pods(row, owner_uid):
    items = row.get('items')
    require(isinstance(items, list) and len(items) <= 32 and not row.get('metadata', {}).get('continue'), 'postgres_pod_list_limit')
    output, excluded = [], 0
    for pod in items:
        meta, spec, status = pod['metadata'], pod['spec'], pod.get('status', {})
        owners = [o for o in meta.get('ownerReferences', []) if o.get('controller') is True]
        if len(owners) != 1 or owners[0].get('uid') != owner_uid or owners[0].get('kind') != 'StatefulSet':
            excluded += 1
            continue
        require(meta.get('namespace') == NAMESPACE, 'postgres_namespace_mismatch')
        statuses = {c['name']: c for c in status.get('containerStatuses', [])}
        output.append({'name': name(meta['name']), 'uid_sha256': stamp(meta['uid']),
                       'node': name(spec['nodeName']) if spec.get('nodeName') else 'UNSCHEDULED',
                       'phase': status.get('phase') if status.get('phase') in ('Pending', 'Running', 'Succeeded', 'Failed', 'Unknown') else 'OTHER',
                       'ready': any(c.get('type') == 'Ready' and c.get('status') == 'True' for c in status.get('conditions', [])),
                       'deleting': bool(meta.get('deletionTimestamp')),
                       'containers': [{'name': name(c['name']), 'image': image(c.get('image')),
                                       'image_digest': image_digest(statuses.get(c['name'], {}).get('imageID')),
                                       'image_id_sha256': stamp(statuses.get(c['name'], {}).get('imageID')),
                                       'container_id_sha256': stamp(statuses.get(c['name'], {}).get('containerID')),
                                       'ready': statuses.get(c['name'], {}).get('ready') is True,
                                       'restarts': statuses.get(c['name'], {}).get('restartCount', 0)} for c in spec['containers']]})
    require(len(output) <= 8, 'postgres_owned_pod_limit')
    return {'owned': sorted(output, key=lambda p: p['name']), 'foreign_selector_matches_excluded': excluded,
            'historical_image_reference': PG_REFERENCE, 'local_cache': 'NOT_COLLECTED'}


def template_string(path):
    return '{{with ' + path + '}}{{printf "%q" .}}{{else}}""{{end}}'


def template_map(path):
    return '{ ' + '{{ $first := true }}{{range $k, $v := ' + path + '}}{{if not $first}},{{end}}{{$first = false}}{{printf "%q" $k}}:{{printf "%q" $v}}{{end}}' + '}'


def container_resource_template(path):
    return '[' + '{{range $i, $c := ' + path + '}}{{if $i}},{{end}}{"restartPolicy":' + template_string('$c.restartPolicy') + ',"requests":' + template_map('$c.resources.requests') + ',"limits":' + template_map('$c.resources.limits') + ',"claims":{{if $c.resources.claims}}true{{else}}false{{end}}}{{end}}' + ']'


# Fixed template strips all env, command, mount and reference content at kubectl.
POD_RESOURCE_TEMPLATE = ('{"uid":' + template_string('$p.metadata.uid') +
    ',"node":' + template_string('$p.spec.nodeName') + ',"phase":' + template_string('$p.status.phase') +
    ',"deleting":{{if $p.metadata.deletionTimestamp}}true{{else}}false{{end}},"containers":' + container_resource_template('$p.spec.containers') +
    ',"initContainers":' + container_resource_template('$p.spec.initContainers') + ',"overhead":' + template_map('$p.spec.overhead') +
    ',"podResources":{{if $p.spec.resources}}true{{else}}false{{end}},"resourceClaims":{{if $p.spec.resourceClaims}}true{{else}}false{{end}},' +
    '"resize":{{if $p.status.resize}}true{{else}}false{{end}},"allocatedResources":[' +
    '{{range $j, $c := $p.status.containerStatuses}}{{if $j}},{{end}}{{if or $c.allocatedResources $c.resources}}true{{else}}false{{end}}{{end}}]' +
    '}')
RESOURCE_TEMPLATE = ('{"items":[{{range $i, $p := .items}}{{if $i}},{{end}}' + POD_RESOURCE_TEMPLATE +
                     '{{end}}],"continued":{{if .metadata.continue}}true{{else}}false{{end}}}')


def quantity(value, kind):
    require(isinstance(value, str) and len(value) <= 64, 'unsupported_resource_quantity')
    match = re.fullmatch(r'([0-9]+(?:\.[0-9]+)?)([eE][+-]?[0-9]{1,2}|[numkKMGTPE]|[KMGTPE]i)?', value)
    require(match is not None, 'unsupported_resource_quantity')
    number, suffix = decimal.Decimal(match.group(1)), match.group(2) or ''
    decimal_scale = {'n': -9, 'u': -6, 'm': -3, '': 0, 'k': 3, 'K': 3, 'M': 6, 'G': 9, 'T': 12, 'P': 15, 'E': 18}
    if suffix.endswith('i'):
        factor = decimal.Decimal(1024) ** ('KMGTPE'.index(suffix[0]) + 1)
    elif suffix.startswith(('e', 'E')) and len(suffix) > 1:
        factor = decimal.Decimal(10) ** int(suffix[1:])
    else:
        factor = decimal.Decimal(10) ** decimal_scale[suffix]
    number *= factor
    require(number.is_finite() and 0 <= number <= decimal.Decimal(2) ** 63, 'resource_quantity_range')
    # Scheduler CPU is expressed in millicores, storage/memory in bytes.
    return int((number * (1000 if kind == 'cpu' else 1)).to_integral_value(rounding=decimal.ROUND_CEILING))


def resource_envelope(pod, which):
    require(which in ('requests', 'limits'), 'invalid_resource_mode')
    require(not pod.get('podResources') and not pod.get('resourceClaims') and not pod.get('resize') and not any(pod.get('allocatedResources', [])), 'unsupported_pod_resource_semantics')
    all_containers = pod['containers'] + pod.get('initContainers', [])
    require(all_containers and len(all_containers) <= 64, 'container_resource_count_limit')
    for c in all_containers:
        require(not c.get('claims') and c.get('restartPolicy', '') in ('', 'Always'), 'unsupported_container_resource_semantics')
        for field in ('requests', 'limits'):
            require(isinstance(c.get(field), dict) and set(c[field]) <= set(RESOURCE_KEYS), 'unsupported_resource_type')
    require(isinstance(pod.get('overhead', {}), dict) and set(pod.get('overhead', {})) <= set(RESOURCE_KEYS), 'unsupported_overhead_type')
    result = {}
    for k in RESOURCE_KEYS:
        # A missing limit means unbounded, unlike a missing request.
        if which == 'limits' and any(k not in c[which] for c in all_containers):
            result[k] = None
            continue
        def val(c):
            return quantity(c[which].get(k, '0'), k)
        sidecars = 0
        peak_init = 0
        for c in pod.get('initContainers', []):
            if c.get('restartPolicy') == 'Always':
                sidecars += val(c)
                peak_init = max(peak_init, sidecars)
            else:
                peak_init = max(peak_init, sidecars + val(c))
        steady = sidecars + sum(val(c) for c in pod['containers'])
        result[k] = max(steady, peak_init) + quantity(pod.get('overhead', {}).get(k, '0'), k)
    return result


def resources(row, target_node):
    require(isinstance(row.get('items'), list) and len(row['items']) <= MAX_PODS and row.get('continued') is False, 'node_pod_list_incomplete')
    totals = {k: 0 for k in RESOURCE_KEYS}
    limits = {k: 0 for k in RESOURCE_KEYS}
    unbounded = {k: 0 for k in RESOURCE_KEYS}
    unknown, active, terminal = [], 0, 0
    for pod in row['items']:
        require(pod.get('node') == TARGET, 'resource_pod_wrong_node')
        if pod.get('phase') in ('Succeeded', 'Failed'):
            terminal += 1
            continue
        active += 1
        try:
            require(pod.get('phase') in ('Pending', 'Running', 'Unknown'), 'unsupported_pod_phase')
            req, lim = resource_envelope(pod, 'requests'), resource_envelope(pod, 'limits')
            for k in RESOURCE_KEYS:
                totals[k] += req[k]
                if lim[k] is None:
                    unbounded[k] += 1
                else:
                    limits[k] += lim[k]
        except Failure as exc:
            unknown.append({'pod_uid_sha256': stamp(pod.get('uid')), 'code': exc.code})
    available = {}
    try:
        for k in RESOURCE_KEYS:
            available[k] = quantity(target_node['allocatable'][k], k) - totals[k]
    except (KeyError, Failure):
        available = {'status': 'UNDETERMINED_NODE_ALLOCATABLE'}
    return {'active_pods': active, 'terminal_pods_excluded': terminal, 'unknown_pods': unknown,
            'units': {'cpu': 'millicores', 'memory': 'bytes', 'ephemeral-storage': 'bytes'},
            'known_requests': totals, 'known_bounded_limits': limits, 'unbounded_limit_pods': unbounded,
            'request_headroom': available if not unknown else 'UNDETERMINED_UNKNOWN_POD_SEMANTICS',
            'capacity_pass': False, 'live_utilization': 'NOT_COLLECTED',
            'scope': 'SCHEDULER_REQUESTS_ACCOUNTING_NOT_EXECUTION_ADMISSION'}


class Collector:
    def __init__(self, binary, runner=bounded_run):
        self.binary, self.runner = binary, runner
        self.started = time.monotonic()
        self.steps = []
        self.allowed_nodes = {TARGET}

    def get(self, label, kind, resource=None, selector_text=None, resource_projection=False):
        allowed = ((kind == 'nodes' and resource in self.allowed_nodes) or
                   (kind == 'deployments' and resource in DEPLOYMENTS) or
                   (kind == 'statefulsets' and resource == 'postgres') or
                   (kind == 'pods' and resource is None and (selector_text is not None or resource_projection)))
        require(allowed, 'forbidden_read_scope')
        argv = [self.binary, '--kubeconfig=/etc/kubernetes/admin.conf', '--cache-dir=/dev/null', '--request-timeout=20s', 'get', kind]
        if resource:
            argv.append(resource)
        if kind != 'nodes':
            argv.extend(['--all-namespaces'] if resource_projection else ['--namespace', NAMESPACE])
        if selector_text is not None:
            require(kind == 'pods' and isinstance(selector_text, str) and len(selector_text) <= 4096, 'invalid_read_selector')
            argv.extend(['--selector', selector_text])
        if resource_projection:
            require(kind == 'pods' and selector_text is None, 'invalid_projection_scope')
            argv.extend(['--field-selector', 'spec.nodeName=' + TARGET, '--chunk-size=0', '--output=go-template=' + RESOURCE_TEMPLATE])
        else:
            argv.extend(['--output=json'])
            if kind == 'pods':
                argv.append('--chunk-size=0')
        remaining = TOTAL_SECONDS - (time.monotonic() - self.started)
        require(remaining > 0, 'collection_deadline')
        raw, receipt = self.runner(argv, timeout=min(COMMAND_SECONDS, remaining), limit=MAX_BYTES)
        self.steps.append({'step': label, 'read': kind, 'status': 'READ_COMPLETE', **receipt})
        return bounded_json(raw)


def collect(kube):
    report = {'schema': 'portal-ppt-native-preflight.v1', 'scope': 'READ_ONLY_MINIMAL_CURRENT_STATE',
              'target_node': TARGET, 'production_mutation': False, 'source_collection': False,
              'database_connection': False, 'environment_values': 'NOT_EXPORTED',
              'ready_for_native_execution': False, 'ready_for_build': False, 'ready_for_rollout': False,
              'before': {}, 'after': {}, 'errors': [], 'steps': kube.steps,
              'not_collected': ['CNI_POLICY_ENFORCEMENT', 'LOCAL_NODE_IMAGE_CACHE', 'NATIVE_CANDIDATE_RUNTIME',
                                'PRODUCTION_ENV_VALUES', 'SECRET_OR_CONFIGMAP_CONTENTS', 'LIVE_RESOURCE_UTILIZATION']}
    def step(label, fn):
        print(json.dumps({'progress': label}), file=sys.stderr, flush=True)
        try:
            return fn()
        except Exception as exc:
            error = {'step': label, 'code': exc.code if isinstance(exc, Failure) else 'unexpected_read_failure'}
            if isinstance(exc, Failure):
                error.update({k: v for k, v in exc.evidence.items() if k in ('returncode',) and type(v) is int})
            report['errors'].append(error)
            return None
    raw_nodes = {}
    label_keys = set()
    for phase in ('before', 'after'):
        result = report[phase]
        raw_node = step(phase + '.node', lambda: kube.get(phase + '.node', 'nodes', TARGET))
        if raw_node is not None:
            raw_nodes[phase] = raw_node
        result['deployments'] = {}
        for component in DEPLOYMENTS:
            def read_component(component=component):
                raw = kube.get(phase + '.' + component, 'deployments', component)
                projected = workload(raw, 'Deployment', component)
                scheduling_raw = raw['spec']['template']['spec']
                label_keys.update(scheduling_raw.get('nodeSelector', {}).keys())
                def find_keys(value):
                    if isinstance(value, dict):
                        for key, val in value.items():
                            if key in ('key', 'topologyKey') and isinstance(val, str):
                                label_keys.add(val)
                            find_keys(val)
                    elif isinstance(value, list):
                        for val in value:
                            find_keys(val)
                find_keys(projected['scheduling'])
                return projected
            result['deployments'][component] = step(phase + '.' + component, read_component)
        raw_pg = step(phase + '.postgres', lambda: kube.get(phase + '.postgres', 'statefulsets', 'postgres'))
        if raw_pg is not None:
            result['postgres'] = step(phase + '.postgres_projection', lambda: workload(raw_pg, 'StatefulSet', 'postgres'))
            def read_pg_pods():
                return pg_pods(kube.get(phase + '.postgres_pods', 'pods', selector_text=selector(raw_pg['spec']['selector'])), raw_pg['metadata']['uid'])
            result['postgres_pods'] = step(phase + '.postgres_pods', read_pg_pods)
        if phase == 'before':
            if raw_node is not None:
                result['node'] = step('before.node_projection', lambda: node(raw_node, TARGET, label_keys))
            resource_rows = step('node_resource_inventory', lambda: kube.get('node_resource_inventory', 'pods', resource_projection=True))
            if resource_rows is not None and result.get('node') is not None:
                report['resources'] = step('resource_accounting', lambda: resources(resource_rows, result['node']))
    for phase, raw in raw_nodes.items():
        report[phase]['node'] = step(phase + '.final_node_projection', lambda raw=raw: node(raw, TARGET, label_keys))
    pg_nodes = set()
    for phase in ('before', 'after'):
        for pod in (report[phase].get('postgres_pods') or {}).get('owned', []):
            if pod['node'] not in (TARGET, 'UNSCHEDULED'):
                pg_nodes.add(pod['node'])
    report['postgres_nodes'] = {}
    if len(pg_nodes) > 8:
        report['errors'].append({'step': 'postgres_nodes', 'code': 'postgres_node_limit'})
    else:
        for target in sorted(pg_nodes):
            kube.allowed_nodes.add(target)
            report['postgres_nodes'][target] = step('postgres_node.' + target, lambda target=target: node(kube.get('postgres_node.' + target, 'nodes', target), target))
    report['identity_and_shape_stable'] = bool(not report['errors'] and report['before'] == report['after'])
    report['collection'] = 'COMPLETE_REVIEW_REQUIRED' if not report['errors'] else 'INCOMPLETE_REVIEW_REQUIRED'
    report['resource_inventory_freshness'] = 'ONE_SNAPSHOT_NOT_A_CAPACITY_RESERVATION'
    report['stability_scope'] = 'FOUR_DEPLOYMENTS_POSTGRES_STATEFULSET_OWNED_POSTGRES_PODS_AND_TARGET_NODE_ONLY_NOT_70_GATEWAY_PODS_OR_ENTIRE_CLUSTER'
    report['isolation_enforcement'] = 'UNPROVEN_REQUIRES_SEPARATE_EXECUTION_GATE'
    report['elapsed_ms'] = int((time.monotonic() - kube.started) * 1000)
    return report


def emit_report(report, exitcode):
    data = (json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2) + '\n').encode('utf-8')
    require(len(data) <= 2 * 1024 * 1024, 'report_output_limit')
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()
    print(json.dumps({'collection': report['collection'], 'identity_and_shape_stable': report.get('identity_and_shape_stable', False),
                      'report_bytes': len(data), 'report_sha256': sha(data), 'exitcode': exitcode}, sort_keys=True), file=sys.stderr, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--collect', action='store_true', required=True)
    parser.parse_args()
    require(os.name == 'posix' and os.geteuid() == 0, 'linux_root_required')
    require(os.uname().nodename == 'datax-01', 'operator_host_mismatch')
    binary = shutil.which('kubectl', path='/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin')
    require(binary is not None, 'kubectl_missing')
    report = collect(Collector(binary))
    exitcode = 0 if report['collection'] == 'COMPLETE_REVIEW_REQUIRED' else 2
    emit_report(report, exitcode)
    return exitcode


if __name__ == '__main__':
    try:
        sys.exit(main())
    except BaseException as exc:
        if isinstance(exc, SystemExit):
            raise
        emit_report({'collection': 'FAILED_REVIEW_REQUIRED', 'code': exc.code if isinstance(exc, Failure) else 'unexpected_failure',
                     'ready_for_native_execution': False, 'ready_for_build': False, 'ready_for_rollout': False}, 2)
        sys.exit(2)
