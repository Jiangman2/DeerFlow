#!/usr/bin/env python3
"""Three-node READ-ONLY review. No scale, restart, delete, signal or provider call.

datax-01: current control-plane baseline plus six-node capacity proposal.
datax-02/03: local CRI/tasks/cgroup scan for retired process identities.
Writes only a fresh private receipt. A PASS never authorizes expansion itself.
"""
import datetime
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import socket
import stat
import subprocess
import sys
import tempfile
import time
import traceback
import types

RECONCILE_CODE = '__EMBED_PINNED_RECONCILE__'
RECONCILE_SHA = '16ab016cf65bdad39682832642ae91c162a7de80d987ea3606a0905edcacf68b'
ACCEPTED = Path('/var/lib/mineru-retirement-reconcile-uc_1ia_f/RESULT.json')
ACCEPTED_SHA = '934436ea26d48ec06f7973dd19e4f2a8bc5c66758b3e6bd3c17097828c9656dc'
RETIRED_IDS = {
    'datax-02': {
        'deer-flow-mineru-adapter-7f59bc8879-5gjqj': '81bd476a60b44bf921d22f48909a7ac9b584a048ecdce78b161b8e14bdbd81d4',
        'deer-flow-mineru-adapter-7f59bc8879-7l8jb': 'c27646daedc11b0d054f610b1fe37c889ef882cc36b1ab642148cfe4f428e1d1',
        'deer-flow-mineru-adapter-7f59bc8879-wfvmj': 'cfdd122f52b3d49163abe2bf26e8a4d71accf1d1166c2803c216b0dbe5367036',
        'deer-flow-mineru-dispatcher-5bddf84f95-bfjqb': 'a9da7ba02a9f15b76d749f09b7cd35a47525878e0677317a8756b13361328352',
    },
    'datax-03': {
        'deer-flow-mineru-adapter-7f59bc8879-hcxlr': '6718252dcafa40131af003e1f5680bd1fe203ca49ffe430670fcd104fc77d4ab',
        'deer-flow-mineru-adapter-7f59bc8879-jgjtm': '2912462e8fb035603a4e328f5abb39b398bf82f4151354ba0873e0856f78a439',
        'deer-flow-mineru-adapter-7f59bc8879-kwd9b': 'cabedf1a7129fa89fe3f6791bcce7bb29657f509b1153458c25218a8cb410ea6',
    },
}
NODES = tuple('ecs-zdz-pro-%04d' % n for n in range(11, 17))
GIB = 1024 ** 3
CRI = ('crictl', '--runtime-endpoint', 'unix:///run/containerd/containerd.sock',
       '--image-endpoint', 'unix:///run/containerd/containerd.sock', '--timeout', '20s')
RUNTIME_COMMANDS = (CRI + ('pods', '-o', 'json'), CRI + ('ps', '--all', '-o', 'json'),
    ('ctr', '--address', '/run/containerd/containerd.sock', '--namespace', 'k8s.io', 'tasks', 'list'))
PHASE = 'BOOTSTRAP'
LAST = None


class ReviewFailure(RuntimeError):
    pass


def require(ok, label):
    if not ok:
        raise ReviewFailure(label)


def emit(row):
    print(json.dumps(row, sort_keys=True), flush=True)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def safe_dir(path):
    for item in reversed([path] + list(path.parents)):
        s = item.lstat()
        require(stat.S_ISDIR(s.st_mode) and s.st_uid == 0 and not s.st_mode & 0o022,
                'unsafe_private_directory')


def save(root, report):
    safe_dir(root)
    raw = (json.dumps(report, indent=2, sort_keys=True) + '\n').encode()
    fd = os.open(str(root/'RESULT.json'), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    fd = os.open(str(root), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    return sha(raw)


def capture_local(args):
    """Bound stdout/stderr and time; allow exactly three inventory commands."""
    global LAST
    require(tuple(args) in RUNTIME_COMMANDS, 'local_command_not_readonly_inventory')
    LAST = dict(program=args[0], operation='LOCAL_RUNTIME_INVENTORY')
    child = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, shell=False)
    sel, out, err = selectors.DefaultSelector(), bytearray(), bytearray()
    try:
        sel.register(child.stdout, selectors.EVENT_READ, out)
        sel.register(child.stderr, selectors.EVENT_READ, err)
        deadline = time.monotonic() + 35
        while sel.get_map():
            require(time.monotonic() < deadline, 'local_read_deadline')
            for key, _ in sel.select(min(1, max(0, deadline-time.monotonic()))):
                block = os.read(key.fileobj.fileno(), 65536)
                if not block:
                    sel.unregister(key.fileobj)
                else:
                    key.data.extend(block)
                    require(len(out) <= 16*1024*1024 and len(err) <= 65536, 'local_read_output_limit')
        code = child.wait(timeout=max(0.01, deadline-time.monotonic()))
        LAST.update(exit_code=code, stdout_bytes=len(out), stderr_bytes=len(err))
        require(code == 0, 'local_runtime_command_failed')
        return bytes(out)
    finally:
        sel.close()
        if child.poll() is None:
            # Only our own bounded read-only CLI child; never a service process.
            child.kill()
        child.wait()
        child.stdout.close()
        child.stderr.close()


def old_name(name):
    return bool(re.fullmatch(r'deer-flow-mineru-(?:adapter|dispatcher)-[a-z0-9]+-[a-z0-9]+', name or ''))


def selected_records(node, pods, containers):
    """Combine exact retired IDs with any discoverable old-Deployment CRI rows."""
    ids = set(RETIRED_IDS[node].values())
    uids, sandboxes, states = set(), set(), []
    for pod in pods:
        m, labels = pod.get('metadata', {}), pod.get('labels', {})
        if m.get('namespace', labels.get('io.kubernetes.pod.namespace')) == 'deer-flow' and old_name(m.get('name')):
            require(re.fullmatch('[0-9a-f]{64}', pod.get('id', '')), 'sandbox_id_invalid')
            require(re.fullmatch('[0-9a-f-]{36}', m.get('uid', '')), 'sandbox_uid_invalid')
            sandboxes.add(pod['id'])
            uids.add(m['uid'])
    for row in containers:
        labels = row.get('labels', {})
        matches = (row.get('id') in ids or row.get('podSandboxId') in sandboxes
            or (labels.get('io.kubernetes.pod.namespace') == 'deer-flow'
                and old_name(labels.get('io.kubernetes.pod.name'))))
        if matches:
            require(re.fullmatch('[0-9a-f]{64}', row.get('id', '')), 'container_id_invalid')
            ids.add(row['id'])
            uid = labels.get('io.kubernetes.pod.uid')
            if uid:
                require(re.fullmatch('[0-9a-f-]{36}', uid), 'container_pod_uid_invalid')
                uids.add(uid)
            states.append(row.get('state', 'UNKNOWN'))
    return ids | sandboxes, uids, states, len(sandboxes)


def task_matches(raw, identifiers):
    lines = raw.decode('utf-8', 'strict').splitlines()
    if not lines:
        return []
    require(lines[0].split() == ['TASK', 'PID', 'STATUS'], 'containerd_task_header_changed')
    hits = []
    for line in lines[1:]:
        cols = line.split()
        if not cols:
            continue
        require(len(cols) == 3 and cols[1].isdigit(), 'containerd_task_row_changed')
        if cols[0] in identifiers:
            hits.append(dict(id=cols[0], pid=int(cols[1]), state=cols[2]))
    return hits


def cgroup_scan(identifiers, uids, proc=Path('/proc')):
    tokens = identifiers | uids | {u.replace('-', '_') for u in uids}
    require(tokens and all(len(t) >= 36 for t in tokens), 'cgroup_identity_scope_empty')
    scanned, unreadable, hits = 0, 0, []
    deadline = time.monotonic() + 25
    for item in proc.iterdir():
        if not item.name.isdigit():
            continue
        require(time.monotonic() < deadline and scanned < 32768, 'cgroup_scan_budget')
        scanned += 1
        try:
            with (item/'cgroup').open('rb') as stream:
                raw = stream.read(16385)
            require(len(raw) <= 16384, 'cgroup_file_limit')
            text = raw.decode('ascii', 'strict')
            if any(t in text for t in tokens):
                hits.append(int(item.name))
        except (FileNotFoundError, ProcessLookupError):
            continue
        except (PermissionError, UnicodeError):
            unreadable += 1
    return dict(scanned_processes=scanned, unreadable_cgroup_files=unreadable,
                matching_cgroup_processes=len(hits))


def local_sample(node):
    pods = json.loads(capture_local(list(RUNTIME_COMMANDS[0]))).get('items')
    containers = json.loads(capture_local(list(RUNTIME_COMMANDS[1]))).get('containers')
    require(isinstance(pods, list) and isinstance(containers, list), 'cri_inventory_shape_changed')
    ids, uids, states, sandboxes = selected_records(node, pods, containers)
    tasks = task_matches(capture_local(list(RUNTIME_COMMANDS[2])), ids)
    scan = cgroup_scan(ids, uids)
    return dict(utc=utc(), cri_container_states=states, cri_sandbox_records=sandboxes,
        matching_containerd_tasks=len(tasks), **scan,
        no_activity_observed=(all(s == 'CONTAINER_EXITED' for s in states)
            and not tasks and scan['matching_cgroup_processes'] == 0 and scan['unreadable_cgroup_files'] == 0))


def local_review(node, report):
    global PHASE
    PHASE = 'LOCAL_OLD_PROCESS_ABSENCE'
    sock = Path('/run/containerd/containerd.sock')
    safe_dir(sock.parent)
    st = sock.lstat()
    require(stat.S_ISSOCK(st.st_mode) and st.st_uid == 0 and not st.st_mode & 0o002,
            'containerd_socket_identity')
    report['known_retired_container_ids'] = RETIRED_IDS[node]
    report['samples'] = []
    for number in range(2):
        emit(dict(old_node_runtime='RUNNING', node=node, sample=number+1))
        row = local_sample(node)
        report['samples'].append(row)
        emit(dict(old_node_runtime_sample=row, node=node))
        require(row['no_activity_observed'], 'old_activity_or_incomplete_runtime_read')
        if number == 0:
            time.sleep(5)
    report.update(old_runtime_absence='PASS', identity_scope='EXACT_RETIRED_CONTAINERS_AND_DISCOVERABLE_OLD_CRI_RECORDS',
                  snapshot_only=True, expansion_review='PASS')


def capacity_capture(original, reconcile):
    extra = set()
    for name in NODES:
        extra.add(('kubectl', '--request-timeout=20s', 'get', 'pods', '--all-namespaces',
            '--field-selector', 'spec.nodeName='+name, '--show-managed-fields=false', '-o', 'json'))
        extra.add(('kubectl', '--request-timeout=20s', 'get', '--raw',
                   '/api/v1/nodes/'+name+'/proxy/stats/summary'))
    guarded = reconcile.readonly_capture(original)
    def call(args):
        if tuple(args) in extra:
            return original(args)
        return guarded(args)
    return call


def slot_budget(pf, node, pods, worker_uids, spec, measured):
    unit = dict(cpu='2', memory='4Gi', **{'ephemeral-storage': '4Gi'})
    require(len(spec.get('containers', [])) == 1
            and spec['containers'][0].get('resources') == dict(requests=unit, limits=unit),
            'worker_resource_unit_changed')
    base = pf.planning_row(node, pods, worker_uids, spec, measured)
    active = [p for p in pods if p.get('status', {}).get('phase') not in ('Succeeded', 'Failed')]
    total = pf.cap.quantity(node['status']['allocatable']['ephemeral-storage'])
    requested = sum((pf.cap.pod_requests(p, 'ephemeral-storage') for p in active), Decimal(0))
    ephemeral = max(0, int((total*Decimal('0.8')-requested) // Decimal(4*GIB)))
    disks = [max(0, (f['available_bytes']-f['planning_reserve_bytes']) // (4*GIB))
             if f['inodes_headroom'] else 0 for f in measured['filesystems'].values()]
    require(disks, 'filesystem_metrics_absent')
    extra = min([base['additional_isolated_worker_slots_planning_only'], ephemeral] + disks)
    return dict(node=node['metadata']['name'], uid=node['metadata']['uid'],
        current_workers=1, additional_workers_planning_only=extra,
        node_eligible=base['temporary_fixture_node_eligible'], cpu_memory_budget=base['planning_budget'],
        ephemeral_request_slots=ephemeral, disk_slots=disks, measurements=measured,
        per_worker=dict(cpu_cores=2, memory_GiB=4, ephemeral_storage_GiB=4),
        scheduling_guaranteed=False)


def proposed_counts(rows):
    require(len(rows) == 6 and {r['node'] for r in rows} == set(NODES), 'capacity_nodes_changed')
    counts = {r['node']: 1 for r in rows}
    limits = {r['node']: 1+r['additional_workers_planning_only'] for r in rows}
    for _ in range(10):
        choices = [n for n in counts if counts[n] < limits[n]]
        if not choices:
            return None
        counts[min(choices, key=lambda n: (counts[n], n))] += 1
    return counts


def bind_runtime_ids(inventory):
    require(set(r['node'] for r in inventory['targets']) <= set(RETIRED_IDS), 'old_runtime_nodes_changed')
    for node, wanted in RETIRED_IDS.items():
        actual = {r['name']: r['container_id'].split('://')[-1] for r in inventory['targets'] if r['node'] == node}
        require(actual == wanted, 'retired_process_identity_pins_changed')


def load_reconcile():
    require(sha(RECONCILE_CODE.encode()) == RECONCILE_SHA, 'embedded_reconcile_hash_changed')
    c = types.ModuleType('reviewed_reconcile')
    c.__file__ = str(Path(__file__).absolute())
    exec(compile(RECONCILE_CODE, '<pinned-reconcile>', 'exec'), c.__dict__)
    r = types.ModuleType('reviewed_retirement')
    r.__file__ = c.__file__
    exec(compile(c.RETIRE_CODE, '<pinned-retire>', 'exec'), r.__dict__)
    c.r = r
    return c, r


def controller_review(root, report):
    global PHASE
    PHASE = 'BIND_SUCCESSFUL_RETIREMENT_AND_CURRENT_BASELINE'
    c, r = load_reconcile()
    context, accepted, _, _ = r.bootstrap()
    r.patch_call = r.retire_once = r.run = r.restore_review = c.deny_mutation
    r.s.core.inv.bounded_capture = capacity_capture(r.s.core.inv.bounded_capture, c)
    prior = json.loads(r.a.locked(ACCEPTED, ACCEPTED_SHA))
    require(prior.get('mineru_retirement_reconcile') == 'PASS'
            and prior.get('old_pool_control_plane_retired') is True
            and prior.get('candidate_runtime_acceptance') == 'PASS'
            and prior.get('public_route') == 'candidate' and prior.get('new_pool_slots') == 6,
            'retirement_receipt_not_accepted')
    state = c.bind_evidence()
    require(state['inventory'] == accepted['inventory'], 'retired_inventory_changed')
    bind_runtime_ids(state['inventory'])
    originals = r.original_snapshots(context)
    c.accept_acks(originals, state)
    counts = {n: 0 for n in r.TARGETS}
    deps, candidates = r.invariants(context, originals, counts, state['inventory'])
    for name in r.TARGETS:
        c.check_controller(deps[name])
        require(not r.owned_state(deps[name], state['inventory'], True), 'old_pods_reappeared')
    report['retirement_receipt_sha256'] = ACCEPTED_SHA
    report['old_node_leases'] = r.node_leases(state['inventory'])
    PHASE = 'FRESH_SIX_NODE_CAPACITY'
    inv, pf = r.s.core.inv, r.s.core.pf
    nodes = {n['metadata']['name']: n for n in inv.get('nodes')['items']}
    node_hashes = {}
    workers = [p for p in candidates if p['container'] == 'adapter']
    require(len(workers) == 6 and {p['node'] for p in workers} == set(NODES), 'new_worker_placement_changed')
    specs = {}
    for name in r.s.WORKERS:
        obj = context['created'][name]
        spec = obj['spec']['template']['spec']
        node = spec['nodeSelector']['kubernetes.io/hostname']
        require(node in NODES and node not in specs and obj['spec']['replicas'] == 1,
                'new_worker_deployment_shape_changed')
        specs[node] = spec
    report['capacity'] = []
    for name in NODES:
        emit(dict(expansion_capacity='RUNNING', node=name))
        node = nodes[name]
        require(node['metadata']['uid'] == r.s.core.guard.NODES[name]['uid'], 'node_uid_changed')
        node_hashes[name] = inv.digest(inv.node_projection(node))
        pods = pf.node_pods(name)
        measured = pf.metrics(inv.get('node_summary', name), name, pf.utc())
        row = slot_budget(pf, node, pods, {p['uid'] for p in workers}, specs[name], measured)
        report['capacity'].append(row)
        emit(dict(node=name, extra_workers_planning_only=row['additional_workers_planning_only'],
                  node_eligible=row['node_eligible']))
    report['proposed_final_worker_counts'] = proposed_counts(report['capacity'])
    require(report['proposed_final_worker_counts'] is not None, 'insufficient_capacity_for_ten_additional_workers')
    PHASE = 'BASELINE_RECHECK'
    deps, _ = r.invariants(context, originals, counts, state['inventory'])
    for name in r.TARGETS:
        c.check_controller(deps[name])
        require(not r.owned_state(deps[name], state['inventory'], True), 'old_pods_reappeared')
    final_nodes = {n['metadata']['name']: n for n in inv.get('nodes')['items']}
    require(all(inv.digest(inv.node_projection(final_nodes[n])) == node_hashes[n] for n in NODES),
            'node_rules_or_health_changed_during_audit')
    report.update(expansion_review='PASS', public_route='candidate', current_slots=6,
        target_slots=16, old_control_plane_retired=True,
        node_local_results_datax02_03='REQUIRED_SEPARATE_RECEIPTS',
        dispatcher_restarted=False, scheduling_and_admission='NOT_TESTED',
        capacity_policy='20_PERCENT_CPU_MEMORY_EPHEMERAL_RESERVE;4_POD_RESERVE;4GIB_DISK_PER_WORKER',
        remaining_gates=['BIND_TWO_NODE_LOCAL_RUNTIME_RECEIPTS',
            'FRESH_PREAPPLY_CAPACITY_AND_SERVER_DRY_RUN',
            'REVIEW_REPLICA_ONLY_EXPANSION_WITHOUT_DISPATCHER_RESTART'])


def main():
    global PHASE
    node = socket.gethostname().split('.')[0]
    require(sys.platform.startswith('linux') and os.geteuid() == 0
            and node in ('datax-01', 'datax-02', 'datax-03'), 'root_linux_expected_node_required')
    os.umask(0o077)
    os.environ['KUBECONFIG'] = '/etc/kubernetes/admin.conf'
    safe_dir(Path('/var/lib'))
    root = Path(tempfile.mkdtemp(prefix='mineru-expansion-review-', dir='/var/lib'))
    report = dict(node=node, expansion_review='INCOMPLETE', utc_started=utc(),
        production_writes=0, production_workloads_mutated=False, provider_requests=0,
        queues_modified=False, images_imported=False, new_private_evidence_only=True,
        ready_to_expand_16=False, concurrency_changed=False, automatic_retry=False,
        script_sha256=sha(Path(__file__).read_bytes()))
    emit(dict(node=node, expansion_review_evidence=str(root), production_writes=0, provider_requests=0))
    try:
        if node == 'datax-01':
            controller_review(root, report)
        else:
            local_review(node, report)
    except Exception as exc:
        allowed = type(exc).__name__ in ('ReviewFailure', 'ReconcileFailure', 'RetireFailure',
            'AuditFailure', 'StageFailure', 'SwitchFailure', 'CheckFailure', 'PreflightFailure', 'LoadFailure')
        label = str(exc)
        report.update(error_type=type(exc).__name__, failed_stage=PHASE,
            failed_check=label if allowed and re.fullmatch('[A-Za-z0-9_:./-]{1,240}', label)
                else 'SAFE_DIAGNOSTIC_REQUIRED',
            code_frames=[dict(file=Path(f.filename).name, function=f.name, line=f.lineno)
                         for f in traceback.extract_tb(exc.__traceback__)[-10:]],
            last_operation=LAST, raw_error_printed=False)
    report['utc_finished'] = utc()
    digest = save(root, report)
    emit({k: report.get(k) for k in ('node', 'expansion_review', 'old_runtime_absence',
        'current_slots', 'target_slots', 'proposed_final_worker_counts', 'failed_stage', 'failed_check',
        'ready_to_expand_16')})
    emit(dict(report=str(root/'RESULT.json'), report_sha256=digest,
              production_writes=0, provider_requests=0))
    return 0 if report['expansion_review'] == 'PASS' else 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        emit(dict(expansion_review='INCOMPLETE', failed_stage=PHASE, error_type=type(exc).__name__,
            failed_check=str(exc) if isinstance(exc, ReviewFailure) else 'SAFE_DIAGNOSTIC_REQUIRED',
            raw_error_printed=False, production_writes=0, provider_requests=0, ready_to_expand_16=False))
        sys.exit(1)
