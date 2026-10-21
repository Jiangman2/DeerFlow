import ast
import contextlib
import copy
from decimal import Decimal
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import audit_capacity as c


def container(name, cpu, memory='1Gi', **extra):
    return dict(name=name, resources={'requests': {'cpu': cpu, 'memory': memory}}, **extra)


def node():
    return {'metadata': {'name': 'worker-1', 'labels': {'zone': 'a'}}, 'spec': {},
            'status': {'allocatable': {'cpu': '8', 'memory': '16Gi', 'pods': '110'},
                       'nodeInfo': {'architecture': 'arm64'}, 'conditions': [
                {'type': 'Ready', 'status': 'True'}, {'type': 'DiskPressure', 'status': 'False'},
                {'type': 'MemoryPressure', 'status': 'False'}, {'type': 'PIDPressure', 'status': 'False'}]}}


class CapacityTests(unittest.TestCase):
    def test_python37_syntax(self):
        ast.parse(Path(c.__file__).read_text(), feature_version=(3, 7))
        ast.parse(c.HEALTH, feature_version=(3, 7))

    def test_quantity(self):
        for value, expected in [('500m', '.5'), ('1200u', '.0012'), ('123n', '.000000123'),
                                 ('1Gi', str(1024 ** 3)), ('1.5Gi', str(int(1.5 * 1024 ** 3))),
                                 ('1e3', '1000'), ('100M', '100000000')]:
            self.assertEqual(c.quantity(value), Decimal(expected))
        with self.assertRaises(c.a.CheckFailure):
            c.quantity('private-text')

    def test_init_peak_and_overhead(self):
        pod = {'spec': {'containers': [container('main', '1')],
                        'initContainers': [container('setup', '4')], 'overhead': {'cpu': '250m'}}}
        self.assertEqual(c.pod_requests(pod, 'cpu'), Decimal('4.25'))

    def test_restartable_init_contributes_to_later_init_and_running_total(self):
        pod = {'spec': {'containers': [container('main', '2')], 'initContainers': [
            container('sidecar', '1', restartPolicy='Always'), container('setup', '4')]}}
        self.assertEqual(c.pod_requests(pod, 'cpu'), Decimal('5'))

    def test_resize_allocation_and_pod_level_requests_are_not_ignored(self):
        pod = {'spec': {'containers': [container('main', '1')], 'resources': {'requests': {'cpu': '4'}}},
               'status': {'containerStatuses': [{'name': 'main', 'allocatedResources': {'cpu': '3'}}]}}
        self.assertEqual(c.pod_requests(pod, 'cpu'), Decimal('4'))
        pod['spec'].pop('resources')
        self.assertEqual(c.pod_requests(pod, 'cpu'), Decimal('3'))

    def test_node_rules_and_pressure(self):
        n = node()
        self.assertTrue(c.static_eligible(n, {}))
        self.assertFalse(c.static_eligible(n, {'nodeSelector': {'zone': 'b'}}))
        n['status']['conditions'][1]['status'] = 'True'
        self.assertFalse(c.static_eligible(n, {}))

    def test_taints_and_unschedulable(self):
        n = node()
        n['spec']['taints'] = [{'key': 'dedicated', 'value': 'other', 'effect': 'NoSchedule'}]
        self.assertFalse(c.static_eligible(n, {}))
        spec = {'tolerations': [{'key': 'dedicated', 'operator': 'Exists', 'effect': 'NoSchedule'}]}
        self.assertTrue(c.static_eligible(n, spec))
        n['spec']['unschedulable'] = True
        self.assertFalse(c.static_eligible(n, spec))

    def test_required_affinity_is_not_ignored(self):
        spec = {'affinity': {'nodeAffinity': {'requiredDuringSchedulingIgnoredDuringExecution': {
            'nodeSelectorTerms': [{'matchExpressions': [{'key': 'zone', 'operator': 'In', 'values': ['b']}]}]}}}}
        self.assertFalse(c.static_eligible(node(), spec))
        spec['affinity']['nodeAffinity']['requiredDuringSchedulingIgnoredDuringExecution']['nodeSelectorTerms'][0]['matchExpressions'][0]['values'] = ['a']
        self.assertTrue(c.static_eligible(node(), spec))

    def test_budget_removes_only_owned_workers_and_excludes_terminal_pods(self):
        def pod(uid, cpu, phase):
            return {'metadata': {'uid': uid}, 'spec': {'containers': [container('main', cpu)]}, 'status': {'phase': phase}}
        pods = [pod('ours', '1', 'Running'), pod('other', '4', 'Running'), pod('done', '8', 'Failed')]
        summary = c.node_budget(node(), pods, {'ours'}, {'containers': [container('adapter', '500m')]})
        self.assertEqual(summary['steady_state_worker_slots_upper_bound'], 8)
        self.assertEqual(summary['cpu']['reserved_requests'], 5.0)
        self.assertEqual(summary['active_bound_pods'], 2)
        self.assertTrue(summary['estimate_only'])

    def test_no_capacity_for_ineligible_node(self):
        summary = c.node_budget(node(), [], set(), {'containers': [container('adapter', '500m')], 'nodeSelector': {'zone': 'b'}})
        self.assertEqual(summary['steady_state_worker_slots_upper_bound'], 0)

    def test_status_and_event_messages_are_redacted(self):
        p = {'metadata': {'uid': 'pod-id', 'name': 'pod-name'}, 'spec': {}, 'status': {
            'message': 'SECRET-BODY', 'phase': 'Pending', 'containerStatuses': [
                {'name': 'adapter', 'state': {'waiting': {'reason': 'ErrImageNeverPull', 'message': 'SECRET-BODY'}}}]}}
        result = json.dumps(c.pod_summary(p))
        self.assertNotIn('SECRET-BODY', result)
        self.assertIn('ErrImageNeverPull', result)
        result = c.event_summary([{'reason': 'FailedScheduling', 'message': 'Insufficient memory SECRET-BODY'}])
        self.assertIn('insufficient_memory', result['message_categories'])
        self.assertNotIn('SECRET-BODY', json.dumps(result))

    def test_no_mutation_or_secret_read_verbs(self):
        tree = ast.parse(Path(c.__file__).read_text())
        strings = [x.value for x in ast.walk(tree) if isinstance(x, ast.Constant) and isinstance(x.value, str)]
        for word in ('patch', 'scale', 'apply', 'delete', 'rollout', 'secret', 'secrets'):
            self.assertNotIn(word, strings)

    def test_dispatcher_probe_uses_its_actual_container_and_stdin(self):
        p = {'metadata': {'uid': 'p', 'name': 'dispatcher-pod'},
             'spec': {'containers': [{'name': 'dispatcher', 'image': c.IMAGE}]},
             'status': {'phase': 'Running', 'containerStatuses': [
                 {'name': 'dispatcher', 'ready': True, 'imageID': c.IMAGE_ID}]}}
        with patch.object(c.a, 'capture', return_value=b'{"dispatcher":{"busy":0,"ready":true}}') as capture:
            result = c.health(p, 'dispatcher')
        self.assertTrue(result['dispatcher']['ready'])
        args = capture.call_args[0][0]
        self.assertEqual(args[args.index('-c') + 1], 'dispatcher')
        self.assertEqual(capture.call_args[0][1], c.HEALTH.encode())
        self.assertNotIn(c.HEALTH, args)

    def test_changed_or_unready_container_is_not_executed(self):
        p = {'metadata': {'uid': 'p', 'name': 'worker'},
             'spec': {'containers': [{'name': 'adapter', 'image': c.IMAGE}]},
             'status': {'phase': 'Running', 'containerStatuses': [
                 {'name': 'adapter', 'ready': True, 'imageID': 'sha256:changed'}]}}
        with patch.object(c.a, 'capture') as capture:
            self.assertEqual(c.health(p, 'adapter')['health'], 'BASELINE_IMAGE_CHANGED_NOT_EXECUTED')
            p['status']['containerStatuses'][0]['ready'] = False
            self.assertEqual(c.health(p, 'adapter')['health'], 'NOT_READY_NOT_EXECUTED')
        capture.assert_not_called()

    def test_embedded_health_is_loopback_only_and_redacts_other_fields(self):
        class Response:
            code = 200
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self, maximum):
                self_outer.assertEqual(maximum, 65537)
                return b'{"dispatcher":{"busy":2,"ready":true,"token":"PRIVATE"},"secret":"PRIVATE"}'
        class Opener:
            def open(self, url, timeout):
                self_outer.assertEqual(url, 'http://127.0.0.1:50052/health')
                self_outer.assertEqual(timeout, 12)
                return Response()
        self_outer = self
        output = io.StringIO()
        with patch.dict(os.environ, {}, clear=True), patch('pathlib.Path.is_file', return_value=False), \
                patch('urllib.request.build_opener', return_value=Opener()), contextlib.redirect_stdout(output):
            exec(compile(c.HEALTH, '<health-fixture>', 'exec'), {})
        self.assertNotIn('PRIVATE', output.getvalue())
        result = json.loads(output.getvalue())
        self.assertEqual(result['provider_requests'], 0)
        self.assertEqual(result['dispatcher'], {'busy': 2, 'ready': True})

    def test_private_result_permissions_and_hash(self):
        with tempfile.TemporaryDirectory() as parent:
            target = Path(parent) / 'new-evidence'
            target.mkdir(mode=0o700)
            with patch.object(c.tempfile, 'mkdtemp', return_value=str(target)):
                result, digest = c.save_private({'provider_requests': 0})
            self.assertEqual(Path(result).stat().st_mode & 0o777, 0o600)
            self.assertEqual(c.hashlib.sha256(Path(result).read_bytes()).hexdigest(), digest)

    def run_collection_fixture(self, drift=False):
        names = (c.WORKER, c.DISPATCHER, 'weknora-app', 'weknora-docreader')
        deployments = {name: {'metadata': {'name': name, 'uid': name + '-uid', 'generation': 1},
            'spec': {'replicas': 1, 'template': {'spec': {'containers': [
                container('adapter' if name == c.WORKER else 'dispatcher', '500m', image=c.IMAGE)]}}},
            'status': {'readyReplicas': 1}} for name in names}
        counts = {}
        def get(resource, name=None):
            if resource == 'nodes': return {'items': [node()]}
            self.assertEqual(resource, 'deployment')
            counts[name] = counts.get(name, 0) + 1
            row = copy.deepcopy(deployments[name])
            if drift and name == c.WORKER and counts[name] == 2:
                row['spec']['replicas'] = 2
            return row
        def owned(deployment):
            name = deployment['metadata']['name']
            return [{'metadata': {'name': name + '-pod', 'uid': name + '-pod-uid'},
                'spec': {'nodeName': 'worker-1'}, 'status': {'phase': 'Running'}}]
        with patch.object(c.os, 'geteuid', return_value=0), \
                patch.object(c.socket, 'gethostname', return_value='datax-01'), \
                patch.object(c.a, 'get', side_effect=get), patch.object(c, 'owned', side_effect=owned), \
                patch.object(c, 'health', return_value={'dispatcher': {'ready': True}}) as health, \
                patch.object(c.a, 'capture', return_value=b'{"items":[]}') as capture, \
                patch.object(c.a, 'emit') as emit, \
                patch.object(c, 'save_private', return_value=('/var/lib/mineru-capacity-fixture/RESULT.json', 'sha')) as save:
            c.main()
        report = save.call_args.args[0]
        self.assertEqual(health.call_count, 2)
        self.assertEqual([call.args[1] for call in health.call_args_list], ['adapter', 'dispatcher'])
        self.assertEqual(capture.call_count, 1)
        self.assertIn('spec.nodeName=worker-1', capture.call_args.args[0])
        self.assertFalse(report['ready_for_rollout'])
        self.assertFalse(report['production_workloads_mutated'])
        self.assertEqual(report['provider_requests'], 0)
        self.assertEqual(report['steady_state_slots_upper_bound_requests_only'], 16)
        self.assertEqual(emit.call_args.args[0]['inventory'], 'COMPLETE')
        return report

    def test_complete_collection_has_no_provider_or_release_approval(self):
        self.assertTrue(self.run_collection_fixture()['baseline_stable_during_audit'])

    def test_baseline_drift_is_retained_not_silently_approved(self):
        report = self.run_collection_fixture(drift=True)
        self.assertFalse(report['baseline_stable_during_audit'])
        self.assertFalse(report['deployment_stability'][c.WORKER])


if __name__ == '__main__':
    unittest.main()
