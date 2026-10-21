#!/usr/bin/env python3
"""Python3.7+ post-switch observation. NO scale, signal, route or policy writes."""
import datetime
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
import types

TOOL = Path('/var/lib/mineru-bluegreen16-switch-tool-fshgGTe2')
SWITCH_SHA = '3b7eafbdfb507a1a68e2329912d1dfaab211b5daac4aa312294df41c4fe37847'
RESULT_SHA = '7e41a210db4a85e489ffb9d902acaaca99c543ffd59c416fdf56bcb8edae3294'
# Narrowly bound to the three Failed records supplied by the operator. They are
# NOT counted as inactive merely because Kubernetes reports Failed.
HISTORICAL = {
    'deer-flow-mineru-adapter-7f59bc8879-2vvwx': ('Error', '804cb13867d9ee30f74aa0cbc6226bff6eaf75f1883fe0730032fa00a2d1253a'),
    'deer-flow-mineru-adapter-7f59bc8879-mdr5k': ('ContainerStatusUnknown', None),
    'deer-flow-mineru-adapter-7f59bc8879-zmqw5': ('Error', 'a3ebe85baea9281a3c8a66393b54468a491ee2a5151d77bf335b965f96e9a9de'),
}
SOCKET = '/run/containerd/containerd.sock'
CRI = ['crictl', '--runtime-endpoint', 'unix://'+SOCKET, '--image-endpoint', 'unix://'+SOCKET, '--timeout', '20s']
CTR = ['ctr', '--address', SOCKET, '--namespace', 'k8s.io']
PROBE = '__EMBED_OLD_POOL_PROBE__'
PHASE = 'BOOTSTRAP'
sw = None


class AuditFailure(RuntimeError):
    pass


def require(ok, label):
    if not ok:
        raise AuditFailure(label)


def emit(row):
    print(json.dumps(row, sort_keys=True), flush=True)


def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def safe_dir(path):
    for p in reversed([path]+list(path.parents)):
        s = p.lstat()
        require(stat.S_ISDIR(s.st_mode) and s.st_uid == 0 and not s.st_mode & 0o022,
                'unsafe_directory')


def locked(path, digest):
    safe_dir(path.parent)
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as f:
        s = os.fstat(f.fileno())
        require(stat.S_ISREG(s.st_mode) and s.st_uid == 0 and s.st_nlink == 1
                and not s.st_mode & 0o022 and 0 < s.st_size < 2*1024**2, 'unsafe_file')
        raw = f.read(2*1024**2)
        after = os.fstat(f.fileno())
    ident = lambda v: (v.st_ino, v.st_dev, v.st_size, v.st_mtime_ns, v.st_ctime_ns,
                       v.st_uid, v.st_mode, v.st_nlink)
    require(ident(s) == ident(after) == ident(path.lstat()) and len(raw) == s.st_size
            and sha(raw) == digest, 'locked_file_changed')
    return raw


def bootstrap():
    global sw
    raw = locked(TOOL/'switch_bluegreen16.py', SWITCH_SHA)
    sw = types.ModuleType('locked_mineru_switch')
    sw.__file__ = str(TOOL/'switch_bluegreen16.py')
    exec(compile(raw, sw.__file__, 'exec'), sw.__dict__)
    _, route_code = sw.bootstrap()
    result = json.loads(locked(sw.ROOT/'RESULT.json', RESULT_SHA))
    require(result.get('mineru_bluegreen16_switch') == 'PASS'
            and result.get('public_route') == 'candidate'
            and result.get('live_patch_acknowledged') is True, 'switch_not_accepted')
    require(not (sw.BACK/'PATCH-REQUESTED.json').exists(), 'rollback_intent_present')
    require(PROBE != '__EMBED_'+'OLD_POOL_PROBE__', 'probe_not_packaged')
    # The audit can only reach get/exec operations through this helper.
    original_call = sw.s.call

    def readonly_call(args, **kwargs):
        require(args and args[0] in ('get', 'exec'), 'audit_write_attempt_blocked')
        return original_call(args, **kwargs)

    sw.s.call = readonly_call
    return route_code


def pod_identity(pod, container):
    require(pod and not pod['metadata'].get('deletionTimestamp')
            and pod.get('status', {}).get('phase') == 'Running', 'pod_not_running')
    rows = [c for c in pod.get('status', {}).get('containerStatuses', []) if c['name'] == container]
    require(len(rows) == 1 and rows[0].get('state', {}).get('running')
            and rows[0].get('containerID'), 'container_not_running')
    return dict(name=pod['metadata']['name'], uid=pod['metadata']['uid'], node=pod['spec']['nodeName'],
        container=container, container_id=rows[0]['containerID'], image_id=rows[0].get('imageID'),
        restarts=rows[0].get('restartCount'), started=rows[0]['state']['running'].get('startedAt'))


def old_inventory(deps):
    s = sw.s
    targets, pending, terminal, services, labels = [], [], [], [], {}
    for name, container in ((s.prep.WORKER, 'adapter'), (s.prep.DISPATCHER, 'dispatcher')):
        dep = deps[name]
        labels[name] = dep['spec']['template']['metadata']['labels']
        for pod in s.core.inv.owned_pods(dep):
            cs = [c for c in pod.get('status', {}).get('containerStatuses', []) if c['name'] == container]
            if cs and cs[0].get('state', {}).get('running'):
                row = pod_identity(pod, container)
                require(row['image_id'].split('://')[-1] in s.core.inv.BASE_IDS, 'old_image_changed')
                require(not pod['spec'].get('hostNetwork', False), 'old_host_network_unsupported')
                targets.append(row)
            else:
                reason = cs[0].get('state', {}).get('waiting', {}).get('reason') if len(cs) == 1 else None
                emit(dict(old_pool_unrunning_pod=pod['metadata']['name'], node=pod['spec'].get('nodeName'),
                    phase=pod.get('status', {}).get('phase'), waiting_reason=reason,
                    terminated_reason=cs[0].get('state', {}).get('terminated', {}).get('reason') if len(cs) == 1 else None,
                    restarts=cs[0].get('restartCount') if len(cs) == 1 else None))
                if pod.get('status', {}).get('phase') == 'Failed':
                    terminal.append(terminal_record(pod, container))
                    continue
                # Never skip a terminated/unready process and call it drained.
                require(pod.get('status', {}).get('phase') == 'Pending' and reason in
                        ('ErrImageNeverPull', 'ImagePullBackOff', 'ErrImagePull') and
                        not cs[0].get('containerID') and cs[0].get('restartCount') == 0,
                        'old_pod_state_requires_review')
                pending.append(dict(pod=pod['metadata']['name'], uid=pod['metadata']['uid'],
                    node=pod['spec'].get('nodeName'), reason=reason, running=False))
    require(6 <= len([r for r in targets if r['container'] == 'adapter']) <= 10
            and len([r for r in targets if r['container'] == 'dispatcher']) == 1,
            'old_running_pool_shape')
    for service in s.get('services')['items']:
        selector = service.get('spec', {}).get('selector', {})
        matches = [name for name, lbl in labels.items() if selector and
                   all(lbl.get(k) == v for k, v in selector.items())]
        if matches:
            services.append(dict(name=service['metadata']['name'], uid=service['metadata']['uid'],
                old_deployments_selected=matches, service_type=service['spec'].get('type'),
                headless=service['spec'].get('clusterIP') == 'None'))
    return dict(targets=sorted(targets, key=lambda x: x['name']), pending=sorted(pending, key=lambda x: x['pod']),
                terminal=sorted(terminal, key=lambda x: x['pod']),
                services=sorted(services, key=lambda x: x['name']),
                scope='DEER_FLOW_SELECTOR_SERVICES_ONLY_DIRECT_IP_CALLERS_NOT_PROVEN_ABSENT')


def terminal_record(pod, container):
    name = pod['metadata']['name']
    require(name in HISTORICAL and pod['spec'].get('nodeName') == 'datax-01'
            and container == 'adapter' and not pod['metadata'].get('deletionTimestamp'),
            'unreviewed_failed_pod:'+name)
    require(pod.get('status', {}).get('phase') == 'Failed'
            and len(pod['spec'].get('containers', [])) == 1
            and not pod['spec'].get('initContainers') and not pod['spec'].get('ephemeralContainers')
            and not pod['spec'].get('hostNetwork', False), 'failed_pod_shape_changed:'+name)
    statuses = pod.get('status', {}).get('containerStatuses', [])
    require(len(statuses) == 1 and statuses[0].get('name') == container, 'failed_pod_container_status')
    status = statuses[0]
    term = status.get('state', {}).get('terminated', {})
    reason, cid = HISTORICAL[name]
    require(set(status.get('state', {})) == {'terminated'} and term.get('reason') == reason
            and status.get('ready') is False, 'failed_pod_state_changed:'+name)
    require((status.get('containerID') or None) == ('containerd://'+cid if cid else None),
            'failed_pod_container_identity_changed:'+name)
    uid = pod['metadata']['uid']
    require(re.fullmatch('[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', uid), 'failed_pod_uid')
    ids = {cid} if cid else set()
    for value in (term.get('containerID'), status.get('lastState', {}).get('terminated', {}).get('containerID')):
        if value:
            require(re.fullmatch('containerd://[0-9a-f]{64}', value), 'failed_pod_runtime_id')
            ids.add(value.split('://')[1])
    return dict(pod=name, uid=uid, node='datax-01', phase='Failed', reason=reason,
        exit_code=term.get('exitCode'), finished_at=term.get('finishedAt'),
        restarts=status.get('restartCount'), known_container_ids=sorted(ids),
        node_runtime_check_required=True)


def parse_tasks(raw):
    lines = raw.decode('ascii').splitlines()
    require(lines and lines[0].split() == ['TASK', 'PID', 'STATUS'] and len(lines) <= 8193,
            'runtime_tasks_format')
    result = {}
    for line in lines[1:]:
        if not line.strip():
            continue
        fields = line.split()
        require(len(fields) == 3 and re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', fields[0])
                and fields[1].isdigit() and fields[0] not in result, 'runtime_task_row')
        require(fields[2] in ('RUNNING', 'STOPPED', 'CREATED', 'PAUSED', 'PAUSING', 'UNKNOWN'),
                'runtime_task_state')
        result[fields[0]] = fields[2]
    return result


def runtime_row(record, sandboxes, containers, tasks, cgroups):
    matches = [p for p in sandboxes if p.get('metadata', {}).get('uid') == record['uid']]
    require(all(p['metadata'].get('namespace') == 'deer-flow'
                and p['metadata'].get('name') == record['pod'] for p in matches), 'cri_pod_identity_conflict')
    sandbox_ids = {p['id'] for p in matches}
    ids = set(record['known_container_ids'])
    found = [c for c in containers if c.get('podSandboxId') in sandbox_ids
        or c.get('labels', {}).get('io.kubernetes.pod.uid') == record['uid'] or c.get('id') in ids]
    ids.update(c['id'] for c in found)
    require(all(isinstance(i, str) and re.fullmatch('[0-9a-f]{64}', i) for i in ids | sandbox_ids),
            'cri_record_id_invalid')
    state_names = {0: 'CREATED', 1: 'RUNNING', 2: 'EXITED', 3: 'UNKNOWN',
        'CONTAINER_CREATED': 'CREATED', 'CONTAINER_RUNNING': 'RUNNING',
        'CONTAINER_EXITED': 'EXITED', 'CONTAINER_UNKNOWN': 'UNKNOWN'}
    states = []
    for c in found:
        value = c.get('state')
        require(type(value) in (int, str) and value in state_names, 'cri_container_state_missing')
        states.append(state_names[value])
    matched_tasks = [tasks[i] for i in sorted(ids | sandbox_ids) if i in tasks]
    # Include sandbox tasks and all Pod-cgroup processes conservatively. A pause
    # process alone is not proof of parsing, but is NOT silently counted as absent.
    count = cgroups['matches'][record['uid']]
    clear = (all(v == 'EXITED' for v in states) and not matched_tasks
             and count == 0 and cgroups['unreadable'] == 0)
    return dict(pod=record['pod'], uid=record['uid'], node='datax-01',
        cri_sandbox_records=len(matches), cri_container_states=states,
        matching_containerd_task_states=matched_tasks, pod_cgroup_processes=count,
        unreadable_cgroup_files=cgroups['unreadable'],
        runtime_observation='NO_ACTIVITY_OBSERVED' if clear else 'REVIEW_REQUIRED',
        no_activity_observed=clear, snapshot_only=True)


def cgroup_matches(raw, record):
    uid = record['uid']
    tokens = ('pod'+uid, 'pod'+uid.replace('-', '_')) + tuple(record['known_container_ids'])
    return any(t.encode('ascii') in raw for t in tokens)


def process_cgroups(records):
    matches = {r['uid']: 0 for r in records}
    unreadable = 0
    deadline = time.monotonic()+20
    with os.scandir('/proc') as entries:
        pids = [p.name for p in entries if p.name.isdigit()]
    require(len(pids) <= 32768, 'proc_process_limit')
    for pid in pids:
        require(time.monotonic() < deadline, 'proc_scan_deadline')
        try:
            with open('/proc/'+pid+'/cgroup', 'rb') as f:
                raw = f.read(16385)
            require(len(raw) <= 16384, 'cgroup_file_limit')
        except (FileNotFoundError, ProcessLookupError):
            continue
        except PermissionError:
            unreadable += 1
            continue
        for row in records:
            if cgroup_matches(raw, row):
                matches[row['uid']] += 1
    return dict(matches=matches, unreadable=unreadable)


def terminal_runtime(records):
    if not records:
        return dict(check='NOT_REQUIRED', rows=[], no_activity_observed=True)
    emit(dict(terminal_runtime_audit='RUNNING', node='datax-01', records=len(records)))
    try:
        require(socket.gethostname().split('.')[0] == 'datax-01', 'runtime_node_mismatch')
        safe_dir(Path(SOCKET).parent)
        sock = Path(SOCKET).lstat()
        require(stat.S_ISSOCK(sock.st_mode) and sock.st_uid == 0, 'runtime_socket_invalid')
        allowed = (CRI+['pods', '-o', 'json'], CRI+['ps', '--all', '-o', 'json'], CTR+['tasks', 'list'])
        outputs = []
        for args in allowed:
            # Fixed read-only argv; never inspect credentials, signal, exec in,
            # stop or remove a container, even on a contradictory runtime state.
            outputs.append(sw.s.core.inv.bounded_capture(args))
        pods = json.loads(outputs[0]).get('items')
        containers = json.loads(outputs[1]).get('containers')
        require(isinstance(pods, list) and isinstance(containers, list)
                and len(pods) <= 8192 and len(containers) <= 8192, 'cri_list_missing_or_oversized')
        tasks = parse_tasks(outputs[2])
        cgroups = process_cgroups(records)
        rows = [runtime_row(r, pods, containers, tasks, cgroups) for r in records]
        for row in rows:
            emit(dict(terminal_runtime_observation=row))
        return dict(check='COMPLETE', rows=rows, no_activity_observed=all(r['no_activity_observed'] for r in rows),
                    files_modified=False, runtime_mutation=False)
    except Exception as exc:
        label = str(exc)
        result = dict(check='INCOMPLETE', no_activity_observed=False, error_type=type(exc).__name__,
            failed_check=label if isinstance(exc, AuditFailure) and re.fullmatch('[A-Za-z0-9_:./-]{1,240}', label)
            else 'RUNTIME_READ_UNAVAILABLE', raw_error_printed=False, runtime_mutation=False)
        emit(dict(terminal_runtime_observation=result))
        return result


def observe(row):
    s = sw.s
    before = s.get('pods', row['name'])
    require(pod_identity(before, row['container']) == row, 'pod_changed_before_probe')
    role = 'worker' if row['container'] == 'adapter' else 'dispatcher'
    raw = s.call(['exec', row['name'], '-c', row['container'], '--', '/app/.venv/bin/python',
                  '-I', '-B', '-c', PROBE, '--role', role], timeout=25)
    require(len(raw) <= 32768, 'probe_output_limit')
    receipt = json.loads(raw)
    require(receipt.get('old_pool_probe') == 'PASS' and receipt.get('role') == role
            and receipt.get('source_sha256') == '1c54fbea077137f39a4ce66b1cc309d7e643541808f4b9670fe0fb8037eb6b39'
            and receipt.get('provider_requests') == 0, 'old_probe_incomplete')
    require(pod_identity(s.get('pods', row['name']), row['container']) == row,
            'pod_changed_during_probe')
    return dict(pod=row['name'], pod_uid=row['uid'], sample_utc=utc(), receipt=receipt)


def aggregate(rows):
    worker_active = worker_waiting = busy = waiting = sockets = backlog = 0
    for row in rows:
        r = row['receipt']
        c = r['counters']
        if r['role'] == 'worker':
            worker_active += c['active']
            worker_waiting += c['waiting']
        else:
            busy += c['busy']
            waiting += c['waiting']
        sockets += max(r[k]['inbound_nonterminal'] for k in ('tcp_before', 'tcp_after'))
        backlog += max(r[k]['listener_receive_queue'] for k in ('tcp_before', 'tcp_after'))
    return dict(worker_active=worker_active, worker_waiting=worker_waiting,
        dispatcher_busy=busy, dispatcher_waiting=waiting,
        inbound_nonterminal_sockets=sockets, listener_pending_connections=backlog,
        idle_observed=not any((worker_active, worker_waiting, busy, waiting, sockets, backlog)),
        connection_counts_may_include_health_probes=True, simultaneous_snapshot=False)


def conclusions(samples):
    quiet = len(samples) == 3 and all(x['summary']['idle_observed'] for x in samples)
    return dict(observation='QUIET_SAMPLES_ONLY' if quiet else 'ACTIVITY_OR_CONNECTIONS_OBSERVED',
        admission_closed='NOT_PROVEN', http_drain='NOT_PROVEN', ready_to_retire=False,
        ready_to_expand_16=False, automatic_scaling=False,
        reason='READ_ONLY_OBSERVATION_IS_NOT_AN_ADMISSION_BARRIER')


def run(root, report):
    global PHASE
    route_code = bootstrap()
    context = sw.bind()
    PHASE = 'CURRENT_BASELINES_AND_ROUTING'
    emit(dict(old_pool_audit='RUNNING', phase=PHASE))
    deps = sw.invariants(context, 'candidate')
    candidates = sw.candidate_ready(context)
    dispatch = [r for r in candidates if r['container'] == 'dispatcher']
    sw.s.endpoints(sw.public_route(context, 'candidate'), dispatch)
    inventory = old_inventory(deps)
    report['inventory'] = inventory
    report['terminal_runtime_before'] = terminal_runtime(inventory['terminal'])
    report['termination_and_routing'] = sw.s.core.pf.termination_and_routing(deps)
    emit(dict(old_pool_inventory='COMPLETE', running=len(inventory['targets']),
              not_started=len(inventory['pending']), failed_records=len(inventory['terminal']),
              selector_services=inventory['services']))
    for index in range(3):
        PHASE = 'OBSERVATION_%d' % (index+1)
        sample = dict(index=index+1, utc_started=utc(), rows=[])
        report['samples'].append(sample)
        sw.public_route(context, 'candidate')
        sw.s.endpoints(sw.public_route(context, 'candidate'), dispatch)
        for row in inventory['targets']:
            emit(dict(old_pool_audit='PROBE', sample=index+1, pod=row['name']))
            sample['rows'].append(observe(row))
        sample['summary'] = aggregate(sample['rows'])
        sample['utc_finished'] = utc()
        emit(dict(old_pool_sample=index+1, **sample['summary']))
        if index < 2:
            time.sleep(10)
    PHASE = 'FINAL_BASELINE_RECHECK'
    deps = sw.invariants(context, 'candidate')
    require(sw.candidate_ready(context) == candidates, 'candidate_identity_changed')
    require(old_inventory(deps) == inventory, 'old_inventory_changed')
    report['terminal_runtime_after'] = terminal_runtime(inventory['terminal'])
    sw.s.endpoints(sw.public_route(context, 'candidate'), dispatch)
    workers = [r for r in candidates if r['container'] == 'adapter']
    report['public_health'] = [sw.public_health(r, route_code, 'candidate', 'candidate', root, i)
                               for i, r in enumerate((workers[0], workers[2]))]
    report.update(conclusions(report['samples']))
    report.update(old_pool_audit='COMPLETE', public_route='candidate', baseline_stable=True)
    report['terminal_runtime_clear'] = all(report[k]['no_activity_observed'] for k in
        ('terminal_runtime_before', 'terminal_runtime_after'))
    if not report['terminal_runtime_clear']:
        report['observation'] = 'TERMINAL_RUNTIME_REVIEW_REQUIRED'
        report['old_pool_audit'] = 'OBSERVATIONS_COLLECTED_WITH_RUNTIME_GAP'


def save_result(root, value):
    safe_dir(root)
    raw = (json.dumps(value, sort_keys=True, indent=2)+'\n').encode('utf-8')
    path = root/'RESULT.json'
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as f:
        f.write(raw)
        f.flush()
        os.fsync(f.fileno())
    return str(path), sha(raw)


def safe_last_operation():
    if sw is None or sw.s is None:
        return None
    last = getattr(sw.s, 'LAST_OPERATION', None)
    if not isinstance(last, dict):
        return None
    result = {k: last[k] for k in ('operation', 'exit_code', 'stdout_bytes', 'stderr_bytes', 'stderr_classes') if k in last}
    failure = last.get('probe_failure')
    if isinstance(failure, dict):
        result['probe_failure'] = {k: v for k, v in failure.items()
            if k in ('stage_runtime', 'error_type', 'failed_check') and isinstance(v, str)
            and re.fullmatch('[A-Za-z0-9_:./-]{1,160}', v)}
    return result


def main():
    require(len(sys.argv) == 1 and os.geteuid() == 0
            and socket.gethostname().split('.')[0] == 'datax-01', 'run_as_root_on_datax01_no_flags')
    os.umask(0o077)
    safe_dir(Path('/var/lib'))
    root = Path(tempfile.mkdtemp(prefix='mineru-old-pool-audit-', dir='/var/lib'))
    emit(dict(old_pool_audit_evidence=str(root), production_writes=0, provider_requests=0))
    report = dict(utc_started=utc(), old_pool_audit='INCOMPLETE', samples=[],
        switch_result_sha256=RESULT_SHA, production_workloads_mutated=False,
        production_configuration_modified=False, provider_requests=0, documents_submitted=0,
        local_evidence_files_written=True, old_pool_retired=False, final_16_expansion=False,
        ready_to_retire=False, ready_to_expand_16=False, automatic_retry=False,
        business_upload_acceptance='USER_REPORTED_BOTH_PATHS_SUCCESS_NOT_RETESTED_HERE')
    code = 0
    try:
        run(root, report)
    except Exception as exc:
        code = 1
        label = str(exc)
        known = isinstance(exc, AuditFailure) or type(exc).__name__ in ('StageFailure', 'SwitchFailure', 'CheckFailure', 'PreflightFailure')
        report.update(failed_stage=PHASE, error_type=type(exc).__name__, raw_error_printed=False,
            failed_check=label if known and re.fullmatch('[A-Za-z0-9_:./-]{1,240}', label) else 'SAFE_DIAGNOSTIC_REQUIRED',
            last_operation=safe_last_operation())
    report['utc_finished'] = utc()
    path, digest = save_result(root, report)
    emit(dict(old_pool_audit=report['old_pool_audit'], failed_stage=report.get('failed_stage'),
        failed_check=report.get('failed_check'), report=path, report_sha256=digest,
        last_operation=report.get('last_operation'),
        summaries=[s.get('summary') for s in report['samples']],
        observation=report.get('observation'), admission_closed='NOT_PROVEN', http_drain='NOT_PROVEN',
        terminal_runtime_clear=report.get('terminal_runtime_clear'),
        production_writes=0, provider_requests=0, ready_to_retire=False, ready_to_expand_16=False))
    return code


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        emit(dict(old_pool_audit='INCOMPLETE', failed_stage=PHASE,
                  error_type=type(exc).__name__, raw_error_printed=False,
                  production_writes=0, provider_requests=0, ready_to_retire=False))
        sys.exit(1)
