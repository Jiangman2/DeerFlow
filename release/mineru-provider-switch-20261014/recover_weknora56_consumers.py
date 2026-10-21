#!/usr/bin/env python3
"""Restore stopped56 consumers, NOT an88 upgrade or quarantine bypass.

Python3.7+. Exactly one identity-bound app template annotation may be patched.
No signals, queue writes, model changes, MinerU restarts, or automatic retries.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import sys
import time
import uuid

import diagnose_mineru_quarantine as helper

ROOT = Path('/var/lib/weknora-resume56-20261016-r1')
ANNOTATION = 'deerflow.cnooc.com.cn/resume-stopped-consumers'
FAILURE = 'RESULT-1343a7d18e9b439e9570da30eec82682.json'
FAILURE_SHA = '8336eaa0b16870dc54235a06b6036c502df26bed9444195d08d33fec1dff50f7'
AUDIT = Path('/var/lib/mineru-quarantine-audit-hh7o0ch_/RESULT.json')
AUDIT_SHA = '8f95854c2ac1222505d46e6c61e995b85c139690c34450085913a09802a1cbc3'
e = obs = None
require = helper.require
emit = helper.emit


def initialize():
    global e, obs
    obs = helper.load_helpers()
    e = sys.modules['tune_weknora88']


def exists(path):
    try:
        path.lstat()
        return True
    except FileNotFoundError:
        return False


def write(path, value):
    return obs.write_new(path, value)


def restart_spec(original, token):
    require(isinstance(token,str) and re.fullmatch('[0-9a-f]{32}',token), 'invalid_operation_token')
    target = copy.deepcopy(original)
    meta = target['template'].setdefault('metadata', {})
    require(isinstance(meta,dict), 'template_metadata_shape')
    annotations = meta.setdefault('annotations', {})
    require(isinstance(annotations,dict) and ANNOTATION not in annotations, 'recovery_annotation_already_present')
    annotations[ANNOTATION] = token
    return target


def restart_patch(d, target, token):
    require(restart_spec(d['spec'], token) == target, 'restart_not_annotation_only')
    return [{'op':'test','path':'/metadata/uid','value':d['metadata']['uid']},
            {'op':'test','path':'/metadata/resourceVersion','value':d['metadata']['resourceVersion']},
            {'op':'test','path':'/spec','value':d['spec']},
            {'op':'add','path':'/spec/template/metadata','value':target['template']['metadata']}]


def original_plan():
    failure = e.locked_receipt(e.ROOT/FAILURE, FAILURE_SHA)
    require(failure.get('configuration_switch') == 'UNCONFIRMED'
            and failure.get('failure',{}).get('check') == 'mineru_16_not_healthy'
            and failure.get('live_patch_attempted') is False
            and failure.get('live_patch_acknowledged') is False, 'original_failure_boundary_changed')
    require(not exists(e.ROOT/'rollback'), 'previous_rollback_exists')
    for name in ('PATCH-REQUESTED.json','PATCH-ACK.json','SUCCESS.json'):
        require(not exists(e.ROOT/'forward'/name), '88_patch_or_success_exists_no56_recovery')
    audit = e.locked_receipt(AUDIT, AUDIT_SHA)
    require(audit.get('diagnostic') == 'COMPLETE' and audit.get('production_baseline') == 'UNCHANGED'
            and audit.get('consumers_resumed') is False, 'reviewed_quarantine_audit_changed')
    old = e.read_plan()
    sig = e.load(e.ROOT/'forward'/'SIGNAL-REQUESTED.json')
    ack = e.load(e.ROOT/'forward'/'SIGNAL-ACK.json')
    require(sig['pod_identity'] == old['original_pod'] and sig['binary_sha256'] == e.BINARY
            and ack == {'tstp_sent_once':True,'pid':1,'start_ticks':sig['start_ticks']},
            'stop_receipt_changed_no_repeated_signal')
    return old, sig


def other_baseline(signature):
    result = copy.deepcopy(signature)
    for k in ('deployments','pods'):
        result[k].pop('weknora-app',None)
    return result


def same_json(a, b):
    # Durable JSON turns identity tuples into arrays; retain every field while
    # comparing the canonical representation instead of Python container types.
    return obs.digest(a) == obs.digest(b)


def exact_app(d, spec, identity=None):
    p = e.app_for(d,spec)
    if identity is not None:
        require(same_json(obs.identity(p),identity),'app_process_changed')
    return p


def startup_limit(p):
    files = sorted(ROOT.glob('MODEL-STARTUP-*.json'))
    require(len(files) <= 100,'too_many_startup_records')
    for path in files:
        proof = e.load(path)
        if same_json(proof.get('pod_identity'),obs.identity(p)):
            require(proof.get('limit') == 64 and proof.get('distributed_via_redis') is True
                    and proof.get('raw_logs_saved') is False,'stored_startup_proof_changed')
            return proof
    return e.capture_startup_limit(ROOT,p,64)


def check_recovery_health(h):
    c = h.get('counters',{})
    require(h.get('role') == 'dispatcher' and c.get('discovered') == 16 and c.get('live') == 16
            and c.get('ready') is True and type(c.get('quarantined')) is int
            and 0 <= c['quarantined'] <= 2, 'mineru_outside_reviewed56_recovery_envelope')
    return {'state':'DEGRADED_56_RECOVERY_ONLY' if c['quarantined'] else 'HEALTHY_SAMPLE',
            'health':h, 'usable_lower_bound':16-c['quarantined'], 'expansion_approved':False,
            'local56_parse_handlers':12, 'quarantine_cleared':False}


def dependencies():
    _, pods, signature, _ = obs.baseline()
    h = obs.health(pods[obs.DISPATCHER][0])
    report = check_recovery_health(h)
    workers = [p for n in obs.WORKERS for p in pods[n]]
    with obs.ThreadPoolExecutor(max_workers=4) as pool:
        states = list(pool.map(obs.health,workers))
    require(len(states) == 16 and all(x.get('role') == 'worker' and not x.get('unavailable')
            and x['counters'].get('active') in (0,1) and x['counters'].get('waiting') == 0 for x in states),
            'worker_serialization_or_health_changed')
    report['serialized_workers_checked'] = 16
    return report, other_baseline(signature)


def app_service():
    s = obs.get('services','weknora-app')
    return {'uid':s['metadata']['uid'], 'spec_sha256':obs.digest(s['spec'])}


def endpoint_check(p, expected):
    require(app_service() == expected, 'app_service_changed')
    slices = obs.get('endpointslices',selector='kubernetes.io/service-name=weknora-app')['items']
    endpoints = [x for s in slices for x in s.get('endpoints',[])]
    require(endpoints and all(x.get('targetRef',{}).get('uid') == p['metadata']['uid']
            and x.get('conditions',{}).get('ready') is True
            and not x.get('conditions',{}).get('terminating',False) for x in endpoints),
            'app_endpoints_not_converged')


def confirm_stopped(plan, report):
    rows = []
    for i in range(3):
        p = exact_app(e.deployment(),plan['original'],plan['original_pod'])
        s = e.snapshot(e.redis_binding(p)); e.check_capacities(s,e.CURRENT,'stopped')
        require(e.drain_complete(s) and s['server_identity_hashes'] == plan['server_ids'],
                'claimed_work_or_consumer_identity_changed_no_restart')
        row = {'sample':i+1,'utc':obs.utc(),'stopped_pools':6,'active_handlers':0,
               'redis_active':0,'pending_retained':sum(q['pending'] for q in s['queues'].values())}
        write(ROOT/('DRAIN-'+uuid.uuid4().hex+'.json'),row)
        rows.append(row); emit({'resume56':'VERIFY_ALREADY_STOPPED',**row})
        if i < 2:
            time.sleep(10)
    report['drain_samples'] = rows
    return p


def load_plan():
    prepared = e.load(ROOT/'PREPARED.json')
    plan = e.locked_receipt(ROOT/'PLAN.private.json',prepared['plan_sha256'])
    old, sig = original_plan()
    require(plan['original'] == old['original'] and plan['original_pod'] == old['original_pod']
            and plan['server_ids'] == sig['server_ids'] and plan['placement'] == old['placement']
            and plan['image_id'] == old['original_image_id']
            and plan['target'] == restart_spec(plan['original'],plan['token']), 'recovery_plan_changed')
    return plan


def recover(report):
    report['phase'] = 'BIND_UNPATCHED_88_AND_ACCEPTED56'
    old, sig = original_plan()
    d = e.deployment(); p = exact_app(d,old['original'],old['original_pod'])
    pr = e.binary_probe(p)
    require(pr['start_ticks'] == sig['start_ticks'], 'original_process_identity_changed')
    e.runtime_env(p,e.CURRENT_ENV)
    e.placement.unchanged(old['placement'],e.scheduling_gate(d,p))
    db = e.database(p); report['pre_restart_resources'] = e.resources(p,db,expanding=False)
    health, others = dependencies(); report['pre_restart_mineru'] = health
    token = uuid.uuid4().hex
    plan = {'original':old['original'], 'target':restart_spec(old['original'],token),
            'token':token, 'original_pod':old['original_pod'], 'server_ids':sig['server_ids'],
            'placement':old['placement'], 'image_id':old['original_image_id'],
            'other_baseline':others, 'service':app_service()}
    write(ROOT/'ORIGINAL.private.json',d)
    raw = write(ROOT/'PLAN.private.json',plan)
    write(ROOT/'PREPARED.json',{'plan_sha256':hashlib.sha256(raw).hexdigest()})
    report['phase'] = 'SERVER_DRY_RUN_ONLY'
    dry = ROOT/'DRY-RUN.private.json'; write(dry,restart_patch(d,plan['target'],token))
    projected = e.patch_call(dry,True)
    require(projected['metadata']['uid'] == d['metadata']['uid'] and projected['spec'] == plan['target'],
            'server_admission_changed_restart')
    report['phase'] = 'VERIFY_ALREADY_STOPPED_NO_NEW_SIGNAL'
    p = confirm_stopped(plan,report)
    # No88 health override: this is a distinct operator-approved restoration of
    # the existing56/64 configuration, with the known degraded MinerU state kept.
    health, final_others = dependencies()
    require(same_json(final_others,plan['other_baseline']), 'other_workload_changed_before_restart')
    report['final_pre_restart_mineru'] = health
    e.placement.unchanged(plan['placement'],e.scheduling_gate(e.deployment(),p))
    e.database(p); e.runtime_env(p,e.CURRENT_ENV)
    require(app_service() == plan['service'],'app_service_changed')
    d = e.deployment(); exact_app(d,plan['original'],plan['original_pod'])
    s = e.snapshot(e.redis_binding(p)); e.check_capacities(s,e.CURRENT,'stopped')
    require(e.drain_complete(s) and s['server_identity_hashes'] == plan['server_ids'],
            'final_drain_changed_no_restart')
    patchfile = ROOT/'PATCH.private.json'
    raw = write(patchfile,restart_patch(d,plan['target'],token))
    write(ROOT/'PATCH-REQUESTED.json',{'utc':obs.utc(),'patch_sha256':hashlib.sha256(raw).hexdigest(),
            'source_sha256':obs.digest(plan['original']),'target_sha256':obs.digest(plan['target']),
            'scope':'APP_TEMPLATE_ANNOTATION_ONLY'})
    report.update(phase='CAS_APP_RESTART_ONCE',live_patch_attempted=True)
    emit({'resume56':'RECREATE_SAME_CONFIGURATION','single_replica_api_interruption':True,
          'target_slots':56,'model_limit':64,'mineru_restarted':False,'signals_sent':0})
    reply = e.patch_call(patchfile,False)
    require(reply['metadata']['uid'] == d['metadata']['uid'] and reply['spec'] == plan['target'],
            'patch_ack_mismatch_use_status_no_resubmit')
    write(ROOT/'PATCH-ACK.json',{'utc':obs.utc(),'uid':reply['metadata']['uid'],
            'generation':reply['metadata']['generation'],'target_sha256':obs.digest(plan['target'])})
    report['live_patch_acknowledged'] = True
    accept(plan,report)


def accept(plan,report):
    report['phase'] = 'WAIT_NEW_PROCESS_AND56_ACTIVE_POOLS'
    started = time.monotonic(); stable = 0; last = None; identity = None; startup = None
    transient = {'one_ready_app_required','app_terminating','rollout_not_converged','app_not_recreated_yet',
                 'six_pool_process_required','incomplete_queue_snapshot','effective_capacity_mismatch',
                 'pool_state_mismatch','model_governor_startup_not_confirmed','app_endpoints_not_converged'}
    while time.monotonic()-started < 600:
        try:
            d = e.deployment(); require(d['spec'] == plan['target'],'live_spec_changed_after_restart')
            p = exact_app(d,plan['target'])
            require(p['metadata']['uid'] != plan['original_pod'][0],'app_not_recreated_yet')
            st = d.get('status',{})
            require(st.get('observedGeneration',0) >= d['metadata']['generation']
                    and st.get('updatedReplicas') == 1 and st.get('availableReplicas') == 1,'rollout_not_converged')
            require(p['status']['containerStatuses'][0].get('imageID') == plan['image_id'],'replacement_image_changed')
            if identity != obs.identity(p):
                identity = obs.identity(p); stable = 0; startup = None
            if startup is None:
                startup = startup_limit(p)
            s = e.snapshot(e.redis_binding(p)); e.check_capacities(s,e.CURRENT,'active')
            endpoint_check(p,plan['service'])
            stable += 1; last = None
            if stable >= 3:
                break
        except (helper.AuditFailure,obs.CheckFailure) as exc:
            last = str(exc); stable = 0
            if last not in transient:
                raise
        emit({'resume56':'WAIT_RUNTIME','elapsed_seconds':round(time.monotonic()-started),
              'stable_samples':stable,'last_check':last})
        time.sleep(10)
    require(stable >= 3,'runtime_deadline_use_status_no_repeated_restart')
    report['consumer_runtime_acceptance'] = 'PASS'
    e.runtime_env(p,e.CURRENT_ENV); e.binary_probe(p)
    report['post_restart_database'] = e.database(p)
    report['post_restart_resources'] = e.resources(p,report['post_restart_database'],expanding=False)
    e.placement.unchanged(plan['placement'],e.scheduling_gate(e.deployment(),p))
    health, others = dependencies()
    require(same_json(others,plan['other_baseline']),'other_workload_changed_during_recovery')
    report['post_restart_mineru'] = health
    exact_app(e.deployment(),plan['target'],identity)
    endpoint_check(p,plan['service'])
    report.update(consumer_recovery='PASS',effective_worker_slots=56,effective_pools=e.CURRENT,
                  model_governor_startup_limit=64,new_pod={'name':p['metadata']['name'],'uid':p['metadata']['uid']},
                  pending_queue_preserved=True,queue_counts_can_change_after_resume=True,
                  old88_continuation='INVALIDATED_NEW_PROCESS_REQUIRES_NEW_UPGRADE_PLAN',
                  throughput_acceptance='NOT_TESTED',mineru_quarantine_fixed=False,ready_for88=False)
    if not exists(ROOT/'SUCCESS.json'):
        write(ROOT/'SUCCESS.json',{k:report[k] for k in ('consumer_recovery','effective_worker_slots',
                  'effective_pools','model_governor_startup_limit','new_pod','old88_continuation')})


def reconcile(report):
    require(exists(ROOT/'PATCH-REQUESTED.json'),'no_patch_requested_no_mutating_resume')
    plan = load_plan()
    require(e.deployment()['spec'] == plan['target'],'uncertain_patch_not_visible_no_resubmit')
    report['previous_patch_requested'] = True
    accept(plan,report)  # Read-only production reconciliation; never patch again.


def status():
    out = {'consumer_recovery_status':'READ_ONLY','production_writes':0,'signals_sent':0,
           'files_modified':False,'state':str(ROOT)}
    if not exists(ROOT):
        emit(dict(out,state_exists=False)); return
    obs.safe_parent(ROOT)
    out['records'] = {n:exists(ROOT/n) for n in ('PREPARED.json','PATCH-REQUESTED.json','PATCH-ACK.json','SUCCESS.json')}
    if exists(ROOT/'PREPARED.json'):
        plan = load_plan(); d = e.deployment()
        out['live_configuration'] = ('RECOVERY56' if d['spec'] == plan['target'] else
                                     'ORIGINAL56' if d['spec'] == plan['original'] else 'CHANGED_REVIEW_REQUIRED')
        ps = obs.owned(d)
        out['pods'] = [{'name':p['metadata']['name'],'uid':p['metadata']['uid'],'ready':obs.ready(p),
                        'phase':p.get('status',{}).get('phase')} for p in ps]
        if len(ps) == 1 and obs.ready(ps[0]):
            try:
                s = e.snapshot(e.redis_binding(ps[0]))
                out['pools'] = s['effective_servers']
                out['queue_counts'] = {q:{k:v[k] for k in ('pending','active','retry','failed_total')}
                                       for q,v in s['queues'].items()}
            except Exception as exc:
                out['snapshot_failure'] = obs.failure(exc)
    emit(out)


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    report = {'consumer_recovery':'UNCONFIRMED','phase':'READ_ONLY_PREFLIGHT',
              'live_patch_attempted':False,'live_patch_acknowledged':False,
              'signals_sent':0,'provider_probe_submissions':0,'automatic_retry':False,'automatic_rollback':False,
              'image_changed':False,'mineru_modified':False,'model_configuration_changed':False,
              'database_migration_requested':False,'state':str(ROOT)}
    locks = []; created = False; code = 1
    try:
        require(sys.platform == 'linux' and hasattr(os,'geteuid') and os.geteuid() == 0
                and socket.gethostname().split('.')[0] == 'datax-01','run_on_datax01_as_root')
        require(len(args) == 1 and args[0] in ('--recover','--status','--reconcile'),'explicit_mode_required')
        initialize(); os.umask(0o077)
        import fcntl
        fd = helper.open_safe(e.ROOT/'controller.lock'); locks.append(fd)
        fcntl.flock(fd,(fcntl.LOCK_SH if args[0] == '--status' else fcntl.LOCK_EX)|fcntl.LOCK_NB)
        locks.append(e.acquire_previous_lock())
        if args[0] == '--status':
            status(); return 0
        if args[0] == '--recover':
            require(not exists(ROOT),'operation_exists_use_status_do_not_repeat_recover')
            e.fresh_directory(ROOT); created = True
        else:
            obs.safe_parent(ROOT); created = True
        report.update(utc_started=obs.utc(),mode=args[0])
        emit({'resume56_evidence':str(ROOT),'mode':args[0],'target_slots':56,'model_limit':64,
              'no88_expansion':True,'queue_empty_required':False})
        if args[0] == '--recover':
            recover(report)
        else:
            reconcile(report)
        code = 0
    except KeyboardInterrupt:
        report['failure'] = {'check':'OPERATOR_INTERRUPTED','raw_error_printed':False}; code = 130
    except Exception as exc:
        report['failure'] = {'check':str(exc) if isinstance(exc,helper.AuditFailure) else
                            (obs.failure(exc)['check'] if obs else 'SAFE_DIAGNOSTIC_REQUIRED'),
                             'error_type':type(exc).__name__,'raw_error_printed':False}
        if e is not None:
            report['last_operation'] = dict(e.LAST)
    finally:
        if created:
            report['utc_finished'] = obs.utc()
            path = ROOT/('RESULT-'+uuid.uuid4().hex+'.json')
            try:
                raw = write(path,report)
                emit(report)
                emit({'report':str(path),'report_sha256':hashlib.sha256(raw).hexdigest(),
                      'instruction':'USE_STATUS_ON_FAILURE_NO_REPEAT_RECOVER_NO_OLD88_CONTINUE'})
            except Exception as exc:
                emit({'consumer_recovery':'UNCONFIRMED','failure':obs.failure(exc),
                      'live_patch_attempted':report['live_patch_attempted'],
                      'instruction':'USE_STATUS_NO_REPEATED_RESTART'})
                code = 1
        elif args != ['--status'] or code != 0:
            # status() returns through finally; it already emitted its receipt.
            if 'failure' in report:
                emit(report)
        for fd in reversed(locks):
            os.close(fd)
    return code


if __name__ == '__main__':
    sys.exit(main())
