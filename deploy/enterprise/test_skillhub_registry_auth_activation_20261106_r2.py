"""Offline refusal, scope, CAS and synthetic orchestration tests. No cluster access."""
import ast
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import types
import unittest
from contextlib import ExitStack, redirect_stdout
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
PATH = HERE / 'activate-portal-skillhub-registry-auth-20261106-r2.py'


def module():
    spec = importlib.util.spec_from_file_location('skillhub_activation', PATH)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def fixture(m):
    value = {'apiVersion': 'apps/v1', 'kind': 'Deployment',
        'metadata': {'name': m.SERVER, 'namespace': m.NS, 'uid': m.BASELINE['uid'],
                     'generation': 14, 'resourceVersion': '101'},
        'spec': {'replicas': 1, 'strategy': {'type': 'Recreate'},
            'selector': {'matchLabels': {'app': 'skillhub'}},
            'template': {'spec': {'nodeSelector': {'kubernetes.io/hostname': m.NODE},
                'containers': [{'name': 'server', 'image': m.OLD, 'imagePullPolicy': 'Never',
                    'env': [{'name': 'SKILLHUB_AUTH_DEERFLOW_SESSION_ENABLED', 'value': 'true'},
                            {'name': 'PASSWORD', 'valueFrom': {'secretKeyRef': {'name': 'preserved', 'key': 'password'}}}],
                    'resources': {'limits': {'memory': '1Gi'}},
                    'volumeMounts': [{'name': 'keep', 'mountPath': '/var/keep'}]}],
                'volumes': [{'name': 'keep', 'emptyDir': {}}]}}},
        'status': {'observedGeneration': 14, 'replicas': 1, 'updatedReplicas': 1,
                   'readyReplicas': 1, 'availableReplicas': 1, 'terminatingReplicas': 0}}
    m.BASELINE['spec_sha256'] = m.binding(value)['spec_sha256']
    return value


class Tests(unittest.TestCase):
    def setUp(self):
        self.m = module()
        self.d = fixture(self.m)

    def seal(self):
        return {'bytes': 100, 'sha256': 'a' * 64, 'operation': 'b' * 32}

    def preload(self):
        m = self.m
        return {'schema': 'portal-skillhub-registry-auth-cache-preload.v1',
            'status': 'SKILLHUB_AUTH_CACHE_PRELOADED_NOT_DEPLOYED', 'operation': 'b' * 32,
            'hostname': m.NODE, 'expected_node_uid': m.NODE_UID, 'manifest_digest': m.MANIFEST,
            'config_digest': m.CONFIG, 'preclaim': {'pass': True}, 'phase_a': 'PASS', 'phase_b': 'PASS',
            'content_and_unpack': 'PASS', 'cri_visibility': 'PASS', 'old_references_preserved': True,
            'production_workload_mutation': False}

    def node(self):
        m = self.m
        return {'metadata': {'name': m.NODE, 'uid': m.NODE_UID}, 'spec': {}, 'status': {
            'nodeInfo': {'architecture': 'arm64', 'operatingSystem': 'linux'},
            'conditions': [{'type': k, 'status': 'True' if k == 'Ready' else 'False'}
                           for k in ('Ready', 'DiskPressure', 'MemoryPressure', 'PIDPressure')]}}

    def host(self, m):
        stack = ExitStack()
        stack.enter_context(patch.object(m.sys, 'platform', 'linux'))
        stack.enter_context(patch.object(m.socket, 'gethostname', return_value=m.NODE))
        stack.enter_context(patch.object(m.os, 'geteuid', return_value=0, create=True))
        stack.enter_context(patch.object(m.os, 'uname', return_value=types.SimpleNamespace(machine='aarch64'), create=True))
        stack.enter_context(patch.object(m.sys, 'flags', types.SimpleNamespace(isolated=1, dont_write_bytecode=1)))
        return stack

    def test_python37_syntax(self):
        ast.parse(PATH.read_text(encoding='utf-8'), feature_version=(3, 7))

    def test_unsealed_copy_refuses_before_filesystem(self):
        self.m.PRELOAD_BINDING = None
        with patch.object(self.m, 'trusted_dir', side_effect=AssertionError('no filesystem')):
            with self.assertRaisesRegex(self.m.Refused, 'PRELOAD_RECEIPT_NOT_SEALED'):
                self.m.read_preload()

    def test_operator_preload_exact_binding(self):
        self.assertEqual(self.m.PRELOAD_BINDING, {'bytes': 62575,
            'sha256': '1d1e580d2c5846d5e9b16f6a449c782fcc93409ddd024d21d7a9fb7c1f2f144d',
            'operation': '796641e830e84b05bf098d1ba309300d'})

    def test_wrong_host_has_no_commands_or_claim(self):
        e = self.m.Engine()
        with patch.object(self.m.socket, 'gethostname', return_value='wrong-host'), redirect_stdout(io.StringIO()):
            self.assertEqual(e.run(), 1)
        self.assertEqual(e.report['commands'], 0)
        self.assertFalse(e.report['once_claimed'])

    def test_unbound_on_right_host_stops_before_tools(self):
        self.m.PRELOAD_BINDING = None
        e = self.m.Engine()
        with self.host(self.m), patch.object(self.m.shutil, 'which', side_effect=AssertionError('no tools')):
            with self.assertRaisesRegex(self.m.Refused, 'PRELOAD_RECEIPT_NOT_SEALED'):
                e.execute()

    def test_real_preload_schema_accepts_full_receipt_not_stdout_summary(self):
        self.m.receipt_gate(self.preload(), self.seal())
        v = self.preload()
        v.pop('preclaim')
        v.update(preclaim_passed=True, exitcode=0)
        with self.assertRaises(self.m.Refused):
            self.m.receipt_gate(v, self.seal())

    def test_preload_negative_evidence_rejected(self):
        changes = {'status': 'INCOMPLETE', 'hostname': 'datax-02', 'operation': 'c' * 32,
            'expected_node_uid': 'other', 'manifest_digest': 'old', 'config_digest': 'old',
            'phase_b': 'FAIL', 'content_and_unpack': None, 'cri_visibility': None,
            'old_references_preserved': False, 'production_workload_mutation': True,
            'preclaim': {'pass': False}, 'error_code': 'command_limit'}
        for key, value in changes.items():
            with self.subTest(key=key):
                r = self.preload(); r[key] = value
                with self.assertRaises(self.m.Refused): self.m.receipt_gate(r, self.seal())

    def test_seal_needs_real_size_hash_operation(self):
        for key, value in [('bytes', 0), ('sha256', ''), ('operation', 'pending')]:
            seal = self.seal(); seal[key] = value
            with self.subTest(key=key), self.assertRaises(self.m.Refused):
                self.m.receipt_gate(self.preload(), seal)

    def test_healthy_server_baseline(self):
        self.m.validate_server(self.d)

    def test_readiness_staleness_pause_and_extra_pods_rejected(self):
        for field, value in [('observedGeneration', 13), ('replicas', 2), ('updatedReplicas', 0),
                             ('readyReplicas', 0), ('availableReplicas', 0), ('terminatingReplicas', 1)]:
            d = copy.deepcopy(self.d); d['status'][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(self.m.Refused, 'NOT_CONVERGED'):
                self.m.validate_server(d)

    def test_only_image_changes_in_patch(self):
        operations, after = self.m.make_patch(self.d)
        writes = [x for x in operations if x['op'] != 'test']
        self.assertEqual(writes, [{'op': 'replace', 'path': '/spec/template/spec/containers/0/image', 'value': self.m.NEW}])
        d = copy.deepcopy(self.d); d['spec']['template']['spec']['containers'][0]['image'] = self.m.NEW
        d['metadata']['generation'] = 15
        self.assertEqual(after, self.m.binding(d))
        self.assertEqual(self.d['spec']['template']['spec']['containers'][0]['image'], self.m.OLD)

    def test_cas_tests_uid_resourceversion_generation_and_entire_spec(self):
        operations, _ = self.m.make_patch(self.d)
        tests = {x['path']: x['value'] for x in operations if x['op'] == 'test'}
        self.assertEqual(set(tests), {'/metadata/uid', '/metadata/resourceVersion', '/metadata/generation', '/spec'})
        self.assertEqual(tests['/spec'], self.d['spec'])

    def test_missing_resourceversion_cannot_patch(self):
        self.d['metadata'].pop('resourceVersion')
        with self.assertRaisesRegex(self.m.Refused, 'RESOURCE_VERSION'): self.m.make_patch(self.d)

    def test_scope_guards_fail_even_if_rebound_synthetic_spec(self):
        for mutate in (lambda d: d['spec'].update(strategy={'type': 'RollingUpdate'}),
                lambda d: d['spec'].update(replicas=2),
                lambda d: d['spec']['template']['spec']['containers'][0].update(imagePullPolicy='Always'),
                lambda d: d['spec']['template']['spec'].update(nodeSelector={'pool': 'all'}),
                lambda d: d['spec']['template']['spec']['containers'][0].update(env=[])):
            d = copy.deepcopy(self.d); mutate(d)
            with self.assertRaises(self.m.Refused): self.m.validate_server(d, self.m.binding(d))

    def test_node_guards(self):
        self.m.validate_node(self.node())
        for mutate in (lambda n: n['metadata'].update(uid='other'),
                       lambda n: n['spec'].update(unschedulable=True),
                       lambda n: n['status']['nodeInfo'].update(architecture='amd64'),
                       lambda n: n['status']['conditions'][2].update(status='True')):
            n = self.node(); mutate(n)
            with self.assertRaises(self.m.Refused): self.m.validate_node(n)

    def check_pod(self):
        desired = self.m.check_pod_manifest('c' * 32)
        pod = copy.deepcopy(desired); pod['metadata']['uid'] = '00000000-0000-0000-0000-000000000001'
        return desired, pod

    def test_candidate_check_cannot_start_app_or_access_production(self):
        desired, pod = self.check_pod()
        self.m.validate_check_pod(pod, desired, pod['metadata']['uid'])
        s = desired['spec']; c = s['containers'][0]
        self.assertFalse(s['automountServiceAccountToken'])
        self.assertFalse(s['enableServiceLinks'])
        self.assertEqual(c['args'], ['exec sha256sum /app/app.jar'])
        self.assertEqual(c['imagePullPolicy'], 'Never')
        self.assertNotIn('env', c); self.assertNotIn('volumes', s)
        self.assertEqual(c['securityContext']['capabilities']['drop'], ['ALL'])
        self.assertTrue(c['securityContext']['readOnlyRootFilesystem'])

    def test_check_pod_mutation_or_wrong_uid_rejected(self):
        for mutate in (lambda p: p['metadata'].update(uid='other'),
                       lambda p: p['spec'].update(hostNetwork=True),
                       lambda p: p['spec'].update(enableServiceLinks=True),
                       lambda p: p['spec'].update(restartPolicy='Always'),
                       lambda p: p['spec'].update(activeDeadlineSeconds=999),
                       lambda p: p['spec'].update(volumes=[{'name': 'secret'}]),
                       lambda p: p['spec']['containers'][0].update(env=[{'name': 'SECRET', 'value': 'x'}]),
                       lambda p: p['spec']['securityContext'].update(runAsUser=0)):
            desired, pod = self.check_pod(); uid = pod['metadata']['uid']; mutate(pod)
            with self.assertRaises(self.m.Refused): self.m.validate_check_pod(pod, desired, uid)

    def test_delete_is_uid_preconditioned(self):
        uid = '00000000-0000-0000-0000-000000000001'
        self.assertEqual(self.m.delete_options(uid)['preconditions'], {'uid': uid})
        with self.assertRaises(self.m.Refused): self.m.delete_options('')

    def endpoints(self, pod):
        return {'items': [{'metadata': {'labels': {'kubernetes.io/service-name': self.m.SERVER}},
            'endpoints': [{'conditions': {'ready': True, 'terminating': False}, 'targetRef': {
                'kind': 'Pod', 'namespace': self.m.NS, 'name': pod['metadata']['name'], 'uid': pod['metadata']['uid']}}]}]}

    def test_endpoint_uid_ready_and_only_current_pod(self):
        _, pod = self.check_pod(); self.m.endpoint_gate(self.endpoints(pod), pod)
        for mutate in (lambda e: e['items'][0]['endpoints'][0]['targetRef'].update(uid='old'),
                       lambda e: e['items'][0]['endpoints'][0]['conditions'].update(ready=False),
                       lambda e: e['items'][0]['endpoints'][0]['conditions'].update(terminating=True),
                       lambda e: e.update(items=[])):
            e = self.endpoints(pod); mutate(e)
            with self.assertRaises(self.m.Refused): self.m.endpoint_gate(e, pod)

    def test_http_probe_discards_bodies_and_never_authenticates(self):
        e = self.m.Engine(); _, pod = self.check_pod(); calls = []
        def call(kind, args, **kwargs):
            calls.append(args)
            return b'200' if args[-1].endswith('/health') else b'401'
        e.call = call; e.http(pod)
        self.assertEqual(len(calls), 5)
        self.assertTrue(all('/dev/null' in c and '--max-time' in c and '--noproxy' in c for c in calls))
        self.assertFalse(any('--location' in c or '-L' in c for c in calls))
        self.assertEqual(sum('Authorization: Bearer invalid-synthetic-registry-auth-verification' in c for c in calls), 2)

    def test_http_failure_stops_immediately(self):
        e = self.m.Engine(); _, pod = self.check_pod()
        e.call = lambda *a, **k: b'200'
        with self.assertRaisesRegex(self.m.Refused, 'NATIVE_HTTP'): e.http(pod)

    def test_report_does_not_claim_no_application_database_access(self):
        r = self.m.Engine().report
        self.assertNotIn('database_access', r); self.assertNotIn('database_modified', r)
        self.assertEqual(r['explicit_database_queries'], 0)
        self.assertFalse(r['database_repair_requested'])
        self.assertTrue(r['expected_brief_skillhub_outage'])

    @unittest.skipUnless(os.name == 'posix', 'POSIX private filesystem semantics')
    def test_private_report_is_durable_replace_without_overwriting_backup(self):
        m = self.m
        with tempfile.TemporaryDirectory() as d:
            e = m.Engine(); e.fd = os.open(d, os.O_RDONLY | os.O_DIRECTORY)
            try:
                e.persist('BEFORE.private.json', {'preserved': True})
                with self.assertRaises(FileExistsError): e.persist('BEFORE.private.json', {'preserved': False})
                e.save(); e.report['status'] = 'TESTED'; e.save()
                self.assertEqual(json.loads((Path(d) / 'REPORT.json').read_bytes())['status'], 'TESTED')
                self.assertEqual((Path(d) / 'REPORT.json').stat().st_mode & 0o777, 0o600)
                self.assertFalse(list(Path(d).glob('*.pending')))
            finally: os.close(e.fd)

    @unittest.skipUnless(os.name == 'posix', 'POSIX bounded subprocess transport')
    def test_bounded_process_output_limit_and_deadline(self):
        import sys
        self.assertEqual(self.m.bounded([sys.executable, '-I', '-B', '-c', 'print("ok")']), b'ok\n')
        with self.assertRaisesRegex(self.m.Refused, 'OUTPUT_BOUND'):
            self.m.bounded([sys.executable, '-I', '-B', '-c', 'print("x"*10000)'], limit=32)
        with self.assertRaisesRegex(self.m.Refused, 'TIMEOUT'):
            self.m.bounded([sys.executable, '-I', '-B', '-c', 'import time; time.sleep(5)'], timeout=0.1)

    @unittest.skipUnless(os.name == 'posix' and hasattr(os, 'geteuid') and os.geteuid() == 0,
                         'root POSIX safe receipt filesystem')
    def test_preload_actual_file_hash_identity_symlink_and_mode(self):
        m = self.m
        with tempfile.TemporaryDirectory(dir='/root') as d:
            p = Path(d) / 'REPORT.json'
            receipt = self.preload(); raw = m.canonical(receipt)
            p.write_bytes(raw); os.chmod(p, 0o600)
            seal = dict(self.seal(), bytes=len(raw), sha256=m.sha(raw))
            with patch.object(m, 'PRELOAD_PATH', str(p)), patch.object(m, 'PRELOAD_BINDING', seal):
                self.assertEqual(m.read_preload(), receipt)
                p.write_bytes(raw.replace(b'"PASS"', b'"FAIL"', 1))
                with self.assertRaisesRegex(m.Refused, 'PRELOAD_BYTES_CHANGED'): m.read_preload()
                p.write_bytes(raw); os.chmod(p, 0o666)
                with self.assertRaisesRegex(m.Refused, 'PRELOAD_FILE_GUARD'): m.read_preload()
                p.unlink(); p.symlink_to('/dev/null')
                with self.assertRaises(OSError): m.read_preload()

    def fake_engine(self):
        m = self.m; e = m.Engine(); e.events = []; e.patched = False
        before = {m.SERVER: copy.deepcopy(self.d)}
        for name in m.PROTECTED:
            d = copy.deepcopy(self.d); d['metadata']['name'] = name; before[name] = d
        after = copy.deepcopy(before)
        after[m.SERVER]['spec']['template']['spec']['containers'][0]['image'] = m.NEW
        after[m.SERVER]['metadata']['generation'] += 1
        after[m.SERVER]['status']['observedGeneration'] += 1
        service = {'metadata': {'name': m.SERVER, 'uid': 'service-uid'}, 'spec': {'selector': {'keep': 'keep'}}}
        pod = {'metadata': {'name': 'current-synthetic-server', 'uid': '00000000-0000-0000-0000-000000000002'},
               'status': {'containerStatuses': [{'ready': True}]}}
        def get(kind, *args):
            if kind == 'node': return self.node()
            if kind == 'service': return service
            if kind == 'endpointslices': return self.endpoints(pod)
            raise AssertionError('unexpected GET')
        def claim(*args):
            e.events.append('durable_claim_backup_plan'); e.report['once_claimed'] = True
        def call(kind, args, **kwargs):
            self.assertEqual(args[:4], ['patch', 'deployment', m.SERVER, '--type=json'])
            self.assertIn('--patch-file', args); self.assertNotIn('-p', args)
            self.assertEqual(e.report['patch_request_count'], 1)
            self.assertTrue(e.report['patch_may_have_applied'])
            e.events.append('patch'); e.patched = True; return b'{}'
        e.deployments = lambda: copy.deepcopy(after if e.patched else before)
        e.get = get; e.server_pod = lambda *a: pod; e.jar = lambda *a: e.events.append('jar')
        e.cache = lambda: e.events.append('cache'); e.claim = claim; e.save = lambda: e.events.append('save')
        e.installed_check = lambda: e.events.append('arm64_check_and_uid_cleanup')
        e.http = lambda p: e.events.append('native_http'); e.call = call
        return e

    def environment(self):
        stack = self.host(self.m)
        stack.enter_context(patch.object(self.m, 'PRELOAD_BINDING', self.seal()))
        stack.enter_context(patch.object(self.m, 'read_preload', return_value=self.preload()))
        stack.enter_context(patch.object(self.m.os.path, 'lexists', return_value=False))
        stack.enter_context(patch.object(self.m.shutil, 'which', side_effect=lambda k, **kw: '/usr/bin/' + k))
        stack.enter_context(redirect_stdout(io.StringIO()))
        return stack

    def test_synthetic_full_workflow_one_patch_after_durable_backup_and_installed_check(self):
        e = self.fake_engine()
        with self.environment(): e.execute()
        self.assertEqual(e.events.count('patch'), 1)
        self.assertLess(e.events.index('durable_claim_backup_plan'), e.events.index('arm64_check_and_uid_cleanup'))
        self.assertLess(e.events.index('arm64_check_and_uid_cleanup'), e.events.index('patch'))
        self.assertEqual(e.report['status'], 'SKILLHUB_REGISTRY_AUTH_RUNTIME_ACCEPTED_EDGE_PENDING')
        self.assertTrue(e.report['normal_application_startup_database_and_redis_access_may_occur'])

    def test_unrelated_rollout_does_not_block_when_spec_identity_is_stable(self):
        e = self.fake_engine(); original = e.deployments
        def progressing():
            result = original()
            result['gateway']['status']['updatedReplicas'] = 0
            return result
        e.deployments = progressing
        with self.environment(): e.execute()
        self.assertEqual(e.events.count('patch'), 1)

    def test_failed_patch_ack_is_never_retried_or_rolled_back(self):
        e = self.fake_engine(); calls = []
        def refused(*args, **kw):
            calls.append(args); raise self.m.Refused('COMMAND_TIMEOUT')
        e.call = refused
        with self.environment(), self.assertRaisesRegex(self.m.Refused, 'COMMAND_TIMEOUT'): e.execute()
        self.assertEqual(len(calls), 1); self.assertTrue(e.report['patch_may_have_applied'])

    def test_prepatch_concurrent_change_refuses_without_patch(self):
        e = self.fake_engine()
        def installed():
            e.events.append('arm64_check_and_uid_cleanup')
            def changed():
                d = {self.m.SERVER: copy.deepcopy(self.d)}
                for name in self.m.PROTECTED:
                    row = copy.deepcopy(self.d); row['metadata']['name'] = name; d[name] = row
                d['gateway']['metadata']['generation'] += 1
                return d
            e.deployments = changed
        e.installed_check = installed
        with self.environment(), self.assertRaisesRegex(self.m.Refused, 'UNRELATED_DEPLOYMENT_CHANGED'): e.execute()
        self.assertNotIn('patch', e.events)

    def test_installed_check_failure_cannot_patch(self):
        e = self.fake_engine()
        e.installed_check = lambda: (_ for _ in ()).throw(self.m.Refused('WRONG_JAR'))
        with self.environment(), self.assertRaisesRegex(self.m.Refused, 'WRONG_JAR'): e.execute()
        self.assertNotIn('patch', e.events)

    def server_inventory(self):
        m = self.m
        rs_name = 'skillhub-server-86c7d457bb'
        rs_uid = '936e6d0b-a4b4-4823-bb99-ba54f2062924'
        owner = {'apiVersion': 'apps/v1', 'kind': 'ReplicaSet', 'name': rs_name,
                 'uid': rs_uid, 'controller': True, 'blockOwnerDeletion': True}
        active = {'metadata': {'name': 'skillhub-server-86c7d457bb-7vvbw',
            'uid': '04e4804a-f04e-49e2-b181-bbdfb276245f', 'namespace': m.NS,
            'ownerReferences': [copy.deepcopy(owner)]},
            'spec': {'nodeName': m.NODE, 'containers': [{'name': 'server', 'image': m.OLD, 'imagePullPolicy': 'Never'}]},
            'status': {'phase': 'Running', 'conditions': [{'type': 'Ready', 'status': 'True'}],
                'containerStatuses': [{'name': 'server', 'ready': True, 'restartCount': 0,
                    'state': {'running': {'startedAt': '2026-11-06T00:00:00Z'}}, 'imageID': m.OLD_ROOT}]}}
        pods = [active]
        for name, pin in m.HISTORICAL_FAILED_PODS.items():
            row = copy.deepcopy(active)
            row['metadata'].update(name=name, uid=pin['uid'])
            row['status'].update(phase='Failed', conditions=[{'type': 'Ready', 'status': 'False'}])
            row['status']['containerStatuses'][0].update(ready=False, state={'waiting': {'reason': 'ContainerStatusUnknown'}})
            pods.append(row)
        rs = {'metadata': {'name': rs_name, 'namespace': m.NS, 'uid': rs_uid,
            'ownerReferences': [{'apiVersion': 'apps/v1', 'kind': 'Deployment', 'name': m.SERVER,
                'uid': self.d['metadata']['uid'], 'controller': True, 'blockOwnerDeletion': True}]}}
        return pods, rs

    def inventory_engine(self, pods, rs):
        e = self.m.Engine(); e.reads = []
        def get(kind, *args):
            e.reads.append((kind, args))
            if kind == 'pods': return {'items': copy.deepcopy(pods)}
            if kind == 'replicaset': return copy.deepcopy(rs)
            raise AssertionError('unexpected I/O')
        e.get = get
        return e

    def test_actual_three_pod_shape_reproduces_r1_refusal_and_r2_accepts_only_active(self):
        pods, rs = self.server_inventory()
        path = HERE / 'activate-portal-skillhub-registry-auth-20261106-r1.py'
        self.assertEqual(self.m.sha(path.read_bytes()), 'a130269d6aa88f4e9249fb332efd7d58d2667943517ca661c98202d394bd853b')
        spec = importlib.util.spec_from_file_location('unchanged_server_activation_r1', path)
        prior = importlib.util.module_from_spec(spec); spec.loader.exec_module(prior)
        old = prior.Engine(); old.get = self.inventory_engine(pods, rs).get
        with self.assertRaisesRegex(prior.Refused, 'ONE_SERVER_POD_REQUIRED'):
            old.server_pod(self.d, self.m.OLD)
        e = self.inventory_engine(pods, rs)
        self.assertEqual(e.server_pod(self.d, self.m.OLD)['metadata']['uid'], pods[0]['metadata']['uid'])
        rows = e.report['server_pod_inventory_checks'][0]['excluded_historical_failed_pods']
        self.assertEqual({r['uid'] for r in rows}, {p['uid'] for p in self.m.HISTORICAL_FAILED_PODS.values()})
        self.assertTrue(all(r['deployment_owner_verified'] for r in rows))
        self.assertEqual(e.report['historical_failed_pods_deleted'], 0)
        self.assertEqual(e.report['temporary_pod_delete_requests'], 0)
        self.assertTrue(all(kind in ('pods', 'replicaset') for kind, _ in e.reads))

    def test_absent_historical_records_are_not_required_or_recreated(self):
        pods, rs = self.server_inventory()
        e = self.inventory_engine(pods[:1], rs)
        self.assertEqual(e.server_pod(self.d, self.m.OLD)['metadata']['uid'], pods[0]['metadata']['uid'])
        self.assertEqual(e.report['server_pod_inventory_checks'][0]['excluded_historical_failed_pods'], [])
        self.assertEqual(e.report['temporary_pod_create_requests'], 0)

    def test_each_pinned_historical_guard_fails_closed(self):
        changes = (
            lambda p: p['metadata'].update(uid='changed-uid'),
            lambda p: p['metadata'].update(name='unknown-failed-pod'),
            lambda p: p['metadata'].update(namespace='other'),
            lambda p: p['metadata'].update(deletionTimestamp='2026-11-06T00:00:00Z'),
            lambda p: p['spec'].update(nodeName='datax-02'),
            lambda p: p['metadata']['ownerReferences'][0].update(uid='other-rs'),
            lambda p: p['metadata']['ownerReferences'][0].update(name='other-rs'),
            lambda p: p['status'].update(phase='Running'),
            lambda p: p['status'].update(phase='Pending'),
            lambda p: p['status'].update(phase='Succeeded'),
            lambda p: p['status']['containerStatuses'][0].update(ready=True),
            lambda p: p['status']['containerStatuses'][0].pop('ready'),
            lambda p: p['status'].update(containerStatuses=[]),
            lambda p: p['status']['containerStatuses'][0].update(state={'running': {}}),
            lambda p: p['status']['conditions'][0].update(status='True'),
            lambda p: p['spec']['containers'][0].update(image=self.m.NEW),
        )
        for index in (1, 2):
            for case, mutate in enumerate(changes):
                pods, rs = self.server_inventory(); mutate(pods[index])
                with self.subTest(index=index, case=case), self.assertRaises(self.m.Refused):
                    self.inventory_engine(pods, rs).server_pod(self.d, self.m.OLD)

    def test_terminal_records_never_hide_running_init_or_ephemeral_containers(self):
        for key in ('initContainerStatuses', 'ephemeralContainerStatuses'):
            for running in ({}, {'startedAt': '2026-11-06T00:00:00Z'}):
                pods, rs = self.server_inventory()
                pods[1]['status'][key] = [{'name': 'other', 'state': {'running': running}}]
                with self.subTest(key=key, running=running), self.assertRaises(self.m.Refused):
                    self.inventory_engine(pods, rs).server_pod(self.d, self.m.OLD)

    def test_terminal_record_owner_chain_must_still_reach_fixed_deployment(self):
        pods, rs = self.server_inventory()
        rs['metadata']['ownerReferences'][0]['uid'] = 'other-deployment'
        with self.assertRaisesRegex(self.m.Refused, 'SERVER_RS_OWNER'):
            self.inventory_engine(pods, rs).server_pod(self.d, self.m.OLD)

    def test_unknown_extra_duplicate_or_missing_active_pod_rejected(self):
        for case in ('unknown_failed', 'duplicate_name', 'duplicate_uid', 'no_active'):
            pods, rs = self.server_inventory()
            if case == 'unknown_failed':
                other = copy.deepcopy(pods[1]); other['metadata'].update(name='unobserved-history', uid='other-uid'); pods.append(other)
            elif case == 'duplicate_name': pods.append(copy.deepcopy(pods[1]))
            elif case == 'duplicate_uid': pods[0]['metadata']['uid'] = pods[1]['metadata']['uid']
            else: pods.pop(0)
            with self.subTest(case=case), self.assertRaises(self.m.Refused):
                self.inventory_engine(pods, rs).server_pod(self.d, self.m.OLD)

    def real_inventory_flow(self):
        e = self.fake_engine(); pods, rs = self.server_inventory(); oldget = e.get
        candidate = copy.deepcopy(pods[0])
        candidate['metadata'].update(name='skillhub-server-candidate-current', uid='00000000-0000-0000-0000-000000000099')
        candidate['spec']['containers'][0]['image'] = self.m.NEW
        candidate['status']['containerStatuses'][0]['imageID'] = self.m.MANIFEST
        candidate['metadata']['ownerReferences'][0].update(name='skillhub-server-candidate', uid='00000000-0000-0000-0000-000000000098')
        newrs = copy.deepcopy(rs); newrs['metadata'].update(name='skillhub-server-candidate', uid='00000000-0000-0000-0000-000000000098')
        def get(kind, *args):
            if kind == 'pods': return {'items': [candidate if e.patched else pods[0]] + pods[1:]}
            if kind == 'replicaset': return newrs if args[0] == 'skillhub-server-candidate' else rs
            if kind == 'endpointslices':
                e.events.append('endpoint_after' if e.patched else 'endpoint_before')
                return self.endpoints(candidate if e.patched else pods[0])
            return oldget(kind, *args)
        e.get = get; e.server_pod = types.MethodType(self.m.Engine.server_pod, e)
        return e, pods, rs

    def test_actual_inventory_runs_full_controller_path_with_prepatch_and_postpatch_endpoints(self):
        e, _, _ = self.real_inventory_flow()
        with self.environment(): e.execute()
        self.assertEqual(e.events.count('patch'), 1)
        self.assertEqual(e.events.count('endpoint_before'), 2)
        self.assertLess(e.events.index('endpoint_before'), e.events.index('durable_claim_backup_plan'))
        self.assertLess(max(i for i, event in enumerate(e.events) if event == 'endpoint_before'), e.events.index('patch'))
        self.assertGreater(e.events.index('endpoint_after'), e.events.index('patch'))
        self.assertEqual(len(e.report['server_pod_inventory_checks']), 4)
        self.assertTrue(all(len(r['excluded_historical_failed_pods']) == 2 for r in e.report['server_pod_inventory_checks']))
        self.assertTrue(e.report['old_service_endpoints_rechecked_before_patch'])
        self.assertEqual(e.report['historical_failed_pods_deleted'], 0)

    def test_unknown_extra_in_real_preflight_cannot_claim_or_patch(self):
        e, pods, _ = self.real_inventory_flow()
        extra = copy.deepcopy(pods[1]); extra['metadata'].update(name='unobserved', uid='other'); pods.append(extra)
        with self.environment(), self.assertRaises(self.m.Refused): e.execute()
        self.assertNotIn('durable_claim_backup_plan', e.events); self.assertNotIn('patch', e.events)
        self.assertFalse(e.report['once_claimed'])

    def test_historical_pod_in_endpoint_refuses_before_marker(self):
        e, pods, _ = self.real_inventory_flow(); oldget = e.get
        e.get = lambda kind, *args: self.endpoints(pods[1]) if kind == 'endpointslices' else oldget(kind, *args)
        with self.environment(), self.assertRaisesRegex(self.m.Refused, 'ENDPOINT_NOT_CURRENT_POD'): e.execute()
        self.assertNotIn('durable_claim_backup_plan', e.events); self.assertNotIn('patch', e.events)

    def test_existing_marker_refuses_before_reads_or_mutations(self):
        e = self.fake_engine()
        with self.environment(), patch.object(self.m.os.path, 'lexists', return_value=True):
            with self.assertRaisesRegex(self.m.Refused, 'ONCE_EXISTS'): e.execute()
        self.assertEqual(e.events, [])


if __name__ == '__main__':
    unittest.main()
