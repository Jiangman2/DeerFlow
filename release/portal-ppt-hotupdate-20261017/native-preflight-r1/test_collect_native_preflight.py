import ast
import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('native_preflight', str(HERE / 'collect_native_preflight.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def workload(kind='Deployment', name='gateway'):
    return {'kind': kind, 'metadata': {'name': name, 'namespace': 'deer-flow', 'uid': name + '-uid', 'generation': 1},
            'spec': {'replicas': 1, 'selector': {'matchLabels': {'app': name}}, 'template': {'spec': {
                'containers': [{'name': name, 'image': 'test.invalid/' + name + ':fixed'}]}}},
            'status': {'replicas': 1, 'readyReplicas': 1, 'updatedReplicas': 1, 'availableReplicas': 1, 'observedGeneration': 1}}


def node():
    return {'kind': 'Node', 'metadata': {'name': m.TARGET, 'uid': m.NODE_UID, 'labels': {'kubernetes.io/hostname': m.TARGET, 'irrelevant': 'DO_NOT_EXPORT'}},
            'spec': {}, 'status': {'nodeInfo': {'architecture': 'arm64', 'operatingSystem': 'linux', 'containerRuntimeVersion': 'containerd://1.7.1'},
            'conditions': [{'type': k, 'status': 'True' if k == 'Ready' else 'False'} for k in ('Ready', 'DiskPressure', 'MemoryPressure', 'PIDPressure')],
            'addresses': [{'type': 'InternalIP', 'address': m.NODE_IP}],
            'capacity': {'cpu': '8', 'memory': '16Gi', 'ephemeral-storage': '40Gi', 'pods': '110'},
            'allocatable': {'cpu': '8', 'memory': '16Gi', 'ephemeral-storage': '40Gi', 'pods': '110'}}}


def container(cpu='100m', policy=''):
    return {'restartPolicy': policy, 'requests': {'cpu': cpu, 'memory': '1Mi', 'ephemeral-storage': '1Mi'},
            'limits': {'cpu': cpu, 'memory': '2Mi', 'ephemeral-storage': '2Mi'}, 'claims': False}


def projected_pod():
    return {'uid': 'resource-uid', 'node': m.TARGET, 'phase': 'Running', 'deleting': False,
            'containers': [container()], 'initContainers': [], 'overhead': {},
            'podResources': False, 'resourceClaims': False, 'resize': False, 'allocatedResources': []}


def pg_pod(owner='postgres-uid'):
    return {'metadata': {'name': 'postgres-0', 'namespace': 'deer-flow', 'uid': 'pg-pod-uid',
                         'ownerReferences': [{'controller': True, 'kind': 'StatefulSet', 'uid': owner}]},
            'spec': {'nodeName': m.TARGET, 'containers': [{'name': 'postgres', 'image': m.PG_REFERENCE,
                      'env': [{'name': 'PASSWORD', 'value': 'DO_NOT_EXPORT'}]}]},
            'status': {'phase': 'Running', 'conditions': [{'type': 'Ready', 'status': 'True'}], 'containerStatuses': [{
                'name': 'postgres', 'imageID': 'containerd://sha256:' + 'a' * 64, 'containerID': 'containerd://' + 'b' * 64,
                'restartCount': 0, 'ready': True}]}}


class FakeRunner:
    def __init__(self, fail=False, drift=False):
        self.argv = []
        self.counts = {}
        self.fail, self.drift = fail, drift
    def __call__(self, argv, **kwargs):
        self.argv.append(argv)
        offset = argv.index('get')
        kind = argv[offset + 1]
        key = tuple(argv[offset:offset + 3])
        self.counts[key] = self.counts.get(key, 0) + 1
        if kind == 'nodes':
            row = node()
        elif kind == 'deployments':
            component = argv[offset + 2]
            if self.fail and component == 'frontend':
                raise m.Failure('command_failed', {'returncode': 1, 'stderr': 'DO_NOT_EXPORT'})
            row = workload(name=component)
            if self.drift and self.counts[key] == 2 and component == 'gateway':
                row['spec']['template']['spec']['containers'][0]['image'] = 'test.invalid/gateway:changed'
        elif kind == 'statefulsets':
            row = workload('StatefulSet', 'postgres')
        elif any(s.startswith('--output=go-template=') for s in argv):
            row = {'items': [projected_pod()], 'continued': False}
        else:
            row = {'items': [pg_pod()], 'metadata': {}}
        raw = json.dumps(row).encode()
        return raw, {'stdout_bytes': len(raw), 'stderr_bytes': 0, 'elapsed_ms': 1}


class Tests(unittest.TestCase):
    def test_quantities(self):
        self.assertEqual(m.quantity('100m', 'cpu'), 100)
        self.assertEqual(m.quantity('1.25', 'cpu'), 1250)
        self.assertEqual(m.quantity('1Mi', 'memory'), 1048576)
        self.assertEqual(m.quantity('1e3', 'memory'), 1000)
        self.assertEqual(m.quantity('1n', 'cpu'), 1)
        for invalid in ('secret', '-1', 'NaN', '1MiB', '1e999'):
            with self.assertRaises(m.Failure):
                m.quantity(invalid, 'cpu')

    def test_restartable_init_prefix_peak_and_overhead(self):
        p = projected_pod()
        p['containers'] = [container('300m')]
        p['initContainers'] = [container('100m', 'Always'), container('600m'), container('50m', 'Always'), container('200m')]
        p['overhead'] = {'cpu': '10m', 'memory': '1Mi'}
        result = m.resource_envelope(p, 'requests')
        self.assertEqual(result['cpu'], 710)
        self.assertEqual(result['memory'], 4 * 1048576)

    def test_missing_request_is_zero_but_missing_limit_unbounded(self):
        p = projected_pod()
        p['containers'][0]['requests'] = {}
        p['containers'][0]['limits'] = {}
        self.assertEqual(m.resource_envelope(p, 'requests'), dict.fromkeys(m.RESOURCE_KEYS, 0))
        self.assertEqual(m.resource_envelope(p, 'limits'), dict.fromkeys(m.RESOURCE_KEYS, None))

    def test_unknown_semantics_do_not_claim_headroom(self):
        for key in ('podResources', 'resourceClaims', 'resize', 'allocatedResources'):
            p = projected_pod()
            p[key] = [True] if key == 'allocatedResources' else True
            report = m.resources({'items': [p], 'continued': False}, m.node(node(), m.TARGET))
            self.assertEqual(report['request_headroom'], 'UNDETERMINED_UNKNOWN_POD_SEMANTICS')
            self.assertFalse(report['capacity_pass'])
        p = projected_pod()
        p['containers'][0]['requests']['example.invalid/gpu'] = '1'
        with self.assertRaises(m.Failure):
            m.resource_envelope(p, 'requests')

    def test_terminal_excluded_deleting_running_still_counted(self):
        p, q = projected_pod(), projected_pod()
        p['deleting'] = True
        q['phase'] = 'Succeeded'
        value = m.resources({'items': [p, q], 'continued': False}, m.node(node(), m.TARGET))
        self.assertEqual(value['active_pods'], 1)
        self.assertEqual(value['terminal_pods_excluded'], 1)
        self.assertEqual(value['known_requests']['cpu'], 100)

    def test_runtime_projection_does_not_export_sensitive_data(self):
        w = workload()
        pod = w['spec']['template']['spec']
        pod['volumes'] = [{'name': 'private', 'secret': {'secretName': 'DO_NOT_EXPORT'}}]
        c = pod['containers'][0]
        c.update(command=['sh', '-c'], args=['echo DO_NOT_EXPORT'], env=[{'name': 'TOKEN', 'value': 'DO_NOT_EXPORT'}],
                 envFrom=[{'secretRef': {'name': 'DO_NOT_EXPORT'}}], volumeMounts=[{'name': 'private', 'mountPath': '/safe'}],
                 readinessProbe={'httpGet': {'path': '/health?token=DO_NOT_EXPORT', 'httpHeaders': [{'name': 'Authorization', 'value': 'DO_NOT_EXPORT'}]}})
        result = m.workload(w, 'Deployment', 'gateway')
        self.assertNotIn('DO_NOT_EXPORT', json.dumps(result))
        c = result['containers'][0]
        self.assertEqual(c['launcher'], 'CUSTOM_REDACTED')
        self.assertEqual(c['mounts'][0]['reference_type'], 'secret')
        self.assertEqual(c['probes']['readinessProbe']['header_count'], 1)

    def test_owner_uid_and_runtime_digest(self):
        result = m.pg_pods({'items': [pg_pod(), pg_pod('foreign')], 'metadata': {}}, 'postgres-uid')
        self.assertEqual(len(result['owned']), 1)
        self.assertEqual(result['foreign_selector_matches_excluded'], 1)
        self.assertEqual(result['owned'][0]['containers'][0]['image_digest'], 'sha256:' + 'a' * 64)
        self.assertNotIn('DO_NOT_EXPORT', json.dumps(result))
        self.assertEqual(m.image_digest('credentials:DO_NOT_EXPORT'), 'UNRECOGNIZED')

    def test_node_projection_is_narrow(self):
        result = m.node(node(), m.TARGET)
        self.assertTrue(result['historical_identity_matches'])
        self.assertEqual(result['architecture'], 'arm64')
        self.assertNotIn('DO_NOT_EXPORT', json.dumps(result))
        changed = node()
        changed['metadata']['uid'] = 'another-uid'
        self.assertFalse(m.node(changed, m.TARGET)['historical_identity_matches'])

    def test_only_fixed_get_commands_and_projected_resource_list(self):
        fake = FakeRunner()
        k = m.Collector('kubectl', fake)
        for kind, name in (('secrets', 'x'), ('configmaps', 'x'), ('pods/exec', 'x'), ('deployments', 'unrelated'), ('nodes', 'unrelated')):
            with self.assertRaises(m.Failure):
                k.get('bad', kind, name)
        with contextlib.redirect_stderr(io.StringIO()):
            report = m.collect(k)
        self.assertEqual(report['collection'], 'COMPLETE_REVIEW_REQUIRED')
        self.assertTrue(report['identity_and_shape_stable'])
        self.assertFalse(report['ready_for_native_execution'])
        self.assertTrue(all('get' in a and '--cache-dir=/dev/null' in a for a in fake.argv))
        projection = [a for a in fake.argv if '--all-namespaces' in a]
        self.assertEqual(len(projection), 1)
        self.assertIn('--output=go-template=' + m.RESOURCE_TEMPLATE, projection[0])
        self.assertNotIn('env', m.RESOURCE_TEMPLATE)
        self.assertNotIn('command', m.RESOURCE_TEMPLATE)

    def test_failure_preserves_other_steps_and_never_stderr(self):
        with contextlib.redirect_stderr(io.StringIO()):
            report = m.collect(m.Collector('kubectl', FakeRunner(fail=True)))
        self.assertEqual(report['collection'], 'INCOMPLETE_REVIEW_REQUIRED')
        self.assertIsNotNone(report['after']['postgres_pods'])
        self.assertEqual(len(report['errors']), 2)
        self.assertNotIn('DO_NOT_EXPORT', json.dumps(report))

    def test_spec_drift_is_detected(self):
        with contextlib.redirect_stderr(io.StringIO()):
            report = m.collect(m.Collector('kubectl', FakeRunner(drift=True)))
        self.assertFalse(report['identity_and_shape_stable'])
        self.assertNotEqual(report['before']['deployments']['gateway']['spec_sha256'], report['after']['deployments']['gateway']['spec_sha256'])

    def test_subprocess_bounded_drain_and_safe_error(self):
        command = [sys.executable, '-I', '-B', '-c', 'import sys;sys.stderr.write("DO_NOT_EXPORT");sys.exit(7)']
        with self.assertRaises(m.Failure) as caught:
            m.bounded_run(command, timeout=3, limit=2048)
        self.assertEqual(caught.exception.evidence['returncode'], 7)
        self.assertNotIn('DO_NOT_EXPORT', str(caught.exception))
        command[-1] = 'import sys;sys.stdout.write("x"*100000);sys.stderr.write("y"*100000)'
        with self.assertRaises(m.Failure) as caught:
            m.bounded_run(command, timeout=3, limit=1024)
        self.assertEqual(caught.exception.code, 'command_output_limit')

    def test_subprocess_timeout(self):
        with self.assertRaises(m.Failure) as caught:
            m.bounded_run([sys.executable, '-I', '-B', '-c', 'import time;time.sleep(10)'], timeout=0.1, limit=1024)
        self.assertEqual(caught.exception.code, 'command_timeout')

    def test_json_duplicate_and_syntax_37_and_no_persistent_writes(self):
        with self.assertRaises(m.Failure):
            m.bounded_json(b'{"key":1,"key":2}')
        text = (HERE / 'collect_native_preflight.py').read_text(encoding='utf-8')
        tree = ast.parse(text, feature_version=(3, 7))
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]
        forbidden = {'open', 'mkdir', 'mkdtemp', 'write_bytes', 'write_text', 'chmod', 'chown', 'fchown'}
        self.assertFalse(any((getattr(c.func, 'id', None) or getattr(c.func, 'attr', None)) in forbidden for c in calls))
        self.assertIn("os.uname().nodename == 'datax-01'", text)

    def test_stdout_exact_bytes_hash_and_newline(self):
        class Output:
            buffer = io.BytesIO()
        output, diagnostic = Output(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(diagnostic):
            m.emit_report({'collection': 'TEST_ONLY', 'identity_and_shape_stable': False}, 2)
        data = output.buffer.getvalue()
        summary = json.loads(diagnostic.getvalue())
        self.assertTrue(data.endswith(b'\n') and not data.endswith(b'\r\n'))
        self.assertEqual(summary['report_bytes'], len(data))
        self.assertEqual(summary['report_sha256'], m.sha(data))
        self.assertEqual(summary['exitcode'], 2)


class RealKubectlTemplate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.binary = shutil.which('kubectl')
        if not cls.binary:
            raise unittest.SkipTest('No existing kubectl; no install or download permitted')

    def render(self, command, template):
        # The typed ConfigMap generator is verified to work without discovery.
        # Do not use kubectl run: even client dry-run tries discovery in this build.
        self.assertEqual(command[:2], ['create', 'configmap'])
        result = subprocess.run([self.binary] + command + ['--dry-run=client', '--kubeconfig=' + os.devnull,
            '--namespace=fixture', '--output=go-template=' + template], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=10, check=False)
        self.assertEqual(result.returncode, 0, 'offline_kubectl_template_failed_no_raw_stderr')
        return json.loads(result.stdout)

    def test_full_template_compiles_and_renders_empty_list(self):
        self.assertEqual(self.render(['create', 'configmap', 'native-template-fixture', '--from-literal=fixture=ok'], m.RESOURCE_TEMPLATE),
                         {'items': [], 'continued': False})

    def test_same_pod_body_real_render_missing_fields(self):
        result = self.render(['create', 'configmap', 'native-template-fixture', '--from-literal=fixture=ok'],
                             '{{$p := .}}' + m.POD_RESOURCE_TEMPLATE)
        self.assertEqual(result['initContainers'], [])
        self.assertEqual(result['overhead'], {})
        self.assertEqual(result['containers'], [])

    def test_same_resource_map_real_render_quantities(self):
        result = self.render(['create', 'configmap', 'native-template-fixture', '--from-literal=cpu=300m',
                              '--from-literal=memory=1Mi', '--from-literal=ephemeral-storage=64Mi'], m.template_map('.data'))
        self.assertEqual(result, {'cpu': '300m', 'memory': '1Mi', 'ephemeral-storage': '64Mi'})
        p = projected_pod()
        p['containers'][0]['requests'] = result
        p['initContainers'] = [container('100m', 'Always'), container('600m')]
        p['overhead'] = {'cpu': '10m'}
        self.assertEqual(m.resource_envelope(p, 'requests')['cpu'], 710)


if __name__ == '__main__':
    unittest.main(verbosity=2)
