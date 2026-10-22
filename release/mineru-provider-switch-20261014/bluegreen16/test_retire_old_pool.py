import ast
import copy
import datetime
import json
from pathlib import Path
import types
import unittest
from unittest.mock import MagicMock, patch

import retire_old_pool as r
import retire_runtime_probe as live

HERE = Path(__file__).absolute().parent


def dep(name, replicas=None):
    if replicas is None:
        replicas = r.ORIGINAL_COUNTS[name]
    return dict(apiVersion='apps/v1', kind='Deployment',
        metadata=dict(name=name, namespace='deer-flow', uid='uid-'+name, resourceVersion='42', generation=3),
        spec=dict(replicas=replicas, template=dict(metadata=dict(labels={'app': name}),
            spec=dict(containers=[dict(name='adapter', image='old')]))),
        status=dict(observedGeneration=3, replicas=replicas, readyReplicas=replicas, availableReplicas=replicas))


class PatchTests(unittest.TestCase):
    def setUp(self):
        self.fake = MagicMock()
        self.stack = patch.object(r, 's', self.fake)
        self.stack.start()

    def tearDown(self):
        self.stack.stop()

    def test_python37_grammar(self):
        for name in ('retire_old_pool.py', 'retire_runtime_probe.py', 'package_retire.py'):
            if (HERE/name).exists():
                ast.parse((HERE/name).read_bytes(), feature_version=(3, 7))

    def test_only_old_replica_field_is_written(self):
        for name in r.TARGETS:
            before = dep(name)
            p = r.scale_patch(before)
            self.assertEqual([(x['op'], x['path']) for x in p], [('test', '/metadata/uid'),
                ('test', '/metadata/resourceVersion'), ('test', '/spec'), ('replace', '/spec/replicas')])
            self.assertEqual(p[-1]['value'], 0)
            self.assertEqual(before['spec']['replicas'], r.ORIGINAL_COUNTS[name])

    def test_new_pool_cannot_be_scaled(self):
        with self.assertRaises(r.RetireFailure):
            r.scale_patch(dep('deer-flow-mineru-dedicated-dispatch-r1', 1))

    def test_wrong_namespace_rejected(self):
        obj = dep(r.DISPATCHER)
        obj['metadata']['namespace'] = 'other'
        with self.assertRaises(r.RetireFailure):
            r.scale_patch(obj)

    def test_changed_original_count_rejected(self):
        for count in (0, 8, 10, True, '9'):
            with self.subTest(count=count), self.assertRaises(r.RetireFailure):
                r.scale_patch(dep(r.WORKER, count))

    def test_exact_spec_and_uid_required(self):
        before = dep(r.WORKER)
        after = dep(r.WORKER, 0)
        self.assertTrue(r.deployment_matches(after, before, 0))
        for field in ('uid', 'name', 'namespace', 'deletionTimestamp'):
            changed = copy.deepcopy(after)
            changed['metadata'][field] = 'different'
            self.assertFalse(r.deployment_matches(changed, before, 0))
        changed = copy.deepcopy(after)
        changed['spec']['template']['spec']['containers'][0]['image'] = 'other'
        self.assertFalse(r.deployment_matches(changed, before, 0))

    def test_live_call_is_scoped_json_patch(self):
        self.fake.core.inv.bounded_capture.return_value = b'{}'
        path = r.ROOT/(r.WORKER+'.scale.patch.private.json')
        r.patch_call(r.WORKER, path, False)
        args = self.fake.core.inv.bounded_capture.call_args[0][0]
        self.assertEqual(args[:8], ['kubectl', '--request-timeout=20s', '-n', 'deer-flow',
            'patch', 'deployment', r.WORKER, '--type=json'])
        self.assertNotIn('--force', args)
        self.assertNotIn('--dry-run=server', args)

    def test_dry_run_never_live(self):
        self.fake.core.inv.bounded_capture.return_value = b'{}'
        r.patch_call(r.DISPATCHER, r.ROOT/(r.DISPATCHER+'.scale.patch.private.json'), True)
        self.assertIn('--dry-run=server', self.fake.core.inv.bounded_capture.call_args[0][0])

    def test_path_outside_state_rejected(self):
        with self.assertRaises(r.RetireFailure):
            r.patch_call(r.WORKER, Path('/tmp/patch.json'), False)
        self.fake.core.inv.bounded_capture.assert_not_called()

    def test_live_transport_error_not_retried(self):
        self.fake.core.inv.bounded_capture.side_effect = RuntimeError('uncertain')
        with self.assertRaises(RuntimeError):
            r.patch_call(r.WORKER, r.ROOT/(r.WORKER+'.scale.patch.private.json'), False)
        self.assertEqual(self.fake.core.inv.bounded_capture.call_count, 1)


class PhaseTests(unittest.TestCase):
    def setUp(self):
        self.fake = MagicMock()
        self.stack = patch.object(r, 's', self.fake)
        self.stack.start()
        self.before = {n: dep(n) for n in r.TARGETS}
        self.report = dict(patches_attempted=[], patches_acknowledged=[])
        self.inventory = dict(targets=[dict(container='adapter'), dict(container='dispatcher')], pending=[], terminal=[])

    def tearDown(self):
        self.stack.stop()

    def test_intent_precedes_single_live_patch_and_ack(self):
        order = []
        def save(name, value):
            order.append(name)
            return 'hash'
        def call(name, path, dry):
            order.append('dry' if dry else 'live')
            return dep(name, 0)
        counts = dict(r.ORIGINAL_COUNTS)
        with patch.object(r, 'invariants', return_value=(self.before, [])), patch.object(r, 'quiet'), \
             patch.object(r, 'node_leases'), patch.object(r, 'emit'), patch.object(r, 'save', side_effect=save), \
             patch.object(r, 'patch_call', side_effect=call) as calls:
            r.retire_once(r.DISPATCHER, {}, self.before, counts, self.inventory, self.report)
        self.assertEqual(calls.call_count, 2)
        self.assertLess(order.index(r.DISPATCHER+'.PATCH-REQUESTED.json'), order.index('live'))
        self.assertLess(order.index('live'), order.index(r.DISPATCHER+'.PATCH-ACK.private.json'))
        self.assertEqual(counts[r.DISPATCHER], 0)
        self.assertEqual(counts[r.WORKER], 9)

    def test_unknown_live_outcome_keeps_intent_no_ack_no_count_advance(self):
        counts = dict(r.ORIGINAL_COUNTS)
        with patch.object(r, 'invariants', return_value=(self.before, [])), patch.object(r, 'quiet'), \
             patch.object(r, 'node_leases'), patch.object(r, 'emit'), patch.object(r, 'save') as save, \
             patch.object(r, 'patch_call', side_effect=[dep(r.DISPATCHER, 0), RuntimeError('uncertain')]) as call:
            with self.assertRaises(RuntimeError):
                r.retire_once(r.DISPATCHER, {}, self.before, counts, self.inventory, self.report)
        self.assertEqual(call.call_count, 2)
        self.assertEqual(self.report['patches_attempted'], [r.DISPATCHER])
        self.assertEqual(self.report['patches_acknowledged'], [])
        self.assertEqual(counts[r.DISPATCHER], 1)
        self.assertNotIn(r.DISPATCHER+'.PATCH-ACK.private.json', [c.args[0] for c in save.call_args_list])

    def test_busy_observation_prevents_live_patch(self):
        with patch.object(r, 'invariants', return_value=(self.before, [])), \
             patch.object(r, 'quiet', side_effect=r.RetireFailure('busy')), patch.object(r, 'node_leases'), \
             patch.object(r, 'save'), patch.object(r, 'patch_call', return_value=dep(r.DISPATCHER, 0)) as call:
            with self.assertRaises(r.RetireFailure):
                r.retire_once(r.DISPATCHER, {}, self.before, dict(r.ORIGINAL_COUNTS), self.inventory, self.report)
        self.assertEqual(call.call_count, 1)
        self.assertTrue(call.call_args.args[2])
        self.assertEqual(self.report['patches_attempted'], [])

    def test_resource_version_drift_prevents_live_patch(self):
        changed = copy.deepcopy(self.before)
        changed[r.DISPATCHER]['metadata']['resourceVersion'] = '43'
        with patch.object(r, 'invariants', side_effect=[(self.before, []), (changed, [])]), \
             patch.object(r, 'quiet'), patch.object(r, 'node_leases'), patch.object(r, 'save'), \
             patch.object(r, 'patch_call', return_value=dep(r.DISPATCHER, 0)) as call:
            with self.assertRaises(r.RetireFailure):
                r.retire_once(r.DISPATCHER, {}, self.before, dict(r.ORIGINAL_COUNTS), self.inventory, self.report)
        self.assertEqual(call.call_count, 1)

    def test_invalid_server_dry_run_cannot_continue(self):
        bad = dep(r.DISPATCHER, 0)
        bad['spec']['template']['spec']['containers'][0]['image'] = 'injected'
        with patch.object(r, 'invariants', return_value=(self.before, [])), patch.object(r, 'save'), \
             patch.object(r, 'quiet') as quiet, patch.object(r, 'patch_call', return_value=bad) as call:
            with self.assertRaises(r.RetireFailure):
                r.retire_once(r.DISPATCHER, {}, self.before, dict(r.ORIGINAL_COUNTS), self.inventory, self.report)
        self.assertEqual(call.call_count, 1)
        quiet.assert_not_called()

    def test_no_force_on_termination_deadline(self):
        with patch.object(r, 'invariants', return_value=(self.before, [])), \
             patch.object(r, 'owned_state', return_value=[{'remaining': True}]), \
             patch.object(r.time, 'monotonic', side_effect=[0, 181, 182]), patch.object(r, 'emit'), \
             patch.object(r, 'patch_call') as call:
            with self.assertRaises(r.RetireFailure):
                r.wait_retired(r.DISPATCHER, {}, self.before, {r.DISPATCHER: 0, r.WORKER: 9}, self.inventory)
        call.assert_not_called()

    def test_three_zero_controller_samples_required(self):
        zero = {n: dep(n, 0) for n in r.TARGETS}
        with patch.object(r, 'invariants', return_value=(zero, [])) as inv, \
             patch.object(r, 'owned_state', return_value=[]), patch.object(r.time, 'sleep'), \
             patch.object(r, 'node_leases'), patch.object(r, 'emit'), patch.object(r, 'save') as save:
            r.wait_retired(r.DISPATCHER, {}, self.before, {n: 0 for n in r.TARGETS}, self.inventory)
        self.assertEqual(inv.call_count, 3)
        self.assertEqual(save.call_args.args[1]['control_plane_termination'], 'VERIFIED')

    def test_termination_does_not_accept_observed_generation_lag(self):
        zero = {n: dep(n, 0) for n in r.TARGETS}
        zero[r.DISPATCHER]['status']['observedGeneration'] = 2
        with patch.object(r, 'invariants', return_value=(zero, [])), \
             patch.object(r, 'owned_state', return_value=[]), patch.object(r.time, 'monotonic', side_effect=[0, 181, 182]), \
             patch.object(r, 'emit'), patch.object(r, 'save') as save:
            with self.assertRaises(r.RetireFailure):
                r.wait_retired(r.DISPATCHER, {}, self.before, {n: 0 for n in r.TARGETS}, self.inventory)
        save.assert_not_called()

    def test_worker_endpoint_leftover_prevents_acceptance(self):
        zero = {n: dep(n, 0) for n in r.TARGETS}
        self.fake.get.return_value = {'items': [{'endpoints': [{'addresses': ['old']}]}]}
        with patch.object(r, 'invariants', return_value=(zero, [])), \
             patch.object(r, 'owned_state', return_value=[]), patch.object(r.time, 'monotonic', side_effect=[0, 181, 182]), \
             patch.object(r, 'emit'), patch.object(r, 'save') as save:
            with self.assertRaises(r.RetireFailure):
                r.wait_retired(r.WORKER, {}, self.before, {n: 0 for n in r.TARGETS}, self.inventory)
        save.assert_not_called()

    def test_fresh_lease_checked_on_exact_node(self):
        self.fake.core.guard.NODES = {'datax-02': {'uid': 'node-uid'}}
        self.fake.core.inv.get.return_value = {'items': [dict(metadata=dict(name='datax-02', uid='node-uid'),
            status={'conditions': [dict(type=k, status=v) for k, v in [('Ready', 'True'),
                ('MemoryPressure', 'False'), ('DiskPressure', 'False'), ('PIDPressure', 'False')]]})]}
        lease = dict(metadata=dict(name='datax-02', namespace='kube-node-lease'),
            spec=dict(renewTime=datetime.datetime.now(datetime.timezone.utc).isoformat()))
        self.fake.core.inv.bounded_capture.return_value = json.dumps(lease).encode()
        result = r.node_leases(dict(targets=[{'node': 'datax-02'}], pending=[], terminal=[]))
        self.assertEqual(result[0]['node'], 'datax-02')
        lease['spec']['renewTime'] = '2020-01-01T00:00:00Z'
        self.fake.core.inv.bounded_capture.return_value = json.dumps(lease).encode()
        with self.assertRaises(r.RetireFailure):
            r.node_leases(dict(targets=[{'node': 'datax-02'}], pending=[], terminal=[]))

    def test_unknown_old_pod_uid_never_adopted(self):
        self.fake.core.inv.owned_pods.return_value = [{'metadata': {'uid': 'surprise'}}]
        with self.assertRaises(r.RetireFailure):
            r.owned_state(dep(r.WORKER, 0), dict(targets=[], pending=[], terminal=[]), True)

    def test_worker_still_exists_after_zero_not_declared_gone(self):
        self.fake.core.inv.owned_pods.return_value = [{'metadata': {'uid': 'old'}}]
        remaining = r.owned_state(dep(r.WORKER, 0), dict(targets=[dict(container='adapter', uid='old')],
            pending=[], terminal=[]), True)
        self.assertEqual(len(remaining), 1)


class LiveTrafficTests(unittest.TestCase):
    def test_busy_candidate_worker_allowed(self):
        self.assertEqual(live.validate_live(dict(status='ok', role='worker', parser=dict(active=1, waiting=2)),
            'worker', 6), dict(active=1, waiting=2))

    def test_new_worker_must_remain_serial(self):
        for value in (2, -1, True, None):
            with self.subTest(value=value), self.assertRaises(live.LiveFailure):
                live.validate_live(dict(status='ok', role='worker', parser=dict(active=value, waiting=0)), 'worker', 6)

    def test_busy_dispatcher_allowed_with_full_healthy_capacity(self):
        d = dict(live=6, discovered=6, minimum=6, busy=4, waiting=8, quarantined=0, ready=True, capacity_target_met=True)
        self.assertEqual(live.validate_live(dict(status='ok', role='dispatcher', dispatcher=d), 'dispatcher', 6)['busy'], 4)
        for key, value in [('quarantined', 1), ('busy', 7), ('live', 5), ('waiting', True), ('minimum', 16)]:
            changed = dict(d, **{key: value})
            with self.subTest(key=key), self.assertRaises(live.LiveFailure):
                live.validate_live(dict(status='ok', role='dispatcher', dispatcher=changed), 'dispatcher', 6)

    def test_no_provider_socket_opened(self):
        self.assertEqual(live.no_provider_connection('checked_ca')['tls_connections'], 0)


class RestoreTests(unittest.TestCase):
    def setUp(self):
        self.fake, self.audit = MagicMock(), MagicMock()
        self.originals = {n: dep(n) for n in r.TARGETS}
        self.receipt = dict(mineru_rollback_alias_coverage='PASS', rollback_image=r.ROLLBACK_IMAGE,
            rollback_config='sha256:bfbdd4d3309110be003549b2959d5080204b27bcd6440e33d4c67d80ee3775e9',
            rollback_manifest='sha256:3c3f5d07bc290206b80bb2ca2af46101b874e4137fe00970498104ad062e7b64')
        self.audit.locked.return_value = json.dumps(self.receipt).encode()
        self.fake.core.pf.RECEIPTS = {'rollback': ('/private/RESULT.json', 'locked-hash')}
        self.fake.core.inv.get.side_effect = lambda kind, name: copy.deepcopy(self.originals[name])
        def answer(args):
            obj = copy.deepcopy(self.originals[args[6]])
            obj['spec']['template']['spec']['containers'][0]['image'] = r.ROLLBACK_IMAGE
            return json.dumps(obj).encode()
        self.fake.core.inv.bounded_capture.side_effect = answer
        self.patches = [patch.object(r, 's', self.fake), patch.object(r, 'a', self.audit)]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def test_restore_admission_rehearsal_is_always_dry_run(self):
        with patch.object(r, 'save') as save:
            r.restore_review(self.originals)
        calls = self.fake.core.inv.bounded_capture.call_args_list
        self.assertEqual(len(calls), 2)
        self.assertEqual([c.args[0][6] for c in calls], [r.WORKER, r.DISPATCHER])
        for c in calls:
            self.assertIn('--dry-run=server', c.args[0])
            self.assertEqual(c.args[0][2:6], ['-n', 'deer-flow', 'patch', 'deployment'])
        plan = next(c.args[1] for c in save.call_args_list if c.args[0] == 'RESTORE-PLAN.json')
        self.assertFalse(plan['ready_to_execute'])
        self.assertFalse(plan['automatic_restore'])
        self.assertEqual(self.originals[r.WORKER]['spec']['template']['spec']['containers'][0]['image'], 'old')

    def test_restore_receipt_mismatch_blocks_all_requests(self):
        self.receipt['rollback_image'] = 'unexpected'
        self.audit.locked.return_value = json.dumps(self.receipt).encode()
        with self.assertRaises(r.RetireFailure):
            r.restore_review(self.originals)
        self.fake.core.inv.bounded_capture.assert_not_called()

    def test_restore_admission_mutation_blocks_ready_plan(self):
        bad = dep(r.WORKER, 10)
        bad['spec']['template']['spec']['containers'][0]['image'] = r.ROLLBACK_IMAGE
        self.fake.core.inv.bounded_capture.side_effect = lambda args: json.dumps(bad).encode()
        with patch.object(r, 'save') as save, self.assertRaises(r.RetireFailure):
            r.restore_review(self.originals)
        self.assertNotIn('RESTORE-PLAN.json', [c.args[0] for c in save.call_args_list])
        self.assertEqual(self.fake.core.inv.bounded_capture.call_count, 1)


class InvariantTests(unittest.TestCase):
    def setUp(self):
        self.fake, self.switch = MagicMock(), MagicMock()
        self.originals = {n: dep(n) for n in r.TARGETS}
        self.counts = {r.DISPATCHER: 0, r.WORKER: 9}
        self.current = {n: dep(n, self.counts[n]) for n in r.TARGETS}
        self.fake.core.inv.get.side_effect = lambda kind, name: self.current[name]
        self.switch.BACK.__truediv__.return_value.exists.return_value = False
        self.fake.WORKERS, self.fake.DISPATCHER = [], 'new-dispatch'
        self.new = dep('new-dispatch', 1)
        self.fake.get.return_value = self.new
        self.fake.prep.live_policies.return_value = {'locked': True}
        self.fake.core.ca_bytes.return_value = b'ca'
        self.context = dict(created={'new-dispatch': self.new},
            prepared=dict(policies={'locked': True}, ca_sha256=r.sha(b'ca')))
        self.switch.candidate_ready.return_value = [{'container': 'dispatcher'}]
        ca_patch = patch.object(r, 'candidate_ca_hash', return_value=r.sha(b'ca'))
        self.ca_mock = ca_patch.start()
        self.addCleanup(ca_patch.stop)
        for p in (patch.object(r, 's', self.fake), patch.object(r, 'sw', self.switch),
                  patch.object(r, 'owned_state', return_value=[])):
            p.start()
            self.addCleanup(p.stop)

    def test_partial_retirement_does_not_require_old_dispatcher_running(self):
        deps, _ = r.invariants(self.context, self.originals, self.counts, {})
        self.assertEqual(deps[r.DISPATCHER]['spec']['replicas'], 0)
        self.fake.core.guard.source_guard.assert_not_called()
        self.fake.core.pf.controls_snapshot.assert_called_once()
        self.fake.endpoints.assert_called_once()

    def test_other_old_fields_cannot_drift(self):
        self.current[r.WORKER]['spec']['template']['spec']['containers'][0]['image'] = 'other'
        with self.assertRaises(r.RetireFailure):
            r.invariants(self.context, self.originals, self.counts, {})

    def test_unexpected_new_pool_replacement_blocks_retirement(self):
        changed = copy.deepcopy(self.new)
        changed['metadata']['uid'] = 'replacement'
        self.fake.get.return_value = changed
        with self.assertRaises(r.RetireFailure):
            r.invariants(self.context, self.originals, self.counts, {})

    def test_changed_ca_blocks_retirement(self):
        self.ca_mock.return_value = r.sha(b'changed')
        with self.assertRaises(r.RetireFailure):
            r.invariants(self.context, self.originals, self.counts, {})

    def test_no_old_pod_ca_dependency_after_retirement(self):
        self.counts[r.WORKER] = 0
        self.current[r.WORKER] = dep(r.WORKER, 0)
        self.fake.core.ca_bytes.side_effect = RuntimeError('no_ready_ca_source')
        r.invariants(self.context, self.originals, self.counts, {})
        self.fake.core.ca_bytes.assert_not_called()


class CandidateCATests(unittest.TestCase):
    def setUp(self):
        self.fake = MagicMock()
        self.rows = [dict(name='new-%d' % i, container='adapter', uid='uid-%d' % i,
                          container_id='containerd://id-%d' % i) for i in range(6)]
        self.pod = dict(metadata=dict(uid='uid-0'), status=dict(containerStatuses=[
            dict(name='adapter', ready=True, containerID='containerd://id-0')]))
        self.fake.get.return_value = self.pod
        self.fake.call.return_value = json.dumps(dict(ca_sha256=r.sha(b'ca'), bytes=2)).encode()
        p = patch.object(r, 's', self.fake)
        p.start()
        self.addCleanup(p.stop)

    def test_hash_comes_from_candidate_only_without_ca_bytes(self):
        self.assertEqual(r.candidate_ca_hash(self.rows), r.sha(b'ca'))
        args = self.fake.call.call_args.args[0]
        self.assertEqual(args[:4], ['exec', 'new-0', '-c', 'adapter'])
        self.assertNotIn('base64', args[-1])
        self.fake.core.ca_bytes.assert_not_called()

    def test_changed_candidate_process_blocks_ca_acceptance(self):
        changed = copy.deepcopy(self.pod)
        changed['status']['containerStatuses'][0]['containerID'] = 'changed'
        self.fake.get.side_effect = [self.pod, changed]
        with self.assertRaises(r.RetireFailure):
            r.candidate_ca_hash(self.rows)

    def test_missing_candidate_blocks_exec(self):
        with self.assertRaises(r.RetireFailure):
            r.candidate_ca_hash(self.rows[:5])
        self.fake.call.assert_not_called()

    def test_invalid_ca_receipt_fails_closed(self):
        self.fake.call.return_value = b'{"ca_sha256":"skip", "bytes":2}'
        with self.assertRaises(r.RetireFailure):
            r.candidate_ca_hash(self.rows)


class OrderTests(unittest.TestCase):
    def test_dispatcher_then_worker_never_expands(self):
        fake_a, fake_sw, fake_s = MagicMock(), MagicMock(), MagicMock()
        inv = dict(targets=[], terminal=[], pending=[])
        fake_a.old_inventory.return_value = inv
        fake_a.terminal_runtime.return_value = {'no_activity_observed': True}
        candidates = [dict(container='adapter', name='new-worker')]
        fake_sw.candidate_ready.return_value = candidates
        events = []
        def retire(name, context, originals, counts, inventory, report):
            events.append(('scale', name))
            counts[name] = 0
        def wait(name, *args):
            events.append(('wait', name))
        report = dict(termination_verified=[], public_health_checks=[])
        with patch.object(r, 'a', fake_a), patch.object(r, 'sw', fake_sw), patch.object(r, 's', fake_s), \
             patch.object(r, 'original_snapshots', return_value={}), patch.object(r, 'node_leases'), \
             patch.object(r, 'save'), patch.object(r, 'restore_review'), patch.object(r, 'invariants'), \
             patch.object(r, 'retire_once', side_effect=retire), patch.object(r, 'wait_retired', side_effect=wait):
            r.run(report, {}, {'inventory': inv}, 'probe', 'route')
        self.assertEqual(events, [('scale', r.DISPATCHER), ('wait', r.DISPATCHER), ('scale', r.WORKER), ('wait', r.WORKER)])
        self.assertEqual(report['new_pool_slots'], 6)
        self.assertFalse(report['ready_to_expand_16'])

    def test_dispatcher_wait_failure_never_scales_workers(self):
        fake_a, fake_sw, fake_s = MagicMock(), MagicMock(), MagicMock()
        inv = dict(targets=[], terminal=[], pending=[])
        fake_a.old_inventory.return_value = inv
        fake_a.terminal_runtime.return_value = {'no_activity_observed': True}
        fake_sw.candidate_ready.return_value = [dict(container='adapter', name='new-worker')]
        report = dict(termination_verified=[], public_health_checks=[])
        with patch.object(r, 'a', fake_a), patch.object(r, 'sw', fake_sw), patch.object(r, 's', fake_s), \
             patch.object(r, 'original_snapshots', return_value={}), patch.object(r, 'node_leases'), \
             patch.object(r, 'save'), patch.object(r, 'restore_review'), patch.object(r, 'invariants'), \
             patch.object(r, 'retire_once') as retire, \
             patch.object(r, 'wait_retired', side_effect=r.RetireFailure('timeout')):
            with self.assertRaises(r.RetireFailure):
                r.run(report, {}, {'inventory': inv}, 'probe', 'route')
        self.assertEqual(retire.call_count, 1)
        self.assertEqual(retire.call_args.args[0], r.DISPATCHER)


if __name__ == '__main__':
    unittest.main()
