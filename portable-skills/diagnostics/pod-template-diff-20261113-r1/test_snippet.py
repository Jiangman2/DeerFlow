import copy
import importlib.util
import json
from pathlib import Path
import runpy
import unittest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('readonly_template_diff', str(HERE / 'snippet.py'))
snippet = importlib.util.module_from_spec(spec); spec.loader.exec_module(snippet)
helper = runpy.run_path(str(HERE.parents[1] / 'deployment/scheduler-baseline-inspection-20261113-r1/inspect-scheduler-baseline-20261113-r1.py'), run_name='helper_fixture')


class DiffTests(unittest.TestCase):
    def test_real_added_selector_leaves_match_label_hash_without_values(self):
        left = {'affinity': {'podAffinity': {'terms': [{'matchLabelKeys': ['pod-template-hash']}]}}}
        right = copy.deepcopy(left)
        label = 'DO_NOT_PRINT_LABEL_VALUE'
        right['affinity']['podAffinity']['terms'][0]['labelSelector'] = {'matchExpressions': [{'key': 'pod-template-hash', 'operator': 'In', 'values': [label]}]}
        rows, truncated = snippet.differences(left, right)
        selected = next(row for row in rows if row['path'].endswith('/values/0'))
        self.assertEqual(selected['actual']['sha256'], snippet.summary(label)['sha256'])
        self.assertFalse(selected['requested']['present']); self.assertFalse(truncated)
        self.assertNotIn(label, json.dumps(rows))
        self.assertTrue(snippet.selector_keys(left)[0]['contains_pod_template_hash'])

    def test_diff_cap_and_no_raw_secret_values(self):
        canary = 'DO_NOT_PRINT_CREDENTIAL_VALUE'
        rows, truncated = snippet.differences({'securityContext': {}}, {'securityContext': {'test%03d' % i: canary for i in range(100)}})
        self.assertEqual(len(rows), 64); self.assertTrue(truncated)
        self.assertNotIn(canary, json.dumps(rows))
        self.assertEqual(set(rows[0]['actual']), {'present', 'type', 'sha256'})

    def test_valid_owner_closure_and_wrong_owner_rejection(self):
        dep = {'spec': {'template': {'metadata': {'labels': {'app': 'gateway'}}, 'spec': {}}}}
        rs_uid = '00000000-0000-4000-8000-000000000000'
        pod = {'metadata': {'name': snippet.POD, 'uid': snippet.POD_UID, 'namespace': 'deer-flow', 'ownerReferences': [
            {'name': 'gateway-test', 'kind': 'ReplicaSet', 'uid': rs_uid, 'controller': True}]}}
        rs = {'metadata': {'name': 'gateway-test', 'uid': rs_uid, 'ownerReferences': [
            {'name': 'gateway', 'kind': 'Deployment', 'uid': snippet.DEP_UID, 'controller': True}]}, 'spec': {'template': copy.deepcopy(dep['spec']['template'])}}
        snippet.closure(helper, pod, rs, dep)
        rs['metadata']['ownerReferences'][0]['uid'] = rs_uid
        with self.assertRaisesRegex(snippet.Refused, 'OWNER_CLOSURE_CHANGED'):
            snippet.closure(helper, pod, rs, dep)


if __name__ == '__main__': unittest.main()
