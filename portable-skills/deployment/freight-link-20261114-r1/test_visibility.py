"""Readonly postcheck regressions; no Kubernetes or production file access."""
import ast
import copy
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location('freight_readonly_visibility', HERE / 'verify_visibility.py')
visibility = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(visibility)


def pod():
    return {
        'metadata': {'name': 'sandbox-fixture', 'uid': '01234567-89ab-cdef-0123-456789abcdef',
                     'labels': {'app.kubernetes.io/component': 'sandbox'}},
        'spec': {'nodeName': 'fixture-node',
                 'containers': [{'name': 'sandbox', 'volumeMounts': [
                     {'name': 'skills-public', 'mountPath': '/mnt/skills/public', 'subPath': 'public', 'readOnly': True}]}],
                 'volumes': [{'name': 'skills-public', 'persistentVolumeClaim': {'claimName': visibility.CLAIM}}]},
        'status': {'phase': 'Running', 'containerStatuses': [
            {'name': 'sandbox', 'ready': True, 'state': {'running': {'startedAt': '2026-11-14T00:00:00Z'}},
             'containerID': 'containerd://fixture', 'imageID': 'sha256:fixture', 'restartCount': 0}]}}


class VisibilityTests(unittest.TestCase):
    def test_python37_entry_syntax(self):
        ast.parse((HERE / 'verify_visibility.py').read_text(encoding='utf-8'), feature_version=(3, 7))

    def test_pending_empty_image_is_ignored(self):
        value = pod()
        value['status'] = {'phase': 'Pending', 'containerStatuses': [{'name': 'sandbox', 'imageID': ''}]}
        self.assertIsNone(visibility.ready_candidate(value))

    def test_terminating_and_unready_pods_are_ignored(self):
        value = pod()
        value['metadata']['deletionTimestamp'] = '2026-11-14T00:00:01Z'
        self.assertIsNone(visibility.ready_candidate(value))
        value = pod()
        value['status']['containerStatuses'][0]['ready'] = False
        self.assertIsNone(visibility.ready_candidate(value))

    def test_public_mount_can_be_observed_when_readonly_flag_is_false(self):
        value = pod()
        value['spec']['containers'][0]['volumeMounts'][0]['readOnly'] = False
        result = visibility.ready_candidate(value)
        self.assertTrue(result['mount_contract'])
        self.assertFalse(result['mount_readonly'])

    def test_wrong_subpath_expression_and_claim_are_excluded(self):
        for mutation in ('subpath', 'expression', 'claim'):
            with self.subTest(mutation=mutation):
                value = pod()
                mount = value['spec']['containers'][0]['volumeMounts'][0]
                if mutation == 'subpath':
                    mount['subPath'] = 'private'
                elif mutation == 'expression':
                    mount['subPathExpr'] = '$(UNTRUSTED)'
                else:
                    value['spec']['volumes'][0]['persistentVolumeClaim']['claimName'] = 'other-claim'
                self.assertFalse(visibility.ready_candidate(value)['mount_contract'])

    def test_duplicate_public_mount_is_excluded(self):
        value = pod()
        mounts = value['spec']['containers'][0]['volumeMounts']
        mounts.append(copy.deepcopy(mounts[0]))
        self.assertFalse(visibility.ready_candidate(value)['mount_contract'])

    def test_success_uses_structured_list_and_reports_changed_runtime_separately(self):
        value = pod()
        pending = pod()
        pending['status'] = {'phase': 'Pending'}
        child = {'status': 'ORDINARY_SANDBOX_PUBLIC_RUNTIME_VISIBLE', 'uid': 1000}
        inspector = visibility.Inspector('fixed-child')
        with patch.object(inspector, 'query', side_effect=[{'items': [pending, value]}, value, child, value]) as query:
            result = inspector.run()
        self.assertEqual(query.call_count, 4)
        self.assertEqual(result['status'], 'PUBLISHED_SKILL_VISIBLE_IN_ORDINARY_SANDBOX')
        self.assertFalse(result['current_image_matches_qualified'])
        self.assertTrue(result['stable_pod_readback'])
        self.assertEqual(result['ready_ordinary_pods'], 1)
        self.assertNotIn('container_id', result['sample'])

    def test_no_ready_candidate_returns_pending_without_exec(self):
        inspector = visibility.Inspector('fixed-child')
        with patch.object(inspector, 'query', return_value={'items': []}) as query:
            result = inspector.run()
        self.assertEqual(query.call_count, 1)
        self.assertEqual(result['error_code'], 'NO_READY_PUBLIC_MOUNT_CANDIDATE')

    def test_child_failure_detail_is_preserved(self):
        value = pod()
        child = {'status': 'INCOMPLETE', 'error_class': 'ValueError', 'error_code': 'ORDINARY_SKILL_TREE_MISMATCH'}
        inspector = visibility.Inspector('fixed-child')
        with patch.object(inspector, 'query', side_effect=[{'items': [value]}, value, child, value]):
            result = inspector.run()
        self.assertEqual(result['status'], 'VISIBILITY_PENDING')
        self.assertEqual(result['observation'], child)

    def test_changed_pod_before_read_stops_before_exec(self):
        value, replacement = pod(), pod()
        replacement['metadata']['uid'] = '11234567-89ab-cdef-0123-456789abcdef'
        inspector = visibility.Inspector('fixed-child')
        with patch.object(inspector, 'query', side_effect=[{'items': [value]}, replacement]) as query:
            with self.assertRaisesRegex(visibility.Refused, 'POD_CHANGED_BEFORE_READ'):
                inspector.run()
        self.assertEqual(query.call_count, 2)

    def test_restart_during_read_is_not_accepted(self):
        value, after = pod(), pod()
        after['status']['containerStatuses'][0]['restartCount'] = 1
        inspector = visibility.Inspector('fixed-child')
        with patch.object(inspector, 'query', side_effect=[{'items': [value]}, value,
                          {'status': 'ORDINARY_SANDBOX_PUBLIC_RUNTIME_VISIBLE'}, after]):
            with self.assertRaisesRegex(visibility.Refused, 'POD_CHANGED_DURING_READ'):
                inspector.run()

    def test_query_rejects_mutations_and_nonfixed_exec_before_launch(self):
        inspector = visibility.Inspector('fixed-child')
        inspector.selected = visibility.ready_candidate(pod())
        for command in (['delete', 'pod', 'sandbox-fixture'], ['get', 'secrets', '-o', 'json'],
                        ['exec', 'sandbox-fixture', '-c', 'sandbox', '--', 'python3', '-c', 'print(1)']):
            with self.subTest(command=command), patch.object(visibility.subprocess, 'Popen') as launch:
                with self.assertRaisesRegex(visibility.Refused, 'READONLY_FIXED_COMMAND_REQUIRED'):
                    inspector.query(command)
                launch.assert_not_called()
        self.assertEqual(inspector.queries, 0)


if __name__ == '__main__':
    unittest.main()
