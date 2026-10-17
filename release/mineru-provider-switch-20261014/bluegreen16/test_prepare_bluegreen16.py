import ast
import copy
import importlib.util
import json
from pathlib import Path
import types
import unittest
from unittest.mock import patch

HERE = Path(__file__).absolute().parent
spec = importlib.util.spec_from_file_location('bluegreen_prepare', HERE/'prepare_bluegreen16.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def old(role='worker'):
    app = m.WORKER if role == 'worker' else m.DISPATCHER
    return {'kind': 'Deployment', 'metadata': {'name': app, 'uid': app+'-uid', 'resourceVersion': '17'},
        'spec': {'replicas': 9 if role == 'worker' else 1,
            'strategy': {'type': 'RollingUpdate', 'rollingUpdate': {'maxSurge': 1}},
            'template': {'metadata': {'labels': {'app': app}, 'annotations': {'checksum/config': 'kept'}},
                'spec': {'nodeSelector': {'kubernetes.io/arch': 'arm64'},
                    'affinity': {'podAntiAffinity': {'preferredDuringSchedulingIgnoredDuringExecution': [{'weight': 100}]}},
                    'topologySpreadConstraints': [{'maxSkew': 1}],
                    'volumes': [{'name': 'ca', 'configMap': {'name': 'existing-ca'}}],
                    'securityContext': {'runAsUser': 0},
                    'containers': [{'name': 'adapter' if role == 'worker' else 'dispatcher',
                        'image': 'old-image', 'imagePullPolicy': 'Never',
                        'envFrom': [{'secretRef': {'name': 'old-settings'}}],
                        'env': [{'name': 'KEEP', 'value': 'unchanged'},
                                {'name': 'MINERU_REQUEST_MODE', 'value': 'sync'},
                                {'name': 'MINERU_API_TOKEN', 'valueFrom': {'secretKeyRef': {'name': 'old-settings', 'key': 'token'}}}],
                        'volumeMounts': [{'name': 'ca', 'mountPath': '/ca', 'readOnly': True}],
                        'command': ['/app/.venv/bin/python', '/app/deerflow_mineru_adapter.py'],
                        'resources': {'requests': {'cpu': '500m'}, 'limits': {'cpu': '2', 'memory': '4Gi'}}}]}}}}


def pods(count, terminating=0):
    return [{'metadata': dict(name='w-'+str(i), **({'deletionTimestamp': 'now'} if i < terminating else {})),
             'status': {'phase': 'Running'}} for i in range(count)]


def distribute(rows, count):
    available = {r['node']: r['additional_isolated_worker_slots_planning_only'] for r in rows}
    result = {}
    for _ in range(count):
        options = [n for n in available if result.get(n, 0) < available[n]]
        if not options:
            return None
        name = min(options, key=lambda n: (result.get(n, 0), n))
        result[name] = result.get(name, 0)+1
    return result


class PrepareTests(unittest.TestCase):
    def setUp(self):
        m.core = types.SimpleNamespace(
            guard=types.SimpleNamespace(NODES={'datax-01': {}, 'datax-02': {}}),
            pf=types.SimpleNamespace(host=types.SimpleNamespace(OLD_IMAGE='old-image'), distribute=distribute),
            inv=types.SimpleNamespace(CANDIDATE={'image': 'candidate-image'}, digest=lambda obj: m.sha(json.dumps(obj, sort_keys=True).encode())),
            SECRET='candidate-secret', SECRET_UID='candidate-secret-uid')

    def test_python37_syntax(self):
        ast.parse((HERE/'prepare_bluegreen16.py').read_text(encoding='utf-8'), feature_version=(3, 7))

    def test_hashes_are_sha256(self):
        for value in (m.CORE_SHA, m.MANIFEST_SHA, m.LOAD_SHA):
            self.assertRegex(value, '^[a-f0-9]{64}$')

    def test_budget_counts_desired_not_just_ready(self):
        row = m.transition_budget(old(), pods(6))
        self.assertEqual((row['old_reserved_slots'], row['new_initial_slots']), (10, 6))

    def test_budget_counts_pending(self):
        p = pods(9)
        p[8]['status']['phase'] = 'Pending'
        self.assertEqual(m.transition_budget(old(), p)['old_reserved_slots'], 10)

    def test_budget_counts_excess_old_pods(self):
        self.assertEqual(m.transition_budget(old(), pods(11))['new_initial_slots'], 5)

    def test_budget_counts_terminating_separately(self):
        self.assertEqual(m.transition_budget(old(), pods(10, 2))['old_reserved_slots'], 12)

    def test_budget_ignores_completed_pods(self):
        p = pods(12)
        for r in p[9:]:
            r['status']['phase'] = 'Succeeded'
        self.assertEqual(m.transition_budget(old(), p)['old_reserved_slots'], 10)

    def test_no_remaining_slots_fails(self):
        with self.assertRaises(m.PrepareFailure):
            m.transition_budget(old(), pods(16))

    def test_surge_percent_rounds_up(self):
        d = old()
        d['spec']['strategy']['rollingUpdate']['maxSurge'] = '25%'
        self.assertEqual(m.surge_count(d), 3)

    def test_surge_recreate_zero(self):
        d = old()
        d['spec']['strategy'] = {'type': 'Recreate'}
        self.assertEqual(m.surge_count(d), 0)

    def test_invalid_surge_rejected(self):
        for v in (True, -1, 'evil', '101%', 16):
            d = old()
            d['spec']['strategy']['rollingUpdate']['maxSurge'] = v
            with self.subTest(v=v), self.assertRaises(m.PrepareFailure):
                m.surge_count(d)

    def test_worker_delta_keeps_old_untouched(self):
        d = old()
        before = copy.deepcopy(d)
        obj, info = m.deployment(d, 'worker', 'datax-02', 2, 6)
        self.assertEqual(d, before)
        p = obj['spec']['template']['spec']
        self.assertEqual(p['nodeSelector'], {'kubernetes.io/arch': 'arm64', 'kubernetes.io/hostname': 'datax-02'})
        self.assertNotIn('podAntiAffinity', p['affinity'])
        self.assertNotIn('topologySpreadConstraints', p)
        self.assertEqual(p['volumes'], d['spec']['template']['spec']['volumes'])
        env = {r['name']: r for r in p['containers'][0]['env']}
        self.assertEqual(env['KEEP']['value'], 'unchanged')
        self.assertEqual(env['MINERU_API_TOKEN']['valueFrom']['secretKeyRef']['name'], 'candidate-secret')
        self.assertNotIn('value', env['MINERU_API_TOKEN'])
        self.assertEqual(env['MINERU_REQUEST_MODE']['value'], 'async')
        self.assertEqual(env['MINERU_TRUST_ENV']['value'], 'false')
        self.assertEqual(env['MINERU_TASK_QUERY_URL']['value'], m.TASK_URL)
        self.assertEqual(p['containers'][0]['resources'], m.RESOURCES)
        self.assertTrue(info['old_preferred_pod_affinity_replaced'])

    def test_dispatcher_only_sees_new_headless_service(self):
        obj, _ = m.deployment(old('dispatcher'), 'dispatcher', 'datax-01', 1, 6)
        env = {r['name']: r for r in obj['spec']['template']['spec']['containers'][0]['env']}
        self.assertEqual(env['MINERU_DISPATCH_BACKEND_HOST']['value'], m.NEW_WORKERS+'.deer-flow.svc.cluster.local')
        self.assertEqual(env['MINERU_DISPATCH_MIN_BACKENDS']['value'], '6')

    def test_required_affinity_not_silently_dropped(self):
        for k in ('podAffinity', 'podAntiAffinity'):
            d = old()
            d['spec']['template']['spec']['affinity'][k] = {'requiredDuringSchedulingIgnoredDuringExecution': [{'topologyKey': 'zone'}]}
            with self.assertRaises(m.PrepareFailure):
                m.deployment(d, 'worker', 'datax-01', 1, 6)

    def test_host_namespace_rejected(self):
        for k in ('hostNetwork', 'hostPID', 'hostIPC'):
            d = old()
            d['spec']['template']['spec'][k] = True
            with self.assertRaises(m.PrepareFailure):
                m.deployment(d, 'worker', 'datax-01', 1, 6)

    def test_extra_container_rejected(self):
        d = old()
        d['spec']['template']['spec']['containers'].append({'name': 'sidecar'})
        with self.assertRaises(m.PrepareFailure):
            m.deployment(d, 'worker', 'datax-01', 1, 6)

    def test_unpreloaded_node_rejected(self):
        with self.assertRaises(m.PrepareFailure):
            m.deployment(old(), 'worker', 'other-node', 1, 6)

    def test_wrong_old_image_rejected(self):
        d = old()
        d['spec']['template']['spec']['containers'][0]['image'] = 'changed'
        with self.assertRaises(m.PrepareFailure):
            m.deployment(d, 'worker', 'datax-01', 1, 6)

    def test_duplicate_env_rejected(self):
        c = {'env': [{'name': 'A'}, {'name': 'A'}]}
        with self.assertRaises(m.PrepareFailure):
            m.env_set(c, {})

    def test_route_disjoint(self):
        obj, _ = m.deployment(old(), 'worker', 'datax-01', 1, 6)
        m.routes_disjoint([obj], [{'metadata': {'name': 'old'}, 'spec': {'selector': {'app': m.WORKER}}}])

    def test_accidental_service_match_rejected(self):
        obj, _ = m.deployment(old(), 'worker', 'datax-01', 1, 6)
        with self.assertRaises(m.PrepareFailure):
            m.routes_disjoint([obj], [{'metadata': {'name': 'bad'}, 'spec': {'selector': {m.LABEL: m.RELEASE}}}])

    def test_resource_controller_reserved_before_workers(self):
        rows = [{'node': n, 'additional_isolated_worker_slots_planning_only': 4} for n in ('datax-01', 'datax-02')]
        result = m.make_plan(rows, 6, ['datax-02'])
        self.assertEqual(result['controller'], 'datax-02')
        self.assertLessEqual(result['workers'].count('datax-02'), 3)
        self.assertEqual(rows[1]['additional_isolated_worker_slots_planning_only'], 4)

    def test_dispatcher_constraints_respected(self):
        rows = [{'node': 'datax-01', 'additional_isolated_worker_slots_planning_only': 10}]
        with self.assertRaises(m.PrepareFailure):
            m.make_plan(rows, 6, ['datax-02'])

    def test_not_enough_resources(self):
        rows = [{'node': 'datax-01', 'additional_isolated_worker_slots_planning_only': 6}]
        with self.assertRaises(m.PrepareFailure):
            m.make_plan(rows, 6, ['datax-01'])

    def test_dry_run_accepts_defaults_but_not_env_mutation(self):
        obj, _ = m.deployment(old(), 'worker', 'datax-01', 1, 6)
        answer = copy.deepcopy(obj)
        answer['spec']['template']['spec']['hostNetwork'] = False
        m.validate_admission(obj, answer)
        answer['spec']['template']['spec']['containers'][0]['env'].append({'name': 'INJECTED', 'value': 'x'})
        with self.assertRaises(m.PrepareFailure):
            m.validate_admission(obj, answer)

    def test_dry_run_rejects_privilege_injection(self):
        obj, _ = m.deployment(old(), 'worker', 'datax-01', 1, 6)
        answer = copy.deepcopy(obj)
        answer['spec']['template']['spec']['containers'][0]['securityContext'] = {'privileged': True}
        with self.assertRaises(m.PrepareFailure):
            m.validate_admission(obj, answer)

    def test_dry_run_rejects_hidden_host_alias(self):
        obj, _ = m.deployment(old(), 'worker', 'datax-01', 1, 6)
        answer = copy.deepcopy(obj)
        answer['spec']['template']['spec']['hostAliases'] = [{'ip': '127.0.0.1'}]
        with self.assertRaises(m.PrepareFailure):
            m.validate_admission(obj, answer)

    def test_service_never_external(self):
        obj = m.service(m.NEW_WORKERS, 'worker', True, [{'port': 50052}])
        answer = copy.deepcopy(obj)
        m.validate_admission(obj, answer)
        answer['spec']['externalIPs'] = ['10.0.0.1']
        with self.assertRaises(m.PrepareFailure):
            m.validate_admission(obj, answer)

    def test_route_patch_is_cas_selector_only(self):
        oldsvc = {'metadata': {'name': m.WORKER, 'uid': 'old-uid', 'resourceVersion': '77'},
                  'spec': {'selector': {'app': m.DISPATCHER}, 'clusterIP': '10.0.0.2'}}
        result = m.route_patch(oldsvc, m.labels('dispatcher'))
        self.assertEqual([p['op'] for p in result], ['test', 'test', 'test', 'replace'])
        self.assertEqual(result[-1]['path'], '/spec/selector')
        self.assertEqual(oldsvc['spec']['selector']['app'], m.DISPATCHER)

    def test_only_server_dry_runs_sent(self):
        obj, _ = m.deployment(old(), 'worker', 'datax-01', 1, 6)
        commands = []
        m.core.save = lambda *args: None
        m.core.inv.bounded_capture = lambda args: commands.append(args) or json.dumps(obj).encode()
        with patch.object(m, 'read_resource', return_value=None), patch.object(m, 'emit'):
            m.dry_create(obj, Path('/evidence'))
        self.assertEqual(len(commands), 1)
        self.assertIn('--dry-run=server', commands[0])

    def test_existing_new_pool_never_overwritten(self):
        obj, _ = m.deployment(old(), 'worker', 'datax-01', 1, 6)
        with patch.object(m, 'read_resource', return_value={'metadata': {'name': 'exists'}}):
            with self.assertRaises(m.PrepareFailure):
                m.dry_create(obj, Path('/evidence'))

    def test_secret_output_template_has_no_value_interpolation(self):
        commands = []
        def capture(args):
            commands.append(args)
            return json.dumps({'uid': 'candidate-secret-uid', 'immutable': True,
                               'type': 'Opaque', 'keys': ['MINERU_API_TOKEN', '']}).encode() if len(commands) == 1 else b'old\told-secret\n'
        m.core.inv.bounded_capture = capture
        m.secret_metadata()
        self.assertNotIn('{{$value}}', commands[0][-1])
        self.assertNotIn('base64decode', commands[0][-1])

    def test_secret_already_referenced_fails(self):
        values = iter([json.dumps({'uid': 'candidate-secret-uid', 'immutable': True,
                                  'type': 'Opaque', 'keys': ['MINERU_API_TOKEN', '']}).encode(), b'existing\tcandidate-secret\n'])
        m.core.inv.bounded_capture = lambda args: next(values)
        with self.assertRaises(m.PrepareFailure):
            m.secret_metadata()

    def test_secret_uid_changed_fails(self):
        m.core.inv.bounded_capture = lambda args: json.dumps({'uid': 'different', 'immutable': True,
            'type': 'Opaque', 'keys': ['MINERU_API_TOKEN', '']}).encode()
        with self.assertRaises(m.PrepareFailure):
            m.secret_metadata()

    def test_load_16_is_not_relabeling_32(self):
        rows = [{'count': 8}, {'count': 16}]
        original = {'stages': rows, 'production_workloads_mutated': False,
                    'failed_stage_count': 32, 'failed_check': 'controller_failed_no_resubmission'}
        m.core.accept = lambda row, count: self.assertEqual(row['count'], count)
        m.core.statistics = lambda row: {'reviewed': row['count']}
        def reader(path):
            count = int(path.parent.name.split('-')[-1])
            return json.dumps({'count': count} if path.name == 'RUNTIME-RESULT.json' else
                {'stage': count, 'runtime': 'PASS', 'cleanup': 'CONFIRMED_ABSENT'}).encode()
        m.core.pf.read_private = reader
        with patch.object(m, 'read_locked', return_value=json.dumps(original).encode()):
            result = m.load_acceptance()
        self.assertEqual(result['selected_global_slots'], 16)
        self.assertEqual(result['concurrent_32'], 'NOT_ACCEPTED')
        self.assertFalse(result['uncertain_submit']['resubmission_allowed'])
        self.assertEqual(original['failed_stage_count'], 32)

    def test_runtime_changed_after_report_rejected(self):
        report = {'stages': [{'count': 8}, {'count': 16}], 'production_workloads_mutated': False,
                  'failed_stage_count': 32, 'failed_check': 'controller_failed_no_resubmission'}
        m.core.accept = lambda *args: None
        m.core.pf.read_private = lambda p: b'{"count":8,"changed":true}'
        with patch.object(m, 'read_locked', return_value=json.dumps(report).encode()):
            with self.assertRaises(m.PrepareFailure):
                m.load_acceptance()

    def test_no_live_write_or_provider_call_added(self):
        tree = ast.parse((HERE/'prepare_bluegreen16.py').read_text())
        self.assertFalse(any(isinstance(n, ast.Import) and any(a.name == 'requests' for a in n.names) for n in ast.walk(tree)))
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == 'bounded_capture':
                if isinstance(n.args[0], ast.List):
                    strings = [e.value for e in n.args[0].elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
                    if any(v in strings for v in ('create', 'patch', 'delete', 'apply', 'scale')):
                        self.assertIn('--dry-run=server', strings)


if __name__ == '__main__':
    unittest.main()
