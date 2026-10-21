import ast
import copy
from pathlib import Path
import unittest
from unittest.mock import patch

import weknora_tune_placement as m


def fixture():
    volumes = [{'name': 'enterprise-ca', 'secret': {'secretName': 'not-read'}},
               {'name': 'config', 'configMap': {'name': 'not-read'}}]
    mounts = []; objects = {}
    for name, path, ro, claim, cuid, pv, puid in m.VOLUMES:
        volumes.append({'name': name, 'persistentVolumeClaim': {'claimName': claim}})
        mounts.append({'name': name, 'mountPath': path, 'readOnly': ro})
        objects[claim] = {'metadata': {'name': claim, 'uid': cuid}, 'status': {'phase': 'Bound'}, 'spec': {
            'volumeName': pv, 'accessModes': ['ReadWriteOnce'], 'volumeMode': 'Filesystem', 'storageClassName': 'deerflow-weknora-local'}}
        objects[pv] = {'metadata': {'name': pv, 'uid': puid}, 'status': {'phase': 'Bound'}, 'spec': {
            'accessModes': ['ReadWriteOnce'], 'volumeMode': 'Filesystem', 'storageClassName': 'deerflow-weknora-local',
            'claimRef': {'name': claim, 'uid': cuid, 'namespace': 'deer-flow'}, 'local': {'path': '/private/data'},
            'nodeAffinity': {'required': {'nodeSelectorTerms': [{'matchExpressions': [
                {'key': 'kubernetes.io/hostname', 'operator': 'In', 'values': [m.NODE]}]}]}}}}
    spec = {'schedulerName': 'default-scheduler', 'volumes': volumes, 'containers': [
        {'name': 'app', 'image': m.IMAGE, 'imagePullPolicy': 'Never', 'volumeMounts': mounts}]}
    d = {'metadata': {'uid': m.audit.DEP_UID}, 'spec': {'template': {'spec': spec}}}
    p = {'spec': {'nodeName': m.NODE}, 'status': {'containerStatuses': [{'name': 'app', 'imageID': m.IMAGE_ID}]}}
    n = {'metadata': {'name': m.NODE, 'uid': m.NODE_UID, 'labels': {
        'kubernetes.io/hostname': m.NODE, 'kubernetes.io/os': 'linux', 'kubernetes.io/arch': 'arm64'}},
        'spec': {}, 'status': {'conditions': [{'type': k, 'status': v} for k, v in
        [('Ready', 'True'), ('DiskPressure', 'False'), ('MemoryPressure', 'False'), ('PIDPressure', 'False')]],
        'images': [{'names': [m.IMAGE]}]}}
    return d, p, n, objects


class Tests(unittest.TestCase):
    def invoke(self, d, p, n, objects, count=1):
        with patch.object(m.audit, 'named', side_effect=lambda kind, name: objects[name]), \
             patch.object(m.obs, 'get', return_value={'items': [n]*count}):
            return m.check(d, p)

    def test_python37(self):
        ast.parse(Path(m.__file__).read_text(), feature_version=(3, 7))

    def test_pv_pin_without_deployment_pin_passes(self):
        result = self.invoke(*fixture())
        self.assertEqual(result['replacement_node'], 'datax-03')
        self.assertEqual(result['pin_authority'], 'TWO_BOUND_LOCAL_PV_NODE_AFFINITIES')
        self.assertFalse(result['image_content_rehashed'])
        self.assertEqual(len(result['bindings']), 2)
        self.assertNotIn('/private/data', str(result))

    def test_gate_never_mutates_inputs(self):
        data = fixture(); original = copy.deepcopy(data)
        self.invoke(*data)
        self.assertEqual(data, original)

    def test_cordoned_node(self):
        d, p, n, objects = fixture(); n['spec']['unschedulable'] = True
        with self.assertRaisesRegex(m.obs.CheckFailure, 'app_node_cordoned'):
            self.invoke(d, p, n, objects)

    def test_node_taint(self):
        d, p, n, objects = fixture(); n['spec']['taints'] = [{'key': 'maintenance', 'effect': 'NoSchedule'}]
        with self.assertRaisesRegex(m.obs.CheckFailure, 'taint_not_tolerated'):
            self.invoke(d, p, n, objects)

    def test_node_health(self):
        d, p, n, objects = fixture(); n['status']['conditions'][0]['status'] = 'False'
        with self.assertRaisesRegex(m.obs.CheckFailure, 'pressure_or_unready'):
            self.invoke(d, p, n, objects)

    def test_missing_tag(self):
        d, p, n, objects = fixture(); n['status']['images'] = []
        with self.assertRaisesRegex(m.obs.CheckFailure, 'image_cache_metadata_missing'):
            self.invoke(d, p, n, objects)

    def test_wrong_image_id(self):
        d, p, n, objects = fixture(); p['status']['containerStatuses'][0]['imageID'] = 'other'
        with self.assertRaisesRegex(m.obs.CheckFailure, 'live_image_changed'):
            self.invoke(d, p, n, objects)

    def test_duplicate_hostname(self):
        with self.assertRaisesRegex(m.obs.CheckFailure, 'hostname_label_not_unique'):
            self.invoke(*fixture(), count=2)

    def test_changed_node_uid(self):
        d, p, n, objects = fixture(); n['metadata']['uid'] = 'replaced'
        with self.assertRaisesRegex(m.obs.CheckFailure, 'node_identity_changed'):
            self.invoke(d, p, n, objects)

    def test_other_node(self):
        d, p, n, objects = fixture(); p['spec']['nodeName'] = 'datax-02'
        with self.assertRaisesRegex(m.obs.CheckFailure, 'app_node_changed'):
            self.invoke(d, p, n, objects)

    def test_new_scheduling_constraint(self):
        d, p, n, objects = fixture(); d['spec']['template']['spec']['nodeSelector'] = {'kubernetes.io/hostname': m.NODE}
        with self.assertRaisesRegex(m.obs.CheckFailure, 'template_scheduling_changed'):
            self.invoke(d, p, n, objects)

    def test_claim_uid_changed(self):
        d, p, n, objects = fixture(); objects[m.VOLUMES[0][3]]['metadata']['uid'] = 'changed'
        with self.assertRaisesRegex(m.obs.CheckFailure, 'pvc_binding_changed'):
            self.invoke(d, p, n, objects)

    def test_claim_not_bound(self):
        d, p, n, objects = fixture(); objects[m.VOLUMES[0][3]]['status']['phase'] = 'Pending'
        with self.assertRaisesRegex(m.obs.CheckFailure, 'pvc_binding_changed'):
            self.invoke(d, p, n, objects)

    def test_pv_wrong_claim(self):
        d, p, n, objects = fixture(); objects[m.VOLUMES[1][5]]['spec']['claimRef']['uid'] = 'wrong'
        with self.assertRaisesRegex(m.obs.CheckFailure, 'pv_binding_changed'):
            self.invoke(d, p, n, objects)

    def test_pv_deletion(self):
        d, p, n, objects = fixture(); objects[m.VOLUMES[0][5]]['metadata']['deletionTimestamp'] = 'now'
        with self.assertRaisesRegex(m.obs.CheckFailure, 'pv_binding_changed'):
            self.invoke(d, p, n, objects)

    def test_affinity_or_term_cannot_broaden(self):
        d, p, n, objects = fixture()
        objects[m.VOLUMES[0][5]]['spec']['nodeAffinity']['required']['nodeSelectorTerms'].append({})
        with self.assertRaisesRegex(m.obs.CheckFailure, 'pv_node_affinity_changed'):
            self.invoke(d, p, n, objects)

    def test_multiple_node_values_cannot_broaden(self):
        d, p, n, objects = fixture()
        objects[m.VOLUMES[1][5]]['spec']['nodeAffinity']['required']['nodeSelectorTerms'][0]['matchExpressions'][0]['values'].append('datax-02')
        with self.assertRaisesRegex(m.obs.CheckFailure, 'pv_node_affinity_changed'):
            self.invoke(d, p, n, objects)

    def test_missing_local_source(self):
        d, p, n, objects = fixture(); objects[m.VOLUMES[0][5]]['spec'].pop('local')
        with self.assertRaisesRegex(m.obs.CheckFailure, 'pv_binding_changed'):
            self.invoke(d, p, n, objects)

    def test_second_volume_mount_read_only_preserved(self):
        d, p, n, objects = fixture(); d['spec']['template']['spec']['containers'][0]['volumeMounts'][1]['readOnly'] = False
        with self.assertRaisesRegex(m.obs.CheckFailure, 'mount_changed'):
            self.invoke(d, p, n, objects)

    def test_storage_drift_since_prepare(self):
        data = fixture(); first = self.invoke(*data)
        data[3][m.VOLUMES[0][5]]['spec']['local']['path'] = '/different/data'
        second = self.invoke(*data)
        with self.assertRaisesRegex(m.obs.CheckFailure, 'binding_changed_since_prepare'):
            m.unchanged(first, second)


if __name__ == '__main__':
    unittest.main()
