import ast
import base64
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import subprocess
import selectors
import signal
import time
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

import native_glm_acceptance as a
import qualify_runtime_candidate_fix1 as q


def fixture():
    return a.manifests('weknora-glm-check-'+'a'*16, 'candidate:locked', b'\x00\xffCA', {},
                       ['10.10.1.2'], [])


def result():
    return dict(native_glm_probe='PASS', native_exit_code=0,
        scope='NATIVE_GO_VLM_CLIENT_SYNTHETIC_IMAGE', maximum_submissions=1,
        synthetic_red_check=True, credentials_saved=False, raw_response_saved=False,
        production_model_changed=False, ready_for_rollout=False,
        native_enrichment_acceptance='NOT_TESTED')


class ManifestTests(unittest.TestCase):
    def test_binary_ca_transport(self):
        policy, cm, pod = fixture()
        self.assertEqual(base64.b64decode(cm['binaryData']['ca.bin']), b'\x00\xffCA')
        self.assertTrue(cm['immutable'])
        self.assertNotIn('ca.bin', cm['data'])

    def test_no_production_configuration_credentials_or_service(self):
        objs = fixture()
        self.assertEqual([o['kind'] for o in objs], ['NetworkPolicy', 'ConfigMap', 'Pod'])
        s = objs[2]['spec']; c = s['containers'][0]
        self.assertFalse(s['automountServiceAccountToken'])
        self.assertFalse(s['enableServiceLinks'])
        self.assertEqual(c['command'], ['python3', '-I', '-B', '/probe/runner.py', 'idle'])
        self.assertNotIn('env', c); self.assertNotIn('envFrom', c)
        self.assertNotIn('secret', json.dumps(s).lower())
        self.assertNotIn('hostPath', json.dumps(s))
        self.assertNotIn('persistentVolumeClaim', json.dumps(s))
        self.assertEqual(s['restartPolicy'], 'Never')
        self.assertEqual(c['imagePullPolicy'], 'Never')
        self.assertEqual(s['activeDeadlineSeconds'], 900)

    def test_exact_egress_no_general_dns(self):
        policy, _, pod = fixture()
        self.assertEqual(policy['spec']['ingress'], [])
        self.assertEqual(policy['spec']['egress'], [{'to': [{'ipBlock': {'cidr': '10.10.1.2/32'}}],
            'ports': [{'protocol': 'TCP', 'port': 443}]}])
        self.assertEqual(pod['spec']['dnsPolicy'], 'None')
        self.assertEqual(pod['spec']['hostAliases'], [{'ip': '10.10.1.2', 'hostnames': [a.HOST]}])

    def test_control_plane_toleration_only(self):
        taint = {'key': 'node-role.kubernetes.io/control-plane', 'effect': 'NoSchedule'}
        pod = a.manifests('n', 'i', b'c', {}, ['10.0.0.2'], [taint])[2]
        self.assertEqual(pod['spec']['tolerations'][0]['operator'], 'Equal')
        for t in ({'key': 'node.kubernetes.io/disk-pressure', 'effect': 'NoSchedule'},
                  {'key': taint['key'], 'effect': 'NoExecute'}):
            with self.assertRaises(a.AcceptanceFailure): a.manifests('n', 'i', b'c', {}, ['10.0.0.2'], [t])

    def test_server_defaulting_and_false_omission(self):
        expected = fixture()[2]; actual = copy.deepcopy(expected)
        actual['metadata']['uid'] = 'ours'
        actual['spec'].update(hostNetwork=False, nodeName='datax-01', serviceAccountName='default')
        actual['spec']['containers'][0]['terminationMessagePolicy'] = 'File'
        a.validate_pod(actual, expected, 'ours')

    def test_injection_and_identity_fail_closed(self):
        expected = fixture()[2]
        for change in ('hostNetwork', 'initContainers', 'extraVolume', 'env', 'image', 'uid', 'node', 'sidecar'):
            actual = copy.deepcopy(expected); actual['metadata']['uid'] = 'ours'
            s = actual['spec']; c = s['containers'][0]
            if change == 'hostNetwork': s[change] = True
            if change == 'initContainers': s[change] = [{'name': 'injected'}]
            if change == 'extraVolume': s['volumes'].append({'name': 'bad', 'hostPath': {'path': '/'}})
            if change == 'env': c['env'] = [{'name': 'DB_PASSWORD', 'value': 'not-real'}]
            if change == 'image': c['image'] = 'foreign:tag'
            if change == 'uid': actual['metadata']['uid'] = 'foreign'
            if change == 'node': s['nodeName'] = 'datax-03'
            if change == 'sidecar': s['containers'].append(copy.deepcopy(c))
            with self.subTest(change=change), self.assertRaises(a.AcceptanceFailure):
                a.validate_pod(actual, expected, 'ours')

    def test_native_pass_not_business_pass(self):
        self.assertTrue(a.accepted(result()))
        for k, v in [('native_exit_code', 1), ('synthetic_red_check', False), ('maximum_submissions', 2),
                     ('credentials_saved', True), ('scope', 'PYTHON_EMULATION'), ('ready_for_rollout', True)]:
            with self.subTest(key=k): self.assertFalse(a.accepted(dict(result(), **{k: v})))

    def test_dns_bound_and_no_unsafe_answers(self):
        for ips in (['127.0.0.1'], ['169.254.1.2'], ['224.0.0.1'], ['0.0.0.0'], [], ['::1']):
            r = types.SimpleNamespace(returncode=0, stdout=json.dumps(ips).encode())
            with patch.object(a.subprocess, 'run', return_value=r), self.assertRaises(a.AcceptanceFailure): a.resolve_ips()
        with patch.object(a.subprocess, 'run', return_value=types.SimpleNamespace(returncode=0, stdout=b'["10.0.0.2"]')) as call:
            self.assertEqual(a.resolve_ips(), ['10.0.0.2'])
            self.assertEqual(call.call_args.kwargs['timeout'], 20)

    def test_remote_code_compiles_and_clean_environment(self):
        ast.parse(a.RUNNER, feature_version=(3, 7)); ast.parse(a.CA_READ, feature_version=(3, 7))
        tree = ast.parse(a.RUNNER)
        fn = next(x for x in tree.body if isinstance(x, ast.FunctionDef) and x.name == 'env')
        namespace = {}; exec(compile(ast.Module(body=[fn], type_ignores=[]), '<env>', 'exec'), namespace)
        env = namespace['env']()
        self.assertEqual(env['SSRF_WHITELIST_EXTRA'], a.HOST)
        self.assertEqual(env['LLM_DEBUG_LOG'], 'false'); self.assertEqual(env['LOG_PATH'], '')
        self.assertFalse(any(k.lower().endswith('proxy') and k.lower() != 'no_proxy' for k in env))
        self.assertNotIn('DATABASE_URL', env); self.assertNotIn('REDIS_URL', env)
        self.assertLess(a.RUNNER.index("save('ATTEMPTED.json'"), a.RUNNER.index('try: result=native(raw)'))

    def test_uses_live_app_dns_without_model_request(self):
        with patch.object(a, 'call', return_value=(0, b'["10.10.1.2"]')) as call:
            self.assertEqual(a.resolve_ips('reviewed-app'), ['10.10.1.2'])
            self.assertEqual(call.call_args[0][0][:8], ['exec', '-n', 'deer-flow', 'reviewed-app', '-c', 'app', '--', 'python3'])
            self.assertNotIn('api_key', call.call_args[0][0][-1])


class LocalCommandTests(unittest.TestCase):
    def test_stdin_not_argv_or_log(self):
        with patch.object(a, 'KUBE', [sys.executable, '-c']), patch.object(a, 'emit') as emit:
            code, raw = a.call(['import sys; print(len(sys.stdin.buffer.read()))'], b'private-token', limit=100)
        self.assertEqual(code, 0); self.assertEqual(raw.strip(), b'13')
        self.assertNotIn('private', json.dumps(a.LAST)); emit.assert_not_called()

    def test_no_raw_error_on_nonzero(self):
        with patch.object(a, 'KUBE', [sys.executable, '-c']):
            with self.assertRaisesRegex(a.AcceptanceFailure, 'kubernetes_command_failed') as raised:
                a.call(['import sys;sys.stderr.write("fake-credential");sys.exit(2)'])
        self.assertNotIn('credential', str(raised.exception)); self.assertEqual(a.LAST['stderr_bytes'], 15)

    def test_output_bound(self):
        with patch.object(a, 'KUBE', [sys.executable, '-c']), self.assertRaisesRegex(a.AcceptanceFailure, 'output_bound'):
            a.call(['print("x"*10000)'], limit=50)

    def test_timeout_no_retry(self):
        with patch.object(a, 'KUBE', [sys.executable, '-c']), self.assertRaisesRegex(a.AcceptanceFailure, 'deadline'):
            a.call(['import time;time.sleep(10)'], timeout=.1)


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.namespace = dict(os=os, json=json, Path=Path, selectors=selectors,
                              signal=signal, time=time, subprocess=subprocess, ROOT=Path(self.tmp.name))
        tree = ast.parse(a.RUNNER)
        fns = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in ('save', 'native', 'env')]
        exec(compile(ast.Module(body=fns, type_ignores=[]), '<runner>', 'exec'), self.namespace)

    def tearDown(self): self.tmp.cleanup()

    def test_remote_attempt_is_exclusive(self):
        self.namespace['save']('ATTEMPTED.json', {'maximum_submissions': 1})
        with self.assertRaises(FileExistsError): self.namespace['save']('ATTEMPTED.json', {'maximum_submissions': 1})

    def test_native_result_saved_without_logs(self):
        original = subprocess.Popen
        script = 'import sys,json;sys.stdin.buffer.read();print("not-a-receipt-fake-secret");print(json.dumps(%r))' % result()
        def launch(argv, **kwargs):
            self.assertEqual(argv, ['/usr/local/bin/weknora-glm-probe', '-probe-once'])
            self.assertEqual(kwargs['env']['LLM_DEBUG_LOG'], 'false')
            return original([sys.executable, '-c', script], **kwargs)
        with patch.object(subprocess, 'Popen', side_effect=launch): row = self.namespace['native'](b'{"api_key":"fake-key"}')
        self.assertTrue(a.accepted(row))
        self.assertNotIn('fake-secret', json.dumps(row))
        self.assertFalse(row['raw_logs_saved'])

    def test_unknown_failure_detail_not_exported(self):
        original = subprocess.Popen
        script = 'import sys,json;sys.stdin.buffer.read();print(json.dumps({"native_glm_probe":"UNCONFIRMED","failed_check":"fake-private-token"}));sys.exit(1)'
        with patch.object(subprocess, 'Popen', side_effect=lambda argv, **kwargs: original([sys.executable, '-c', script], **kwargs)):
            row = self.namespace['native'](b'{}')
        self.assertEqual(row['native_exit_code'], 1)
        self.assertNotIn('failed_check', row)

    def test_duplicate_receipts_rejected(self):
        original = subprocess.Popen
        script = 'import sys;sys.stdin.buffer.read();print(\'{"native_glm_probe":"PASS"}\\n{"native_glm_probe":"PASS"}\')'
        with patch.object(subprocess, 'Popen', side_effect=lambda argv, **kwargs: original([sys.executable, '-c', script], **kwargs)), \
             self.assertRaises(AssertionError): self.namespace['native'](b'{}')


@unittest.skipUnless(os.name == 'posix' and hasattr(os, 'geteuid') and os.geteuid() == 0, 'Linux root private-file tests')
class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir='/root')
        self.root = Path(self.temp.name)/'operation'
        self.state = patch.object(a, 'STATE', self.root); self.state.start()
        self.output = patch.object(a, 'emit'); self.output.start()
        self.p = types.SimpleNamespace(IMAGE='candidate:locked', MANIFEST='sha256:manifest', CONFIG='sha256:config')
        self.guard = Mock()
        self.phase = patch.object(a.time, 'sleep'); self.phase.start()
        self.pre = patch.object(a, 'preflight', return_value=(self.guard, {}, b'ca', {}, ['10.10.1.2'], [])); self.pre.start()
        self.key = patch.object(a, 'hidden_key', return_value='not-real-test-token'); self.key.start()
        self.objects = {}; self.calls = []; self.send_error = False
        self.remote = {'READY.json': {'runtime_files': 'PASS'}}

    def tearDown(self):
        self.key.stop(); self.pre.stop(); self.phase.stop(); self.output.stop(); self.state.stop(); self.temp.cleanup()

    def call(self, args, data=None, **kwargs):
        self.calls.append((args, data))
        if args[0] == 'create':
            obj = json.loads(data); obj['metadata']['uid'] = 'uid-'+obj['kind']
            if '--dry-run=server' not in args: self.objects[obj['kind']] = obj
            return 0, json.dumps(obj).encode()
        if args[0] == 'exec':
            self.assertTrue((self.root/'PROVIDER-REQUESTED.json').exists())
            if self.send_error: raise a.AcceptanceFailure('command_deadline_USE_STATUS')
            self.remote['ATTEMPTED.json'] = {'maximum_submissions': 1}
            self.remote['RESULT.json'] = result()
        return 0, b'{}'

    def get(self, kind, name, namespace=None, **kwargs):
        if kind == 'pod':
            obj = copy.deepcopy(self.objects['Pod'])
            obj['spec']['nodeName'] = 'datax-01'
            obj['status'] = {'phase': 'Running', 'containerStatuses': [{'restartCount': 0,
                'imageID': self.p.CONFIG, 'state': {'running': {}}}]}
            return obj
        if kind == 'networkpolicy': return self.objects['NetworkPolicy']
        if kind == 'configmap': return self.objects['ConfigMap']
        raise AssertionError(kind)

    def run_mocked(self):
        with patch.object(a, 'call', side_effect=self.call), patch.object(a, 'get', side_effect=self.get), \
             patch.object(a, 'remote_status', side_effect=lambda ns: copy.deepcopy(self.remote)):
            return a.run(self.p, q)

    def test_success_one_send_and_uid_cleanup(self):
        self.assertEqual(self.run_mocked(), 0)
        sends = [(args, data) for args, data in self.calls if args[0] == 'exec']
        self.assertEqual(len(sends), 1)
        self.assertNotIn('not-real-test-token', json.dumps(sends[0][0]))
        deletion = next(data for args, data in self.calls if args[0] == 'delete')
        self.assertEqual(json.loads(deletion)['preconditions'], {'uid': 'uid-Namespace'})
        report = q.document(self.root/'RESULT.json')
        self.assertEqual(report['native_glm_acceptance'], 'PASS')
        self.assertFalse(report['ready_for_rollout'])
        self.assertEqual(report['native_enrichment_acceptance'], 'NOT_TESTED')
        for f in self.root.iterdir(): self.assertNotIn(b'not-real-test-token', f.read_bytes())

    def test_uncertain_send_retained_no_retry_no_cleanup(self):
        self.send_error = True
        self.assertEqual(self.run_mocked(), 1)
        report = q.document(self.root/'RESULT.json')
        self.assertTrue(report['provider_submission_attempted'])
        self.assertEqual(len([c for c in self.calls if c[0][0] == 'exec']), 1)
        self.assertFalse(any(c[0][0] == 'delete' for c in self.calls))
        with self.assertRaisesRegex(a.AcceptanceFailure, 'previous_operation_exists'): self.run_mocked()

    def test_failed_preflight_has_no_state_or_cluster_write(self):
        with patch.object(a, 'preflight', side_effect=a.AcceptanceFailure('baseline_changed')), \
             patch.object(a, 'call') as call:
            with self.assertRaises(a.AcceptanceFailure): a.run(self.p, q)
            self.assertFalse(self.root.exists()); call.assert_not_called()

    def test_changed_image_stops_before_key_or_send(self):
        self.p.CONFIG = 'sha256:expected-other'
        original = self.get
        def bad_get(*args, **kwargs):
            obj = original(*args, **kwargs)
            if args[0] == 'pod': obj['status']['containerStatuses'][0]['imageID'] = 'sha256:foreign'
            return obj
        with patch.object(a, 'call', side_effect=self.call), patch.object(a, 'get', side_effect=bad_get), \
             patch.object(a, 'hidden_key') as key:
            self.assertEqual(a.run(self.p, q), 1); key.assert_not_called()
        self.assertFalse((self.root/'PROVIDER-REQUESTED.json').exists())

    def test_status_does_not_submit_or_write(self):
        self.assertEqual(self.run_mocked(), 0)
        before = {f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in self.root.iterdir()}
        with patch.object(a, 'get', return_value=None), patch.object(a, 'call') as call:
            a.status(q); call.assert_not_called()
        self.assertEqual(before, {f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in self.root.iterdir()})


if __name__ == '__main__': unittest.main()
