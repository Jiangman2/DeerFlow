import ast
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import audit_weknora_scheduling as m


class Tests(unittest.TestCase):
    def test_syntax37(self):
        ast.parse(Path(m.__file__).read_text(), feature_version=(3, 7))

    def test_failure_exact_no_action(self):
        r = {'phase': 'READ_ONLY_PREFLIGHT', 'failure': {'check': 'app_scheduling_not_pinned_to_reviewed_node'},
             'signals_attempted': 0, 'live_patch_attempted': False, 'live_patch_acknowledged': False}
        m.validate_failure(r)
        for key, bad in (('signals_attempted', 1), ('live_patch_attempted', True), ('phase', 'DRAIN')):
            x = copy.deepcopy(r); x[key] = bad
            with self.assertRaises(m.obs.CheckFailure):
                m.validate_failure(x)

    def test_no_env_args_annotations_in_projection(self):
        s = {'nodeSelector': {'role': 'worker'}, 'containers': [{'env': ['SECRET']}], 'command': ['PRIVATE'], 'annotations': {'key': 'PRIVATE'}}
        self.assertEqual(m.placement(s), {'nodeSelector': {'role': 'worker'}})

    def test_volume_sensitive_details_not_exported(self):
        objects = [{'metadata': {'uid': 'c'}, 'spec': {'volumeName': 'pv', 'accessModes': ['ReadWriteOnce']}, 'status': {'phase': 'Bound'}},
                   {'metadata': {'uid': 'p'}, 'spec': {'claimRef': {'uid': 'c'}, 'csi': {'driver': 'driver', 'volumeHandle': 'SENSITIVE'},
                    'nodeAffinity': {'required': {'nodeSelectorTerms': [{'matchExpressions': [{'key': 'disk', 'operator': 'In', 'values': ['one']}]}]}}}}]
        with patch.object(m, 'named', side_effect=objects):
            rows = m.volumes({'volumes': [{'name': 'data', 'persistentVolumeClaim': {'claimName': 'claim'}}]})
        self.assertTrue(rows[0]['pv']['claim_uid_matches'])
        self.assertNotIn('SENSITIVE', json.dumps(rows))
        self.assertIn('disk', m.label_keys(rows))

    def test_secret_volume_contents_not_read(self):
        with patch.object(m, 'named') as read:
            rows = m.volumes({'volumes': [{'name': 'ca', 'secret': {'secretName': 'PRIVATE'}}]})
        read.assert_not_called()
        self.assertEqual(rows, [{'name': 'ca', 'source_types': ['secret']}])

    def test_topology_and_labels_preserved(self):
        keys = m.label_keys({'nodeSelector': {'custom-role': 'worker'}, 'affinity': {'nodeAffinity': {
            'requiredDuringSchedulingIgnoredDuringExecution': {'nodeSelectorTerms': [{'matchExpressions': [{'key': 'zone'}]}]}}},
            'topologySpreadConstraints': [{'topologyKey': 'rack'}]})
        self.assertTrue({'zone', 'rack', 'custom-role', 'kubernetes.io/hostname'} <= keys)

    def test_node_cache_not_verification(self):
        n = {'metadata': {'name': 'node', 'uid': 'uid', 'labels': {'role': 'worker', 'PRIVATE': 'hide'}},
             'status': {'images': [{'names': ['image']}], 'allocatable': {'cpu': '32'}}}
        row = m.node_projection(n, {'role'}, 'image')
        self.assertTrue(row['exact_image_tag_reported'])
        self.assertFalse(row['image_content_verified'])
        self.assertEqual(row['eligible_for_restart'], 'NOT_YET_EVALUATED')
        self.assertNotIn('PRIVATE', json.dumps(row))

    def test_named_only_read_allowlist(self):
        with patch.object(m.obs, 'call', side_effect=AssertionError('unexpected external read')):
            with self.assertRaises(m.obs.CheckFailure):
                m.named('secrets', 'credential')
            with self.assertRaises(m.obs.CheckFailure):
                m.named('persistentvolumes', '--all')
        with patch.object(m.obs, 'call', return_value=b'{}') as call:
            m.named('persistentvolumes', 'data')
        self.assertEqual(call.call_args[0][0][0], 'get')

    def test_wrong_host_does_not_read_cluster(self):
        with patch.object(m.sys, 'platform', 'win32'), patch.object(m, 'audit') as audit, patch.object(m.obs, 'emit'):
            self.assertEqual(m.main(), 1)
        audit.assert_not_called()

    def test_readonly_audit_sequence(self):
        d = {'metadata': {'uid': m.DEP_UID, 'generation': 66}, 'spec': {'template': {'spec': {
            'containers': [{'name': 'app', 'image': 'image'}]}}}}
        p = {'metadata': {'name': 'app', 'uid': 'pod'}, 'spec': {'nodeName': 'datax-03',
            'containers': [{'name': 'app', 'image': 'image'}]}, 'status': {'containerStatuses': []}}
        with patch.object(m, 'read_failure', return_value={}) as receipt, patch.object(m.obs, 'emit') as emit, \
             patch.object(m.obs, 'get', side_effect=[d, {'items': []}, d]) as get, \
             patch.object(m.obs, 'owned', return_value=[p]), patch.object(m.obs, 'ready', return_value=True), \
             patch.object(m.obs, 'digest', return_value=m.DEP_SPEC):
            m.audit()
        self.assertEqual(receipt.call_count, 2)
        self.assertEqual([x.args[0] for x in get.call_args_list], ['deployments', 'nodes', 'deployments'])
        self.assertEqual(emit.call_args.args[0]['scheduling_audit'], 'COMPLETE')
        self.assertFalse(emit.call_args.args[0]['ready_to_apply'])

    def test_changed_deployment_stops_before_node_reads(self):
        with patch.object(m, 'read_failure', return_value={}), patch.object(m.obs, 'emit'), \
             patch.object(m.obs, 'get', return_value={'metadata': {'uid': 'wrong'}}) as get:
            with self.assertRaises(m.obs.CheckFailure):
                m.audit()
        self.assertEqual(get.call_count, 1)


if __name__ == '__main__':
    unittest.main()
