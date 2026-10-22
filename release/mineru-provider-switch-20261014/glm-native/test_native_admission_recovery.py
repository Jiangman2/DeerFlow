import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

import native_glm_acceptance as original
import qualify_runtime_candidate_fix1 as q
import recover_native_admission as r
import test_native_glm_acceptance as previous_tests


def loaded():
    spec = importlib.util.spec_from_file_location('test_original_acceptance', original.__file__)
    a = importlib.util.module_from_spec(spec); spec.loader.exec_module(a)
    return a


def policy(a):
    return a.manifests(r.OLD_NAMESPACE, 'candidate', b'', {}, ['10.10.1.2'], [])[0]


class PolicyTests(unittest.TestCase):
    def test_reproduce_original_empty_ingress_false_alarm(self):
        a = loaded(); expected = policy(a); actual = copy.deepcopy(expected)
        del actual['spec']['ingress']
        self.assertFalse(a.subset(expected, actual))
        self.assertTrue(r.policy_equivalent(expected, actual, a.subset))

    def test_realistic_dry_run_and_live_json(self):
        a = loaded(); expected = policy(a); actual = copy.deepcopy(expected)
        del actual['spec']['ingress']
        actual['metadata'].update(uid='ours', resourceVersion='123', creationTimestamp='2026-10-17T00:00:00Z')
        r.install_comparison(a)
        self.assertTrue(a.subset(expected, actual))
        self.assertTrue(a.subset(expected, expected))

    def test_never_drop_empty_nested_selectors_or_open_rules(self):
        a = loaded(); expected = policy(a); r.install_comparison(a)
        for change in ('allow_ingress', 'selector', 'all_destination', 'all_ports', 'wide_cidr',
                       'extra_port', 'extra_rule', 'remove_egress', 'remove_ingress_type', 'null_ingress',
                       'namespace', 'wrong_kind'):
            bad = copy.deepcopy(expected); s = bad['spec']; del s['ingress']
            if change == 'allow_ingress': s['ingress'] = [{}]
            if change == 'selector': s['podSelector'] = {'matchLabels': {'app': 'foreign'}}
            if change == 'all_destination': s['egress'][0]['to'] = []
            if change == 'all_ports': s['egress'][0]['ports'] = []
            if change == 'wide_cidr': s['egress'][0]['to'][0]['ipBlock']['cidr'] = '0.0.0.0/0'
            if change == 'extra_port': s['egress'][0]['ports'].append({'port': 5432, 'protocol': 'TCP'})
            if change == 'extra_rule': s['egress'].append({})
            if change == 'remove_egress': del s['egress']
            if change == 'remove_ingress_type': s['policyTypes'] = ['Egress']
            if change == 'null_ingress': s['ingress'] = None
            if change == 'namespace': bad['metadata']['namespace'] = 'deer-flow'
            if change == 'wrong_kind': bad['kind'] = 'Other'
            with self.subTest(change=change): self.assertFalse(a.subset(expected, bad))

    def test_other_defaults_not_globally_normalized(self):
        a = loaded(); r.install_comparison(a)
        self.assertFalse(a.subset({'unrelated': []}, {}))
        self.assertFalse(a.subset({'enabled': False}, {}))
        self.assertTrue(a.subset({'name': 'ok'}, {'name': 'ok', 'uid': 'extra'}))

    def test_extra_configmap_script_rejected(self):
        a = loaded(); r.install_comparison(a)
        cm = a.manifests('test', 'candidate', b'ca', {}, ['10.10.1.2'], [])[1]
        actual = copy.deepcopy(cm); actual['data']['extra.py'] = 'not-allowed'
        self.assertFalse(a.subset(cm, actual)); self.assertTrue(a.subset(cm, cm))


@unittest.skipUnless(os.name == 'posix' and hasattr(os, 'geteuid') and os.geteuid() == 0, 'Linux root filesystem tests')
class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir='/root'); self.root = Path(self.temp.name)
        self.a = loaded(); self.p = types.SimpleNamespace(IMAGE='candidate')
        self.patches = [patch.object(r, 'OLD_STATE', self.root/'old'),
                        patch.object(r, 'STATE', self.root/'recovery'), patch.object(r, 'NEW_STATE', self.root/'fresh')]
        for p in self.patches: p.start()
        r.OLD_STATE.mkdir(mode=0o700)
        self.report = {'native_glm_acceptance': 'UNCONFIRMED',
            'failed_stage': 'CREATE_ISOLATED_FIXTURE_NO_PRODUCTION_STORES',
            'failed_check': 'admission_changed_isolation_or_probe', 'provider_submission_attempted': False,
            'namespace': r.OLD_NAMESPACE, 'production_writes': 0}
        q.record(r.OLD_STATE/'RESULT.json', self.report)
        self.sha = patch.object(r, 'OLD_RESULT_SHA', q.digest(r.OLD_STATE/'RESULT.json')); self.sha.start()
        q.record(r.OLD_STATE/'STARTED.json', {'namespace': r.OLD_NAMESPACE})
        q.record(r.OLD_STATE/'NAMESPACE-REQUESTED.json', {'name': r.OLD_NAMESPACE})
        q.record(r.OLD_STATE/'NAMESPACE-ACK.json', {'name': r.OLD_NAMESPACE, 'uid': 'ours'})
        pod = self.a.manifests(r.OLD_NAMESPACE, 'candidate', b'', {}, ['10.10.1.2'], [])[2]
        q.record(r.OLD_STATE/'POD-EXPECTED.json', pod)
        self.a.emit = Mock(); self.calls = []
        self.a.get = Mock(return_value={'metadata': {'uid': 'ours', 'labels': {'app': 'weknora-native-qualification'}}})
        self.a.call = Mock(side_effect=self.call)
        self.inject = None

    def tearDown(self):
        self.sha.stop()
        for p in reversed(self.patches): p.stop()
        self.temp.cleanup()

    def call(self, args, data=None, **kwargs):
        self.calls.append(args)
        if args[0] == 'get':
            items = [{'metadata': {'name': 'kube-root-ca.crt'}}] if args[1] == 'configmaps' else []
            if args[1] == self.inject: items = [{'metadata': {'name': 'unexpected'}}]
            return 0, json.dumps({'items': items}).encode()
        if args[0] == 'create':
            self.assertIn('--dry-run=server', args)
            obj = json.loads(data); del obj['spec']['ingress']
            return 0, json.dumps(obj).encode()
        if args[0] == 'delete':
            self.assertEqual(json.loads(data)['preconditions'], {'uid': 'ours'})
            self.assertEqual(args[2], '/api/v1/namespaces/'+r.OLD_NAMESPACE)
            return 0, b'{}'
        raise AssertionError(args)

    def test_diagnosis_has_no_live_write_or_provider_request(self):
        hashes, uid, row = r.diagnose(self.a, self.p, q)
        self.assertEqual(uid, 'ours'); self.assertEqual(len(hashes), 5)
        self.assertFalse(row['returned_ingress_present'])
        self.assertTrue(row['exact_policy_after_empty_ingress_normalization'])
        self.assertFalse(r.STATE.exists())
        self.assertTrue(all(x[0] == 'get' or '--dry-run=server' in x for x in self.calls))

    def test_send_marker_or_pod_request_blocks_recovery(self):
        for name in ('PROVIDER-REQUESTED.json', 'POD-REQUESTED.json', 'POLICY-REQUESTED.json'):
            f = r.OLD_STATE/name; q.record(f, {})
            with self.subTest(name=name), self.assertRaisesRegex(r.RecoveryFailure, 'boundary_requires_review'):
                r.diagnose(self.a, self.p, q)
            f.unlink()

    def test_old_pod_or_policy_not_assumed_safe(self):
        for kind in ('pods', 'networkpolicies', 'jobs'):
            self.inject = kind
            with self.subTest(kind=kind), self.assertRaisesRegex(r.RecoveryFailure, 'unexpected_old_namespace_resources'):
                r.diagnose(self.a, self.p, q)

    def test_namespace_uid_change_stops_before_dry_run(self):
        self.a.get.return_value['metadata']['uid'] = 'foreign'
        with self.assertRaisesRegex(r.RecoveryFailure, 'previous_namespace_changed'): r.diagnose(self.a, self.p, q)
        self.a.call.assert_not_called()

    def test_failure_hash_change_blocks(self):
        with patch.object(r, 'OLD_RESULT_SHA', '0'*64), self.assertRaisesRegex(r.RecoveryFailure, 'receipt_changed'):
            r.diagnose(self.a, self.p, q)

    def test_a_real_admission_change_is_not_bypassed(self):
        call = self.call
        def mutated(args, data=None, **kwargs):
            code, raw = call(args, data, **kwargs)
            if args[0] == 'create':
                value = json.loads(raw); value['spec']['egress'] = [{}]; raw = json.dumps(value).encode()
            return code, raw
        self.a.call.side_effect = mutated
        with self.assertRaisesRegex(r.RecoveryFailure, 'not_the_known_empty_ingress'): r.recover(self.a, self.p, q)
        self.assertFalse(r.STATE.exists()); self.assertFalse(r.NEW_STATE.exists())

    def test_once_only_new_operation_preserves_old_and_cleans_uid(self):
        before = r.failure_binding(q)[0]
        def run(p, helper):
            self.assertEqual(self.a.STATE, r.NEW_STATE)
            expected = policy(self.a); actual = copy.deepcopy(expected); del actual['spec']['ingress']
            self.assertTrue(self.a.subset(expected, actual))
            r.NEW_STATE.mkdir(mode=0o700); return 0
        self.a.run = Mock(side_effect=run)
        self.assertEqual(r.recover(self.a, self.p, q), 0)
        self.assertEqual(r.failure_binding(q)[0], before)
        self.assertEqual(self.a.run.call_count, 1)
        self.assertTrue(any(x[0] == 'delete' for x in self.calls))
        with self.assertRaisesRegex(r.RecoveryFailure, 'recovery_exists'): r.recover(self.a, self.p, q)

    def test_native_uncertainty_no_automatic_retry_or_cleanup(self):
        self.a.run = Mock(return_value=1)
        self.assertEqual(r.recover(self.a, self.p, q), 1)
        self.assertEqual(self.a.run.call_count, 1)
        self.assertFalse(any(x[0] == 'delete' for x in self.calls))
        with self.assertRaisesRegex(r.RecoveryFailure, 'recovery_exists'): r.recover(self.a, self.p, q)

    def test_status_no_probe_or_mutation(self):
        self.a.status = Mock(); self.a.run = Mock()
        r.status(self.a, q)
        self.a.status.assert_called_once_with(q); self.a.run.assert_not_called()
        self.assertFalse(r.STATE.exists()); self.assertFalse(r.NEW_STATE.exists())


class FixedFullWorkflowTests(previous_tests.WorkflowTests):
    """Replay the original controller through realistic API empty-field omission."""
    def setUp(self):
        super().setUp()
        self.old_comparison = original.subset
        r.install_comparison(original)

    def tearDown(self):
        original.subset = self.old_comparison
        super().tearDown()

    def call(self, args, data=None, **kwargs):
        code, raw = super().call(args, data, **kwargs)
        if args[0] == 'create':
            obj = json.loads(raw)
            if obj.get('kind') == 'NetworkPolicy':
                del obj['spec']['ingress']
                if '--dry-run=server' not in args: self.objects['NetworkPolicy'] = obj
                raw = json.dumps(obj).encode()
        return code, raw


if __name__ == '__main__': unittest.main()
