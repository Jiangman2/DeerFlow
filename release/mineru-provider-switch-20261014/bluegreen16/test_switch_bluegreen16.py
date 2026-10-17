import ast
import copy
import json
from pathlib import Path
import types
import unittest
from unittest.mock import MagicMock, patch

from test_stage_bluegreen16 import load, fake_core, object_for, actual_for, m, p, digest

x = load('switch_test', 'switch_bluegreen16.py')
probe = load('switch_probe_test', 'switch_route_probe.py')
HERE = Path(__file__).absolute().parent


class SwitchTests(unittest.TestCase):
    def setUp(self):
        m.core = p.core = fake_core()
        m.prep = p
        x.s = m
        self.created = {n: actual_for(object_for(n)) for n in m.ALLOWED}
        self.old = dict(apiVersion='v1', kind='Service', metadata=dict(name=p.WORKER, uid='public-uid',
            namespace='deer-flow', resourceVersion='123'), spec=dict(type='ClusterIP', clusterIP='10.96.0.5',
            ports=[{'name': 'http', 'port': 50052, 'protocol': 'TCP', 'targetPort': 'http'}], selector={'app': 'old'}))
        self.new = copy.deepcopy(self.old)
        self.new['spec']['selector'] = p.labels('dispatcher')
        self.controls = dict(services=[dict(name=p.WORKER, uid='public-uid', spec=self.old['spec'],
            spec_sha256=digest(self.old['spec'])), dict(name='old-workers', uid='workers-uid', spec={'other': 'unchanged'},
            spec_sha256=digest({'other': 'unchanged'}))], pdbs=[], hpas=[])
        self.staged = dict(mineru_bluegreen16_stage='PASS', ready_for_switch_review=True,
            public_route_changed=False, existing_workloads_modified=False, new_worker_slots=6,
            new_dispatchers=1, provider_http_requests=0, documents_submitted=0,
            created=[dict(name=n, kind=o['kind'], uid=o['metadata']['uid'], spec_sha256=digest(o['spec']))
                     for n, o in self.created.items()], pods=[{'uid': str(i)} for i in range(7)],
            runtime=[{'receipt': {'stage_runtime': 'PASS'}} for _ in range(7)])
        self.ctx = dict(uid='public-uid', specs={'original': self.old['spec'], 'candidate': self.new['spec']},
            controls=self.controls, created=self.created, staged=self.staged,
            prepared={'initial_plan': {}, 'policies': {}, 'concurrency_plan': {}})
        self.root = Path('/var/lib/test-switch')

    def test_python37_and_frozen_hashes(self):
        for file in ('switch_bluegreen16.py', 'switch_route_probe.py'):
            ast.parse((HERE/file).read_bytes(), feature_version=(3, 7))
        self.assertEqual(x.sha((HERE/'stage_bluegreen16.py').read_bytes()), x.STAGE_SOURCE_SHA)
        self.assertEqual(m.sha((HERE/'prepare_bluegreen16.py').read_bytes()), m.PREPARE_SHA)
        self.assertEqual(m.sha((HERE/'stage_runtime_probe.py').read_bytes()), m.PROBE_SHA)

    def test_stage_pass_contract(self):
        x.report_contract(self.staged)

    def test_unaccepted_stage_rejected(self):
        for key, value in (('mineru_bluegreen16_stage','FAIL'), ('ready_for_switch_review',False),
            ('public_route_changed',True), ('existing_workloads_modified',True), ('new_worker_slots',16),
            ('new_dispatchers',2), ('provider_http_requests',1), ('documents_submitted',1)):
            row = dict(self.staged, **{key: value})
            with self.subTest(key=key), self.assertRaises(x.SwitchFailure):
                x.report_contract(row)

    def test_stage_receipt_sets_incomplete_rejected(self):
        for key in ('created', 'pods', 'runtime'):
            row = dict(self.staged, **{key: self.staged[key][:-1]})
            with self.subTest(key=key), self.assertRaises(x.SwitchFailure):
                x.report_contract(row)

    def test_original_and_candidate_route_classification(self):
        self.assertEqual(x.route_state(self.old, self.ctx), 'original')
        self.assertEqual(x.route_state(self.new, self.ctx), 'candidate')

    def test_ambiguous_or_recreated_service_not_adopted(self):
        for change in ('missing', 'uid', 'selector', 'ports', 'deleting'):
            obj = copy.deepcopy(self.old)
            if change == 'missing': obj = None
            elif change == 'uid': obj['metadata']['uid'] = 'recreated'
            elif change == 'selector': obj['spec']['selector'] = {'app': 'other'}
            elif change == 'ports': obj['spec']['ports'][0]['port'] = 8080
            else: obj['metadata']['deletionTimestamp'] = 'now'
            with self.subTest(change=change):
                self.assertEqual(x.route_state(obj, self.ctx), 'UNKNOWN')

    def test_forward_patch_only_replaces_selector_with_cas(self):
        patches = x.make_patch(self.old, self.ctx, 'original', 'candidate')
        self.assertEqual([r['path'] for r in patches], ['/metadata/uid', '/metadata/resourceVersion', '/spec', '/spec/selector'])
        self.assertEqual([r['op'] for r in patches], ['test','test','test','replace'])
        self.assertEqual(patches[0]['value'], 'public-uid')
        self.assertEqual(patches[1]['value'], '123')
        self.assertEqual(patches[2]['value'], self.old['spec'])
        self.assertEqual(patches[3]['value'], p.labels('dispatcher'))
        self.assertEqual(self.old['spec']['selector'], {'app': 'old'})

    def test_rollback_patch_keeps_candidate_and_old_workloads_untouched(self):
        patches = x.make_patch(self.new, self.ctx, 'candidate', 'original')
        self.assertEqual(patches[-1], {'op': 'replace', 'path': '/spec/selector', 'value': {'app': 'old'}})
        self.assertEqual(len(patches), 4)

    def test_same_or_wrong_source_direction_rejected(self):
        for source, target in (('original','original'), ('other','candidate'), ('candidate','original')):
            with self.subTest(source=source, target=target), self.assertRaises(x.SwitchFailure):
                x.make_patch(self.old, self.ctx, source, target)

    def test_patch_answer_requires_full_expected_service(self):
        x.accept_route_answer(self.new, self.ctx, 'candidate')
        for change in ('spec', 'kind', 'name', 'namespace', 'uid'):
            obj = copy.deepcopy(self.new)
            if change == 'spec': obj['spec']['ports'][0]['port'] = 99
            elif change == 'kind': obj['kind'] = 'Deployment'
            else: obj['metadata'][change] = 'wrong'
            with self.subTest(change=change), self.assertRaises(x.SwitchFailure):
                x.accept_route_answer(obj, self.ctx, 'candidate')

    def test_control_override_exact_one_service_and_two_reviewed_new_services(self):
        original = copy.deepcopy(self.controls)
        updated = x.controls_for(self.ctx, 'candidate')
        self.assertEqual(self.controls, original)
        self.assertEqual(updated['services'][0]['spec'], self.new['spec'])
        self.assertEqual(updated['services'][1], original['services'][1])
        self.assertEqual({r['name'] for r in updated['services'][2:]}, set(m.SERVICES))
        self.assertEqual(updated['pdbs'], [])
        self.assertEqual(updated['hpas'], [])

    def test_missing_public_control_rejected(self):
        self.ctx['controls']['services'] = self.ctx['controls']['services'][1:]
        with self.assertRaisesRegex(x.SwitchFailure, 'public_service_missing'):
            x.controls_for(self.ctx, 'candidate')

    def test_duplicate_or_changed_public_control_rejected(self):
        for change in ('duplicate', 'uid', 'spec'):
            context = copy.deepcopy(self.ctx)
            controls = context['controls']['services']
            if change == 'duplicate': controls.append(copy.deepcopy(controls[0]))
            elif change == 'uid': controls[0]['uid'] = 'bad'
            else: controls[0]['spec']['type'] = 'LoadBalancer'
            with self.subTest(change=change), self.assertRaises(x.SwitchFailure):
                x.controls_for(context, 'candidate')

    def test_patch_dry_run_then_single_live_request_intent_precedes_write(self):
        events = []
        def call(args):
            live = '--dry-run=server' not in args
            events.append('live' if live else 'dry')
            self.assertEqual(args[:4], ['patch', 'service', p.WORKER, '--type=json'])
            self.assertNotIn('--force', args)
            if live: self.assertIn('PATCH-REQUESTED.json', events)
            return json.dumps(self.new).encode()
        report = dict(live_patch_attempted=False, live_patch_acknowledged=False)
        with patch.object(m, 'call', side_effect=call) as request, \
             patch.object(m.core, 'save', side_effect=lambda root, name, value: events.append(name)), \
             patch.object(x, 'public_route', return_value=self.old), patch.object(x, 'emit'):
            self.assertEqual(x.patch_once(self.root, self.old, self.ctx, 'original', 'candidate', report), self.new)
            self.assertEqual(request.call_count, 2)
        self.assertTrue(report['live_patch_attempted'])
        self.assertTrue(report['live_patch_acknowledged'])

    def test_uncertain_live_response_never_repeated_or_auto_rolled_back(self):
        report = dict(live_patch_attempted=False, live_patch_acknowledged=False)
        with patch.object(m, 'call', side_effect=[json.dumps(self.new).encode(), TimeoutError()]) as request, \
             patch.object(x, 'public_route', return_value=self.old), patch.object(x, 'emit'), \
             self.assertRaises(TimeoutError):
            x.patch_once(self.root, self.old, self.ctx, 'original', 'candidate', report)
        self.assertEqual(request.call_count, 2)
        self.assertTrue(report['live_patch_attempted'])
        self.assertFalse(report['live_patch_acknowledged'])
        names = [c.args[1] for c in m.core.save.call_args_list]
        self.assertIn('PATCH-REQUESTED.json', names)
        self.assertNotIn('PATCH-ACK.private.json', names)

    def test_server_dry_run_failure_has_no_live_write(self):
        report = dict(live_patch_attempted=False, live_patch_acknowledged=False)
        with patch.object(m, 'call', side_effect=RuntimeError('dry run rejected')) as request, \
             patch.object(x, 'emit'), self.assertRaises(RuntimeError):
            x.patch_once(self.root, self.old, self.ctx, 'original', 'candidate', report)
        self.assertEqual(request.call_count, 1)
        self.assertFalse(report['live_patch_attempted'])

    def test_resource_version_race_stops_before_intent(self):
        now = copy.deepcopy(self.old)
        now['metadata']['resourceVersion'] = '124'
        report = dict(live_patch_attempted=False)
        with patch.object(m, 'call', return_value=json.dumps(self.new).encode()) as request, \
             patch.object(x, 'public_route', return_value=now), patch.object(x, 'emit'), \
             self.assertRaisesRegex(x.SwitchFailure, 'resource_version_changed'):
            x.patch_once(self.root, self.old, self.ctx, 'original', 'candidate', report)
        self.assertEqual(request.call_count, 1)
        self.assertFalse(report['live_patch_attempted'])
        self.assertNotIn('PATCH-REQUESTED.json', [c.args[1] for c in m.core.save.call_args_list])

    def test_mutating_admission_fails_before_live(self):
        bad = copy.deepcopy(self.new)
        bad['spec']['type'] = 'LoadBalancer'
        report = dict(live_patch_attempted=False)
        with patch.object(m, 'call', return_value=json.dumps(bad).encode()) as request, \
             patch.object(x, 'emit'), self.assertRaises(x.SwitchFailure):
            x.patch_once(self.root, self.old, self.ctx, 'original', 'candidate', report)
        self.assertEqual(request.call_count, 1)
        self.assertFalse(report['live_patch_attempted'])

    def test_old_active_work_does_not_require_empty_queue(self):
        m.core.pf.activity = MagicMock(return_value=dict(ready_health_complete=True,
            worker_active_observed=6, dispatcher_busy_observed=6, dispatcher_waiting_observed=20))
        self.assertEqual(x.old_activity({})['dispatcher_waiting_observed'], 20)

    def test_old_unhealthy_blocks_target_acceptance(self):
        m.core.pf.activity = MagicMock(return_value=dict(ready_health_complete=False))
        with self.assertRaises(x.SwitchFailure):
            x.old_activity({})

    def test_endpoint_convergence_is_not_a_drain_proof(self):
        with patch.object(x, 'public_route', return_value=self.new), \
             patch.object(m, 'wait_endpoints', return_value={'ready_endpoints':1}) as wait, \
             patch.object(x.time, 'sleep'):
            receipt = x.confirm_endpoints(self.ctx, 'candidate', [{'uid':'new'}])
            self.assertEqual(wait.call_count, 3)
            self.assertFalse(receipt['old_http_connections_drained'])

    def test_rollback_requires_forward_patch_intent(self):
        with patch.object(Path, 'exists', return_value=False), self.assertRaisesRegex(x.SwitchFailure, 'no_forward_patch_intent'):
            x.rollback_intent(self.ctx)

    def test_rollback_binds_candidate_spec_stage_and_uid(self):
        good = dict(stage_report_sha256=x.STAGED_SHA, service=p.WORKER, uid=self.ctx['uid'],
            source='original', target='candidate', target_spec_sha256=digest(self.new['spec']))
        m.core.pf.read_private = MagicMock(return_value=json.dumps(good).encode())
        with patch.object(Path, 'exists', return_value=True):
            x.rollback_intent(self.ctx)
            for key in good:
                bad = dict(good, **{key: 'changed'})
                m.core.pf.read_private.return_value = json.dumps(bad).encode()
                with self.subTest(key=key), self.assertRaises(x.SwitchFailure):
                    x.rollback_intent(self.ctx)

    def test_candidate_public_health_accepts_busy_and_waiting(self):
        row = dict(status='ok', role='dispatcher', dispatcher=dict(live=6, discovered=6, minimum=6,
            busy=6, waiting=9, quarantined=0, ready=True, capacity_target_met=True))
        result = probe.validate_health(row, 'candidate')
        self.assertEqual(result['waiting'], 9)

    def test_public_health_rejects_unreviewed_capacity_or_quarantine(self):
        row = dict(status='ok', role='dispatcher', dispatcher=dict(live=6, discovered=6, minimum=6,
            busy=0, waiting=0, quarantined=0, ready=True, capacity_target_met=True))
        for key, value in (('live',5), ('discovered',7), ('minimum',16), ('busy',7), ('quarantined',1),
                           ('ready',False), ('waiting',-1), ('capacity_target_met',False), ('busy',True)):
            bad = copy.deepcopy(row)
            bad['dispatcher'][key] = value
            with self.subTest(key=key), self.assertRaises(probe.ProbeFailure):
                probe.validate_health(bad, 'candidate')

    def test_original_public_health_uses_accepted_degraded_baseline(self):
        row = dict(status='ok', role='dispatcher', dispatcher=dict(live=6, discovered=6, minimum=9,
            busy=6, waiting=10, quarantined=0, ready=True, capacity_target_met=False))
        self.assertEqual(probe.validate_health(row, 'original')['live'], 6)
        row['dispatcher']['live'] = 5
        with self.assertRaises(probe.ProbeFailure):
            probe.validate_health(row, 'original')

    def test_real_route_invariants_include_new_pdb_hpa_and_service_ownership(self):
        deps = {name: dict(metadata={'name':name}, spec={}) for name in ('old-adapter','old-dispatcher')}
        deps[p.WORKER] = dict(metadata={'name':p.WORKER}, spec={})
        m.core.inv.BASELINES = deps
        m.core.inv.get = MagicMock(side_effect=lambda kind, name: deps[name])
        m.core.inv.baseline = MagicMock()
        m.core.inv.owned_pods = MagicMock(return_value=[])
        m.core.guard.source_guard = MagicMock()
        m.core.pf.controls_snapshot = MagicMock()
        m.core.ca_bytes = MagicMock(return_value=b'ca')
        self.ctx['prepared']['ca_sha256'] = x.sha(b'ca')
        def get(kind, name=None, **kwargs):
            if name in self.created: return self.created[name]
            if name == p.WORKER: return self.new
            return {'items': []}
        with patch.object(m, 'get', side_effect=get), patch.object(p, 'live_policies', return_value={}), \
             patch.object(p, 'transition_budget', return_value={}), patch.object(p, 'routes_disjoint'), \
             patch.object(m, 'credential_identity'):
            x.invariants(self.ctx, 'candidate')
        combined, controls = m.core.pf.controls_snapshot.call_args.args
        self.assertEqual(set(combined), set(deps)|set(m.WORKERS+[m.DISPATCHER]))
        self.assertEqual(controls['services'][0]['spec'], self.new['spec'])

    def switch_stubs(self, stack, rollback=False):
        deps = {'old': 'deps'}
        rows = [dict(uid=str(i), name='pod'+str(i), container='adapter' if i < 6 else 'dispatcher') for i in range(7)]
        stack.enter_context(patch.object(x, 'invariants', return_value=deps))
        stack.enter_context(patch.object(x, 'old_activity', return_value={'dispatcher_waiting_observed':12}))
        stack.enter_context(patch.object(x, 'old_dispatcher_rows', return_value=[{'name':'old-dispatcher'}]))
        ready = stack.enter_context(patch.object(x, 'candidate_ready', return_value=rows))
        stack.enter_context(patch.object(m, 'endpoints'))
        stack.enter_context(patch.object(m, 'probe_pod', return_value={'probe':'PASS'}))
        m.core.monitoring = MagicMock(return_value=[])
        stack.enter_context(patch.object(x, 'public_route', return_value=self.new if rollback else self.old))
        mutation = stack.enter_context(patch.object(x, 'patch_once', return_value=self.old if rollback else self.new))
        stack.enter_context(patch.object(x, 'confirm_endpoints', return_value={'old_http_connections_drained':False}))
        stack.enter_context(patch.object(x, 'public_health', return_value={'health':'PASS'}))
        stack.enter_context(patch.object(x, 'rollback_intent'))
        return ready, mutation

    def test_forward_keeps_old_pool_and_does_not_claim_16_active(self):
        from contextlib import ExitStack
        with ExitStack() as stack:
            ready, mutation = self.switch_stubs(stack)
            report = {}
            x.switch(self.root, self.ctx, 'stage-probe', 'route-probe', False, report)
            mutation.assert_called_once()
            self.assertEqual(report['new_pool_slots'], 6)
            self.assertFalse(report['old_pool_retired'])
            self.assertFalse(report['final_16_expansion'])
            self.assertEqual(report['old_http_drain'], 'NOT_PROVEN')
            self.assertEqual(report['release_acceptance'], 'PENDING_REAL_DOCUMENT_END_TO_END')

    def test_rollback_does_not_depend_on_failed_candidate_readiness(self):
        from contextlib import ExitStack
        with ExitStack() as stack:
            ready, mutation = self.switch_stubs(stack, rollback=True)
            ready.side_effect = AssertionError('must not require broken candidate health for rollback')
            report = {}
            x.switch(self.root, self.ctx, 'stage-probe', 'route-probe', True, report)
            ready.assert_not_called()
            self.assertEqual(mutation.call_args.args[3:5], ('candidate','original'))
            self.assertEqual(report['public_route'], 'original')

    def test_post_patch_failure_keeps_uncertainty_and_never_retries(self):
        from contextlib import ExitStack
        with ExitStack() as stack:
            ready, mutation = self.switch_stubs(stack)
            stack.enter_context(patch.object(x, 'confirm_endpoints', side_effect=RuntimeError('route timeout')))
            report = {}
            with self.assertRaises(RuntimeError):
                x.switch(self.root, self.ctx, 'stage-probe', 'route-probe', False, report)
            mutation.assert_called_once()
            self.assertNotEqual(report.get('mineru_bluegreen16_switch'), 'PASS')

    def test_status_only_uses_reads_no_exec_or_mutation(self):
        def get(kind, name=None, **kwargs):
            return self.new if kind == 'services' else {'items': []}
        with patch.object(m, 'get', side_effect=get) as get, patch.object(Path, 'exists', return_value=False), \
             patch.object(x, 'emit') as emit, patch.object(m, 'call') as call:
            x.status(self.ctx)
            call.assert_not_called()
            m.core.save.assert_not_called()
            self.assertEqual(emit.call_args.args[0]['public_route'], 'candidate')
            self.assertEqual(emit.call_args.args[0]['production_writes'], 0)
            self.assertTrue(all(c.args[0] in ('services','endpointslices.discovery.k8s.io') for c in get.call_args_list))

    def test_full_receipt_binding_reads_locked_stage_and_prepared_evidence(self):
        prepared = dict(mineru_bluegreen16_prepare='PASS', production_workloads_mutated=False,
            ready_for_stage_review=True, initial_plan={'controller':'ecs-zdz-pro-0012', 'workers':m.NODES, 'total_slots':7},
            concurrency_plan={'new_initial_slots':6, 'old_reserved_slots':10, 'target_slots':16},
            candidate=m.core.inv.CANDIDATE, dry_runs=[dict(name=n, kind=o['kind'], server_dry_run='PASS')
                for n,o in self.created.items()], bindings={'load':'sha'}, load_review={'selected_global_slots':16},
            route_plan={'uid':'public-uid', 'original_spec_sha256':digest(self.old['spec']),
                        'proposed_spec_sha256':digest(self.new['spec'])})
        self.staged.update(prepare_report_sha256=m.REPORT_SHA, load_review=prepared['load_review'],
                           concurrency_plan=prepared['concurrency_plan'])
        def locked(path, expected):
            self.assertIn(expected, (m.REPORT_SHA,x.STAGED_SHA))
            return json.dumps(self.staged if path.parent == x.STAGED else prepared).encode()
        def private(path):
            if path.name == 'PUBLIC-SERVICE.before.private.json': return json.dumps(self.old).encode()
            name = path.name.removesuffix('.CREATED.private.json')
            return json.dumps(self.created[name]).encode()
        m.core.pf.receipt_bindings = MagicMock(return_value=(prepared['bindings'],self.controls))
        m.core.pf.read_private = MagicMock(side_effect=private)
        with patch.object(m, 'locked', side_effect=locked), patch.object(p, 'load_acceptance', return_value=prepared['load_review']):
            result = x.bind()
        self.assertEqual(result['specs']['candidate'], self.new['spec'])
        self.assertEqual(set(result['created']), set(m.ALLOWED))

    def test_caller_pod_replaced_during_public_probe_rejected(self):
        row = dict(name='worker',uid='uid',container='adapter',container_id='id',node='node')
        before = dict(metadata={'uid':'uid'},status={'containerStatuses':[dict(name='adapter',containerID='id',ready=True)]})
        after = copy.deepcopy(before)
        after['metadata']['uid'] = 'replacement'
        receipt = dict(public_service_health='PASS',expected_pool='candidate',provider_requests=0,documents_submitted=0)
        with patch.object(m,'get',side_effect=[before,after]), patch.object(m,'call',return_value=json.dumps(receipt).encode()), \
             patch.object(x,'emit'), self.assertRaisesRegex(x.SwitchFailure,'process_changed'):
            x.public_health(row, 'probe', 'candidate', 'candidate', self.root, 0)

    def test_candidate_restart_cannot_inherit_stage_acceptance(self):
        with patch.object(m,'deployment_current',return_value=dict(metadata={'generation':1},
            status={'observedGeneration':1,'availableReplicas':1})), \
             patch.object(m.core.inv,'owned_pods',return_value=[{}],create=True), \
             patch.object(m,'validate_pod',return_value={'uid':'replacement'}), \
             self.assertRaisesRegex(x.SwitchFailure,'pod_or_process_replaced'):
            x.candidate_ready(self.ctx)

    def test_main_status_has_no_evidence_directory_creation(self):
        import sys
        with patch.object(sys,'argv',['switch.py','--status']), \
             patch.object(x.os,'geteuid',return_value=0,create=True), patch.object(x.os,'umask'), \
             patch.object(x.socket,'gethostname',return_value='datax-01'), \
             patch.object(x,'bootstrap',return_value=('stage','route')), patch.object(x,'bind',return_value=self.ctx), \
             patch.object(x,'status') as status, patch.object(Path,'mkdir') as mkdir, patch.object(x,'switch') as switch:
            self.assertEqual(x.main(),0)
            status.assert_called_once_with(self.ctx)
            mkdir.assert_not_called()
            switch.assert_not_called()

    def test_main_repeated_switch_cannot_patch(self):
        import sys
        with patch.object(sys,'argv',['switch.py','--switch']), \
             patch.object(x.os,'geteuid',return_value=0,create=True), patch.object(x.os,'umask'), \
             patch.object(x.socket,'gethostname',return_value='datax-01'), \
             patch.object(x,'bootstrap',return_value=('stage','route')), patch.object(x,'bind',return_value=self.ctx), \
             patch.object(m,'safe_dir'), patch.object(Path,'exists',return_value=True), \
             patch.object(Path,'mkdir') as mkdir, patch.object(x,'switch') as switch, \
             self.assertRaisesRegex(x.SwitchFailure,'operation_exists'):
            x.main()
        mkdir.assert_not_called()
        switch.assert_not_called()

    def test_early_bootstrap_failure_is_sanitized(self):
        with patch.object(x, 's', types.SimpleNamespace()):
            result = x.diagnostic(RuntimeError('private unsafe text'))
            self.assertEqual(result['error_type'], 'RuntimeError')
            self.assertFalse(result['raw_error_printed'])
            self.assertNotIn('private unsafe text', json.dumps(result))


if __name__ == '__main__':
    unittest.main()
