"""Exact reviewed local-PV placement, no scheduler/storage mutations. Python 3.7+."""
import observe_pipeline as obs
import audit_weknora_scheduling as audit

NODE = 'datax-03'
NODE_UID = '9acb9556-0d63-46ca-8a6f-384c4b469a18'
IMAGE = 'deerflow.local/weknora-app:0.8.0-enterprise-proxy1-anydoc1-arm64-20260912'
IMAGE_ID = 'sha256:93d6900e88edcd1b5f389c5756f849b34f5051d832a72b0bc17b0af86c709194'
VOLUMES = (
    ('data-files', '/data/files', False, 'weknora-data-files', '39706377-253b-4958-bdd9-7cb36d1370bc',
     'deerflow-weknora-files-pv-datax-03', '744ba5bc-7a88-4719-a21a-7ceb16959c0f'),
    ('docreader-tmp', '/tmp/docreader', True, 'weknora-docreader-tmp', 'b6d3a3c5-1761-4499-9f1b-58753be35e36',
     'deerflow-weknora-docreader-pv-datax-03', '965fe7da-6514-45bb-abb4-5cf95d6521db'),
)


def volume_binding(volume, mount, expected):
    vname, path, read_only, claim, claim_uid, pvname, pv_uid = expected
    obs.require(volume.get('name') == vname and set(volume) == {'name', 'persistentVolumeClaim'}
                and volume['persistentVolumeClaim'].get('claimName') == claim
                and not volume['persistentVolumeClaim'].get('readOnly', False), 'reviewed_pvc_mount_changed')
    obs.require(mount.get('name') == vname and mount.get('mountPath') == path
                and mount.get('readOnly', False) == read_only
                and not mount.get('subPath') and not mount.get('subPathExpr'), 'reviewed_mount_changed')
    pvc = audit.named('persistentvolumeclaims', claim); cs = pvc['spec']
    obs.require(pvc['metadata']['uid'] == claim_uid and not pvc['metadata'].get('deletionTimestamp')
                and pvc.get('status', {}).get('phase') == 'Bound' and cs.get('volumeName') == pvname
                and cs.get('accessModes') == ['ReadWriteOnce'] and cs.get('volumeMode', 'Filesystem') == 'Filesystem'
                and cs.get('storageClassName') == 'deerflow-weknora-local', 'reviewed_pvc_binding_changed')
    pv = audit.named('persistentvolumes', pvname); ps = pv['spec']; ref = ps.get('claimRef', {})
    obs.require(pv['metadata']['uid'] == pv_uid and not pv['metadata'].get('deletionTimestamp')
                and pv.get('status', {}).get('phase') == 'Bound'
                and ref.get('uid') == claim_uid and ref.get('name') == claim and ref.get('namespace') == 'deer-flow'
                and ps.get('accessModes') == ['ReadWriteOnce'] and ps.get('volumeMode', 'Filesystem') == 'Filesystem'
                and ps.get('storageClassName') == 'deerflow-weknora-local'
                and isinstance(ps.get('local', {}).get('path'), str) and ps['local']['path'].startswith('/'),
                'reviewed_pv_binding_changed')
    expected_affinity = {'required': {'nodeSelectorTerms': [{'matchExpressions': [
        {'key': 'kubernetes.io/hostname', 'operator': 'In', 'values': [NODE]}]}]}}
    # Exact form is intentional. An additional OR term, broader values, or an
    # unreviewed volume type must never count as the same single-node pin.
    obs.require(ps.get('nodeAffinity') == expected_affinity, 'reviewed_pv_node_affinity_changed')
    return {'volume': vname, 'pvc': claim, 'pvc_uid': claim_uid, 'pvc_spec_sha256': obs.digest(cs),
            'pv': pvname, 'pv_uid': pv_uid, 'pv_spec_sha256': obs.digest(ps)}


def check(d, app):
    s = d['spec']['template']['spec']
    obs.require(d['metadata']['uid'] == audit.DEP_UID and app['spec'].get('nodeName') == NODE,
                'reviewed_app_node_changed')
    obs.require(s.get('schedulerName', 'default-scheduler') == 'default-scheduler'
                and not any(s.get(k) for k in ('nodeName', 'nodeSelector', 'affinity', 'tolerations',
                                              'topologySpreadConstraints', 'schedulingGates', 'runtimeClassName')),
                'reviewed_template_scheduling_changed')
    cs = s.get('containers', [])
    obs.require(len(cs) == 1 and cs[0].get('name') == 'app' and cs[0].get('image') == IMAGE
                and cs[0].get('imagePullPolicy') == 'Never' and not s.get('initContainers'),
                'reviewed_image_contract_changed')
    statuses = app.get('status', {}).get('containerStatuses', [])
    obs.require(len(statuses) == 1 and statuses[0].get('name') == 'app'
                and statuses[0].get('imageID') == IMAGE_ID, 'reviewed_live_image_changed')
    vs = s.get('volumes', []); mounts = cs[0].get('volumeMounts', [])
    obs.require(len(vs) == 4 and {v['name'] for v in vs} == {'enterprise-ca', 'config', 'data-files', 'docreader-tmp'},
                'reviewed_volume_shape_changed')
    records = []
    for expected in VOLUMES:
        selected = [v for v in vs if v['name'] == expected[0]]
        selected_mounts = [v for v in mounts if v['name'] == expected[0]]
        obs.require(len(selected) == 1 and len(selected_mounts) == 1, 'reviewed_mount_missing_or_duplicate')
        records.append(volume_binding(selected[0], selected_mounts[0], expected))
    nodes = obs.get('nodes', selector='kubernetes.io/hostname='+NODE)['items']
    obs.require(len(nodes) == 1, 'hostname_label_not_unique')
    n = nodes[0]; labels = n['metadata'].get('labels', {})
    obs.require(n['metadata']['name'] == NODE and n['metadata']['uid'] == NODE_UID
                and labels.get('kubernetes.io/hostname') == NODE
                and labels.get('kubernetes.io/arch') == 'arm64' and labels.get('kubernetes.io/os') == 'linux',
                'reviewed_node_identity_changed')
    obs.require(not n.get('spec', {}).get('unschedulable'), 'app_node_cordoned')
    obs.require(not any(t.get('effect') in ('NoSchedule', 'NoExecute') for t in n.get('spec', {}).get('taints', [])),
                'app_node_taint_not_tolerated')
    conditions = {v['type']: v['status'] for v in n.get('status', {}).get('conditions', [])}
    obs.require(conditions.get('Ready') == 'True' and all(conditions.get(k) == 'False' for k in
                ('DiskPressure', 'MemoryPressure', 'PIDPressure')), 'app_node_pressure_or_unready')
    obs.require(any(IMAGE in v.get('names', []) for v in n.get('status', {}).get('images', [])),
                'image_cache_metadata_missing_requires_review')
    return {'replacement_node': NODE, 'node_uid': NODE_UID, 'existing_scheduling_preserved': True,
            'pin_authority': 'TWO_BOUND_LOCAL_PV_NODE_AFFINITIES', 'bindings': records,
            'image': IMAGE, 'current_image_id': IMAGE_ID, 'image_tag_in_node_metadata': True,
            'image_content_rehashed': False, 'replacement_exact_image_id_required': True}


def unchanged(expected, actual):
    obs.require(expected == actual, 'placement_or_volume_binding_changed_since_prepare')
