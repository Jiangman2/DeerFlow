import ast
import copy
import json
from pathlib import Path
import stat
import types
import unittest
from unittest.mock import MagicMock, patch

import reconcile_retirement as c
import retire_old_pool as r
import audit_old_pool as audit
from test_retire_old_pool import dep


class ReadOnlyTests(unittest.TestCase):
    def test_python37(self):
        ast.parse(Path(c.__file__).read_bytes(), feature_version=(3, 7))

    def test_only_get_in_capture(self):
        inner = MagicMock(return_value=b'{}')
        fn = c.readonly_capture(inner)
        for ns in ('deer-flow', 'kube-node-lease'):
            fn(['kubectl', '--request-timeout=20s', '-n', ns, 'get', 'pods'])
        for verb in ('patch', 'scale', 'delete', 'apply', 'replace', 'create', 'exec'):
            with self.subTest(verb=verb), self.assertRaises(c.ReconcileFailure):
                fn(['kubectl', '--request-timeout=20s', '-n', 'deer-flow', verb, 'deployment'])
        self.assertEqual(inner.call_count, 2)

    def test_exact_frozen_runtime_inventory_commands_allowed(self):
        inner = MagicMock(return_value=b'{}')
        fn = c.readonly_capture(inner)
        expected = [audit.CRI+['pods', '-o', 'json'], audit.CRI+['ps', '--all', '-o', 'json'], audit.CTR+['tasks', 'list']]
        self.assertEqual(tuple(tuple(a) for a in expected), c.RUNTIME_READ_COMMANDS)
        for args in expected:
            fn(args)
        self.assertEqual(inner.call_count, 3)

    def test_runtime_mutations_and_near_matches_denied(self):
        inner = MagicMock()
        fn = c.readonly_capture(inner)
        bad = [audit.CRI+['stop', 'id'], audit.CRI+['rm', 'id'], audit.CRI+['exec', 'id', 'true'],
               audit.CTR+['tasks', 'kill', 'id'], audit.CTR+['tasks', 'delete', 'id'],
               audit.CTR+['images', 'rm', 'image'], audit.CTR+['tasks', 'list', '--quiet'],
               list(c.RUNTIME_READ_COMMANDS[0])+['extra']]
        wrong_socket = list(c.RUNTIME_READ_COMMANDS[0])
        wrong_socket[2] = 'unix:///tmp/untrusted.sock'
        bad.append(wrong_socket)
        for args in bad:
            with self.subTest(args=args), self.assertRaises(c.ReconcileFailure):
                fn(args)
        inner.assert_not_called()

    def test_real_terminal_audit_passes_through_readonly_guard(self):
        # Exercise the real frozen caller -> guard -> parser path that the first
        # reconciliation failed to cover. Mock only external Linux IO.
        uid = 'b6fca178-b783-4535-8b13-742379755e66'
        row = dict(uid=uid, pod='deer-flow-mineru-adapter-7f59bc8879-2vvwx', known_container_ids=[])
        capture = MagicMock(side_effect=[b'{"items":[]}', b'{"containers":[]}', b'TASK PID STATUS\n'])
        fake_sw = MagicMock()
        fake_sw.s.core.inv.bounded_capture = c.readonly_capture(capture)
        with patch.object(audit, 'sw', fake_sw), patch.object(audit, 'safe_dir'), \
             patch.object(audit.socket, 'gethostname', return_value='datax-01'), \
             patch.object(audit.Path, 'lstat', return_value=types.SimpleNamespace(st_mode=stat.S_IFSOCK, st_uid=0)), \
             patch.object(audit, 'process_cgroups', return_value={'matches': {uid: 0}, 'unreadable': 0}), \
             patch.object(audit, 'emit'):
            result = audit.terminal_runtime([row])
        self.assertEqual(result['check'], 'COMPLETE')
        self.assertTrue(result['no_activity_observed'])
        self.assertEqual(capture.call_count, 3)

    def test_incomplete_read_is_not_activity_and_is_preserved(self):
        report = {}
        historical = dict(check='INCOMPLETE', no_activity_observed=False, error_type='ReconcileFailure')
        with self.assertRaisesRegex(c.ReconcileFailure, '^historical_runtime_read_incomplete$'):
            c.accept_historical_runtime(report, historical)
        self.assertEqual(report['historical_runtime'], historical)

    def test_actual_activity_remains_blocking(self):
        with self.assertRaisesRegex(c.ReconcileFailure, '^historical_runtime_activity_observed$'):
            c.accept_historical_runtime({}, dict(check='COMPLETE', no_activity_observed=False))

    def test_direct_mutation_denied(self):
        with self.assertRaises(c.ReconcileFailure):
            c.deny_mutation('anything')

    def test_exact_load_failure_reproduced(self):
        fake = MagicMock()
        LoadFailure = type('LoadFailure', (RuntimeError,), {})
        fake.s.core.ca_bytes.side_effect = LoadFailure('no_ready_ca_source')
        with patch.object(c, 'r', fake):
            result = c.legacy_ca_dependency()
        self.assertEqual(result['reproduced_error'], 'LoadFailure:no_ready_ca_source')

    def test_other_legacy_error_not_swallowed(self):
        fake = MagicMock()
        fake.s.core.ca_bytes.side_effect = RuntimeError('different')
        with patch.object(c, 'r', fake), self.assertRaises(c.ReconcileFailure):
            c.legacy_ca_dependency()

    def test_unexpected_old_ca_source_blocks(self):
        fake = MagicMock()
        fake.s.core.ca_bytes.return_value = b'ca'
        with patch.object(c, 'r', fake), self.assertRaises(c.ReconcileFailure):
            c.legacy_ca_dependency()

    def test_controller_must_observe_zero(self):
        c.check_controller(dep(r.WORKER, 0))
        for path, value in [('replicas', 1), ('readyReplicas', 1), ('updatedReplicas', 1), ('observedGeneration', 1)]:
            obj = dep(r.WORKER, 0)
            obj['status'][path] = value
            with self.subTest(path=path), self.assertRaises(c.ReconcileFailure):
                c.check_controller(obj)


class BindingTests(unittest.TestCase):
    def setUp(self):
        self.fake = MagicMock()
        self.fake.WORKER, self.fake.DISPATCHER = r.WORKER, r.DISPATCHER
        self.fake.TARGETS, self.fake.AUDIT_SHA = r.TARGETS, r.AUDIT_SHA
        self.prior = dict(mineru_old_pool_retire='UNCONFIRMED', error_type='LoadFailure',
            failed_stage='RETIRE_'+r.WORKER, patches_attempted=list(r.TARGETS),
            patches_acknowledged=list(r.TARGETS), termination_verified=[r.DISPATCHER],
            candidate_workloads_modified=False, public_route_changed=False, credentials_modified=False,
            queues_modified=False, final_16_expansion=False)
        self.state = dict(audit_sha256=r.AUDIT_SHA, candidate_capacity=6, before_hashes={n:'hash' for n in r.TARGETS})
        self.fake.a.locked.side_effect = lambda path, digest: b'locked code' if path == c.ORIGINAL_TOOL else json.dumps(self.prior).encode()
        self.fake.s.core.pf.read_private.return_value = json.dumps(self.state).encode()
        p = patch.object(c, 'r', self.fake)
        p.start()
        self.addCleanup(p.stop)

    def test_both_scale_acks_required(self):
        self.assertEqual(c.bind_evidence(), self.state)
        self.prior['patches_acknowledged'] = [r.DISPATCHER]
        with self.assertRaises(c.ReconcileFailure):
            c.bind_evidence()

    def test_scope_drift_blocks(self):
        self.prior['public_route_changed'] = True
        with self.assertRaises(c.ReconcileFailure):
            c.bind_evidence()

    def test_acceptance_is_not_expansion_and_does_not_call_mutators(self):
        self.fake.ORIGINAL_COUNTS = r.ORIGINAL_COUNTS
        self.fake.baseline_spec.side_effect = r.baseline_spec
        inventory = dict(targets=[], pending=[], terminal=[])
        candidates = [dict(container='adapter', name='new-worker')]
        self.fake.original_snapshots.return_value = {n: dep(n) for n in r.TARGETS}
        self.fake.invariants.return_value = ({n: dep(n, 0) for n in r.TARGETS}, candidates)
        self.fake.owned_state.return_value = []
        self.fake.a.terminal_runtime.return_value = {'check': 'COMPLETE', 'no_activity_observed': True}
        report = dict(ready_to_expand_16=False)
        with patch.object(c, 'bind_evidence', return_value={'inventory': inventory}), \
             patch.object(c, 'accept_acks'), patch.object(c, 'legacy_ca_dependency', return_value={}), \
             patch.object(c, 'emit'):
            c.reconcile(report, {}, {'inventory': inventory}, 'probe', 'route')
        self.assertEqual(report['mineru_retirement_reconcile'], 'PASS')
        self.assertEqual(report['new_pool_slots'], 6)
        self.assertFalse(report['ready_to_expand_16'])
        self.fake.patch_call.assert_not_called()
        self.fake.retire_once.assert_not_called()
        self.fake.restore_review.assert_not_called()
        self.assertEqual(self.fake.wait_retired.call_count, 2)

    def test_lingering_pod_blocks_reconciliation(self):
        inventory = dict(targets=[], pending=[], terminal=[])
        self.fake.invariants.return_value = ({n: dep(n, 0) for n in r.TARGETS}, [])
        self.fake.owned_state.return_value = [dict(name='still-running')]
        with patch.object(c, 'bind_evidence', return_value={'inventory': inventory}), \
             patch.object(c, 'accept_acks'), self.assertRaises(c.ReconcileFailure):
            c.reconcile({}, {}, {'inventory': inventory}, 'probe', 'route')
        self.fake.wait_retired.assert_not_called()


if __name__ == '__main__':
    unittest.main()
