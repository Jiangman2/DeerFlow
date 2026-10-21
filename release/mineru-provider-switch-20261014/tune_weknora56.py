#!/usr/bin/env python3
"""One bounded, receipt-driven WeKnora configuration change; Python 3.7+."""
import base64
import copy
try:
    import fcntl
except ImportError:  # Permit non-Linux unit tests; main still rejects that host.
    fcntl = None
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
import uuid

import observe_pipeline as obs
import tuning_review as review
import audit_weknora_process as process
import weknora_tune_placement as placement

ROOT = Path('/var/lib/weknora-tune56-20261016-r1-fix1')
BINARY = 'aa1c5bfc46c5d9b3e3a0ef4c9e487f999ac9b67772b446d16164c5dc621f0bfd'
CURRENT = {'core': 4, 'postprocess': 2, 'enrichment': 12, 'maintenance': 4, 'shared': 2, 'wiki': 8}
TARGET = {'core': 8, 'postprocess': 4, 'enrichment': 24, 'maintenance': 4, 'shared': 4, 'wiki': 12}
TARGET_ENV = {review.SETTINGS[k][1]: str(v) for k, v in dict(TARGET, model=64).items()}
require = obs.require
write = obs.write_new
LAST = {}


def load(path):
    obs.safe_parent(path.parent)
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as f:
        s = os.fstat(f.fileno())
        require(stat.S_ISREG(s.st_mode) and s.st_uid == 0 and s.st_nlink == 1
                and not s.st_mode & 0o022 and s.st_size <= 4194304, 'unsafe_private_record')
        return json.loads(f.read(4194305))


def fresh_directory(path):
    obs.safe_parent(path.parent)
    path.mkdir(mode=0o700)  # Never reuse an uncertain operation directory.
    os.chmod(str(path), 0o700)


def locked_receipt(path, expected_sha):
    value = load(Path(path))
    require(hashlib.sha256(Path(path).read_bytes()).hexdigest() == expected_sha, 'review_receipt_changed')
    return value


def change_env(spec, values):
    out = copy.deepcopy(spec)
    cs = out['template']['spec']['containers']
    require(len(cs) == 1 and cs[0]['name'] == 'app' and not cs[0].get('envFrom'), 'app_container_shape')
    env = cs[0].get('env', [])
    require(len({e['name'] for e in env}) == len(env), 'duplicate_environment_key')
    names = {e['name'] for e in env}
    for i, e in enumerate(env):
        if e['name'] in values:
            require('valueFrom' not in e and set(e) <= {'name', 'value'}, 'concurrency_not_literal')
            env[i] = {'name': e['name'], 'value': values[e['name']]}
    env.extend({'name': name, 'value': val} for name, val in values.items() if name not in names)
    cs[0]['env'] = env
    return out


def patch_plan(d, expected_spec):
    before = d['spec']; check = copy.deepcopy(before)
    check['template']['spec']['containers'][0]['env'] = copy.deepcopy(expected_spec['template']['spec']['containers'][0]['env'])
    require(check == expected_spec, 'change_outside_app_environment')
    return [{'op': 'test', 'path': '/metadata/uid', 'value': d['metadata']['uid']},
            {'op': 'test', 'path': '/metadata/resourceVersion', 'value': d['metadata']['resourceVersion']},
            {'op': 'test', 'path': '/spec', 'value': before},
            {'op': 'replace', 'path': '/spec/template/spec/containers/0/env',
             'value': expected_spec['template']['spec']['containers'][0]['env']}]


def patch_call(file, dry):
    # Only one named deployment, JSON patch from a checked private file. Never
    # pass environment values or the patch body on process arguments.
    load(file)
    args = ['kubectl', '--request-timeout=25s', '-n', 'deer-flow', 'patch', 'deployment',
            'weknora-app', '--type=json', '--patch-file', str(file), '-o', 'json']
    if dry:
        args.append('--dry-run=server')
    LAST.clear(); LAST.update(operation='PATCH_DRY_RUN' if dry else 'PATCH_LIVE')
    p = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=dict(os.environ, KUBECONFIG='/etc/kubernetes/admin.conf'))
    data = {'out': bytearray(), 'err': bytearray()}; deadline = time.monotonic()+35
    try:
        with selectors.DefaultSelector() as sel:
            sel.register(p.stdout, selectors.EVENT_READ, 'out'); sel.register(p.stderr, selectors.EVENT_READ, 'err')
            while sel.get_map():
                require(time.monotonic() < deadline, 'patch_response_deadline_use_status')
                for key, _ in sel.select(.2):
                    part = os.read(key.fileobj.fileno(), 65536)
                    if not part:
                        sel.unregister(key.fileobj); continue
                    data[key.data].extend(part)
                    require(len(data[key.data]) <= (1048576 if key.data == 'out' else 65536), 'patch_output_limit')
        code = p.wait(timeout=max(.01, deadline-time.monotonic()))
        LAST.update(exit_code=code, stdout_bytes=len(data['out']), stderr_bytes=len(data['err']))
        require(code == 0, 'patch_command_failed_no_automatic_retry')
        return json.loads(bytes(data['out']))
    finally:
        if p.poll() is None:
            p.kill()  # Local kubectl only; never the app process.
        p.wait(); p.stdout.close(); p.stderr.close()


def deployment():
    d = obs.get('deployments', 'weknora-app')
    require(d['metadata']['uid'] == process.DEP_UID and d['spec'].get('replicas') == 1
            and d['spec']['strategy'].get('type') == 'Recreate', 'app_controller_changed')
    return d


def app_for(d, expected_spec, identity=None):
    require(d['spec'] == expected_spec, 'app_spec_changed')
    ps = obs.owned(d)
    require(len(ps) == 1 and obs.ready(ps[0]), 'one_ready_app_required')
    p = ps[0]
    require(not p['metadata'].get('deletionTimestamp'), 'app_terminating')
    if identity is not None:
        require(obs.identity(p) == identity, 'app_process_changed')
    return p


def binary_probe(p):
    r = json.loads(obs.call(['exec', p['metadata']['name'], '-c', 'app', '--', 'gosu', '1000:1000',
                            'python3', '-I', '-B', '-c', process.REMOTE], timeout=40, limit=16384))
    require(r.get('binary_sha256') == BINARY and r.get('exe_link_is_expected') is True
            and r.get('process_stable') is True and r.get('effective_uid') == 1000
            and r.get('effective_gid') == 1000, 'binary_identity_changed')
    pr = r['process']
    require(pr['uid'] == [1000]*4 and pr['gid'] == [1000]*4 and pr['pid'] == 1
            and pr.get('tstp_handler_installed') is True and all(r.get('markers', {}).get(k) is True
                for k in ('asynq_package', 'asynq_v0260_buildinfo', 'stop_marker')), 'stop_protocol_markers_changed')
    return pr


def scheduling_gate(d, app):
    return placement.check(d, app)


def redis_binding(app):
    d = obs.get('deployments', 'weknora-redis'); ps = obs.owned(d)
    require(len(ps) == 1 and obs.ready(ps[0]), 'redis_not_single_ready')
    return obs.redis_binding({'weknora-app': [app], 'weknora-redis': ps})


def snapshot(binding):
    p, db, host = binding
    raw = obs.call(['exec', p['metadata']['name'], '-c', obs.container(p)['name'], '--', 'sh', '-c',
                    obs.REDIS_SHELL, 'tune', db], timeout=30, limit=1048576)
    r = review.redis_projection(raw, host)
    require(not r['heartbeat_unavailable'] and r['uninspected_queue_count'] == 0, 'incomplete_queue_snapshot')
    require(abs(time.time()-r['redis_sample_epoch']) < 45, 'stale_redis_snapshot')
    require(r['process_ids'] == [1] and len(r['effective_servers']) == 6, 'six_pool_process_required')
    ids = []
    for line in raw.decode('ascii').splitlines():
        if not line.startswith('H\t'):
            continue
        data = base64.b64decode(line.split('\t')[2], validate=True)[:-1]
        if not data:
            continue
        f = obs.protobuf(data)
        # Ignore only *stopped*, zero-active previous hosts after a restart.
        same = f.get(1) == [host.encode()]
        if not same:
            require(f.get(7) in ([b'stopped'], [b'closed']) and f.get(9, [0]) == [0], 'other_active_asynq_host')
            continue
        require(len(f.get(3, [])) == 1 and isinstance(f[3][0], bytes) and f[3][0], 'server_id_missing')
        ids.append(hashlib.sha256(f[3][0]).hexdigest())
    require(len(set(ids)) == 6, 'six_unique_server_ids_required')
    r['server_identity_hashes'] = sorted(ids)
    require(set(x['pool'] for x in r['effective_servers']) == set(CURRENT), 'unexpected_pool_set')
    require(all(q['paused'] == 0 for q in r['queues'].values()), 'preexisting_queue_pause_requires_review')
    return r


def check_capacities(s, caps, state=None):
    require({x['pool']: x['concurrency'] for x in s['effective_servers']} == caps, 'effective_capacity_mismatch')
    if state:
        require(all(x['status'] == state for x in s['effective_servers']), 'pool_state_mismatch')


def drain_complete(s):
    return (len(s['effective_servers']) == 6 and all(x['status'] == 'stopped' and x['active'] == 0
            for x in s['effective_servers']) and all(q['active'] == 0 for q in s['queues'].values()))


def database(app):
    p, _ = review.pg_binding(app)
    raw = obs.call(['exec', p['metadata']['name'], '-c', obs.container(p)['name'], '--', 'sh', '-c',
                    review.DB_SHELL, 'tune', review.sql_review()], timeout=25, limit=65536)
    r = review.clean_database(json.loads(raw))
    require(not r['settings'], 'persisted_settings_changed_requires_review')
    require({m['id'] for m in r['models']} == set(review.MODEL_IDS)
            and all(m['max_concurrency'] is None for m in r['models']), 'model_override_changed')
    return r


def runtime_env(app, expected):
    r = review.parse_env(obs.call(['exec', app['metadata']['name'], '-c', 'app', '--', 'sh', '-c',
                                   review.ENV_SHELL], limit=8192))
    require(r.get('AUTO_MIGRATE') == 'false', 'automatic_migration_not_disabled')
    for k, val in expected.items():
        require(r.get(k) == val, 'runtime_concurrency_env_mismatch')
    return r


def resources(app, db, expanding=True):
    rows = obs.resource_snapshot({'app': [app]})
    require(len(rows) == 1 and 'unavailable' not in rows[0], 'resource_sample_missing')
    r = rows[0]
    for item in ('memory', 'cpu', 'filesystem'):
        t = r[item].get('time')
        require(isinstance(t, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,9})?Z', t), 'resource_time_format')
        sampled = obs.datetime.datetime.strptime(t[:19], '%Y-%m-%dT%H:%M:%S').replace(tzinfo=obs.datetime.timezone.utc)
        require(abs((obs.datetime.datetime.now(obs.datetime.timezone.utc)-sampled).total_seconds()) < 120, 'stale_resource_sample')
    if not expanding:
        return rows  # A lowering rollback must not require expansion headroom.
    gib = 1024**3
    require(r['memory'].get('availableBytes', 0) >= 24*gib, 'node_memory_headroom_below_24GiB')
    cpu = float(r['allocatable']['cpu'])
    require(type(r['cpu'].get('usageNanoCores')) is int and r['cpu']['usageNanoCores'] < cpu*1e9*.8,
            'node_cpu_headroom_insufficient')
    fs = r['filesystem']
    require(fs.get('availableBytes', 0) >= max(10*gib, fs.get('capacityBytes', 0)*.15), 'node_disk_headroom_insufficient')
    require(len(r['pods']) == 1 and 0 < r['pods'][0]['memory'].get('workingSetBytes', 0) <= 16*gib,
            'app_memory_baseline_requires_review')
    c = db['connections']
    require(c.get('max_connections', 0) > 0 and c.get('all', 10**9) < (c['max_connections']-c.get('reserved_connections', 0))*.6
            and c.get('lock_waiters') == 0 and c.get('idle_in_transaction') == 0, 'database_headroom_requires_review')
    n = obs.get('nodes', app['spec']['nodeName'])
    states = {x['type']: x['status'] for x in n['status']['conditions']}
    require(states.get('Ready') == 'True' and all(states.get(k) == 'False' for k in
            ('MemoryPressure', 'DiskPressure', 'PIDPressure')), 'node_pressure_or_unready')
    return rows


def check_dependencies():
    _, pods, _, _ = obs.baseline()
    h = obs.health(pods[obs.DISPATCHER][0])
    c = h.get('counters', {})
    require(h.get('role') == 'dispatcher' and c.get('live') == 16 and c.get('discovered') == 16
            and c.get('quarantined') == 0 and c.get('ready') is True, 'mineru_16_not_healthy')
    return h


def prepare(report):
    report['previous_unpatched_failure'] = placement.audit.read_failure()
    obs.emit({'weknora_recovery': 'PREVIOUS_UNPATCHED_FAILURE_VERIFIED',
              'previous_evidence_modified': False, 'new_state': str(ROOT)})
    a = locked_receipt('/var/lib/weknora-process-audit-rh4eh_14/RESULT.json',
                       'aedff0cc947d827ead21e0671fc105a6a7b4afdb38d3ec7c592dbc450a079adf')
    require(a.get('process_read') == 'PASS' and a['same_nonroot_user_probe']['binary_sha256'] == BINARY,
            'process_audit_not_pass')
    process.baseline(); d = deployment(); p = app_for(d, d['spec'])
    require(obs.digest(d['spec']) == process.DEP_SPEC, 'original_spec_changed')
    report['phase'] = 'READ_ONLY_PREFLIGHT'
    report['scheduling'] = scheduling_gate(d, p)
    report['dependency_health'] = check_dependencies()
    report['database'] = database(p); report['resources'] = resources(p, report['database'])
    env = runtime_env(p, {})
    resolved = {k: review.resolve(k, None, env.get(v[1]))['predicted_next_start'] for k, v in review.SETTINGS.items()}
    require(resolved == dict(CURRENT, model=32), 'original_effective_settings_changed')
    pr = binary_probe(p); snap = snapshot(redis_binding(p)); check_capacities(snap, CURRENT, 'active')
    candidate = change_env(d['spec'], TARGET_ENV)
    plan = {'original': d['spec'], 'candidate': candidate, 'original_pod': obs.identity(p),
            'original_process': pr, 'original_image_id': p['status']['containerStatuses'][0].get('imageID'),
            'initial_server_ids': snap['server_identity_hashes'], 'placement': report['scheduling']}
    write(ROOT/'ORIGINAL.private.json', d); write(ROOT/'PLAN.private.json', plan)
    dryfile = ROOT/'DRY-RUN.private.json'; write(dryfile, patch_plan(d, candidate))
    report['phase'] = 'SERVER_DRY_RUN_ONLY'
    projected = patch_call(dryfile, True)
    require(projected['metadata']['uid'] == process.DEP_UID and projected['spec'] == candidate,
            'server_admission_changed_candidate')
    app_for(deployment(), d['spec'], obs.identity(p))
    report.update(prepare='PASS', server_dry_run='PASS', worker_target=TARGET, local_per_model_default_target=64,
                  queue_empty_required=False, provider_capacity_approval_required=False)
    write(ROOT/'PREPARED.json', {'plan_sha256': hashlib.sha256((ROOT/'PLAN.private.json').read_bytes()).hexdigest(),
            'prepared': 'PASS', 'utc': obs.utc()})
    return plan


def read_plan():
    r = load(ROOT/'PREPARED.json'); plan = load(ROOT/'PLAN.private.json')
    require(r.get('prepared') == 'PASS' and hashlib.sha256((ROOT/'PLAN.private.json').read_bytes()).hexdigest() ==
            r['plan_sha256'], 'prepared_plan_changed')
    require(obs.digest(plan['original']) == process.DEP_SPEC and change_env(plan['original'], TARGET_ENV) == plan['candidate'],
            'plan_contract_changed')
    return plan


SIGNAL_CODE = r'''
import hashlib,json,os,signal,sys
expected=json.loads(sys.argv[1])
assert os.geteuid()==1000 and os.getegid()==1000
def identity():
 d=dict(x.split(':',1) for x in open('/proc/1/status').read(16384).splitlines() if ':' in x)
 ticks=int(open('/proc/1/stat').read(8192).rsplit(')',1)[1].split()[19])
 assert [int(x) for x in d['Uid'].split()]==[1000]*4 and [int(x) for x in d['Gid'].split()]==[1000]*4
 assert int(d['SigCgt'].strip(),16)&(1<<(int(signal.SIGTSTP)-1))
 assert ticks==expected['start_ticks'] and os.readlink('/proc/1/exe')=='/app/WeKnora'
identity()
h=hashlib.sha256()
with open('/proc/1/exe','rb') as f:
 for block in iter(lambda:f.read(1048576), b''): h.update(block)
assert h.hexdigest()==expected['binary_sha256']
identity()
os.kill(1, signal.SIGTSTP)
print(json.dumps({'tstp_sent_once':True,'pid':1,'start_ticks':expected['start_ticks']}))
'''


def wait_drain(op, source, pod_identity, caps, server_ids, report):
    start = time.monotonic(); stable = 0; samples = []
    while time.monotonic()-start < 1800:
        p = app_for(deployment(), source, pod_identity)
        s = snapshot(redis_binding(p)); check_capacities(s, caps)
        require(s['server_identity_hashes'] == server_ids, 'server_identity_changed_after_signal')
        states = {x['pool']: x['status'] for x in s['effective_servers']}
        stable = stable+1 if drain_complete(s) else 0
        row = {'utc': obs.utc(), 'pool_states': states,
               'active_handlers': sum(x['active'] for x in s['effective_servers']),
               'redis_active': sum(q['active'] for q in s['queues'].values()),
               'pending_retained': sum(q['pending'] for q in s['queues'].values()),
               'stable_zero_samples': stable, 'elapsed_seconds': round(time.monotonic()-start)}
        samples.append(row); obs.emit({'weknora_drain': 'RUNNING', **row})
        # Persist bounded, content-free progress on every sample; never overwrite.
        write(op/('drain-'+uuid.uuid4().hex+'.json'), row)
        if stable >= 3:
            write(op/('DRAIN-VERIFIED-'+uuid.uuid4().hex+'.json'), {'samples': samples[-3:], 'six_stopped_pools': True})
            report['live_stop_behavior'] = 'SIX_POOLS_STOPPED_AND_ZERO_ACTIVE_CONFIRMED'
            return p
        require(time.monotonic()-start < 90 or all(v == 'stopped' for v in states.values()),
                'not_all_pools_stopped_no_restart')
        time.sleep(10)
    raise obs.CheckFailure('active_task_wait_deadline_no_force_no_restart_use_continue')


def mutate(op, plan, direction, report, continuing=False):
    source, target = (plan['original'], plan['candidate']) if direction == 'forward' else (plan['candidate'], plan['original'])
    caps = CURRENT if direction == 'forward' else TARGET
    target_caps = TARGET if direction == 'forward' else CURRENT
    model_target = 64 if direction == 'forward' else 32
    d = deployment()
    if (op/'PATCH-REQUESTED.json').exists():
        require(d['spec'] == target, 'patch_outcome_not_target_use_status_no_resubmit')
        report['patch_previously_requested'] = True
        return accept(op, plan, target, target_caps, model_target, report)
    p = app_for(d, source)
    if continuing:
        sig = load(op/'SIGNAL-REQUESTED.json')
        require(obs.identity(p) == sig['pod_identity'], 'signalled_pod_changed')
    else:
        require(not (op/'SIGNAL-REQUESTED.json').exists(), 'signal_already_requested_use_continue')
        report['phase'] = 'FRESH_PREFLIGHT_BEFORE_SIGNAL'
        report['dependency_health'] = check_dependencies()
        report['scheduling'] = scheduling_gate(d, p)
        placement.unchanged(plan['placement'], report['scheduling'])
        db = database(p); report['resource_recheck'] = resources(p, db, expanding=(direction == 'forward'))
        env = runtime_env(p, TARGET_ENV if direction == 'rollback' else {})
        resolved = {k: review.resolve(k, None, env.get(v[1]))['predicted_next_start'] for k, v in review.SETTINGS.items()}
        require(resolved == dict(caps, model=(32 if direction == 'forward' else 64)), 'effective_settings_changed')
        pr = binary_probe(p)
        s = snapshot(redis_binding(p)); check_capacities(s, caps, 'active')
        # Precheck this exact forward/rollback patch before stopping the app's consumers.
        fresh = deployment(); app_for(fresh, source, obs.identity(p))
        dryfile = op/'DRY-RUN.private.json'; write(dryfile, patch_plan(fresh, target))
        projection = patch_call(dryfile, True)
        require(projection['spec'] == target, 'dry_run_target_changed')
        sig = {'pod_identity': obs.identity(p), 'pod': p['metadata']['name'], 'start_ticks': pr['start_ticks'],
               'server_ids': s['server_identity_hashes'], 'binary_sha256': BINARY, 'utc': obs.utc()}
        app_for(deployment(), source, sig['pod_identity'])
        write(op/'SIGNAL-REQUESTED.json', sig)
        report.update(phase='STOP_CLAIMING_ONCE', signals_attempted=1)
        obs.emit({'weknora_change': 'STOP_CLAIMING_ONCE', 'direction': direction, 'queue_empty_required': False})
        raw = obs.call(['exec', p['metadata']['name'], '-c', 'app', '--', 'gosu', '1000:1000',
                       'python3', '-I', '-B', '-c', SIGNAL_CODE,
                       json.dumps({'start_ticks': pr['start_ticks'], 'binary_sha256': BINARY})], timeout=40, limit=4096)
        ack = json.loads(raw)
        require(ack == {'tstp_sent_once': True, 'pid': 1, 'start_ticks': pr['start_ticks']}, 'signal_ack_invalid_use_status')
        write(op/'SIGNAL-ACK.json', ack)
    report['phase'] = 'WAIT_CLAIMED_TASKS_ONLY'
    p = wait_drain(op, source, sig['pod_identity'], caps, sig['server_ids'], report)
    # Database settings can be edited independently of the Deployment. Recheck
    # the exact allowlist immediately before the one compare-and-swap patch.
    db = database(p)
    report['final_resource_recheck'] = resources(p, db, expanding=(direction == 'forward'))
    report['dependency_health'] = check_dependencies()
    d = deployment(); app_for(d, source, sig['pod_identity'])
    placement.unchanged(plan['placement'], scheduling_gate(d, p))
    final = snapshot(redis_binding(p)); check_capacities(final, caps, 'stopped')
    require(drain_complete(final) and final['server_identity_hashes'] == sig['server_ids'], 'drain_changed_no_restart')
    patchfile = op/'PATCH.private.json'; write(patchfile, patch_plan(d, target))
    write(op/'PATCH-REQUESTED.json', {'utc': obs.utc(), 'direction': direction,
            'source_sha256': obs.digest(source), 'target_sha256': obs.digest(target),
            'patch_sha256': hashlib.sha256(patchfile.read_bytes()).hexdigest()})
    report.update(phase='CAS_ENVIRONMENT_PATCH', live_patch_attempted=True)
    obs.emit({'weknora_change': 'CAS_ENVIRONMENT_PATCH', 'direction': direction,
              'old_claimed_tasks_finished': True, 'single_replica_api_interruption_expected': True})
    reply = patch_call(patchfile, False)
    require(reply['metadata']['uid'] == process.DEP_UID and reply['spec'] == target, 'patch_ack_mismatch_use_status')
    write(op/'PATCH-ACK.json', {'uid': reply['metadata']['uid'], 'generation': reply['metadata']['generation'],
                             'target_sha256': obs.digest(target), 'utc': obs.utc()})
    report['live_patch_acknowledged'] = True
    return accept(op, plan, target, target_caps, model_target, report)


def accept(op, plan, target, caps, model_target, report):
    report['phase'] = 'WAIT_RECREATE_AND_EFFECTIVE_CAPACITY'
    start = time.monotonic(); stable = 0; last_error = None
    while time.monotonic()-start < 600:
        try:
            d = deployment(); require(d['spec'] == target, 'app_spec_changed_after_patch')
            p = app_for(d, target)
            st = d.get('status', {})
            require(st.get('observedGeneration', 0) >= d['metadata']['generation']
                    and st.get('updatedReplicas') == 1 and st.get('availableReplicas') == 1, 'rollout_not_converged')
            require(p['status']['containerStatuses'][0].get('imageID') == plan['original_image_id'], 'runtime_image_changed')
            s = snapshot(redis_binding(p)); check_capacities(s, caps, 'active')
            stable += 1
            if stable >= 3:
                break
        except obs.CheckFailure as exc:
            last_error = str(exc); stable = 0
            # Drift is not a readiness transient and must not be ignored.
            if last_error in ('app_spec_changed_after_patch', 'runtime_image_changed', 'app_controller_changed'):
                raise
        obs.emit({'weknora_rollout': 'RUNNING', 'elapsed_seconds': round(time.monotonic()-start),
                  'stable_capacity_samples': stable, 'last_check': last_error})
        time.sleep(10)
    require(stable >= 3, 'rollout_wait_deadline_use_status_no_automatic_rollback')
    expected_env = {review.SETTINGS[k][1]: str(v) for k, v in dict(caps, model=model_target).items()}
    if model_target == 32:  # Original wiki/model use defaults, not literal env.
        expected_env = {e['name']: e['value'] for e in target['template']['spec']['containers'][0]['env']
                        if e['name'] in TARGET_ENV}
    runtime_env(p, expected_env); database(p); binary_probe(p)
    placement.unchanged(plan['placement'], scheduling_gate(deployment(), p))
    raw = obs.call(['logs', p['metadata']['name'], '-c', 'app', '--tail=10000', '--limit-bytes=1048576'], limit=1048577)
    limits = [int(x) for x in re.findall(rb'\[ModelLimiter\] background model concurrency governed per-model, limit=([0-9]+) \(distributed via redis\)', raw)]
    require(limits and set(limits) == {model_target}, 'model_governor_startup_not_confirmed')
    app_for(deployment(), target, obs.identity(p))
    report.update(configuration_switch='PASS', effective_worker_slots=sum(caps.values()), effective_pools=caps,
                  model_governor_startup_limit=model_target, model_actual_parallel_load='NOT_MEASURED',
                  mineru_slots_changed=False, image_changed=False, database_migration_requested=False,
                  pending_queue_preserved=True, throughput_acceptance='PENDING_OBSERVATION_AND_3_10_30_DOCUMENTS',
                  release_acceptance='PENDING_USER_VALIDATION')
    if not (op/'SUCCESS.json').exists():
        write(op/'SUCCESS.json', {k: report[k] for k in ('configuration_switch', 'effective_worker_slots',
                'effective_pools', 'model_governor_startup_limit', 'throughput_acceptance')})


def status():
    r = {'weknora_tuning_status': 'READ_ONLY', 'production_writes': 0, 'signals_sent': 0,
         'files_modified': False, 'state': str(ROOT)}
    if not ROOT.exists():
        r['state_exists'] = False; obs.emit(r); return
    obs.safe_parent(ROOT)
    if not (ROOT/'PREPARED.json').exists():
        r['preparation'] = 'INCOMPLETE_NO_SIGNAL_OR_PATCH_PATH_REACHED'
        r['result_files'] = sorted(p.name for p in ROOT.glob('RESULT-*.json'))[-10:]
        obs.emit(r); return
    plan = read_plan(); d = deployment()
    r['live_configuration'] = ('candidate' if d['spec'] == plan['candidate'] else
                               'original' if d['spec'] == plan['original'] else 'CHANGED_REVIEW_REQUIRED')
    r['operations'] = {}
    for name in ('forward', 'rollback'):
        op = ROOT/name
        if op.exists():
            obs.safe_parent(op)
            r['operations'][name] = {n: (op/n).exists() for n in
                ('SIGNAL-REQUESTED.json', 'SIGNAL-ACK.json', 'PATCH-REQUESTED.json', 'PATCH-ACK.json', 'SUCCESS.json')}
    ps = obs.owned(d)
    r['pods'] = [{'uid': p['metadata']['uid'], 'name': p['metadata']['name'], 'ready': obs.ready(p),
                 'phase': p.get('status', {}).get('phase')} for p in ps]
    if len(ps) == 1 and obs.ready(ps[0]):
        try:
            s = snapshot(redis_binding(ps[0]))
            r['pools'] = s['effective_servers']; r['claimed_tasks_drained'] = drain_complete(s)
            r['queue_counts'] = {q: {k: v[k] for k in ('pending', 'active', 'retry', 'failed_total')}
                                 for q, v in s['queues'].items()}
        except Exception as exc:
            r['snapshot_unavailable'] = obs.failure(exc)
    obs.emit(r)


def main():
    require(sys.platform == 'linux' and fcntl is not None and os.geteuid() == 0 and socket.gethostname().split('.')[0] == 'datax-01',
            'run_on_datax01_as_root')
    require(len(sys.argv) == 2 and sys.argv[1] in ('--apply', '--status', '--continue', '--rollback'), 'explicit_mode_required')
    mode = sys.argv[1]; os.umask(0o077); obs.safe_parent('/var/lib')
    if mode == '--status':
        status(); return 0
    if mode == '--apply':
        require(not ROOT.exists(), 'operation_exists_use_status_do_not_repeat_apply')
        fresh_directory(ROOT)
    else:
        obs.safe_parent(ROOT)
    fd = os.open(str(ROOT/'controller.lock'), os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW, 0o600)
    s = os.fstat(fd)
    require(stat.S_ISREG(s.st_mode) and s.st_uid == 0 and s.st_nlink == 1 and not s.st_mode & 0o022, 'unsafe_controller_lock')
    fcntl.flock(fd, fcntl.LOCK_EX|fcntl.LOCK_NB)
    report = {'utc_started': obs.utc(), 'mode': mode, 'phase': 'PREPARE_INPUTS', 'configuration_switch': 'UNCONFIRMED',
              'state': str(ROOT), 'automatic_retry': False, 'automatic_rollback': False,
              'provider_probe_submissions': 0, 'signals_attempted': 0, 'live_patch_attempted': False,
              'live_patch_acknowledged': False, 'credentials_exported': False, 'queue_empty_required': False}
    code = 1
    try:
        if mode == '--apply':
            plan = prepare(report); direction = 'forward'; op = ROOT/direction; fresh_directory(op)
        elif mode == '--rollback':
            plan = read_plan(); direction = 'rollback'; op = ROOT/direction; fresh_directory(op)
            require((ROOT/'forward'/'PATCH-REQUESTED.json').exists(), 'no_forward_patch_to_rollback')
        else:
            plan = read_plan(); direction = 'rollback' if (ROOT/'rollback').exists() else 'forward'; op = ROOT/direction
            obs.safe_parent(op)
            require((op/'SIGNAL-REQUESTED.json').exists(), 'no_signal_requested_no_blind_resume')
        report['direction'] = direction
        mutate(op, plan, direction, report, continuing=(mode == '--continue'))
        code = 0
    except Exception as exc:
        report['failure'] = obs.failure(exc); report['last_operation'] = dict(LAST)
        report['instruction'] = 'USE_STATUS; NO_REPEAT_APPLY_OR_SIGNAL; DO_NOT_FORCE_TERMINATE'
    finally:
        report['utc_finished'] = obs.utc()
        name = 'RESULT-'+uuid.uuid4().hex+'.json'; raw = write(ROOT/name, report)
        obs.emit(report)
        obs.emit({'report': str(ROOT/name), 'report_sha256': hashlib.sha256(raw).hexdigest(),
                  'status_mode': '--status', 'state': str(ROOT)})
        os.close(fd)
    return code


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        obs.emit({'configuration_switch': 'UNCONFIRMED', 'failure': obs.failure(exc),
                  'instruction': 'USE_STATUS_DO_NOT_REPEAT', 'automatic_retry': False})
        sys.exit(1)
