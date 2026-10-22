import ast
import copy
import json
from pathlib import Path
import types
import hashlib
import logging
import threading
import time
import unittest
import uuid
from unittest.mock import MagicMock, patch

import expand16 as x
import expand_runtime_probe as probe
import retire_old_pool as retire


def deployment(name=None, count=1):
    name = name or next(iter(x.PLAN))
    return dict(apiVersion='apps/v1', kind='Deployment',
        metadata=dict(name=name, namespace='deer-flow', uid='dep-id', resourceVersion='42', generation=2),
        spec=dict(replicas=count, template=dict(metadata=dict(labels={'app': name}),
            spec=dict(containers=[dict(name='adapter', image='candidate')]))),
        status=dict(observedGeneration=2, replicas=count, readyReplicas=count,
                    availableReplicas=count, updatedReplicas=count))


def pod(uid, ready=True):
    return dict(metadata=dict(uid=uid, name='pod-'+uid),
        status=dict(phase='Running', containerStatuses=[dict(name='adapter', ready=ready,
            restartCount=0, state=dict(running=dict(startedAt='then')))]))


def row(uid, dep=None):
    return dict(uid=uid, name='pod-'+uid, container='adapter', deployment=dep or next(iter(x.PLAN)),
                container_id='cid-'+uid, node='node1', ip='10.0.0.1')


class PatchTests(unittest.TestCase):
    def setUp(self):
        self.r, self.s = MagicMock(), MagicMock()
        self.r.deployment_matches = retire.deployment_matches
        self.stack = patch.multiple(x, r=self.r, s=self.s)
        self.stack.start()
        self.addCleanup(self.stack.stop)

    def test_grammar(self):
        for m in (x, probe):
            ast.parse(Path(m.__file__).read_bytes(), feature_version=(3, 7))

    def test_six_only_positive_replica_changes(self):
        self.assertEqual(sum(x.PLAN.values()), 16)
        for name, target in x.PLAN.items():
            before = deployment(name)
            change = x.scale_patch(before, target)
            self.assertEqual([(v['op'], v['path']) for v in change], [
                ('test', '/metadata/uid'), ('test', '/metadata/resourceVersion'),
                ('test', '/spec'), ('replace', '/spec/replicas')])
            self.assertEqual(change[-1]['value'], target)
            self.assertEqual(before['spec']['replicas'], 1)

    def test_no_dispatcher_old_pool_or_downscale(self):
        for name in ('deer-flow-mineru-dedicated-dispatch-r1', 'deer-flow-mineru-adapter',
                     'deer-flow-mineru-dispatcher'):
            with self.assertRaises(x.ExpandFailure):
                x.scale_patch(deployment(name), 3)
        for target in (0, 1, 16, True):
            with self.assertRaises(x.ExpandFailure):
                x.scale_patch(deployment(), target)
        with self.assertRaises(x.ExpandFailure):
            x.scale_patch(deployment(count=3), 3)

    def test_wrong_namespace_rejected(self):
        before = deployment()
        before['metadata']['namespace'] = 'wrong'
        with self.assertRaises(x.ExpandFailure):
            x.scale_patch(before, 3)

    def test_patch_capture_bypasses_only_read_guard_for_exact_approved_patch(self):
        before = deployment()
        name = before['metadata']['name']
        self.r.a.locked.return_value = json.dumps(x.scale_patch(before, 3)).encode()
        self.s.core.pf.read_private.return_value = json.dumps(before).encode()
        with patch.object(x, 'PATCH_CAPTURE', return_value=b'{}') as capture:
            x.patch_call(name, x.ROOT/(name+'.patch.private.json'), 'hash', True)
            args = capture.call_args.args[0]
            self.assertEqual(args[:8], ['kubectl', '--request-timeout=20s', '-n', 'deer-flow',
                'patch', 'deployment', name, '--type=json'])
            self.assertEqual(args[-1], '--dry-run=server')
            capture.reset_mock()
            x.patch_call(name, x.ROOT/(name+'.patch.private.json'), 'hash', False)
            self.assertNotIn('--dry-run=server', capture.call_args.args[0])
            self.assertEqual(capture.call_count, 1)

    def test_patch_altered_template_or_path_is_rejected(self):
        before = deployment()
        name = before['metadata']['name']
        change = x.scale_patch(before, 3)
        change[-1] = dict(op='replace', path='/spec/template', value={})
        self.r.a.locked.return_value = json.dumps(change).encode()
        self.s.core.pf.read_private.return_value = json.dumps(before).encode()
        with patch.object(x, 'PATCH_CAPTURE') as capture:
            with self.assertRaises(x.ExpandFailure):
                x.patch_call(name, x.ROOT/(name+'.patch.private.json'), 'hash', False)
            with self.assertRaises(x.ExpandFailure):
                x.patch_call(name, Path('/tmp/arbitrary'), 'hash', False)
            capture.assert_not_called()

    def test_uncertain_live_request_is_not_retried_and_counts_not_claimed(self):
        before, report = deployment(), dict(patches_attempted=[], patches_acknowledged=[], resource_checks=[])
        name = before['metadata']['name']
        counts = {n: 1 for n in x.PLAN}
        self.s.get.return_value = before
        context = dict(created={name: before})
        with patch.object(x, 'current', return_value=({}, [], True)), \
             patch.object(x, 'capacity', return_value=[]), patch.object(x, 'emit'), \
             patch.object(x, 'patch_call', side_effect=[deployment(count=3), RuntimeError('timeout')]) as call:
            with self.assertRaises(RuntimeError):
                x.apply_one(name, context, {}, {}, counts, report)
        self.assertEqual(call.call_count, 2)  # one dry run, one live attempt.
        self.assertEqual(report['patches_attempted'], [name])
        self.assertEqual(report['patches_acknowledged'], [])
        self.assertEqual(counts[name], 1)
        saves = [a.args[0] for a in self.r.save.call_args_list]
        self.assertIn(name+'.PATCH-REQUESTED.json', saves)
        self.assertNotIn(name+'.PATCH-ACK.private.json', saves)

    def test_wrong_dry_run_response_prevents_live_write(self):
        before = deployment()
        name = before['metadata']['name']
        dry = deployment(count=3)
        dry['spec']['template'] = {}
        report = dict(patches_attempted=[], patches_acknowledged=[], resource_checks=[])
        self.s.get.return_value = before
        with patch.object(x, 'current', return_value=({}, [], True)), \
             patch.object(x, 'capacity', return_value=[]), patch.object(x, 'emit'), \
             patch.object(x, 'patch_call', return_value=dry) as call:
            with self.assertRaisesRegex(x.ExpandFailure, 'admission_changed'):
                x.apply_one(name, dict(created={name: before}), {}, {}, {n: 1 for n in x.PLAN}, report)
        self.assertEqual(call.call_count, 1)
        self.assertEqual(report['patches_attempted'], [])


class PodTests(unittest.TestCase):
    def setUp(self):
        self.s = MagicMock()
        self.stack = patch.object(x, 's', self.s)
        self.stack.start()
        self.addCleanup(self.stack.stop)
        self.s.validate_pod.side_effect = lambda d, p: row(p['metadata']['uid'], d['metadata']['name'])

    def test_expansion_does_not_require_original_one_pod_count(self):
        self.s.core.inv.owned_pods.return_value = [pod('old'), pod('new1'), pod('new2')]
        rows, uids, converged = x.running_rows(deployment(count=3), {'old': row('old')}, False)
        self.assertTrue(converged)
        self.assertEqual(len(rows), 3)
        self.assertEqual(uids, {'old', 'new1', 'new2'})

    def test_pending_new_pod_allowed_but_original_pending_not_allowed(self):
        self.s.core.inv.owned_pods.return_value = [pod('old'), pod('new1', False)]
        _, _, converged = x.running_rows(deployment(count=3), {'old': row('old')}, True)
        self.assertFalse(converged)
        self.s.core.inv.owned_pods.return_value = [pod('old', False)]
        with self.assertRaisesRegex(x.ExpandFailure, 'existing_process_unready'):
            x.running_rows(deployment(count=3), {'old': row('old')}, True)

    def test_original_uid_must_remain(self):
        self.s.core.inv.owned_pods.return_value = [pod('replacement')]
        with self.assertRaisesRegex(x.ExpandFailure, 'original_process_replaced'):
            x.running_rows(deployment(count=3), {'old': row('old')}, True)

    def test_prior_added_worker_becomes_pinned_too(self):
        self.s.core.inv.owned_pods.return_value = [pod('old'), pod('replacement'), pod('new2')]
        with self.assertRaisesRegex(x.ExpandFailure, 'original_process_replaced'):
            x.running_rows(deployment(count=3), {'old': row('old'), 'new1': row('new1')}, False)

    def test_restart_or_failure_rejected(self):
        for state in ('restart', 'Failed', 'ErrImageNeverPull'):
            p = pod('old')
            if state == 'restart':
                p['status']['containerStatuses'][0]['restartCount'] = 1
            elif state == 'Failed':
                p['status']['phase'] = 'Failed'
            else:
                p['status']['containerStatuses'][0]['state'] = dict(waiting=dict(reason=state))
            self.s.core.inv.owned_pods.return_value = [p]
            with self.subTest(state=state), self.assertRaises(x.ExpandFailure):
                x.running_rows(deployment(), {'old': row('old')}, True)

    def test_only_converged_controller_passes(self):
        self.s.core.inv.owned_pods.return_value = [pod('old')]
        d = deployment()
        d['status']['observedGeneration'] = 1
        with self.assertRaisesRegex(x.ExpandFailure, 'worker_controller_not_converged'):
            x.running_rows(d, {'old': row('old')}, False)


class RuntimeTests(unittest.TestCase):
    def dispatcher(self, count):
        return dict(status='ok', role='dispatcher', dispatcher=dict(
            discovered=count, live=count, minimum=6, busy=count, waiting=12,
            quarantined=0, ready=True, capacity_target_met=True))

    def test_minimum_six_is_not_actual_worker_ceiling(self):
        for count in (6, 8, 10, 12, 14, 15, 16):
            result = probe.validate_health(self.dispatcher(count), 'dispatcher', count)
            self.assertEqual(result['minimum'], 6)
            self.assertEqual(result['live'], count)

    def test_six_health_is_not_sixteen_acceptance(self):
        with self.assertRaises(probe.ProbeFailure):
            probe.validate_health(self.dispatcher(6), 'dispatcher', 16)

    def test_busy_worker_is_valid_but_parallel_native_parsing_is_not(self):
        result = dict(status='ok', role='worker', parser=dict(active=1, waiting=2))
        self.assertEqual(probe.validate_health(result, 'worker', 16)['active'], 1)
        result['parser']['active'] = 2
        with self.assertRaises(probe.ProbeFailure):
            probe.validate_health(result, 'worker', 16)

    def test_modified_dispatcher_minimum_blocks(self):
        value = self.dispatcher(16)
        value['dispatcher']['minimum'] = 16
        with self.assertRaisesRegex(probe.ProbeFailure, 'dispatcher_minimum_changed'):
            probe.validate_health(value, 'dispatcher', 16)

    def test_missing_quarantined_or_unknown_counters_fail(self):
        for key, value in (('live', 15), ('quarantined', 1), ('ready', False), ('busy', 17), ('waiting', None)):
            data = self.dispatcher(16)
            data['dispatcher'][key] = value
            with self.subTest(key=key), self.assertRaises(probe.ProbeFailure):
                probe.validate_health(data, 'dispatcher', 16)

    def test_no_expansion_beyond_16(self):
        with self.assertRaises(probe.ProbeFailure):
            probe.validate_health(self.dispatcher(64), 'dispatcher', 64)

    def test_exact_candidate_dispatcher_discovers_16_without_resetting_queue(self):
        path = Path(__file__).parents[3]/'docker/mineru_adapter/app.py'
        raw = path.read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(),
            'a509a7e145ba3d5d72ba86036437a9e5da2e46678054558522a6d61b66a22e74')
        tree = ast.parse(raw)
        body = ast.parse('from __future__ import annotations').body
        body += [n for n in tree.body if isinstance(n, ast.ClassDef)
                 and n.name in ('ParseDispatcher', 'DispatchQueueFull', 'DispatchUnavailable')]
        ns = dict(threading=threading, time=time, uuid=uuid, logger=logging.getLogger('test'))
        exec(compile(ast.Module(body=body, type_ignores=[]), '<exact-candidate-dispatcher>', 'exec'), ns)
        addresses = ['10.0.0.%d' % n for n in range(1, 7)]
        d = ns['ParseDispatcher'](backend_host='headless', backend_port=50052,
            maximum_waiting=64, minimum_backends=6, wait_timeout_seconds=1, request_timeout_seconds=10,
            session=object(), resolver=lambda host: list(addresses), prober=lambda host, path: True)
        self.assertEqual(d.snapshot()['live'], 6)
        # Mimic live work; expanding DNS must not clear either queue or busy set.
        d._busy.add(addresses[0])
        d._pending.append((1, 1, 'queued-request'))
        addresses.extend('10.0.0.%d' % n for n in range(7, 17))
        result = d.snapshot()
        self.assertEqual((result['discovered'], result['live'], result['minimum']), (16, 16, 6))
        self.assertEqual((result['busy'], result['waiting']), (1, 1))
        d._pending.clear()
        picked = []
        for _ in range(15):
            chosen, _, _ = d._acquire_backend('bulk')
            picked.append(chosen)
        self.assertEqual(len(set(picked)), 15)
        self.assertEqual(len(d._busy), 16)

class ExpandedBaselineTests(unittest.TestCase):
    def fixture(self):
        s, r, c = MagicMock(), MagicMock(), MagicMock()
        s.WORKERS = list(x.PLAN)
        s.DISPATCHER = 'deer-flow-mineru-dedicated-dispatch-r1'
        s.SERVICES = ['workers', 'preview']
        s.prep.LABEL, s.prep.RELEASE = 'release', 'pinned'
        r.TARGETS = ['old-d', 'old-w']
        r.deployment_matches = retire.deployment_matches
        r.sw.BACK = MagicMock()
        (r.sw.BACK/'PATCH-REQUESTED.json').exists.return_value = False
        originals = {n: deployment(n, 1) for n in r.TARGETS}
        retired = {n: deployment(n, 0) for n in r.TARGETS}
        s.core.inv.get.side_effect = lambda kind, name: retired[name]
        r.owned_state.return_value = []
        original_created, actual, pods_by_dep, pins, all_pods = {}, {}, {}, [], []
        for name in s.WORKERS+[s.DISPATCHER]:
            original_created[name] = deployment(name, 1)
            actual[name] = deployment(name, x.PLAN.get(name, 1))
            pods = []
            for number in range(actual[name]['spec']['replicas']):
                uid = name+'-'+str(number)
                p = pod(uid)
                p['test_deployment'] = name
                pods.append(p)
                if number == 0:
                    v = row(uid, name)
                    v['container'] = 'dispatcher' if name == s.DISPATCHER else 'adapter'
                    pins.append(v)
            pods_by_dep[name] = pods
            all_pods.extend(pods)
        for name in s.SERVICES:
            original_created[name] = dict(kind='Service', metadata=dict(uid=name),
                                          spec=dict(selector=dict(role=name)))
            actual[name] = copy.deepcopy(original_created[name])
        def get(kind, name=None, **kwargs):
            if kind == 'pods' and 'selector' in kwargs:
                return dict(items=all_pods)
            return actual[name]
        s.get.side_effect = get
        s.core.inv.owned_pods.side_effect = lambda dep: pods_by_dep[dep['metadata']['name']]
        def validate(dep, p):
            v = row(p['metadata']['uid'], dep['metadata']['name'])
            v['container'] = 'dispatcher' if dep['metadata']['name'] == s.DISPATCHER else 'adapter'
            return v
        s.validate_pod.side_effect = validate
        s.prep.live_policies.return_value = {}
        r.candidate_ca_hash.return_value = 'ca-hash'
        ctx = dict(created=original_created, staged=dict(pods=pins),
                   prepared=dict(policies={}, ca_sha256='ca-hash'))
        return s, r, c, originals, actual, ctx

    def test_actual_16_pool_baseline_does_not_call_fixed_six_ready_guard(self):
        s, r, c, originals, actual, ctx = self.fixture()
        with patch.multiple(x, s=s, r=r, c=c):
            _, rows, converged = x.current(ctx, originals, {}, dict(x.PLAN))
        self.assertEqual(len(rows), 17)
        self.assertTrue(converged)
        r.sw.candidate_ready.assert_not_called()
        r.invariants.assert_not_called()
        self.assertEqual(len(r.candidate_ca_hash.call_args.args[0]), 7)

    def test_dispatcher_template_and_old_zero_state_are_not_exceptions(self):
        for kind in ('dispatcher', 'old'):
            s, r, c, originals, actual, ctx = self.fixture()
            if kind == 'dispatcher':
                actual[s.DISPATCHER]['spec']['template']['spec']['containers'][0]['image'] = 'other'
            else:
                s.core.inv.get.side_effect = lambda resource, name: deployment(name, 1)
            with self.subTest(kind=kind), patch.multiple(x, s=s, r=r, c=c), self.assertRaises(x.ExpandFailure):
                x.current(ctx, originals, {}, dict(x.PLAN))


if __name__ == '__main__':
    unittest.main()
