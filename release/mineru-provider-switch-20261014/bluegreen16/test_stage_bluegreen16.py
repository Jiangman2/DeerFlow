import ast
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import ssl
import types
import unittest
from unittest.mock import MagicMock, patch

HERE = Path(__file__).absolute().parent


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, HERE/file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


m = load('stage', 'stage_bluegreen16.py')
p = load('prep_stage_test', 'prepare_bluegreen16.py')
pr = load('runtime_stage_test', 'stage_runtime_probe.py')
fixtures = load('prep_fixtures', 'test_prepare_bluegreen16.py')


def digest(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True).encode()).hexdigest()


def fake_core():
    return types.SimpleNamespace(
        guard=types.SimpleNamespace(NODES={n: {} for n in m.NODES}),
        pf=types.SimpleNamespace(host=types.SimpleNamespace(OLD_IMAGE='old-image'),
            utc=lambda: types.SimpleNamespace(isoformat=lambda: 'now'), GIB=1024**3),
        inv=types.SimpleNamespace(CANDIDATE={'image': 'candidate-image', 'config_digest': 'sha256:config',
            'manifest_digest': 'sha256:manifest', 'source_sha256': pr.SOURCE_SHA},
            digest=digest, controlled=lambda o, kind, uid: any(r.get('kind') == kind and r.get('uid') == uid
                and r.get('controller') is True for r in o.get('metadata', {}).get('ownerReferences', []))),
        SECRET='new-secret', SECRET_UID='new-secret-uid', save=MagicMock())


def object_for(name):
    if name in m.SERVICES:
        return p.service(name, 'worker' if name == m.SERVICES[0] else 'dispatcher', name == m.SERVICES[0],
                         [{'name': 'http', 'port': 50052, 'targetPort': 'http', 'protocol': 'TCP'}])
    role = 'dispatcher' if name == m.DISPATCHER else 'worker'
    node = 'ecs-zdz-pro-0012' if role == 'dispatcher' else m.NODES[m.WORKERS.index(name)]
    return p.deployment(fixtures.old(role), role, node, 1, 6)[0]


def actual_for(obj):
    actual = copy.deepcopy(obj)
    actual['metadata'].update(uid=obj['metadata']['name']+'-uid', generation=1)
    if obj['kind'] == 'Service':
        actual['spec'].setdefault('clusterIP', '10.0.0.15')
        actual['spec']['clusterIPs'] = [actual['spec']['clusterIP']]
        actual['spec'].update(ipFamilies=['IPv4'], ipFamilyPolicy='SingleStack')
    else:
        actual['status'] = {'availableReplicas': 1, 'observedGeneration': 1}
    return actual


def review_for(obj, actual):
    return dict(name=obj['metadata']['name'], kind=obj['kind'], server_dry_run='PASS',
        proposed_spec_sha256=digest(obj['spec']), admitted_spec_sha256=digest(actual['spec']))


def pod_for(dep, number=1):
    template = copy.deepcopy(dep['spec']['template'])
    template['metadata'].update(name='pod-'+str(number), uid='uid-'+str(number))
    template['spec']['nodeName'] = template['spec']['nodeSelector']['kubernetes.io/hostname']
    template['status'] = {'phase': 'Running', 'podIP': '10.244.1.'+str(number),
        'containerStatuses': [{'name': template['spec']['containers'][0]['name'], 'imageID': 'sha256:config',
             'ready': True, 'restartCount': 0, 'state': {'running': {'startedAt': 'time'}}, 'containerID': 'id-'+str(number)}]}
    return template


class StageTests(unittest.TestCase):
    def setUp(self):
        m.core = p.core = fake_core()
        m.prep = p
        self.obj = object_for(m.WORKERS[0])
        self.actual = actual_for(self.obj)
        self.review = review_for(self.obj, self.actual)
        self.pod = pod_for(self.actual)

    def test_python37_grammar_and_locked_sources(self):
        for name in ('stage_bluegreen16.py', 'stage_runtime_probe.py', 'prepare_bluegreen16.py'):
            ast.parse((HERE/name).read_text(encoding='utf-8'), feature_version=(3, 7))
        self.assertEqual(m.sha((HERE/'prepare_bluegreen16.py').read_bytes()), m.PREPARE_SHA)
        self.assertEqual(m.sha((HERE/'stage_runtime_probe.py').read_bytes()), m.PROBE_SHA)

    def test_only_nine_exact_new_names(self):
        for name in m.ALLOWED:
            m.validate_target(object_for(name))
        for name in (p.WORKER, p.DISPATCHER, 'gateway', 'frontend', 'weknora-app'):
            obj = copy.deepcopy(self.obj)
            obj['metadata']['name'] = name
            with self.subTest(name=name), self.assertRaises(m.StageFailure):
                m.validate_target(obj)

    def test_namespace_replica_node_strategy_changes_fail(self):
        for mutate in (lambda o: o['metadata'].update(namespace='other'),
                       lambda o: o['spec'].update(replicas=2),
                       lambda o: o['spec'].update(strategy={'type': 'RollingUpdate'}),
                       lambda o: o['spec']['template']['spec']['nodeSelector'].update({'kubernetes.io/hostname': 'datax-01'})):
            obj = copy.deepcopy(self.obj)
            mutate(obj)
            with self.assertRaises(m.StageFailure):
                m.validate_target(obj)

    def test_admission_exact_deployment(self):
        m.admitted(self.obj, self.actual, self.review, self.actual)
        actual = copy.deepcopy(self.actual)
        actual['spec']['progressDeadlineSeconds'] = 999
        with self.assertRaises((m.StageFailure, p.PrepareFailure)):
            m.admitted(self.obj, actual, self.review, self.actual)

    def test_admission_new_service_allocated_ip_allowed(self):
        obj = object_for(m.SERVICES[1])
        before = actual_for(obj)
        now = copy.deepcopy(before)
        now['spec'].update(clusterIP='10.0.0.123', clusterIPs=['10.0.0.123'])
        m.admitted(obj, now, review_for(obj, before), before)

    def test_service_unreviewed_defaults_fail(self):
        obj = object_for(m.SERVICES[1])
        before = actual_for(obj)
        now = copy.deepcopy(before)
        now['spec']['publishNotReadyAddresses'] = True
        with self.assertRaises(m.StageFailure):
            m.admitted(obj, now, review_for(obj, before), before)

    def test_headless_identity_enforced(self):
        obj = object_for(m.SERVICES[0])
        before = actual_for(obj)
        now = copy.deepcopy(before)
        now['spec']['clusterIP'] = '10.0.0.2'
        with self.assertRaises((m.StageFailure, p.PrepareFailure)):
            m.admitted(obj, now, review_for(obj, before), before)

    def test_create_intent_before_mutation_ack_after(self):
        operations = []
        m.core.save = lambda root, name, value: operations.append(name)
        def command(args):
            operations.append('kubectl-create')
            self.assertEqual(args[0], 'create')
            self.assertNotIn('--dry-run=server', args)
            return json.dumps(self.actual).encode()
        report = {'create_requested': [], 'created': [], 'cluster_writes_attempted': False}
        with patch.object(m, 'get', return_value=None), patch.object(m, 'call', side_effect=command), patch.object(m, 'emit'):
            m.create_once(Path('/private'), self.obj, self.review, self.actual, report)
        self.assertLess(next(i for i,s in enumerate(operations) if s.endswith('CREATE-REQUESTED.json')), operations.index('kubectl-create'))
        self.assertTrue(operations[-1].endswith('CREATED.private.json'))
        self.assertEqual(len(report['created']), 1)

    def test_uncertain_create_no_retry_or_cleanup(self):
        report = {'create_requested': [], 'created': [], 'cluster_writes_attempted': False}
        with patch.object(m, 'get', return_value=None), patch.object(m, 'call', side_effect=TimeoutError) as call, patch.object(m, 'emit'):
            with self.assertRaises(TimeoutError):
                m.create_once(Path('/private'), self.obj, self.review, self.actual, report)
        self.assertEqual(call.call_count, 1)
        self.assertTrue(report['cluster_writes_attempted'])
        self.assertEqual(report['create_requested'], [self.obj['metadata']['name']])
        self.assertFalse(report['created'])

    def test_existing_name_never_adopted_or_written(self):
        with patch.object(m, 'get', return_value=self.actual), patch.object(m, 'call') as call:
            with self.assertRaises(m.StageFailure):
                m.create_once(Path('/private'), self.obj, self.review, self.actual, {})
        call.assert_not_called()
        m.core.save.assert_not_called()

    def test_valid_pod_and_config_manifest_ids(self):
        m.validate_pod(self.actual, self.pod)
        self.pod['status']['containerStatuses'][0]['imageID'] = 'repo@sha256:manifest'
        m.validate_pod(self.actual, self.pod)

    def test_wrong_pod_runtime_fails(self):
        changes = (
            lambda p: p['spec'].update(nodeName='wrong'),
            lambda p: p['spec'].update(hostNetwork=True),
            lambda p: p['spec']['containers'][0]['env'].append({'name': 'SNEAK', 'value': 'changed'}),
            lambda p: p['status']['containerStatuses'][0].update(imageID='wrong'),
            lambda p: p['status']['containerStatuses'][0].update(restartCount=1),
            lambda p: p['metadata'].update(deletionTimestamp='now'),
        )
        for change in changes:
            pod = copy.deepcopy(self.pod)
            change(pod)
            with self.assertRaises(m.StageFailure):
                m.validate_pod(self.actual, pod)

    def test_standard_service_account_mount_allowed_only(self):
        mounts = self.pod['spec']['containers'][0]['volumeMounts']
        mounts.append({'name': 'kube-api-access-xyz', 'mountPath': '/var/run/secrets/kubernetes.io/serviceaccount', 'readOnly': True})
        m.validate_pod(self.actual, self.pod)
        mounts[-1]['mountPath'] = '/app'
        with self.assertRaises(m.StageFailure):
            m.validate_pod(self.actual, self.pod)

    def test_deployment_uid_replacement_rejected(self):
        now = copy.deepcopy(self.actual)
        now['metadata']['uid'] = 'replaced'
        with patch.object(m, 'get', return_value=now), self.assertRaises(m.StageFailure):
            m.deployment_current(self.actual)

    def test_readiness_wait_is_bounded(self):
        m.core.inv.owned_pods = lambda d: []
        with patch.object(m, 'deployment_current', return_value=self.actual), patch.object(m.time, 'monotonic', side_effect=[0, 601]), patch.object(m, 'emit'):
            with self.assertRaisesRegex(m.StageFailure, 'deadline'):
                m.ready_pods([self.actual])

    def test_readiness_restarts_fail_without_waiting(self):
        self.pod['status']['containerStatuses'][0]['restartCount'] = 1
        m.core.inv.owned_pods = lambda d: [self.pod]
        with patch.object(m, 'deployment_current', return_value=self.actual), patch.object(m.time, 'sleep') as sleep:
            with self.assertRaises(m.StageFailure):
                m.ready_pods([self.actual])
            sleep.assert_not_called()

    def test_endpoint_exact_owner_ip_uid_and_ports(self):
        svc = actual_for(object_for(m.SERVICES[0]))
        expected = m.validate_pod(self.actual, self.pod)
        ep = {'metadata': {'ownerReferences': [{'kind': 'Service', 'uid': svc['metadata']['uid'], 'controller': True}]},
              'addressType': 'IPv4', 'ports': [{'port': 50052, 'protocol': 'TCP'}],
              'endpoints': [{'conditions': {'ready': True}, 'addresses': [expected['ip']],
                   'targetRef': {'kind': 'Pod', 'namespace': 'deer-flow', 'name': expected['name'], 'uid': expected['uid']}}]}
        with patch.object(m, 'get', return_value={'items': [ep]}):
            m.endpoints(svc, [expected])
            ep['endpoints'][0]['targetRef']['uid'] = 'other'
            with self.assertRaises(m.StageFailure):
                m.endpoints(svc, [expected])

    def test_endpoint_owner_violation_not_retried(self):
        with patch.object(m, 'endpoints', side_effect=m.StageFailure('endpoint_slice_service_owner')) as call:
            with self.assertRaises(m.StageFailure):
                m.wait_endpoints({'metadata': {'name': 'test'}}, [])
            self.assertEqual(call.call_count, 1)

    def test_probe_checks_process_not_just_name(self):
        row = m.validate_pod(self.actual, self.pod)
        replaced = copy.deepcopy(self.pod)
        replaced['metadata']['uid'] = 'replaced'
        receipt = {'stage_runtime': 'PASS', 'role': 'worker', 'source_sha256': pr.SOURCE_SHA,
                   'provider_http_requests': 0, 'documents_submitted': 0}
        with patch.object(m, 'get', side_effect=[self.pod, replaced]), patch.object(m, 'call', return_value=json.dumps(receipt).encode()), patch.object(m, 'emit'):
            with self.assertRaisesRegex(m.StageFailure, 'process_changed'):
                m.probe_pod(row, 'verified-source', [], Path('/evidence'))

    def test_resource_reserves_worker_plus_dispatcher(self):
        report = {'initial_plan': {'workers': m.NODES, 'controller': m.NODES[1]}}
        rows = [dict(node=n, additional_isolated_worker_slots_planning_only=1,
            metrics={'filesystems': {'root': {'inodes_headroom': True, 'available_bytes': 20*1024**3, 'planning_reserve_bytes': 1024**3}}}) for n in m.NODES]
        m.core.resources = lambda deps: rows
        m.core.monitoring = MagicMock()
        m.core.inv.get = lambda kind: {'items': [{'metadata': {'name': n}} for n in m.NODES]}
        m.core.inv.node_rules_match = lambda *args: True
        with self.assertRaisesRegex(m.StageFailure, 'slots_insufficient'):
            m.fresh_resources({p.DISPATCHER: fixtures.old('dispatcher')}, report)
        rows[1]['additional_isolated_worker_slots_planning_only'] = 2
        m.fresh_resources({p.DISPATCHER: fixtures.old('dispatcher')}, report)
        rows[1]['metrics']['filesystems']['root']['available_bytes'] = 8*1024**3
        with self.assertRaisesRegex(m.StageFailure, 'disk_reserve'):
            m.fresh_resources({p.DISPATCHER: fixtures.old('dispatcher')}, report)

    def test_status_no_exec_create_save_or_adoption(self):
        reviewed = {'route_plan': {'uid': 'public', 'original_spec_sha256': digest({'selector': {'app': 'old'}})}}
        svc = {'metadata': {'uid': 'public'}, 'spec': {'selector': {'app': 'old'}}}
        def get(kind, name=None, **kw):
            return svc if name == p.WORKER else None
        with patch.object(m, 'get', side_effect=get) as read, patch.object(m, 'locked', return_value=json.dumps(reviewed).encode()), patch.object(m.Path, 'exists', return_value=False), patch.object(m, 'emit') as emit:
            m.status_only()
            self.assertEqual(read.call_count, 10)
            self.assertTrue(emit.call_args[0][0]['original_route_unchanged'])
        m.core.save.assert_not_called()

    def test_static_no_patch_scale_apply_delete_provider_submit(self):
        tree = ast.parse((HERE/'stage_bluegreen16.py').read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'call':
                if isinstance(node.args[0], ast.List):
                    values = [e.value for e in node.args[0].elts if isinstance(e, ast.Constant)]
                    self.assertFalse(any(v in values for v in ('patch', 'apply', 'delete', 'scale', 'rollout')))
        self.assertNotIn('requests.post', (HERE/'stage_bluegreen16.py').read_text())

    def test_complete_stage_orchestration_creates_exact_nine_then_reviews(self):
        objects = [object_for(n) for n in m.ALLOWED]
        actuals = {o['metadata']['name']: actual_for(o) for o in objects}
        dry = [review_for(o, actuals[o['metadata']['name']]) for o in objects]
        reviews = {r['name']: r for r in dry}
        report = {'mineru_bluegreen16_prepare': 'PASS', 'production_workloads_mutated': False,
            'ready_for_stage_review': True, 'initial_plan': {'workers': m.NODES, 'controller': m.NODES[1], 'total_slots': 7},
            'concurrency_plan': {'new_initial_slots': 6, 'old_reserved_slots': 10, 'target_slots': 16},
            'dry_runs': dry, 'candidate': m.core.inv.CANDIDATE, 'bindings': {}, 'credential': {'test': 'locked'},
            'ca_sha256': m.sha(b'ca')}
        pods = {n: m.validate_pod(actuals[n], pod_for(actuals[n], i+1)) for i,n in enumerate(m.WORKERS+[m.DISPATCHER])}
        m.core.pf.receipt_bindings = lambda: ({}, {})
        m.core.baselines = lambda controls: ({}, {})
        m.core.ca_bytes = lambda: b'ca'
        m.core.monitoring = lambda plan: []
        m.core.pf.read_private = lambda path: json.dumps(actuals[path.name.replace('.admitted.private.json', '')]).encode()
        created = {}
        sequence = []
        def read(kind, name=None, selector=None):
            if kind == 'pods':
                return {'items': [{'metadata': {'uid': row['uid']}} for row in pods.values()]} if len(created) == 9 else {'items': []}
            if name:
                return created.get(name)
            return {'items': []}
        def create(root, obj, review, reference, result):
            name = obj['metadata']['name']
            sequence.append(name)
            if name == m.DISPATCHER:
                self.assertIn('WAIT_SIX_WORKERS', sequence)
            m.admitted(obj, actuals[name], review, reference)
            created[name] = actuals[name]
            return actuals[name]
        def ready(values):
            if len(values) == 6:
                sequence.append('WAIT_SIX_WORKERS')
            return [pods[v['metadata']['name']] for v in values]
        with patch.object(m, 'locked', return_value=json.dumps(report).encode()), \
                patch.object(p, 'load_acceptance', return_value={'accepted': 16}), \
                patch.object(p, 'secret_metadata', return_value={'test': 'locked'}), \
                patch.object(p, 'manifests', return_value=(objects, [])), \
                patch.object(m, 'old_unchanged', return_value={}) as old_check, \
                patch.object(m, 'fresh_resources', return_value=[]), \
                patch.object(m, 'get', side_effect=read), patch.object(m, 'create_once', side_effect=create), \
                patch.object(m, 'call', side_effect=lambda args: json.dumps(actuals[Path(args[args.index('-f')+1]).name.replace('.dry-run.private.json', '')]).encode()), \
                patch.object(m, 'credential_identity'), \
                patch.object(m, 'ready_pods', side_effect=ready), patch.object(m, 'wait_endpoints', return_value={}), \
                patch.object(m, 'endpoints', return_value={}), patch.object(m, 'probe_pod', return_value={'stage_runtime': 'PASS'}) as probe:
            result = {}
            m.stage('verified-probe', result)
        self.assertEqual(result['mineru_bluegreen16_stage'], 'PASS')
        self.assertTrue(result['ready_for_switch_review'])
        self.assertEqual(result['new_worker_slots'], 6)
        self.assertEqual(probe.call_count, 7)
        self.assertEqual(set(created), set(m.ALLOWED))
        self.assertEqual(sequence[:2], sorted(m.SERVICES))
        self.assertGreaterEqual(old_check.call_count, 11)

    def test_credential_metadata_never_fetches_value(self):
        row = {'uid': m.core.SECRET_UID, 'immutable': True, 'type': 'Opaque', 'keys': ['MINERU_API_TOKEN', '']}
        with patch.object(m, 'call', return_value=json.dumps(row).encode()) as call:
            m.credential_identity()
            self.assertNotIn('{{$value}}', call.call_args[0][0][-1])
            self.assertNotIn('base64decode', call.call_args[0][0][-1])
        row['uid'] = 'replaced'
        with patch.object(m, 'call', return_value=json.dumps(row).encode()), self.assertRaises(m.StageFailure):
            m.credential_identity()

    def test_pod_volume_source_swap_is_not_acceptable(self):
        self.pod['spec']['volumes'][0]['configMap']['name'] = 'attacker-ca'
        with self.assertRaisesRegex(m.StageFailure, 'volume'):
            m.validate_pod(self.actual, self.pod)


class RuntimeTests(unittest.TestCase):
    def test_idle_worker_and_complete_dispatcher(self):
        pr.validate_health({'status': 'ok', 'role': 'worker', 'parser': {'active': 0, 'waiting': 0}}, 'worker', 6)
        row = {'status': 'ok', 'role': 'dispatcher', 'dispatcher': {
            'discovered': 6, 'live': 6, 'minimum': 6, 'ready': True, 'capacity_target_met': True,
            'busy': 0, 'waiting': 0, 'quarantined': 0}}
        pr.validate_health(row, 'dispatcher', 6)
        row['dispatcher']['live'] = 5
        with self.assertRaises(pr.CheckFailure):
            pr.validate_health(row, 'dispatcher', 6)

    def test_new_pool_activity_blocks(self):
        for parser in ({'active': 1, 'waiting': 0}, {'active': 0, 'waiting': 1}, {}):
            with self.assertRaises(pr.CheckFailure):
                pr.validate_health({'status': 'ok', 'role': 'worker', 'parser': parser}, 'worker', 6)

    def test_tls_verifies_host_and_ca_without_http(self):
        ctx = MagicMock()
        ctx.check_hostname = True
        ctx.verify_mode = ssl.CERT_REQUIRED
        tls = ctx.wrap_socket.return_value.__enter__.return_value
        tls.getpeercert.return_value = {'subject': 'test'}
        tls.version.return_value = 'TLSv1.3'
        with patch.object(pr.ssl, 'create_default_context', return_value=ctx) as factory, patch.object(pr.socket, 'create_connection') as connect:
            result = pr.tls_probe('/approved/ca')
        factory.assert_called_once_with(cafile='/approved/ca')
        connect.assert_called_once_with(('aiapi.cnooc.com.cn', 443), timeout=8)
        self.assertEqual(ctx.wrap_socket.call_args.kwargs['server_hostname'], 'aiapi.cnooc.com.cn')
        self.assertEqual(result['provider_http_requests'], 0)
        tls.send.assert_not_called()
        tls.sendall.assert_not_called()

    def test_tls_disabled_rejected_before_network(self):
        ctx = types.SimpleNamespace(check_hostname=False, verify_mode=ssl.CERT_NONE)
        with patch.object(pr.ssl, 'create_default_context', return_value=ctx), patch.object(pr.socket, 'create_connection') as connect:
            with self.assertRaises(pr.CheckFailure):
                pr.tls_probe('/ca')
        connect.assert_not_called()

    def test_health_scope_prevents_arbitrary_get(self):
        with self.assertRaises(pr.CheckFailure):
            pr.health('external.invalid')

    def test_health_no_redirect_or_large_response(self):
        for status, data in ((302, b'{}'), (200, b'x'*32769)):
            connection = MagicMock()
            response = connection.getresponse.return_value
            response.status = status
            response.read.return_value = data
            with patch.object(pr.http.client, 'HTTPConnection', return_value=connection):
                with self.assertRaises(pr.CheckFailure):
                    pr.health('127.0.0.1')
            connection.request.assert_called_once_with('GET', '/health', headers={'Connection': 'close'})

    def test_runtime_config_only_accepts_dedicated_async(self):
        env = {'MINERU_API_URL': pr.BASE, 'MINERU_TASK_QUERY_URL': pr.TASKS, 'MINERU_REQUEST_MODE': 'async',
               'MINERU_TRUST_ENV': 'false', 'MINERU_API_TOKEN': 'dummy-not-real-credential', 'MINERU_CA_BUNDLE': '/ca'}
        with patch.dict(os.environ, env, clear=True), patch.object(pr.Path, 'stat', return_value=types.SimpleNamespace(st_mode=0o100644, st_size=4)), patch.object(pr.Path, 'read_bytes', return_value=b'test'), patch.object(pr, 'CA_SHA', hashlib.sha256(b'test').hexdigest()):
            self.assertEqual(pr.worker_config(), '/ca')
            os.environ['MINERU_TRUST_ENV'] = 'true'
            with self.assertRaises(pr.CheckFailure):
                pr.worker_config()

    def test_no_provider_job_http_in_probe_source(self):
        text = (HERE/'stage_runtime_probe.py').read_text()
        self.assertNotIn("'POST'", text)
        self.assertNotIn('Authorization', text)
        self.assertNotIn('verify=False', text)


if __name__ == '__main__':
    unittest.main()
