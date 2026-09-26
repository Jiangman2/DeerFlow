import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from contextlib import ExitStack

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('canary', str(ROOT / 'canary-wave1.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def deployment():
    return {'metadata': {'uid': 'front', 'generation': 1}, 'status': {
        'observedGeneration': 1, 'replicas': 1, 'updatedReplicas': 1,
        'readyReplicas': 1, 'availableReplicas': 1}, 'spec': {'replicas': 1,
        'template': {'metadata': {'labels': {'app': 'frontend'}}, 'spec': {
            'containers': [{'name': 'frontend', 'image': m.p.IMAGE, 'imagePullPolicy': 'Never',
                'envFrom': [{'secretRef': {'name': 'deer-flow-env'}}],
                'resources': {'requests': {'cpu': '100m', 'memory': '256Mi'},
                              'limits': {'cpu': '1', 'memory': '1Gi'}},
                'readinessProbe': {'httpGet': {'path': '/api/runtime-config', 'port': 3000}}}],
            'volumes': [{'name': 'demo', 'configMap': {'name': 'demo'}}]}}}}


def plan():
    return {'version': m.p.VERSION, 'phase': 'cluster', 'target': m.p.TARGET,
        'collected_epoch': time.time(), 'cluster_uid': 'cluster',
        'frontend': dict(m.p.frontend_projection(deployment()), image_id='sha256:' + 'a'*64,
                         next_runtime={'build_id_sha256': 'build'}, pod_uid='prod-pod'),
        'protected_workloads': {'gateway': {'uid': 'g', 'replicas': 1}},
        'service_spec_sha256': 'svc', 'target_node': {'uid': 'node', 'cordoned': True,
            'pods': [{'namespace': 'kube-system', 'owners': [{'kind': 'DaemonSet', 'name': 'kube-proxy'}], 'phase': 'Running'}]}}


def preload():
    x = plan()
    return {'version': m.p.VERSION, 'phase': 'preload', 'target': m.p.TARGET,
        'collected_epoch': time.time(), 'image_preload': 'PASS', 'image': m.p.IMAGE,
        'image_id': x['frontend']['image_id'], 'containerd_root': '/mnt/data/containerd',
        'kubernetes_mutation': 'NONE', 'rollout': 'NOT_PERFORMED'}


class CanaryTests(unittest.TestCase):
    def setUp(self):
        self.token = 'a' * 16
        self.name = 'cap-w1-fe-' + self.token
        self.intent = {'version': m.VERSION, 'token': self.token, 'name': self.name,
                       'cluster_uid': 'cluster', 'plan_sha256': 'b' * 64}
        self.pod, self.svc = m.manifests(deployment(), self.intent)

    def test_preload_pass(self):
        m.validate_preload(preload(), plan())

    def test_preload_rejections(self):
        for key, value in [('image_id', 'sha256:'+'c'*64), ('target', 'datax-02'),
                           ('image_preload', 'FAIL'), ('phase', 'export'),
                           ('collected_epoch', time.time()-21601), ('containerd_root', '/var/lib/containerd')]:
            x = preload(); x[key] = value
            with self.subTest(key=key), self.assertRaises(m.p.Blocked):
                m.validate_preload(x, plan())

    def test_baseline_match(self):
        x = plan(); m.compare(x, copy.deepcopy(x))

    def test_baseline_drift(self):
        for field in ['cluster_uid', 'service_spec_sha256', 'protected_workloads']:
            x = plan(); y = copy.deepcopy(x); y[field] = 'changed'
            with self.subTest(field=field), self.assertRaises(m.p.Blocked): m.compare(x, y)
        for field in ['uid', 'spec_sha256', 'image_id', 'next_runtime', 'pod_uid']:
            x = plan(); y = copy.deepcopy(x); y['frontend'][field] = 'changed'
            with self.subTest(field=field), self.assertRaises(m.p.Blocked): m.compare(x, y)

    def test_no_cordon_change(self):
        x = plan(); y = copy.deepcopy(x); y['target_node']['cordoned'] = False
        with self.assertRaises(m.p.Blocked): m.compare(x, y)

    def test_busy_target(self):
        x = plan(); m.empty_target(x)
        x['target_node']['pods'][0]['namespace'] = 'deer-flow'
        with self.assertRaises(m.p.Blocked): m.empty_target(x)

    def test_scheduler_not_bypassed(self):
        spec = self.pod['spec']
        self.assertNotIn('nodeName', spec)
        self.assertEqual(spec['nodeSelector'], {'kubernetes.io/hostname': m.p.TARGET})
        self.assertEqual(spec['tolerations'], [{'key': 'node.kubernetes.io/unschedulable', 'operator': 'Exists', 'effect': 'NoSchedule'}])

    def test_limits_and_no_token(self):
        spec = self.pod['spec']
        self.assertFalse(spec['automountServiceAccountToken'])
        self.assertEqual(spec['activeDeadlineSeconds'], 900)
        self.assertEqual(spec['containers'][0]['resources']['limits']['memory'], '1Gi')
        self.assertEqual(spec['containers'][0]['resources']['limits']['ephemeral-storage'], '512Mi')
        self.assertEqual(spec['containers'][0]['envFrom'], deployment()['spec']['template']['spec']['containers'][0]['envFrom'])

    def test_service_private_and_isolated(self):
        self.assertEqual(self.svc['spec']['type'], 'ClusterIP')
        self.assertNotIn('app', self.pod['metadata']['labels'])
        self.assertNotIn('ownerReferences', self.pod['metadata'])
        self.assertEqual(self.svc['spec']['selector'], self.pod['metadata']['labels'])
        m.no_service_overlap([{'metadata': {'name': 'frontend'}, 'spec': {'selector': {'app': 'frontend'}}}], self.pod['metadata']['labels'])

    def test_overlap_rejected(self):
        with self.assertRaises(m.p.Blocked):
            m.no_service_overlap([{'metadata': {'name': 'other'}, 'spec': {'selector': {m.LABEL: self.token}}}], self.pod['metadata']['labels'])

    def test_unsafe_clone_rejected(self):
        for field, value in [('hostNetwork', True), ('initContainers', [{'name': 'init'}]),
                             ('nodeName', 'datax-02'), ('affinity', {'anything': True}),
                             ('volumes', [{'name': 'data', 'persistentVolumeClaim': {'claimName': 'users'}}])]:
            d = deployment(); d['spec']['template']['spec'][field] = value
            with self.subTest(field=field), self.assertRaises(m.p.Blocked): m.manifests(d, self.intent)

    def test_host_port_rejected(self):
        d = deployment(); d['spec']['template']['spec']['containers'][0]['ports'] = [{'containerPort': 3000, 'hostPort': 3000}]
        with self.assertRaises(m.p.Blocked): m.manifests(d, self.intent)

    def test_admission_sidecar_rejected(self):
        actual = copy.deepcopy(self.pod); actual['spec']['containers'].append({'name': 'extra'})
        with self.assertRaises(m.p.Blocked): m.check_admission(actual, self.pod)

    def test_admission_defaulting_accepted(self):
        actual = copy.deepcopy(self.pod)
        actual['spec']['containers'][0]['terminationMessagePath'] = '/dev/termination-log'
        actual['spec']['containers'][0]['ports'] = []
        m.check_admission(actual, self.pod)

    def test_default_noexecute_tolerations_accepted(self):
        actual = copy.deepcopy(self.pod)
        actual['spec']['tolerations'].append({'key': 'node.kubernetes.io/not-ready', 'effect': 'NoExecute', 'operator': 'Exists', 'tolerationSeconds': 300})
        m.check_admission(actual, self.pod)
        actual['spec']['tolerations'].append({'operator': 'Exists'})
        with self.assertRaises(m.p.Blocked): m.check_admission(actual, self.pod)

    def test_service_admission_rejects_public_exposure(self):
        m.check_service(self.svc, self.svc)
        for field, value in [('type', 'NodePort'), ('externalIPs', ['1.2.3.4']), ('selector', {'app': 'frontend'})]:
            actual = copy.deepcopy(self.svc); actual['spec'][field] = value
            with self.subTest(field=field), self.assertRaises(m.p.Blocked): m.check_service(actual, self.svc)

    def test_admission_privilege_rejected(self):
        actual = copy.deepcopy(self.pod)
        actual['spec']['containers'][0]['securityContext'] = {'privileged': True}
        with self.assertRaises(m.p.Blocked): m.check_admission(actual, self.pod)

    def test_uid_precondition(self):
        obj = copy.deepcopy(self.pod); obj['metadata']['uid'] = 'uid-123'
        body = m.delete_options(obj, self.intent, 'uid-123')
        self.assertEqual(body['preconditions'], {'uid': 'uid-123'})
        self.assertGreater(body['gracePeriodSeconds'], 0)

    def test_delete_replacement_rejected(self):
        obj = copy.deepcopy(self.pod); obj['metadata']['uid'] = 'replacement'
        with self.assertRaises(m.p.Blocked): m.delete_options(obj, self.intent, 'old')

    def test_delete_foreign_label_rejected(self):
        obj = copy.deepcopy(self.pod); obj['metadata']['uid'] = 'id'
        obj['metadata']['labels'] = {'app': 'frontend'}
        with self.assertRaises(m.p.Blocked): m.delete_options(obj, self.intent, None)

    def test_cleanup_absent_idempotent(self):
        with tempfile.TemporaryDirectory() as t, patch.object(m, 'named', return_value=None), patch.object(m, 'api') as api:
            m.cleanup(self.intent, Path(t)); api.assert_not_called()

    def test_cleanup_continues_after_failure(self):
        obj = copy.deepcopy(self.pod); obj['metadata']['uid'] = 'id'
        with tempfile.TemporaryDirectory() as t, patch.object(m, 'named', return_value=obj), patch.object(m, 'api', side_effect=m.p.Blocked('test')) as api:
            with self.assertRaises(m.p.Blocked): m.cleanup(self.intent, Path(t))
            self.assertEqual(api.call_count, 2)

    def test_mutation_guard(self):
        for args in [('patch', 'deployment', 'frontend'), ('get', 'secrets'), ('delete', 'pod', 'frontend')]:
            with self.subTest(args=args), self.assertRaises(m.p.Blocked): m.api(*args)

    def test_exec_not_arbitrary(self):
        with self.assertRaises(m.p.Blocked): m.api('exec', 'prod', '--', 'sh', '-c', 'true')

    def test_api_supported_verbs_serialize_no_shell(self):
        from types import SimpleNamespace
        with patch.object(m.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout='{}', stderr='')) as call:
            m.named('pod', self.name)
            m.api('create', '--dry-run=server', '-f', '-', '-o', 'json', data=self.pod)
            m.api('create', '-f', '-', '-o', 'json', data=self.svc)
            m.api('exec', 'frontend-abcd-efgh', '-c', 'frontend', '--', 'node', '-e', m.HTTP_PROBE, self.name+'.deer-flow.svc', '3000')
            m.api('delete', '--raw', '/api/v1/namespaces/deer-flow/pods/'+self.name, '-f', '-',
                  data={'kind': 'DeleteOptions', 'preconditions': {'uid': 'abc'}})
            self.assertEqual(call.call_count, 5)
            for c in call.call_args_list: self.assertNotIn('shell', c.kwargs)

    def test_run_cleanup_on_probe_error_and_success(self):
        for fail in (False, True):
            with self.subTest(fail=fail), tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
                x = plan(); x['frontend']['pod'] = 'frontend-abcd-efgh'
                gateway = {'spec': {'template': {'spec': {'containers': [{'name': 'gateway',
                    'readinessProbe': {'httpGet': {'path': '/health', 'port': 8001}}}]}}}}
                x['protected_workloads']['gateway']['spec_sha256'] = m.p.sha(gateway['spec'])
                def fake_get(kind, name=None):
                    if kind == 'services': return {'items': []}
                    return deployment() if name == 'frontend' else gateway
                def fake_api(*args, **kwargs):
                    if args[0] == 'create':
                        out = copy.deepcopy(kwargs['data']); out['metadata']['uid'] = 'canary-uid'; return out
                    if args[0] == 'exec': return x['frontend']['next_runtime']
                    raise AssertionError(args)
                stack.enter_context(patch.object(m.p, 'cluster', return_value=x))
                stack.enter_context(patch.object(m.p, 'get', side_effect=fake_get))
                stack.enter_context(patch.object(m, 'api', side_effect=fake_api))
                stack.enter_context(patch.object(m, 'wait_endpoint'))
                stack.enter_context(patch.object(m, 'wait_ready', return_value={'metadata': {'uid': 'canary-uid', 'labels': {}},
                    'status': {'containerStatuses': [{'imageID': x['frontend']['image_id']}]}}))
                stack.enter_context(patch.object(m, 'probe', side_effect=m.p.Blocked('synthetic_probe_failure') if fail else None, return_value={'ok': True}))
                cleanup = stack.enter_context(patch.object(m, 'cleanup'))
                if fail:
                    with self.assertRaises(m.p.Blocked): m.execute(x, preload(), Path(tmp))
                else:
                    result = m.execute(x, preload(), Path(tmp))
                    self.assertEqual(result['wave1_frontend_canary'], 'PASS')
                    self.assertEqual(result['rollout'], 'NOT_PERFORMED')
                cleanup.assert_called_once()

    def test_uncertain_create_cleanup_without_recreate(self):
        obj = copy.deepcopy(self.pod); obj['metadata']['uid'] = 'created-but-reply-lost'
        with tempfile.TemporaryDirectory() as t, patch.object(m, 'named', side_effect=[None, obj, None]), patch.object(m, 'api', return_value={}) as api:
            m.cleanup(self.intent, Path(t))
            self.assertEqual(api.call_count, 1)
            self.assertEqual(api.call_args.args[0], 'delete')
            self.assertEqual(api.call_args.kwargs['data']['preconditions']['uid'], 'created-but-reply-lost')


if __name__ == '__main__': unittest.main()
