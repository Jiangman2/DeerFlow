#!/usr/bin/env python3
"""Read-only placement follow-up, not a replacement rollout gate. Python 3.7+."""
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import stat
import sys

import observe_pipeline as obs

STATE = Path('/var/lib/weknora-tune56-20261016-r1')
FAILURE = 'RESULT-d0bec9e6980045ccb1356271520d73bf.json'
FAILURE_SHA = '8eb3beec7ec6e806c2c2903c4c2407d55ae7a1eb90b82f1eeb8cf48484ba11df'
DEP_UID = '44aca5b9-8fc1-44d6-af61-37c6d62723d5'
DEP_SPEC = '99e3d286851159e7ee7ff4d3bbda6396b3fd777c69649b26a2f6d95ae7dd3815'
SCHEDULING = ('nodeName', 'nodeSelector', 'affinity', 'tolerations',
              'topologySpreadConstraints', 'schedulerName', 'schedulingGates',
              'runtimeClassName', 'priorityClassName', 'preemptionPolicy',
              'hostNetwork', 'dnsPolicy', 'overhead')


def validate_failure(r):
    obs.require(r.get('phase') == 'READ_ONLY_PREFLIGHT'
                and r.get('failure', {}).get('check') == 'app_scheduling_not_pinned_to_reviewed_node'
                and r.get('signals_attempted') == 0
                and r.get('live_patch_attempted') is False
                and r.get('live_patch_acknowledged') is False, 'unexpected_failure_boundary')


def read_failure():
    obs.safe_parent(STATE)
    fd = os.open(str(STATE/FAILURE), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as f:
        s = os.fstat(f.fileno())
        obs.require(stat.S_ISREG(s.st_mode) and s.st_uid == 0 and s.st_nlink == 1
                    and not s.st_mode & 0o022 and s.st_size < 2097152, 'unsafe_failure_record')
        raw = f.read(2097152)
    obs.require(hashlib.sha256(raw).hexdigest() == FAILURE_SHA, 'failure_receipt_changed')
    validate_failure(json.loads(raw))
    markers = {}
    for name in ('PREPARED.json', 'ORIGINAL.private.json', 'PLAN.private.json',
                 'DRY-RUN.private.json', 'forward', 'rollback'):
        try:
            (STATE/name).lstat()
            markers[name] = True
        except FileNotFoundError:
            markers[name] = False
    obs.require(not any(markers.values()), 'operation_advanced_requires_status_review')
    return {'failure_sha256': FAILURE_SHA, 'recorded_signal_or_patch': False,
            'later_operation_markers': markers, 'original_evidence_modified': False}


def named(kind, name):
    obs.require(kind in ('persistentvolumeclaims', 'persistentvolumes', 'runtimeclasses'), 'read_kind')
    obs.require(len(name) <= 253 and re.fullmatch('[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', name) is not None, 'read_name')
    return json.loads(obs.call(['get', kind, name, '--show-managed-fields=false', '-o', 'json']))


def placement(spec):
    return {k: spec[k] for k in SCHEDULING if k in spec}


def volumes(spec):
    out = []
    for v in spec.get('volumes', []):
        row = {'name': v['name'], 'source_types': sorted(set(v)-{'name'})}
        if 'persistentVolumeClaim' in v:
            claim = v['persistentVolumeClaim']['claimName']
            c = named('persistentvolumeclaims', claim)
            cs = c['spec']; status = c.get('status', {})
            row['pvc'] = {'name': claim, 'uid': c['metadata']['uid'],
                          'phase': status.get('phase'), 'read_only': v['persistentVolumeClaim'].get('readOnly', False),
                          'access_modes': cs.get('accessModes'), 'storage_class': cs.get('storageClassName'),
                          'volume_mode': cs.get('volumeMode'), 'requested_storage': cs.get('resources', {}).get('requests', {}).get('storage')}
            pv = cs.get('volumeName')
            if pv:
                p = named('persistentvolumes', pv); ps = p['spec']
                row['pv'] = {'name': pv, 'uid': p['metadata']['uid'], 'phase': p.get('status', {}).get('phase'),
                             'access_modes': ps.get('accessModes'), 'node_affinity': ps.get('nodeAffinity', {}),
                             'claim_uid_matches': ps.get('claimRef', {}).get('uid') == c['metadata']['uid'],
                             'source_types': [k for k in ('local', 'hostPath', 'nfs', 'csi', 'iscsi', 'rbd', 'cephfs',
                                                          'flexVolume', 'glusterfs') if k in ps],
                             'csi_driver': ps.get('csi', {}).get('driver')}
        out.append(row)
    return out


def label_keys(value):
    """Select metadata labels needed to interpret placement, not all node labels."""
    result = {'kubernetes.io/hostname', 'kubernetes.io/arch', 'kubernetes.io/os'}
    def walk(v):
        if isinstance(v, dict):
            for key in ('nodeSelector', 'matchLabels'):
                result.update(v.get(key, {}))
            if isinstance(v.get('key'), str):
                result.add(v['key'])
            if isinstance(v.get('topologyKey'), str):
                result.add(v['topologyKey'])
            for child in v.values():
                walk(child)
        elif isinstance(v, list):
            for child in v:
                walk(child)
    walk(value)
    return result


def node_projection(n, keys, image):
    s = n.get('status', {}); meta = n['metadata']
    return {'node': meta['name'], 'uid': meta['uid'],
            'labels': {k: v for k, v in meta.get('labels', {}).items() if k in keys},
            'unschedulable': n.get('spec', {}).get('unschedulable', False),
            'taints': n.get('spec', {}).get('taints', []),
            'conditions': [{'type': c['type'], 'status': c['status']} for c in s.get('conditions', [])
                           if c['type'] in ('Ready', 'MemoryPressure', 'DiskPressure', 'PIDPressure')],
            'allocatable': {k: s.get('allocatable', {}).get(k) for k in ('cpu', 'memory', 'ephemeral-storage', 'pods')},
            'exact_image_tag_reported': any(image in x.get('names', []) for x in s.get('images', [])),
            'image_content_verified': False, 'eligible_for_restart': 'NOT_YET_EVALUATED'}


def audit():
    obs.emit({'failure_boundary': read_failure()})
    d = obs.get('deployments', 'weknora-app')
    obs.require(d['metadata']['uid'] == DEP_UID and obs.digest(d['spec']) == DEP_SPEC,
                'live_deployment_changed_requires_review')
    ps = obs.owned(d)
    obs.require(len(ps) == 1 and obs.ready(ps[0]) and not ps[0]['metadata'].get('deletionTimestamp'),
                'one_ready_app_required')
    p = ps[0]; spec = d['spec']['template']['spec']; c = obs.container(p)
    obs.emit({'deployment': {'name': 'weknora-app', 'uid': DEP_UID,
        'spec_sha256': DEP_SPEC, 'generation': d['metadata']['generation'],
        'replicas': d['spec'].get('replicas'), 'strategy': d['spec'].get('strategy')},
        'template_scheduling': placement(spec), 'live_pod_scheduling': placement(p['spec']),
        'pod': {'name': p['metadata']['name'], 'identity': obs.identity(p),
                'images': [{'name': x['name'], 'imageID': x.get('imageID')} for x in p['status'].get('containerStatuses', [])]},
        'containers': [{k: x[k] for k in ('name', 'image', 'imagePullPolicy', 'resources', 'volumeMounts') if k in x}
                       for x in spec.get('containers', [])],
        'init_containers': [{k: x[k] for k in ('name', 'image', 'imagePullPolicy', 'resources', 'volumeMounts') if k in x}
                            for x in spec.get('initContainers', [])]})
    vs = volumes(spec)
    obs.emit({'volume_placement': vs, 'volume_contents_read': False})
    rc = {}
    if spec.get('runtimeClassName'):
        obj = named('runtimeclasses', spec['runtimeClassName'])
        rc = {k: obj[k] for k in ('handler', 'scheduling', 'overhead') if k in obj}
        obs.emit({'runtime_class': rc})
    keys = label_keys([placement(spec), placement(p['spec']), vs, rc])
    ns = obs.get('nodes')['items']
    obs.require(len(ns) <= 100, 'node_inventory_limit')
    for n in sorted(ns, key=lambda x: x['metadata']['name']):
        obs.emit(node_projection(n, keys, c['image']))
    after = obs.get('deployments', 'weknora-app'); apps = obs.owned(after)
    obs.require(after['metadata']['uid'] == DEP_UID and obs.digest(after['spec']) == DEP_SPEC
                and len(apps) == 1 and obs.identity(apps[0]) == obs.identity(p) and obs.ready(apps[0]),
                'baseline_changed_during_audit')
    read_failure()  # Do not report the original no-action boundary if another operator advanced it.
    obs.emit({'scheduling_audit': 'COMPLETE', 'baseline_stable': True, 'files_modified': False,
              'production_writes': 0, 'signals_sent': 0, 'pod_exec': False, 'provider_requests': 0,
              'ready_to_apply': False, 'image_cache_is_metadata_only': True})


def main():
    try:
        obs.require(sys.platform == 'linux' and os.geteuid() == 0
                    and socket.gethostname().split('.')[0] == 'datax-01', 'wrong_execution_host')
        audit()
        return 0
    except Exception as exc:
        obs.emit({'scheduling_audit': 'INCOMPLETE', 'failure': obs.failure(exc),
                  'production_writes': 0, 'signals_sent': 0, 'provider_requests': 0,
                  'files_modified': False, 'automatic_retry': False, 'ready_to_apply': False})
        return 1


if __name__ == '__main__':
    sys.exit(main())
