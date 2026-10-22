#!/usr/bin/env python3
"""Python 3.7+ one-shot parallel pool staging. NO public-route change or drain.

Only nine reviewed new objects can be created. Existing workloads are never
patched/restarted/deleted. Unknown outcomes retain evidence and require status.
"""
import argparse
from collections import Counter
import copy
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import selectors
import socket
import stat
import subprocess
import sys
import time
import types

ROOT = Path('/var/lib/mineru-bluegreen16-stage-20261015-r1')
PREPARED = Path('/var/lib/mineru-bluegreen16-prepare-1kye80k4')
REPORT_SHA = 'bafea93f168585cfb5ea7ed7ad3eaf6e60ee7b44bcb1117132a559d47593a7d6'
PREPARE_SHA = '1064d935beb33a5e1dc2f741d1f9474fa4cfcc29ac17d6529d5dcca56e8292b4'
PROBE_SHA = 'a5e23fa8b381c6443c86ceabba0e19db14d5a22072a04962d86da1f651e6f503'
NODES = ['ecs-zdz-pro-%04d' % n for n in range(11, 17)]
WORKERS = ['deer-flow-mineru-dedicated-r1-w-ecs-%04d' % n for n in range(11, 17)]
DISPATCHER = 'deer-flow-mineru-dedicated-dispatch-r1'
SERVICES = ['deer-flow-mineru-dedicated-workers-r1', 'deer-flow-mineru-dedicated-preview-r1']
ALLOWED = dict([(n, 'Deployment') for n in WORKERS+[DISPATCHER]] + [(n, 'Service') for n in SERVICES])
prep = core = None
PHASE = 'BOOTSTRAP'
LAST_OPERATION = None


class StageFailure(RuntimeError):
    pass


def require(ok, detail):
    if not ok:
        raise StageFailure(detail)


def emit(value):
    print(json.dumps(value, sort_keys=True), flush=True)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def safe_dir(path):
    require(path.is_absolute(), 'absolute_private_path_required')
    for p in reversed([path]+list(path.parents)):
        s = p.lstat()
        require(stat.S_ISDIR(s.st_mode) and s.st_uid == 0 and not s.st_mode & 0o022,
                'unsafe_private_directory')


def locked(path, expected):
    safe_dir(path.parent)
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        require(stat.S_ISREG(before.st_mode) and before.st_uid == 0 and before.st_nlink == 1
                and not before.st_mode & 0o022 and 0 < before.st_size < 2*1024**2, 'unsafe_locked_file')
        raw = stream.read(2*1024**2)
        after = os.fstat(stream.fileno())
    identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns,
                          s.st_mode, s.st_uid, s.st_gid, s.st_nlink)
    require(identity(before) == identity(after) == identity(path.lstat())
            and len(raw) == before.st_size and sha(raw) == expected, 'locked_file_changed')
    return raw


def bootstrap():
    global prep, core
    here = Path(__file__).absolute().parent
    raw = locked(here/'prepare_bluegreen16.py', PREPARE_SHA)
    prep = types.ModuleType('frozen_bluegreen_prepare')
    prep.__file__ = str(here/'prepare_bluegreen16.py')
    exec(compile(raw, prep.__file__, 'exec'), prep.__dict__)
    core = prep.load_core()
    prep.core = core
    return locked(here/'stage_runtime_probe.py', PROBE_SHA).decode('utf-8')


def call(args, timeout=35):
    global LAST_OPERATION
    LAST_OPERATION = {'operation': args[0], 'target': args[1] if len(args) > 1 else None}
    command = ['kubectl', '--request-timeout=20s', '-n', 'deer-flow']+args
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, env=dict(os.environ, KUBECONFIG='/etc/kubernetes/admin.conf'))
    data = {'out': bytearray(), 'err': bytearray()}
    deadline = time.monotonic()+timeout
    try:
        with selectors.DefaultSelector() as sel:
            sel.register(process.stdout, selectors.EVENT_READ, 'out')
            sel.register(process.stderr, selectors.EVENT_READ, 'err')
            while sel.get_map():
                require(time.monotonic() < deadline, 'command_deadline_no_automatic_retry')
                for key, _ in sel.select(0.25):
                    chunk = os.read(key.fileobj.fileno(), 65536)
                    if not chunk:
                        sel.unregister(key.fileobj)
                        continue
                    limit = 4*1024**2 if key.data == 'out' else 65536
                    require(len(data[key.data])+len(chunk) <= limit, 'command_output_limit')
                    data[key.data].extend(chunk)
        code = process.wait(timeout=max(.01, deadline-time.monotonic()))
        LAST_OPERATION.update(exit_code=code, stdout_bytes=len(data['out']), stderr_bytes=len(data['err']))
        if code:
            err = bytes(data['err']).lower()
            LAST_OPERATION['stderr_classes'] = [label for label, marker in (
                ('forbidden', b'forbidden'), ('conflict', b'alreadyexists'), ('timeout', b'timeout'),
                ('invalid', b'invalid'), ('unavailable', b'unavailable'), ('not_found', b'notfound')) if marker in err]
            if args[0] == 'exec' and len(data['out']) < 32768:
                try:
                    receipt = json.loads(bytes(data['out']))
                    if receipt.get('stage_runtime') == 'FAIL':
                        LAST_OPERATION['probe_failure'] = {k: receipt.get(k) for k in ('stage_runtime', 'error_type', 'failed_check')}
                except (ValueError, TypeError, AttributeError):
                    pass
            raise StageFailure('kubernetes_command_failed:'+args[0])
        return bytes(data['out'])
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        process.stdout.close()
        process.stderr.close()


def get(kind, name=None, selector=None):
    require(kind in ('deployments', 'services', 'pods', 'replicasets', 'horizontalpodautoscalers', 'endpointslices.discovery.k8s.io'),
            'get_kind_out_of_scope')
    args = ['get', kind]
    if name:
        require(re.fullmatch('[a-z0-9-]{1,63}', name), 'invalid_get_name')
        args += [name, '--ignore-not-found']
    if selector:
        require(kind in ('pods', 'replicasets', 'endpointslices.discovery.k8s.io'), 'selector_kind')
        args += ['--selector', selector]
    raw = call(args+['--show-managed-fields=false', '-o', 'json'])
    return json.loads(raw) if raw.strip() else None


def validate_target(obj):
    m = obj['metadata']
    name = m['name']
    require(ALLOWED.get(name) == obj['kind'] and m.get('namespace') == 'deer-flow'
            and m.get('labels', {}).get(prep.LABEL) == prep.RELEASE, 'write_outside_new_pool')
    if obj['kind'] == 'Deployment':
        require(obj['spec']['replicas'] == 1 and obj['spec']['strategy'] == {'type': 'Recreate'},
                'initial_replica_or_strategy_changed')
        role = 'dispatcher' if name == DISPATCHER else 'worker'
        require(all(obj['spec']['template']['metadata']['labels'].get(k) == v
                    for k, v in prep.labels(role).items()), 'new_pool_labels_changed')
        node = NODES[WORKERS.index(name)] if role == 'worker' else 'ecs-zdz-pro-0012'
        require(obj['spec']['template']['spec']['nodeSelector'].get('kubernetes.io/hostname') == node,
                'placement_changed')


def report_contract(report):
    require(report.get('mineru_bluegreen16_prepare') == 'PASS'
            and report.get('production_workloads_mutated') is False
            and report.get('ready_for_stage_review') is True, 'prepare_not_pass')
    require(report['initial_plan'] == {'controller': 'ecs-zdz-pro-0012', 'workers': NODES, 'total_slots': 7},
            'reviewed_placement_changed')
    require(report['concurrency_plan']['new_initial_slots'] == 6
            and report['concurrency_plan']['old_reserved_slots'] == 10
            and report['concurrency_plan']['target_slots'] == 16, 'reviewed_slot_budget_changed')
    dry = report['dry_runs']
    require(len(dry) == 9 and {r['name']: r['kind'] for r in dry} == ALLOWED
            and all(r['server_dry_run'] == 'PASS' for r in dry), 'reviewed_objects_incomplete')
    require(report['candidate'] == core.inv.CANDIDATE, 'candidate_changed')


def service_shape(spec):
    # Only allocated IPs may differ from the server dry run, not routing/defaults.
    result = copy.deepcopy(spec)
    result.pop('clusterIP', None)
    result.pop('clusterIPs', None)
    return result


def admitted(obj, actual, review, reference):
    validate_target(obj)
    prep.validate_admission(obj, actual)
    meta = actual.get('metadata', {})
    require(meta.get('namespace') == 'deer-flow' and meta.get('uid') and not meta.get('deletionTimestamp'),
            'created_identity_invalid')
    require(meta.get('labels', {}).get(prep.LABEL) == prep.RELEASE, 'created_label_changed')
    if obj['kind'] == 'Deployment':
        require(core.inv.digest(actual['spec']) == review['admitted_spec_sha256'], 'admitted_deployment_changed')
    else:
        require(service_shape(actual['spec']) == service_shape(reference['spec']), 'admitted_service_changed')
        ip = actual['spec'].get('clusterIP')
        if obj['metadata']['name'] == SERVICES[0]:
            require(ip == 'None', 'headless_service_changed')
        else:
            require(ip not in (None, '', 'None') and not ipaddress.ip_address(ip).is_loopback,
                    'preview_cluster_ip_invalid')


def old_unchanged(report, controls, objects):
    deps = {n: core.inv.get('deployment', n) for n in core.inv.BASELINES}
    for name, dep in deps.items():
        core.inv.baseline(dep, name)
        lock = report['baselines'][name]
        require(dep['metadata']['uid'] == lock['uid'] and core.inv.digest(dep['spec']) == lock['spec_sha256'],
                'old_deployment_changed:' + name)
    core.pf.controls_snapshot(deps, controls)
    require(not any(h.get('spec', {}).get('scaleTargetRef', {}).get('name') in ALLOWED
                    for h in get('horizontalpodautoscalers')['items']), 'new_pool_autoscaler_requires_review')
    budget = prep.transition_budget(deps[prep.WORKER], core.inv.owned_pods(deps[prep.WORKER]))
    require(budget == report['concurrency_plan'], 'overlap_budget_changed')
    require(prep.live_policies() == report['policies'], 'policy_changed_requires_new_review')
    old_services = [s for s in get('services')['items'] if s['metadata']['name'] not in SERVICES]
    prep.routes_disjoint(objects, old_services)
    return deps


def credential_identity():
    template = ('{"uid":"{{.metadata.uid}}","immutable":{{if .immutable}}true{{else}}false{{end}},'
        '"type":"{{.type}}","keys":[{{range $key,$value := .data}}"{{$key}}",{{end}}""]}')
    actual = json.loads(call(['get', 'secret', core.SECRET, '-o', 'go-template='+template]))
    require(actual == {'uid': core.SECRET_UID, 'immutable': True, 'type': 'Opaque',
            'keys': ['MINERU_API_TOKEN', '']}, 'credential_identity_changed')


def fresh_resources(deps, report):
    rows = core.resources(deps)
    plan = report['initial_plan']
    counts = Counter(plan['workers']+[plan['controller']])
    nodes = {n['metadata']['name']: n for n in core.inv.get('nodes')['items']}
    require(core.inv.node_rules_match(nodes[plan['controller']], deps[prep.DISPATCHER]['spec']['template']['spec']),
            'dispatcher_node_constraints_changed')
    for name, count in counts.items():
        row = next(r for r in rows if r['node'] == name)
        require(row['additional_isolated_worker_slots_planning_only'] >= count, 'fresh_resource_slots_insufficient:'+name)
        require(all(f['inodes_headroom'] and f['available_bytes'] >= f['planning_reserve_bytes']+count*4*core.pf.GIB
                    for f in row['metrics']['filesystems'].values()), 'fresh_disk_reserve_insufficient:'+name)
    core.monitoring(plan)
    return rows


def create_once(root, obj, review, reference, report):
    name = obj['metadata']['name']
    validate_target(obj)
    require(get('deployments' if obj['kind'] == 'Deployment' else 'services', name) is None,
            'new_name_exists_do_not_adopt:'+name)
    filename = name+'.proposed.private.json'
    core.save(root, filename, obj)
    core.save(root, name+'.CREATE-REQUESTED.json', {'name': name, 'kind': obj['kind'],
        'utc': core.pf.utc().isoformat(), 'proposed_spec_sha256': review['proposed_spec_sha256']})
    # Intent is durable BEFORE the only mutating command. Never retry on timeout.
    report['create_requested'].append(name)
    report['cluster_writes_attempted'] = True
    emit(dict(mineru_stage='CREATE', kind=obj['kind'], name=name, public_route_changed=False))
    actual = json.loads(call(['create', '-f', str(root/filename), '-o', 'json']))
    core.save(root, name+'.CREATED.private.json', actual)
    admitted(obj, actual, review, reference)
    row = {'name': name, 'kind': obj['kind'], 'uid': actual['metadata']['uid'],
           'spec_sha256': core.inv.digest(actual['spec'])}
    report['created'].append(row)
    return actual


def deployment_current(original):
    name = original['metadata']['name']
    now = get('deployments', name)
    require(now and now['metadata']['uid'] == original['metadata']['uid']
            and now['spec'] == original['spec'] and not now['metadata'].get('deletionTimestamp'),
            'new_deployment_changed:'+name)
    return now


def validate_pod(dep, pod):
    spec, wanted = pod['spec'], dep['spec']['template']['spec']
    wanted_labels = dep['spec']['template']['metadata']['labels']
    require(all(pod['metadata'].get('labels', {}).get(k) == v for k,v in wanted_labels.items()), 'pod_labels_changed')
    require(not pod['metadata'].get('deletionTimestamp') and spec.get('nodeName') ==
            wanted['nodeSelector']['kubernetes.io/hostname'], 'pod_placement_or_deletion')
    require(len(spec['containers']) == 1 and not spec.get('initContainers') and not spec.get('ephemeralContainers')
            and not any(spec.get(k, False) for k in ('hostNetwork', 'hostPID', 'hostIPC')), 'pod_extra_container_or_host_access')
    c, w = spec['containers'][0], wanted['containers'][0]
    for key in ('name', 'image', 'imagePullPolicy', 'command', 'args', 'env', 'envFrom', 'resources', 'securityContext'):
        require(c.get(key) == w.get(key), 'pod_container_changed:'+key)
    require(spec.get('securityContext') == wanted.get('securityContext'), 'pod_security_context_changed')
    wanted_mounts = w.get('volumeMounts', [])
    actual_mounts = c.get('volumeMounts', [])
    require(all(m in actual_mounts for m in wanted_mounts), 'pod_mount_changed')
    extras = [m for m in actual_mounts if m not in wanted_mounts]
    require(len(extras) <= 1 and all(m.get('name', '').startswith('kube-api-access-')
            and m.get('mountPath') == '/var/run/secrets/kubernetes.io/serviceaccount'
            and m.get('readOnly') is True for m in extras), 'pod_unreviewed_mount')
    wanted_volumes = wanted.get('volumes', [])
    actual_volumes = spec.get('volumes', [])
    require(all(v in actual_volumes for v in wanted_volumes), 'pod_volume_source_changed')
    extra_volumes = [v for v in actual_volumes if v not in wanted_volumes]
    require(len(extra_volumes) <= 1 and all(v.get('name') in {e['name'] for e in extras}
            and 'projected' in v and set(v) == {'name', 'projected'} for v in extra_volumes), 'pod_unreviewed_volume')
    statuses = pod.get('status', {}).get('containerStatuses', [])
    require(len(statuses) == 1 and statuses[0]['name'] == c['name'], 'pod_status_container')
    s = statuses[0]
    image_id = s.get('imageID', '')
    candidate = core.inv.CANDIDATE
    require(image_id == candidate['config_digest'] or image_id.endswith('@'+candidate['manifest_digest']),
            'pod_image_id_changed')
    require(s.get('restartCount') == 0 and s.get('ready') is True and s.get('state', {}).get('running') is not None
            and pod.get('status', {}).get('phase') == 'Running', 'new_pod_not_ready_or_restarted')
    address = pod['status'].get('podIP')
    require(address and ipaddress.ip_address(address).version == 4, 'pod_ip_invalid')
    return dict(name=pod['metadata']['name'], uid=pod['metadata']['uid'],
                deployment=dep['metadata']['name'], deployment_uid=dep['metadata']['uid'],
                node=spec['nodeName'], ip=address, container=c['name'], container_id=s.get('containerID'),
                image_id=image_id)


def ready_pods(created, seconds=600):
    start = time.monotonic()
    while True:
        ready = []
        for original in created:
            dep = deployment_current(original)
            pods = core.inv.owned_pods(dep)
            require(len(pods) <= 1 and not any(p.get('status', {}).get('phase') in ('Failed', 'Succeeded') for p in pods),
                    'unexpected_new_pod_lifecycle')
            if not pods:
                continue
            p = pods[0]
            statuses = p.get('status', {}).get('containerStatuses', [])
            require(not any(s.get('restartCount', 0) or s.get('state', {}).get('terminated') for s in statuses),
                    'new_container_restarted_or_exited')
            waits = [s.get('state', {}).get('waiting', {}).get('reason') for s in statuses]
            require(not any(w in ('ErrImageNeverPull', 'ImagePullBackOff', 'CreateContainerConfigError', 'CrashLoopBackOff') for w in waits),
                    'new_container_start_failure')
            status = dep.get('status', {})
            if status.get('observedGeneration', 0) >= dep['metadata']['generation'] and status.get('availableReplicas') == 1 \
                    and len(statuses) == 1 and statuses[0].get('ready') is True:
                ready.append(validate_pod(dep, p))
        elapsed = int(time.monotonic()-start)
        emit(dict(mineru_stage='WAIT_NEW_POOL', desired=len(created), ready=len(ready), elapsed_seconds=elapsed))
        if len(ready) == len(created):
            return ready
        require(elapsed < seconds, 'new_pool_ready_deadline')
        time.sleep(10)


def endpoints(service, expected):
    uid = service['metadata']['uid']
    name = service['metadata']['name']
    data = get('endpointslices.discovery.k8s.io', selector='kubernetes.io/service-name='+name)['items']
    seen = []
    for item in data:
        require(core.inv.controlled(item, 'Service', uid), 'endpoint_slice_service_owner')
        require(item.get('addressType') == 'IPv4' and len(item.get('ports', [])) == 1
                and item['ports'][0].get('port') == 50052 and item['ports'][0].get('protocol') == 'TCP',
                'endpoint_slice_ports_or_family')
        for ep in item.get('endpoints', []):
            ref = ep.get('targetRef', {})
            require(ref.get('kind') == 'Pod' and ref.get('namespace') == 'deer-flow'
                    and ep.get('conditions', {}).get('ready') is True
                    and not ep.get('conditions', {}).get('terminating', False), 'endpoint_not_owned_ready')
            seen.append((ref.get('uid'), ref.get('name'), tuple(ep.get('addresses', []))))
    wanted = [(p['uid'], p['name'], (p['ip'],)) for p in expected]
    require(sorted(seen) == sorted(wanted), 'private_endpoints_not_converged:'+name)
    return dict(service=name, uid=uid, ready_endpoints=len(seen), owned_pods_only=True)


def wait_endpoints(service, expected):
    start = time.monotonic()
    while True:
        try:
            return endpoints(service, expected)
        except StageFailure as exc:
            if not (str(exc).startswith('private_endpoints_not_converged:') or str(exc) == 'endpoint_not_owned_ready'):
                raise
            require(time.monotonic()-start < 60, 'private_endpoints_deadline')
            emit(dict(mineru_stage='WAIT_PRIVATE_ENDPOINTS', service=service['metadata']['name']))
            time.sleep(5)


def probe_pod(row, code, workers, root):
    before = get('pods', row['name'])
    require(before and before['metadata']['uid'] == row['uid'], 'probe_pod_uid_changed')
    role = 'dispatcher' if row['container'] == 'dispatcher' else 'worker'
    args = ['exec', row['name'], '-c', row['container'], '--', '/app/.venv/bin/python', '-I', '-B', '-c', code,
            '--role', role, '--expected-workers', '6']
    if role == 'dispatcher':
        args += ['--worker-ips', ','.join(sorted(p['ip'] for p in workers))]
    emit(dict(mineru_stage='RUNTIME_PROBE', role=role, node=row['node'], pod=row['name'], documents_submitted=0))
    # Probe always prints a bounded safe receipt; a failure stays blocking.
    raw = call(args, timeout=60)
    require(len(raw) < 32768, 'runtime_receipt_too_large')
    result = json.loads(raw)
    core.save(root, row['name']+'.RUNTIME.json', result)
    after = get('pods', row['name'])
    require(after and after['metadata']['uid'] == row['uid'] and after.get('status', {}).get('containerStatuses') ==
            before.get('status', {}).get('containerStatuses'), 'probe_process_changed')
    require(result.get('stage_runtime') == 'PASS' and result.get('role') == role
            and result.get('source_sha256') == core.inv.CANDIDATE['source_sha256']
            and result.get('provider_http_requests') == 0 and result.get('documents_submitted') == 0,
            'candidate_runtime_probe_failed')
    return dict(pod=row['name'], uid=row['uid'], node=row['node'], receipt=result)


def status_only():
    # Never adopt unknown objects, execute in Pods, rewrite evidence, or restart.
    rows = []
    for name, kind in sorted(ALLOWED.items()):
        now = get('deployments' if kind == 'Deployment' else 'services', name)
        record = ROOT/(name+'.CREATED.private.json')
        prior = json.loads(core.pf.read_private(record)) if record.exists() else None
        rows.append(dict(name=name, kind=kind, exists=now is not None,
            uid_matches_acknowledged=bool(now and prior and now['metadata']['uid'] == prior['metadata']['uid']),
            spec_matches_acknowledged=bool(now and prior and now['spec'] == prior['spec']),
            create_requested=(ROOT/(name+'.CREATE-REQUESTED.json')).exists(),
            available_replicas=(now or {}).get('status', {}).get('availableReplicas')))
    original = get('services', prep.WORKER)
    report = json.loads(locked(PREPARED/'RESULT.json', REPORT_SHA))
    route = report['route_plan']
    unchanged = bool(original and original['metadata']['uid'] == route['uid']
                     and core.inv.digest(original['spec']) == route['original_spec_sha256'])
    emit(dict(mineru_stage_status=rows, original_route_unchanged=unchanged, status_only=True,
              files_modified=False, cluster_writes=0, pod_exec=False, provider_requests=0))


def stage(code, report):
    global PHASE
    PHASE = 'BIND_PREPARE_AND_LIVE_BASELINE'
    reviewed = json.loads(locked(PREPARED/'RESULT.json', REPORT_SHA))
    report_contract(reviewed)
    report['load_review'] = prep.load_acceptance()
    bindings, controls = core.pf.receipt_bindings()
    require(bindings == reviewed['bindings'], 'receipt_bindings_changed')
    deps, _ = core.baselines(controls)
    report['credential'] = prep.secret_metadata()
    require(report['credential'] == reviewed['credential'] and sha(core.ca_bytes()) == reviewed['ca_sha256'],
            'credential_or_ca_changed')
    objects, _ = prep.manifests(deps, reviewed['initial_plan'], reviewed['concurrency_plan'], controls, get('services')['items'])
    reviews = {r['name']: r for r in reviewed['dry_runs']}
    refs = {}
    require(not get('pods', selector=prep.LABEL+'='+prep.RELEASE)['items'], 'release_label_already_has_pods')
    for obj in objects:
        name = obj['metadata']['name']
        validate_target(obj)
        require(core.inv.digest(obj['spec']) == reviews[name]['proposed_spec_sha256'], 'proposal_changed:'+name)
        reference = json.loads(core.pf.read_private(PREPARED/(name+'.admitted.private.json')))
        require(core.inv.digest(reference['spec']) == reviews[name]['admitted_spec_sha256'], 'dry_run_evidence_changed')
        refs[name] = reference
        require(get('deployments' if obj['kind'] == 'Deployment' else 'services', name) is None, 'candidate_name_exists:'+name)
    old_unchanged(reviewed, controls, objects)
    PHASE = 'FRESH_RESOURCES'
    report['fresh_resources'] = fresh_resources(deps, reviewed)
    core.save(ROOT, 'REVIEWED-PLAN.private.json', objects)
    PHASE = 'RECHECK_SERVER_ADMISSION_BEFORE_FIRST_WRITE'
    for obj in objects:
        name = obj['metadata']['name']
        filename = name+'.dry-run.private.json'
        core.save(ROOT, filename, obj)
        answer = json.loads(call(['create', '--dry-run=server', '-f', str(ROOT/filename), '-o', 'json']))
        prep.validate_admission(obj, answer)
        if obj['kind'] == 'Deployment':
            require(core.inv.digest(answer['spec']) == reviews[name]['admitted_spec_sha256'], 'fresh_admission_changed:'+name)
        else:
            require(service_shape(answer['spec']) == service_shape(refs[name]['spec']), 'fresh_service_admission_changed:'+name)
    report['concurrency_plan'] = reviewed['concurrency_plan']
    created = {}
    # Internal Services first, workers next, dispatcher only after workers ready.
    ordered = sorted(objects, key=lambda o: (0 if o['kind'] == 'Service' else 2 if o['metadata']['name'] == DISPATCHER else 1,
                                             o['metadata']['name']))
    workers = []
    for obj in ordered:
        PHASE = 'CREATE_NEW_OBJECTS'
        name = obj['metadata']['name']
        old_unchanged(reviewed, controls, objects)
        if name == DISPATCHER:
            PHASE = 'WAIT_WORKERS'
            workers = ready_pods([created[n] for n in WORKERS])
            wait_endpoints(created[SERVICES[0]], workers)
            PHASE = 'CREATE_NEW_DISPATCHER'
        created[name] = create_once(ROOT, obj, reviews[name], refs[name], report)
    PHASE = 'WAIT_COMPLETE_POOL'
    all_pods = ready_pods([created[n] for n in WORKERS+[DISPATCHER]])
    workers = [p for p in all_pods if p['container'] == 'adapter']
    dispatchers = [p for p in all_pods if p['container'] == 'dispatcher']
    report['pods'] = all_pods
    report['endpoints'] = [wait_endpoints(created[SERVICES[0]], workers), wait_endpoints(created[SERVICES[1]], dispatchers)]
    PHASE = 'PROCESS_CA_TLS_AND_PRIVATE_SERVICE'
    report['runtime'] = []
    for p in workers+dispatchers:
        report['runtime'].append(probe_pod(p, code, workers, ROOT))
    PHASE = 'FINAL_RECHECK'
    credential_identity()
    old_unchanged(reviewed, controls, objects)
    core.baselines(controls)
    for name, actual in created.items():
        now = get('deployments' if actual['kind'] == 'Deployment' else 'services', name)
        require(now and now['metadata']['uid'] == actual['metadata']['uid'] and now['spec'] == actual['spec'],
                'new_object_changed_during_stage:'+name)
    require(ready_pods([created[n] for n in WORKERS+[DISPATCHER]]) == all_pods, 'new_pool_replaced_during_stage')
    require({p['metadata']['uid'] for p in get('pods', selector=prep.LABEL+'='+prep.RELEASE)['items']}
            == {p['uid'] for p in all_pods}, 'unowned_release_label_pods')
    endpoints(created[SERVICES[0]], workers)
    endpoints(created[SERVICES[1]], dispatchers)
    core.monitoring(reviewed['initial_plan'])
    report.update(mineru_bluegreen16_stage='PASS', ready_for_switch_review=True,
        existing_production_baseline='UNCHANGED', new_worker_slots=6, new_dispatchers=1,
        maximum_provider_tls_handshakes=6, provider_http_requests=0, documents_submitted=0,
        remaining_gates=['CAS_PUBLIC_SELECTOR_SWITCH_KEEP_OLD_POOL_ALIVE',
            'VERIFY_ROUTE_CONVERGENCE_AND_OLD_HTTP_INFLIGHT_FINISH',
            'REVIEW_OLD_POOL_RETIREMENT_BEFORE_FINAL_16_EXPANSION',
            'POST_SWITCH_WEKNORA_AND_CONVERSATION_END_TO_END'])


def diagnose(exc):
    row = core.safe_diagnostic(exc) if core else {'error_type': type(exc).__name__}
    if isinstance(exc, (StageFailure, prep.PrepareFailure if prep else StageFailure)):
        row['failed_check'] = str(exc) if re.fullmatch('[A-Za-z0-9_:./-]{1,240}', str(exc)) else 'SAFE_DIAGNOSTIC_REQUIRED'
    row.update(failed_stage=PHASE, last_stage_operation=LAST_OPERATION, raw_error_printed=False)
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--stage-only', action='store_true')
    mode.add_argument('--status', action='store_true')
    args = parser.parse_args()
    require(os.geteuid() == 0 and socket.gethostname().split('.')[0] == 'datax-01', 'root_datax01_required')
    os.umask(0o077)
    code = bootstrap()
    if args.status:
        status_only()
        return 0
    safe_dir(ROOT.parent)
    require(not ROOT.exists() and not ROOT.is_symlink(), 'operation_exists_use_status_do_not_repeat')
    ROOT.mkdir(mode=0o700)
    report = dict(mineru_bluegreen16_stage='UNCONFIRMED', prepare_report_sha256=REPORT_SHA,
        stage_evidence=str(ROOT), ready_for_rollout=False, ready_for_switch_review=False,
        public_route_changed=False, existing_workloads_modified=False, old_pool_restarted=False,
        old_queue_mutated=False, credentials_printed=False, credentials_requested=False,
        automatic_retry=False, automatic_cleanup=False, create_requested=[], created=[],
        cluster_writes_attempted=False, documents_submitted=0, provider_http_requests=0,
        synthetic_load_repeated=False, old_failure_evidence_modified=False)
    emit(dict(bluegreen_stage_evidence=str(ROOT), new_pool_only=True, public_route_changed=False))
    try:
        stage(code, report)
    except BaseException as exc:
        report.update(diagnose(exc))
    report['utc'] = core.pf.utc().isoformat()
    report['production_configuration_modified'] = (True if report['mineru_bluegreen16_stage'] == 'PASS'
        else 'POSSIBLY_NEW_OBJECTS_ONLY' if report['cluster_writes_attempted'] else False)
    core.save(ROOT, 'RESULT.json', report)
    raw = core.pf.read_private(ROOT/'RESULT.json')
    emit({k: report.get(k) for k in ('mineru_bluegreen16_stage', 'failed_stage', 'failed_check', 'new_worker_slots',
        'new_dispatchers', 'ready_for_switch_review', 'public_route_changed', 'existing_workloads_modified')})
    emit(dict(report=str(ROOT/'RESULT.json'), report_sha256=sha(raw), automatic_retry=False,
        ready_for_rollout=False, instruction='SEND_REPORT; USE_STATUS_DO_NOT_REPEAT_STAGE',
        status_command='sudo python3 -I -B '+str(Path(__file__).absolute())+' --status'))
    return 0 if report['mineru_bluegreen16_stage'] == 'PASS' else 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        emit(dict(mineru_bluegreen16_stage='UNCONFIRMED', automatic_retry=False, **diagnose(exc)))
        sys.exit(1)
