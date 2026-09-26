#!/usr/bin/env python3
"""Guarded frontend 1->2 rollout bound to the successful wave-1 canary."""
import argparse
import copy
import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parent
PREP_HASH = '7df921c0675ada9544e33e7d7627e3881e143a331fd0df40d9bde36a80395383'
source = ROOT / 'prepare-wave1.py'
if not source.is_file():
    source = ROOT.parent / 'capacity-wave1-r1' / 'prepare-wave1.py'  # Local development only.
if hashlib.sha256(source.read_bytes().replace(b'\r\n',b'\n')).hexdigest() != PREP_HASH:
    raise RuntimeError('preparation_reader_digest_mismatch')
spec = importlib.util.spec_from_file_location('capacity_wave1_reader', str(source))
p = importlib.util.module_from_spec(spec); spec.loader.exec_module(p)
VERSION = '20260924-capacity-wave1-expand-r2'
EVIDENCE = Path('/opt/deerflow-hotfix-evidence')
CANARY = EVIDENCE / 'capacity-wave1-canary-20260924T111730Z-7dff55d0'
NODES = {'datax-02', p.TARGET}


def read(path):
    path = Path(path)
    p.require(path.is_file() and not path.is_symlink(), 'evidence_missing_or_symlink=' + path.name)
    return json.loads(path.read_text(encoding='utf-8'))


def canary_evidence(plan):
    p.require(CANARY.is_dir() and not CANARY.is_symlink(), 'reviewed_canary_evidence_missing')
    intent = read(CANARY/'intent.json'); result = read(CANARY/'result.json')
    checks = read(CANARY/'checks.json'); before = read(CANARY/'baseline.json'); after = read(CANARY/'after.json')
    p.require(intent.get('plan_sha256') == p.sha(plan) and intent.get('cluster_uid') == plan['cluster_uid'], 'canary_plan_identity_mismatch')
    p.require(result.get('wave1_frontend_canary') == 'PASS' and result.get('temporary_objects') == 'CLEANED'
              and result.get('rollout') == 'NOT_PERFORMED' and result.get('node_cordon') == 'UNCHANGED_TRUE', 'canary_not_complete')
    p.require(result.get('evidence_dir') == str(CANARY), 'canary_evidence_path_mismatch')
    p.require(CANARY.name.startswith('capacity-wave1-canary-'), 'unexpected_canary')
    p.require(before['frontend']['spec_sha256'] == after['frontend']['spec_sha256'] == plan['frontend']['spec_sha256']
              and after['cluster_uid'] == plan['cluster_uid'] and before['protected_workloads'] == after['protected_workloads'], 'canary_mutated_baseline')
    p.require(checks.get('image_id') == plan['frontend']['image_id'] and checks.get('next_runtime') == plan['frontend']['next_runtime'], 'canary_runtime_mismatch')
    http = checks.get('http', {})
    p.require(set(http) == {'loopback', 'target_to_production_frontend_and_gateway', 'production_frontend_to_target'}, 'canary_missing_http_routes')
    for key in ('icon', 'static_js'):
        items = [http[r].get(key, {}) for r in sorted(http)]
        p.require(all(x.get('status') == 200 and 0 < x.get('bytes', 0) <= 2*1024*1024 for x in items)
                  and len({x.get('sha256') for x in items}) == 1 and bool(items[0].get('sha256')), 'canary_http_asset_mismatch=' + key)
    p.require(http['target_to_production_frontend_and_gateway'].get('gateway_health', {}).get('status') == 200, 'canary_gateway_health_not_ok')


def bound(plan, preload):
    p.validate_plan(plan)
    age = time.time()-preload['collected_epoch']
    p.require(preload.get('version') == p.VERSION and preload.get('phase') == 'preload'
              and preload.get('target') == p.TARGET and preload.get('image_preload') == 'PASS'
              and preload.get('image') == p.IMAGE and preload.get('image_id') == plan['frontend']['image_id']
              and preload.get('containerd_root') == '/mnt/data/containerd' and -60 <= age <= 21600,
              'preload_changed_or_expired')
    canary_evidence(plan)
    fresh = p.cluster()
    p.require(fresh['cluster_uid'] == plan['cluster_uid'] and fresh['service_spec_sha256'] == plan['service_spec_sha256']
              and fresh['protected_workloads'] == plan['protected_workloads'], 'cluster_service_or_protected_workload_drift')
    for field in ('uid', 'spec_sha256', 'pod_uid', 'image_id', 'next_runtime'):
        p.require(fresh['frontend'][field] == plan['frontend'][field], 'frontend_drift=' + field)
    p.require(fresh['frontend']['source_node'] == 'datax-02' and fresh['target_node']['uid'] == plan['target_node']['uid']
              and fresh['target_node']['cordoned'] is True, 'source_target_or_cordon_changed')
    for pod in fresh['target_node']['pods']:
        owners = pod.get('owners', [])
        p.require(pod['namespace'] in ('kube-system','kube-flannel') and pod['phase'] == 'Running'
                  and len(owners) == 1 and owners[0] in (
                      {'kind':'DaemonSet','name':'kube-proxy'},
                      {'kind':'DaemonSet','name':'kube-flannel-ds'}), '0015_has_unreviewed_pods')
    node = p.get('node', 'datax-02')
    conditions = {x['type']:x['status'] for x in node['status'].get('conditions', [])}
    p.require(conditions.get('Ready') == 'True' and all(conditions.get(x) == 'False' for x in ('DiskPressure','MemoryPressure','PIDPressure')), 'datax_02_unhealthy')
    return fresh


def desired(current):
    obj = copy.deepcopy(current)
    p.frontend_projection(obj)
    spec = obj['spec']; ps = spec['template']['spec']
    p.require(obj['metadata']['name'] == 'frontend' and obj['metadata']['namespace'] == p.NS, 'wrong_deployment')
    p.require(not any(ps.get(x) for x in ('nodeSelector', 'affinity', 'tolerations', 'topologySpreadConstraints', 'nodeName')), 'unexpected_existing_scheduling')
    p.require(spec.get('strategy', {}).get('type','RollingUpdate') == 'RollingUpdate', 'frontend_strategy_requires_review')
    p.require(spec['selector'].get('matchLabels') == {'app':'frontend'}
              and not spec['selector'].get('matchExpressions'), 'frontend_selector_changed')
    spec['replicas'] = 2
    spec['strategy'] = {'type':'RollingUpdate','rollingUpdate':{'maxSurge':1,'maxUnavailable':0}}
    ps['affinity'] = {'nodeAffinity':{'requiredDuringSchedulingIgnoredDuringExecution':{'nodeSelectorTerms':[
        {'matchExpressions':[{'key':'kubernetes.io/hostname','operator':'In','values':sorted(NODES)}]}]}},
        'podAntiAffinity':{'requiredDuringSchedulingIgnoredDuringExecution':[
            {'labelSelector':{'matchLabels':{'app':'frontend'}},
             'matchLabelKeys':['pod-template-hash'],'topologyKey':'kubernetes.io/hostname'}]}}
    ps['tolerations'] = [{'key':'node.kubernetes.io/unschedulable','operator':'Exists','effect':'NoSchedule'}]
    return obj


def patch_ops(old, new, version):
    return [{'op':'test','path':'/metadata/uid','value':old['metadata']['uid']},
            {'op':'test','path':'/metadata/resourceVersion','value':version},
            {'op':'replace','path':'/spec/replicas','value':new['spec']['replicas']},
            {'op':'replace','path':'/spec/strategy','value':new['spec']['strategy']},
            {'op':'add','path':'/spec/template/spec/affinity','value':new['spec']['template']['spec']['affinity']},
            {'op':'add','path':'/spec/template/spec/tolerations','value':new['spec']['template']['spec']['tolerations']}]


def restore_ops(current, preflight):
    ps = current['spec']['template']['spec']
    expected = preflight['desired_spec_sha256']
    p.require(current['metadata']['uid'] == preflight['deployment_uid'] and p.sha(current['spec']) == expected,
              'rollback_requires_exact_expected_rollout_spec')
    p.require(ps.get('affinity') == preflight['new_affinity'] and ps.get('tolerations') == preflight['new_tolerations'], 'rollback_scheduling_changed')
    ops = [{'op':'test','path':'/metadata/uid','value':preflight['deployment_uid']},
           {'op':'test','path':'/metadata/resourceVersion','value':current['metadata']['resourceVersion']},
           {'op':'replace','path':'/spec/replicas','value':1},
           {'op':'replace','path':'/spec/strategy','value':preflight['old_strategy']}]
    for field in ('affinity','tolerations'):
        if preflight['old_' + field + '_present']:
            ops.append({'op':'replace','path':'/spec/template/spec/'+field,'value':preflight['old_'+field]})
        else:
            ops.append({'op':'remove','path':'/spec/template/spec/'+field})
    return ops


def kubectl(args, data=None, timeout=45):
    p.require(args[0] == 'patch', 'unapproved_kubectl_verb')
    if args[0] == 'patch':
        p.require(args in (['patch','deployment','frontend','--type=json','--patch-file=/dev/stdin','-o','json'],
                           ['patch','deployment','frontend','--type=json','--patch-file=/dev/stdin','--dry-run=server','-o','json'])
                  and isinstance(data,list) and data[0]['op'] == data[1]['op'] == 'test'
                  and data[0]['path'] == '/metadata/uid' and data[1]['path'] == '/metadata/resourceVersion',
                  'patch_allowlist_violation')
    prefix = ['kubectl','-n',p.NS]
    if args[0] == 'patch': prefix.insert(1,'--request-timeout=25s')
    try:
        proc = subprocess.Popen(prefix + args,
                                stdin=subprocess.PIPE if data is not None else subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        output, error = proc.communicate(None if data is None else json.dumps(data), timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill(); proc.communicate()
        raise p.Blocked('kubectl_{}_timeout'.format(args[0]))
    except OSError: raise p.Blocked('kubectl_unavailable')
    category = next((x for x in ('Conflict','Forbidden','Invalid','NotFound') if '('+x+')' in error),'UNCLASSIFIED')
    p.require(proc.returncode == 0, 'kubectl_{}_failed_{}_category_{}_raw_output_suppressed'.format(args[0],proc.returncode,category))
    return json.loads(output) if args[0] == 'patch' else None


def verify_two(preflight):
    deployment = p.get('deployment','frontend')
    status = deployment.get('status',{})
    p.require(p.sha(deployment['spec']) == preflight['desired_spec_sha256'] and deployment['metadata']['uid'] == preflight['deployment_uid'], 'expanded_deployment_spec_changed')
    p.require(status.get('observedGeneration',0) >= deployment['metadata']['generation']
              and all(status.get(x,0) == 2 for x in ('replicas','updatedReplicas','readyReplicas','availableReplicas')),
              'frontend_two_replicas_not_stable')
    pods = p.get('pods','-l','app=frontend')['items']
    p.require(len(pods) == 2 and not any(x['metadata'].get('deletionTimestamp') for x in pods), 'frontend_pod_count_or_termination')
    p.require({x['spec'].get('nodeName') for x in pods} == NODES, 'frontend_nodes_not_datax02_and_0015')
    revisions = {x['metadata'].get('labels',{}).get('pod-template-hash') for x in pods}
    p.require(len(revisions) == 1 and None not in revisions, 'frontend_revisions_not_converged')
    uids = set()
    for pod in pods:
        p.require(pod['spec']['containers'][0]['image'] == p.IMAGE and pod.get('status',{}).get('phase') == 'Running', 'frontend_image_or_phase_changed')
        owner = [x for x in pod['metadata'].get('ownerReferences',[]) if x.get('controller')]
        p.require(len(owner) == 1 and owner[0]['kind'] == 'ReplicaSet', 'frontend_pod_owner_changed')
        rs = p.get('replicaset',owner[0]['name'])
        p.require(rs['metadata']['uid'] == owner[0]['uid'] and any(x.get('controller') and x['uid'] == preflight['deployment_uid'] for x in rs['metadata'].get('ownerReferences',[])), 'pod_not_owned_by_frontend')
        cs = [x for x in pod['status'].get('containerStatuses',[]) if x['name'] == 'frontend']
        p.require(len(cs) == 1 and cs[0].get('ready') and p.digest(cs[0]['imageID']) == preflight['image_id'], 'frontend_container_not_ready_or_exact_image')
        probe = json.loads(p.kube('exec',pod['metadata']['name'],'-c','frontend','--','node','-e',p.PROBE))
        p.require(probe == preflight['next_runtime'], 'frontend_next_runtime_mismatch')
        uids.add(pod['metadata']['uid'])
    service = p.get('service','frontend')
    p.require(p.sha(service['spec']) == preflight['service_spec_sha256'], 'frontend_service_changed')
    slices = p.get('endpointslices','-l','kubernetes.io/service-name=frontend')['items']
    ready = [e for s in slices for e in s.get('endpoints',[]) if e.get('conditions',{}).get('ready') is True and not e.get('conditions',{}).get('terminating')]
    p.require(len(ready) == 2 and {e.get('targetRef',{}).get('uid') for e in ready} == uids, 'frontend_ready_endpoints_not_exactly_two')
    p.require({n:p.workload_stamp(n) for n in ('gateway','ppt-master-runner')} == preflight['protected_workloads'], 'protected_workload_changed')
    target = p.node_projection(p.get('node',p.TARGET))
    p.require(target['cordoned'] is True and target['uid'] == preflight['target_node_uid'], '0015_cordon_or_identity_changed')
    return {'nodes':sorted(NODES),'ready_frontend_pods':2,'ready_frontend_endpoints':2,
            'image_id':preflight['image_id'],'next_runtime':'MATCH','gateway_runner_specs':'UNCHANGED','node_cordon':'UNCHANGED_TRUE'}


def original_ready(preflight):
    deployment = p.get('deployment','frontend')
    status = deployment.get('status',{})
    p.require(deployment['metadata']['uid'] == preflight['deployment_uid']
              and p.sha(deployment['spec']) == preflight['old_spec_sha256']
              and status.get('observedGeneration',0) >= deployment['metadata']['generation']
              and all(status.get(x,0) == 1 for x in ('replicas','updatedReplicas','readyReplicas','availableReplicas')),
              'original_frontend_not_converged')
    pods = p.get('pods','-l','app=frontend')['items']
    p.require(len(pods) == 1 and not pods[0]['metadata'].get('deletionTimestamp')
              and any(x.get('type') == 'Ready' and x.get('status') == 'True' for x in pods[0].get('status',{}).get('conditions',[])),
              'original_frontend_pod_not_ready')
    pod = pods[0]
    owner = [x for x in pod['metadata'].get('ownerReferences',[]) if x.get('controller')]
    p.require(len(owner) == 1 and owner[0]['kind'] == 'ReplicaSet', 'original_frontend_owner_changed')
    rs = p.get('replicaset',owner[0]['name'])
    p.require(rs['metadata']['uid'] == owner[0]['uid'] and any(x.get('controller') and x['uid'] == preflight['deployment_uid']
              for x in rs['metadata'].get('ownerReferences',[])), 'original_pod_not_owned_by_frontend')
    cs = pod.get('status',{}).get('containerStatuses',[])
    p.require(len(cs) == 1 and cs[0]['name'] == 'frontend' and cs[0].get('ready')
              and pod['spec']['containers'][0]['image'] == p.IMAGE
              and p.digest(cs[0].get('imageID')) == preflight['image_id'], 'original_frontend_image_not_verified')
    probe = json.loads(p.kube('exec',pod['metadata']['name'],'-c','frontend','--','node','-e',p.PROBE))
    p.require(probe == preflight['next_runtime'], 'original_frontend_next_runtime_mismatch')
    p.require(p.sha(p.get('service','frontend')['spec']) == preflight['service_spec_sha256'], 'frontend_service_changed')
    slices = p.get('endpointslices','-l','kubernetes.io/service-name=frontend')['items']
    endpoints = [e for s in slices for e in s.get('endpoints',[]) if e.get('conditions',{}).get('ready') is True and not e.get('conditions',{}).get('terminating')]
    p.require(len(endpoints) == 1 and endpoints[0].get('targetRef',{}).get('uid') == pods[0]['metadata']['uid'], 'original_frontend_endpoint_not_exact')
    target = p.node_projection(p.get('node',p.TARGET))
    p.require(target['cordoned'] is True and target['uid'] == preflight['target_node_uid'], '0015_cordon_or_identity_changed')
    p.require({n:p.workload_stamp(n) for n in ('gateway','ppt-master-runner')} == preflight['protected_workloads'], 'protected_workload_changed')
    return {'original_spec':'RESTORED','ready_frontend_endpoints':1,'node':pod['spec'].get('nodeName'),
            'image_id':preflight['image_id'],'next_runtime':'MATCH'}


def progress_snapshot(deployment=None):
    """Never emit full Pod specs, env, HTTP bodies or arbitrary event messages."""
    obj = deployment if deployment is not None else p.get('deployment','frontend')
    status = obj.get('status',{})
    pods = p.get('pods','-l','app=frontend')['items']
    return {'generation':obj['metadata']['generation'],'observed_generation':status.get('observedGeneration',0),
            'replicas':{k:status.get(k,0) for k in ('replicas','updatedReplicas','readyReplicas','availableReplicas')},
            'conditions':[{k:c.get(k) for k in ('type','status','reason')} for c in status.get('conditions',[])],
            'pods':[{'name':x['metadata']['name'],'uid':x['metadata']['uid'],'node':x['spec'].get('nodeName'),
                     'phase':x.get('status',{}).get('phase'),'terminating':bool(x['metadata'].get('deletionTimestamp')),
                     'revision':x['metadata'].get('labels',{}).get('pod-template-hash'),
                     'conditions':[{k:c.get(k) for k in ('type','status','reason')}
                                   for c in x.get('status',{}).get('conditions',[])],
                     'containers':[{'name':c.get('name'),'ready':c.get('ready',False),
                                    'waiting_reason':c.get('state',{}).get('waiting',{}).get('reason'),
                                    'restarts':c.get('restartCount',0)}
                                   for c in x.get('status',{}).get('containerStatuses',[])]} for x in pods]}


def record_failure(evidence):
    try:
        report = progress_snapshot()
        events = p.get('events','--field-selector','involvedObject.kind=Pod')['items']
        report['frontend_events'] = []
        categories = {'anti_affinity':('anti-affinity','antiaffinity'),
                      'node_affinity':('node affinity','node selector'),
                      'unschedulable':('unschedulable',), 'taint':('taint',),
                      'insufficient_cpu':('insufficient cpu',), 'insufficient_memory':('insufficient memory',),
                      'volume':('volume',), 'image':('image',), 'probe':('probe',)}
        for event in events:
            ref = event.get('involvedObject',{})
            if not ref.get('name','').startswith('frontend-'): continue
            message = event.get('message','').lower()
            report['frontend_events'].append({'pod':ref.get('name'),'uid':ref.get('uid'),
                'reason':event.get('reason'),'type':event.get('type'),'count':event.get('count'),
                'last_timestamp':event.get('lastTimestamp'),
                'categories':[key for key,terms in sorted(categories.items()) if any(t in message for t in terms)]})
        p.write_new(evidence/'failure-diagnostics.json',report)
        p.emit(frontend_failure_diagnostics=str(evidence/'failure-diagnostics.json'))
    except Exception as exc:
        p.emit(frontend_failure_diagnostics='UNAVAILABLE',error_type=type(exc).__name__)


def wait_converged(preflight, mode, generation, timeout=720):
    """Wait for current-generation state, not a stale rollout-status condition."""
    p.require(mode in ('expand','restore'), 'invalid_wait_mode')
    replicas = 2 if mode == 'expand' else 1
    expected = preflight['desired_spec_sha256' if mode == 'expand' else 'old_spec_sha256']
    deadline = time.monotonic()+timeout
    last = 'not_yet_sampled'
    while time.monotonic() < deadline:
        obj = p.get('deployment','frontend')
        p.require(obj['metadata']['uid'] == preflight['deployment_uid'] and p.sha(obj['spec']) == expected,
                  'convergence_deployment_drift_do_not_overwrite')
        p.require(obj['metadata']['generation'] >= generation, 'convergence_generation_regressed')
        # Both values are checked so a later spec generation cannot be accepted early.
        status = obj.get('status',{})
        observed = status.get('observedGeneration',0) >= max(generation,obj['metadata']['generation'])
        p.emit(frontend_convergence=mode, state=progress_snapshot(obj))
        if observed and all(status.get(k,0) == replicas for k in ('replicas','updatedReplicas','readyReplicas','availableReplicas')):
            try:
                return verify_two(preflight) if mode == 'expand' else original_ready(preflight)
            except p.Blocked as exc:
                last = str(exc)
        else:
            last = 'generation_or_replica_counts_not_converged'
        p.emit(frontend_convergence=mode,waiting_for=last)
        time.sleep(min(10,max(0,deadline-time.monotonic())))
    raise p.Blocked('frontend_{}_convergence_timeout_last_{}'.format(mode,last))


def make_preflight(plan, preload):
    fresh = bound(plan,preload)
    current = p.get('deployment','frontend')
    p.require(p.sha(current['spec']) == plan['frontend']['spec_sha256'], 'frontend_changed_during_preflight')
    new = desired(current)
    candidate = kubectl(['patch','deployment','frontend','--type=json','--patch-file=/dev/stdin','--dry-run=server','-o','json'],
                        patch_ops(current,new,current['metadata']['resourceVersion']))
    p.require(candidate['metadata']['uid'] == current['metadata']['uid'] and p.sha(candidate['spec']) == p.sha(new['spec']),
              'server_dry_run_changes_frontend_spec')
    ps = current['spec']['template']['spec']; ns = new['spec']['template']['spec']
    return {'version':VERSION,'phase':'preflight','collected_epoch':time.time(),
            'plan_sha256':p.sha(plan),'preload_sha256':p.sha(preload),
            'cluster_uid':fresh['cluster_uid'],'deployment_uid':current['metadata']['uid'],
            'old_spec_sha256':p.sha(current['spec']),'desired_spec_sha256':p.sha(new['spec']),
            'old_strategy':current['spec'].get('strategy',{'type':'RollingUpdate'}),
            'old_affinity_present':'affinity' in ps,'old_affinity':ps.get('affinity'),
            'old_tolerations_present':'tolerations' in ps,'old_tolerations':ps.get('tolerations'),
            'new_affinity':ns['affinity'],'new_tolerations':ns['tolerations'],
            'service_spec_sha256':fresh['service_spec_sha256'],
            'protected_workloads':fresh['protected_workloads'],
            'target_node_uid':fresh['target_node']['uid'],
            'image_id':fresh['frontend']['image_id'],'next_runtime':fresh['frontend']['next_runtime'],
            'canary':'PASS','production_mutation':'NONE',
            'planned_replicas':2,'planned_nodes':sorted(NODES),'max_surge':1,'max_unavailable':0,
            'anti_affinity_scope':'SAME_POD_TEMPLATE_HASH','old_new_revision_coexistence':'ALLOWED'}


def validate_preflight(preflight,plan,preload):
    p.require(preflight.get('version') == VERSION and preflight.get('phase') == 'preflight'
              and preflight.get('plan_sha256') == p.sha(plan) and preflight.get('preload_sha256') == p.sha(preload)
              and preflight.get('canary') == 'PASS' and preflight.get('planned_replicas') == 2
              and preflight.get('planned_nodes') == sorted(NODES) and preflight.get('max_unavailable') == 0,
              'preflight_identity_or_policy_mismatch')
    p.require(preflight.get('anti_affinity_scope') == 'SAME_POD_TEMPLATE_HASH'
              and preflight.get('max_surge') == 1, 'preflight_revision_scheduling_policy_mismatch')
    p.require(-60 <= time.time()-preflight['collected_epoch'] <= 1800, 'preflight_expired_30m')
    fresh = bound(plan,preload)
    p.require(fresh['frontend']['spec_sha256'] == preflight['old_spec_sha256']
              and fresh['cluster_uid'] == preflight['cluster_uid']
              and fresh['protected_workloads'] == preflight['protected_workloads'], 'preflight_live_drift')
    current = p.get('deployment','frontend')
    p.require(p.sha(desired(current)['spec']) == preflight['desired_spec_sha256'], 'desired_spec_drift')
    return current


def patch(new,old,preflight):
    ops = patch_ops(old,new,old['metadata']['resourceVersion'])
    result = kubectl(['patch','deployment','frontend','--type=json','--patch-file=/dev/stdin','-o','json'],ops)
    p.require(result['metadata']['uid'] == preflight['deployment_uid'], 'patched_different_frontend')
    return result


def rollback(preflight):
    current = p.get('deployment','frontend')
    p.require(current['metadata']['uid'] == preflight['deployment_uid'], 'rollback_deployment_identity_changed')
    if p.sha(current['spec']) == preflight['old_spec_sha256']:
        wait_converged(preflight,'restore',current['metadata']['generation'])
        return 'ALREADY_ORIGINAL_READY'
    ops = restore_ops(current,preflight)
    restored = kubectl(['patch','deployment','frontend','--type=json','--patch-file=/dev/stdin','-o','json'],ops)
    p.require(p.sha(restored['spec']) == preflight['old_spec_sha256'], 'rollback_admission_changed_spec')
    wait_converged(preflight,'restore',restored['metadata']['generation'])
    return 'PASS_ORIGINAL_ONE_REPLICA'


def execute(preflight,plan,preload, evidence):
    current = validate_preflight(preflight,plan,preload)
    new = desired(current)
    dry = kubectl(['patch','deployment','frontend','--type=json','--patch-file=/dev/stdin','--dry-run=server','-o','json'],
                  patch_ops(current,new,current['metadata']['resourceVersion']))
    p.require(dry['metadata']['uid'] == current['metadata']['uid'] and p.sha(dry['spec']) == preflight['desired_spec_sha256'],
              'server_dry_run_changes_frontend_spec')
    p.write_new(evidence/'intent.json',{'version':VERSION,'preflight':preflight,'phase':'before_patch'})
    p.emit(frontend_expansion='PATCHING',replicas='1_to_2',nodes=sorted(NODES),evidence_dir=str(evidence))
    try:
        result = patch(new,current,preflight)
        p.write_new(evidence/'patch-result.json',{'uid':result['metadata']['uid'],'resource_version':result['metadata']['resourceVersion'],
                                                  'generation':result['metadata']['generation'],'spec_sha256':p.sha(result['spec'])})
        p.require(p.sha(result['spec']) == preflight['desired_spec_sha256'], 'admission_changed_deployment_spec')
        details = wait_converged(preflight,'expand',result['metadata']['generation'])
        result = dict(frontend_expansion='PASS',details=details,rollback='AVAILABLE_GUARDED',
                      browser_acceptance='REQUIRED',capacity_certification='NOT_PERFORMED',evidence_dir=str(evidence))
        p.write_new(evidence/'result.json',result); p.emit(**result)
    except (Exception,KeyboardInterrupt) as exc:
        reason = str(exc) if isinstance(exc,p.Blocked) else type(exc).__name__
        record_failure(evidence)
        try:
            state = rollback(preflight)
        except (Exception,KeyboardInterrupt) as restore:
            state = 'INCOMPLETE_' + (str(restore) if isinstance(restore,p.Blocked) else type(restore).__name__)
        result = {'frontend_expansion':'NOT_PASSED','reason':reason,'automatic_rollback':state,
                  'evidence_dir':str(evidence),'instruction':'send_output_do_not_reapply_blindly'}
        p.write_new(evidence/'result.json',result); p.emit(**result)
        sys.exit(1)


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('mode',choices=('preflight','rollout','rollback'))
    cli.add_argument('--plan',type=Path); cli.add_argument('--preload',type=Path)
    cli.add_argument('--preflight-report',type=Path); cli.add_argument('--ack')
    args = cli.parse_args()
    p.require(sys.platform.startswith('linux') and os.geteuid() == 0 and socket.gethostname().split('.')[0] == 'datax-01', 'sudo_on_datax01_required')
    os.environ['KUBECONFIG']='/etc/kubernetes/admin.conf'
    import fcntl
    fd=os.open('/run/lock/deerflow-capacity-wave1-expand.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try: fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except OSError: raise p.Blocked('another_frontend_expansion_operation_running')
    p.require(args.plan and args.preload, 'plan_and_preload_required')
    plan=read(args.plan); preload=read(args.preload)
    if args.mode == 'preflight':
        report=make_preflight(plan,preload)
        evidence=EVIDENCE/('capacity-wave1-expand-preflight-'+datetime.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8]+'.json')
        p.write_new(evidence,report)
        p.emit(frontend_expansion_preflight='PASS',report=str(evidence),planned_replicas=2,
               source='datax-02',destination=p.TARGET,production_mutation='NONE',
               anti_affinity_scope='SAME_POD_TEMPLATE_HASH')
        return
    p.require(args.preflight_report is not None and args.preflight_report.is_file()
              and not args.preflight_report.is_symlink() and args.preflight_report.resolve().parent == EVIDENCE
              and args.preflight_report.name.startswith('capacity-wave1-expand-preflight-'), 'unsafe_preflight_report_path')
    preflight=read(args.preflight_report)
    if args.mode == 'rollback':
        p.require(args.ack == 'FRONTEND_RESTORE_ONE', 'rollback_ack_required')
        p.require(preflight.get('version') == VERSION and preflight.get('cluster_uid') == p.get('namespace','kube-system')['metadata']['uid'], 'rollback_wrong_cluster')
        state=rollback(preflight); p.emit(frontend_expansion_rollback=state,original_replicas=1)
        return
    p.require(args.ack == 'FRONTEND_TWO_REPLICAS', 'rollout_ack_required')
    evidence=EVIDENCE/('capacity-wave1-expand-'+datetime.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8])
    evidence.mkdir(mode=0o700,parents=True)
    execute(preflight,plan,preload,evidence)


if __name__=='__main__':
    try: main()
    except (p.Blocked,KeyError,ValueError,OSError) as exc:
        p.emit(frontend_expansion='BLOCKED',reason=str(exc) if isinstance(exc,p.Blocked) else type(exc).__name__,
               instruction='send_output_do_not_retry_blindly')
        sys.exit(1)
