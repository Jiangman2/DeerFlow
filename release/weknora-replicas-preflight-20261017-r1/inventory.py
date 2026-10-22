#!/usr/bin/env python3
"""Read-only first-stage WeKnora replica inventory. Python 3.7+, stdlib only.

GET Kubernetes objects only. No exec, logs, metrics, Secret/ConfigMap reads,
SQL, Redis, HTTP/model requests, process signals, retries or cluster writes.
Only the separate launcher writes a root-private diagnostic log.
"""
import datetime
import hashlib
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import threading
import time

NS = 'deer-flow'
APP = 'weknora-app'
NODES = ('datax-01', 'datax-02', 'datax-03')
KNOWN_POD_UID = '2fb12206-2d91-4b12-806b-471e1ca19c61'
KNOWN_IMAGE = 'deerflow.local/weknora-app:20261016-native-glm-runtimefix-fc44d25744194ece'
KINDS = {'deployments', 'replicasets', 'pods', 'persistentvolumeclaims',
         'persistentvolumes', 'nodes', 'services', 'endpoints',
         'horizontalpodautoscalers', 'poddisruptionbudgets', 'networkpolicies'}
CLUSTER_KINDS = {'nodes', 'persistentvolumes'}
NAME = re.compile(r'[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?\Z')
LABEL = re.compile(r'[A-Za-z0-9_./-]+\Z')
QUANTITY = re.compile(r'[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+|[numkKMGTPE]i?)?\Z')
POOL_ENV = {
    'core': 'WEKNORA_ASYNQ_CORE_CONCURRENCY',
    'postprocess': 'WEKNORA_ASYNQ_POSTPROCESS_CONCURRENCY',
    'enrichment': 'WEKNORA_ASYNQ_ENRICHMENT_CONCURRENCY',
    'maintenance': 'WEKNORA_ASYNQ_MAINTENANCE_CONCURRENCY',
    'shared': 'WEKNORA_ASYNQ_SHARED_CONCURRENCY',
    'wiki': 'WEKNORA_WIKI_ASYNQ_CONCURRENCY',
}
INT_ENV = set(POOL_ENV.values()) | {'WEKNORA_MODEL_MAX_CONCURRENCY', 'CONCURRENCY_POOL_SIZE'}
ENUM_ENV = {
    'STORAGE_TYPE': {'local', 'minio', 'cos', 's3', 'tos'},
    'STREAM_MANAGER_TYPE': {'redis', 'memory'},
    'DB_DRIVER': {'postgres', 'sqlite', 'mysql'},
    'AUTO_MIGRATE': {'true', 'false'},
    'WEKNORA_HOUSEKEEPING_ENABLED': {'true', 'false'},
}
PRESENCE_ENV = {'SYSTEM_AES_KEY', 'JWT_SECRET', 'TENANT_AES_KEY',
                'DB_HOST', 'REDIS_ADDR', 'LOCAL_STORAGE_BASE_DIR'}


class Failure(RuntimeError):
    pass


def need(ok, check):
    if not ok:
        raise Failure(check)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=True).encode()).hexdigest()


def emit(value):
    print(json.dumps(value, ensure_ascii=True, sort_keys=True, allow_nan=False), flush=True)


def bounded_run(args, timeout=25, stdout_limit=10485760, stderr_limit=65536):
    """Bound both pipe buffers, including while a child is still running."""
    proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, close_fds=True)
    buffers = [bytearray(), bytearray()]
    overflow = threading.Event()
    read_error = threading.Event()

    def reader(pipe, index, limit):
        try:
            while True:
                block = pipe.read(4096)
                if not block:
                    return
                if len(buffers[index]) + len(block) > limit:
                    overflow.set()
                    return
                buffers[index].extend(block)
        except Exception:
            read_error.set()
        finally:
            pipe.close()

    workers = [threading.Thread(target=reader, args=(proc.stdout, 0, stdout_limit), daemon=True),
               threading.Thread(target=reader, args=(proc.stderr, 1, stderr_limit), daemon=True)]
    for thread in workers:
        thread.start()
    end = time.monotonic() + timeout
    expired = False
    try:
        while proc.poll() is None:
            if overflow.is_set() or read_error.is_set():
                break
            if time.monotonic() >= end:
                expired = True
                break
            time.sleep(.02)
    finally:
        # Only terminate our own bounded kubectl reader, never a workload/build.
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=5)
        for thread in workers:
            thread.join(timeout=2)
    need(not expired, 'kubectl_read_deadline')
    need(not overflow.is_set(), 'kubectl_output_limit')
    need(not read_error.is_set() and not any(t.is_alive() for t in workers), 'kubectl_pipe_read_failed')
    return proc.returncode, bytes(buffers[0]), bytes(buffers[1])


class Kube:
    def __init__(self, executable):
        self.executable = executable

    def get(self, kind, name=None, selector=None):
        need(kind in KINDS, 'resource_not_allowlisted')
        need(name is None or bool(NAME.fullmatch(name)), 'unsafe_resource_name')
        need(selector is None or bool(re.fullmatch(
            r'[A-Za-z0-9_./-]+=[A-Za-z0-9_.-]*(?:,[A-Za-z0-9_./-]+=[A-Za-z0-9_.-]*)*', selector)),
            'unsafe_label_selector')
        args = [self.executable, '--kubeconfig=/etc/kubernetes/admin.conf', '--request-timeout=20s']
        if kind not in CLUSTER_KINDS:
            args += ['-n', NS]
        args += ['get', kind]
        if name is not None:
            args.append(name)
        if selector is not None:
            args += ['-l', selector]
        args += ['-o', 'json', '--chunk-size=200']
        emit(dict(replica_inventory='READ_ONLY', resource=kind, named_object=name is not None))
        rc, out, err = bounded_run(args)
        need(rc == 0, 'kubectl_get_failed')
        try:
            value = json.loads(out)
        except (ValueError, UnicodeError):
            raise Failure('kubectl_invalid_json')
        need(isinstance(value, dict), 'kubectl_wrong_json_shape')
        if name is None:
            need(isinstance(value.get('items'), list) and len(value['items']) <= 512,
                 'kubectl_list_shape_or_count')
        else:
            need(value.get('metadata', {}).get('name') == name, 'kubectl_identity_mismatch')
        return value


def meta(obj):
    m = obj.get('metadata', {})
    return dict(name=m.get('name'), uid=m.get('uid'), generation=m.get('generation'))


def fingerprint(obj):
    """Do not treat ordinary resourceVersion/status timestamps as config drift."""
    if 'items' in obj:
        return digest(sorted(fingerprint(item) for item in obj['items']))
    value = dict(identity=meta(obj), spec=obj.get('spec'), deleting=bool(
        obj.get('metadata', {}).get('deletionTimestamp')))
    status = obj.get('status', {})
    value['health'] = {key: status.get(key) for key in (
        'phase', 'observedGeneration', 'replicas', 'updatedReplicas', 'readyReplicas',
        'availableReplicas', 'unavailableReplicas')}
    value['conditions'] = sorted((x.get('type', ''), x.get('status', ''))
                                 for x in status.get('conditions', []))
    value['containers'] = [{key: x.get(key) for key in ('name', 'ready', 'restartCount', 'imageID')}
                           for x in status.get('containerStatuses', [])]
    if 'subsets' in obj:  # Endpoints have no spec.
        value['endpoints'] = obj['subsets']
    return digest(value)


def owned(obj, kind, uids):
    return any(x.get('controller') is True and x.get('kind') == kind and x.get('uid') in uids
               for x in obj.get('metadata', {}).get('ownerReferences', []))


def label_selector(value):
    need(not value.get('matchExpressions'), 'deployment_expression_selector_requires_review')
    labels = value.get('matchLabels', {})
    need(isinstance(labels, dict) and 0 < len(labels) <= 20, 'deployment_selector_missing')
    need(all(isinstance(k, str) and isinstance(v, str) and LABEL.fullmatch(k)
             and (not v or re.fullmatch(r'[A-Za-z0-9_.-]+', v)) for k, v in labels.items()),
         'deployment_selector_unsupported')
    return ','.join(k + '=' + v for k, v in sorted(labels.items()))


def env_projection(container):
    result = {}
    entries = container.get('env', [])
    for key in sorted(INT_ENV | set(ENUM_ENV) | PRESENCE_ENV):
        matches = [x for x in entries if x.get('name') == key]
        if len(matches) != 1:
            result[key] = dict(source='DUPLICATE_REVIEW' if matches else 'NOT_EXPLICIT')
            continue
        item = matches[0]
        if 'valueFrom' in item:
            ref = item['valueFrom']
            kind = next((x for x in ('secretKeyRef', 'configMapKeyRef', 'fieldRef', 'resourceFieldRef')
                         if x in ref), 'UNKNOWN_REFERENCE')
            result[key] = dict(source=kind, binding_sha256=digest(ref))
        else:
            value = item.get('value', '')
            projected = dict(source='LITERAL', nonempty=bool(value))
            if key in INT_ENV:
                if isinstance(value, str) and re.fullmatch(r'[0-9]{1,6}', value):
                    projected['value'] = int(value)
                else:
                    projected['value_class'] = 'UNRECOGNIZED_REDACTED'
            elif key in ENUM_ENV:
                if value in ENUM_ENV[key]:
                    projected['value'] = value
                else:
                    projected['value_class'] = 'UNRECOGNIZED_REDACTED'
            result[key] = projected
    return result


def resources(value):
    return {group: {key: val for key, val in value.get(group, {}).items()
                    if key in ('cpu', 'memory', 'ephemeral-storage') and isinstance(val, str)
                    and QUANTITY.fullmatch(val)} for group in ('requests', 'limits')}


def scheduling(spec):
    node_selector = spec.get('nodeSelector', {})
    return dict(node_name=spec.get('nodeName'),
                hostname_selector=node_selector.get('kubernetes.io/hostname'),
                other_node_selector_keys=sorted(k for k in node_selector if k != 'kubernetes.io/hostname'),
                affinity_present=bool(spec.get('affinity')),
                affinity_sha256=digest(spec.get('affinity', {})),
                topology_spread_constraints=len(spec.get('topologySpreadConstraints', [])),
                tolerations_count=len(spec.get('tolerations', [])))


def pod_projection(pod):
    status = pod.get('status', {})
    return dict(meta(pod), node=pod.get('spec', {}).get('nodeName'),
                phase=status.get('phase'), deleting=bool(pod.get('metadata', {}).get('deletionTimestamp')),
                ready=any(x.get('type') == 'Ready' and x.get('status') == 'True'
                          for x in status.get('conditions', [])),
                containers=[dict(name=x.get('name'), ready=x.get('ready'), restarts=x.get('restartCount'),
                                 image_id=x.get('imageID')) for x in status.get('containerStatuses', [])])


def storage_projection(claim, pv):
    cs, ps = claim['spec'], pv['spec']
    ref = ps.get('claimRef', {})
    need(ref.get('uid') == claim['metadata']['uid'] and ref.get('name') == claim['metadata']['name']
         and ref.get('namespace') == NS, 'pv_claim_binding_mismatch')
    backends = [x for x in ('local', 'hostPath', 'nfs', 'csi', 'iscsi', 'cephfs') if x in ps]
    terms = ps.get('nodeAffinity', {}).get('required', {}).get('nodeSelectorTerms', [])
    hostnames = sorted({v for t in terms for e in t.get('matchExpressions', [])
                        if e.get('key') == 'kubernetes.io/hostname' and e.get('operator') == 'In'
                        for v in e.get('values', [])})
    return dict(pvc=meta(claim), pv=meta(pv), pvc_phase=claim.get('status', {}).get('phase'),
                backend=backends[0] if len(backends) == 1 else 'UNKNOWN',
                pvc_access_modes=cs.get('accessModes', []), pv_access_modes=ps.get('accessModes', []),
                storage_class=cs.get('storageClassName'), volume_mode=cs.get('volumeMode', 'Filesystem'),
                node_affinity_present=bool(ps.get('nodeAffinity')),
                node_affinity_hostname_values=hostnames,
                node_affinity_sha256=digest(ps.get('nodeAffinity', {})),
                live_data_paths_read=False, cross_node_mount_test='NOT_PERFORMED')


def collect(kube):
    tracked = []
    unavailable = []
    deadline = time.monotonic() + 240

    def read(kind, name=None, selector=None, optional=False):
        need(time.monotonic() < deadline, 'inventory_total_deadline')
        try:
            value = kube.get(kind, name, selector)
        except Failure:
            if not optional:
                raise
            unavailable.append(kind + ('/' + name if name else ''))
            return None
        tracked.append((kind, name, selector, fingerprint(value)))
        return value

    deploy = read('deployments', APP)
    spec = deploy['spec']
    template = spec['template']['spec']
    select = label_selector(spec['selector'])
    replica_sets = read('replicasets', selector=select)['items']
    rs_uids = {x['metadata']['uid'] for x in replica_sets if owned(x, 'Deployment', {deploy['metadata']['uid']})}
    selected_pods = read('pods', selector=select)['items']
    pods = [x for x in selected_pods if owned(x, 'ReplicaSet', rs_uids)]
    need(len(pods) <= 32, 'unexpected_app_pod_count')
    containers = template.get('containers', [])
    app = [x for x in containers if x.get('name') == 'app']
    need(len(app) == 1, 'app_container_not_unique')
    env = env_projection(app[0])
    storage, volumes = [], []
    # Capture mounts from all containers, including init containers.
    all_containers = containers + template.get('initContainers', [])
    need(len(all_containers) <= 16 and len(template.get('volumes', [])) <= 32,
         'inventory_template_count_limit')
    for vol in template.get('volumes', []):
        claim_name = vol.get('persistentVolumeClaim', {}).get('claimName')
        kind = next((x for x in ('persistentVolumeClaim', 'hostPath', 'emptyDir', 'configMap',
                                'secret', 'projected', 'csi') if x in vol), 'UNKNOWN')
        mounts = [dict(container=c['name'], path=x.get('mountPath'), read_only=x.get('readOnly', False),
                       subpath_present=bool(x.get('subPath') or x.get('subPathExpr')))
                  for c in all_containers for x in c.get('volumeMounts', []) if x.get('name') == vol['name']]
        volumes.append(dict(name=vol['name'], kind=kind, pvc=claim_name, mounts=mounts))
        if claim_name and not any(x['pvc']['name'] == claim_name for x in storage):
            claim = read('persistentvolumeclaims', claim_name)
            pv_name = claim['spec'].get('volumeName')
            need(pv_name, 'app_pvc_not_bound')
            storage.append(storage_projection(claim, read('persistentvolumes', pv_name)))
    services = read('services')['items']
    labels = spec['template'].get('metadata', {}).get('labels', {})
    routes = []
    for service in services:
        ss = service.get('spec', {})
        selector = ss.get('selector', {})
        if not selector or not all(labels.get(k) == v for k, v in selector.items()):
            continue
        need(len(routes) < 8, 'inventory_route_count_limit')
        ep = read('endpoints', service['metadata']['name'], optional=True)
        refs = []
        if ep:
            for subset in ep.get('subsets', []):
                for key in ('addresses', 'notReadyAddresses'):
                    for address in subset.get(key, []):
                        ref = address.get('targetRef', {})
                        refs.append(dict(kind=ref.get('kind'), uid=ref.get('uid'), ready=key == 'addresses'))
        routes.append(dict(service=meta(service), type=ss.get('type'),
                           session_affinity=ss.get('sessionAffinity', 'None'), endpoint_targets=refs))
    hpas = read('horizontalpodautoscalers', optional=True)
    pdbs = read('poddisruptionbudgets', optional=True)
    policies = read('networkpolicies', optional=True)
    nodes = []
    for name in NODES:
        node = read('nodes', name, optional=True)
        if node:
            ns = node.get('status', {})
            nodes.append(dict(identity=meta(node), unschedulable=node.get('spec', {}).get('unschedulable', False),
                taints=[dict(key=x.get('key'), effect=x.get('effect')) for x in node.get('spec', {}).get('taints', [])],
                conditions={x['type']: x.get('status') for x in ns.get('conditions', [])
                            if x.get('type') in ('Ready', 'MemoryPressure', 'DiskPressure', 'PIDPressure')},
                allocatable={k: v for k, v in ns.get('allocatable', {}).items()
                             if k in ('cpu', 'memory', 'ephemeral-storage', 'pods') and isinstance(v, str)
                             and QUANTITY.fullmatch(v)}, actual_utilization='NOT_SAMPLED'))
    changed = []
    for kind, name, selector, before in tracked:
        need(time.monotonic() < deadline, 'inventory_total_deadline')
        try:
            after = kube.get(kind, name, selector)
            if fingerprint(after) != before:
                changed.append(kind + '/' + (name or 'selected-list'))
        except Failure:
            unavailable.append('recheck:' + kind + ('/' + name if name else ''))
    findings = []
    if any(x['backend'] in ('local', 'hostPath') for x in storage) or any(x['kind'] == 'hostPath' for x in volumes):
        findings.append('NODE_LOCAL_STORAGE_REQUIRES_MIGRATION_OR_ARCHITECTURE_CHANGE')
    if any('ReadWriteOnce' in x['pvc_access_modes'] or 'ReadWriteOncePod' in x['pvc_access_modes'] for x in storage):
        findings.append('VOLUME_ACCESS_MODES_REQUIRE_REVIEW_RWO_IS_NOT_SINGLE_POD')
    if any(x['kind'] == 'emptyDir' for x in volumes):
        findings.append('EMPTYDIR_USAGE_REQUIRES_TRANSIENT_VS_PERSISTENT_REVIEW')
    if template.get('nodeName') or template.get('nodeSelector') or template.get('affinity'):
        findings.append('APP_SCHEDULING_CONSTRAINTS_REQUIRE_REVIEW')
    if env['AUTO_MIGRATE'].get('value') != 'false':
        findings.append('AUTO_MIGRATE_FALSE_NOT_CONFIRMED_IN_TEMPLATE_LITERAL')
    if changed:
        findings.append('BASELINE_DRIFT_DO_NOT_USE_FOR_CHANGE_PLAN')
    if len(pods) != len(selected_pods):
        findings.append('SELECTOR_MATCHES_NONOWNED_PODS')
    pools = {k: env[v].get('value') for k, v in POOL_ENV.items()}
    complete_slots = all(isinstance(x, int) and x > 0 for x in pools.values())
    status = deploy.get('status', {})
    return dict(
        collection='INCOMPLETE' if unavailable else 'COMPLETE', ready_to_scale=False,
        production_writes=0, provider_requests=0, pod_exec=False, files_modified_by_script=False,
        database_connections=0, redis_connections=0, secret_objects_read=False, configmap_contents_read=False,
        baseline_stable=not changed and not unavailable, changed_resources=changed,
        unavailable_resources=sorted(set(unavailable)), findings=findings,
        source_behavior_binding='NOT_VERIFIED_AGAINST_RUNNING_BINARY',
        cross_node_file_access_tested=False, hpa_budget_reviewed=False,
        deployment=dict(identity=meta(deploy), spec_sha256=digest(spec), desired=spec.get('replicas', 1),
            current=status.get('replicas'), updated=status.get('updatedReplicas'), ready=status.get('readyReplicas'),
            available=status.get('availableReplicas'), observed_generation=status.get('observedGeneration'),
            strategy=spec.get('strategy'), scheduling=scheduling(template),
            termination_grace_seconds=template.get('terminationGracePeriodSeconds'),
            image=app[0].get('image'), matches_last_reported_candidate=app[0].get('image') == KNOWN_IMAGE,
            resources=resources(app[0].get('resources', {})), environment=env,
            env_from_unresolved_count=len(app[0].get('envFrom', [])),
            configuration_scope='POD_TEMPLATE_ONLY_NOT_EFFECTIVE_RUNTIME_OR_DATABASE_SETTINGS'),
        pools_template_literals=pools,
        per_replica_template_slots=sum(pools.values()) if complete_slots else None,
        hypothetical_two_identical_replicas_slots=2 * sum(pools.values()) if complete_slots else None,
        capacity_arithmetic_is_not_a_proposed_change=True,
        pods=[pod_projection(p) for p in pods], unowned_selector_pods=len(selected_pods) - len(pods),
        last_reported_pod_uid_present=any(p['metadata']['uid'] == KNOWN_POD_UID for p in pods),
        volumes=volumes, persistent_storage=storage, routes=routes, nodes=nodes,
        app_hpas=[dict(identity=meta(h), minimum=h['spec'].get('minReplicas'), maximum=h['spec'].get('maxReplicas'))
                  for h in (hpas or {}).get('items', []) if h.get('spec', {}).get('scaleTargetRef', {}).get('name') == APP
                  and h['spec']['scaleTargetRef'].get('kind') == 'Deployment'],
        namespace_pdb_count=len(pdbs['items']) if pdbs else None,
        namespace_network_policy_count=len(policies['items']) if policies else None,
        remaining_gates=['ACTUAL_CROSS_NODE_STORAGE_AND_EXISTING_QUEUED_FILE_ACCESS',
            'DATABASE_REDIS_CONNECTION_BUDGET_AND_LIVE_NODE_HEADROOM',
            'EXACT_BINARY_SOURCE_BINDING_AND_CROSS_INSTANCE_SCHEDULING_RECOVERY_CACHE_REVIEW',
            'SHARED_AUTH_ENCRYPTION_STREAM_STATE_AND_NETWORK_POLICY_REVIEW',
            'PER_INSTANCE_VS_CLUSTER_SLOT_AND_MODEL_LIMIT_BUDGET',
            'ISOLATED_TWO_REPLICA_CONSUMPTION_AND_RESTART_ACCEPTANCE',
            'APPROVED_MAINTENANCE_MIGRATION_AND_ROLLBACK_PLAN'],
        utc_finished=datetime.datetime.now(datetime.timezone.utc).isoformat())


def trusted_executable():
    path = shutil.which('kubectl', path='/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin')
    need(path, 'kubectl_not_found_in_system_paths')
    path = os.path.realpath(path)
    current = path
    while True:
        info = os.stat(current)
        need(info.st_uid == 0 and not info.st_mode & 0o022, 'kubectl_path_not_root_controlled')
        if current == path:
            need(stat.S_ISREG(info.st_mode), 'kubectl_not_regular')
        else:
            need(stat.S_ISDIR(info.st_mode), 'kubectl_ancestor_not_directory')
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return path


def main():
    try:
        need(sys.platform == 'linux' and os.geteuid() == 0, 'linux_root_required')
        need(socket.gethostname().split('.')[0] == 'datax-01', 'unexpected_node')
        emit(dict(replica_inventory='START', mode='KUBERNETES_GET_ONLY', ready_to_scale=False))
        result = collect(Kube(trusted_executable()))
        emit(result)
        return 0 if result['collection'] == 'COMPLETE' and result['baseline_stable'] else 1
    except Exception as error:
        emit(dict(collection='INCOMPLETE', ready_to_scale=False, production_writes=0,
                  provider_requests=0, failed_check=str(error) if isinstance(error, Failure)
                  else 'SAFE_DIAGNOSTIC_REQUIRED', error_type=type(error).__name__, raw_error_printed=False))
        return 1


if __name__ == '__main__':
    sys.exit(main())
