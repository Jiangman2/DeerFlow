"""Offline controller/receipt tests; never contact production or start apps."""
import ast
import copy
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'deploy/enterprise'))
import skillhub_edge_activation_20261106 as edge

PIN = {'bytes': 999, 'sha256': 'a' * 64, 'operation': 'b' * 32}


def receipt():
    return dict(schema='portal-skillhub-registry-auth-activation.v1',
        status='SKILLHUB_REGISTRY_AUTH_RUNTIME_ACCEPTED_EDGE_PENDING', operation=PIN['operation'],
        image_only_patch_verified=True, stable_readback=True, service_endpoints_verified=True,
        unrelated_deployments_preserved=True, temporary_pod_absent=True, patch_request_count=1,
        installed_arm64_jar_check='PASS', native_http_checks=copy.deepcopy(edge.NATIVE_CHECKS),
        after={'uid': edge.SERVER_UID, 'generation': 15, 'spec_sha256': 'd' * 64},
        protected_before={name: dict(edge.ENTRY_BINDING) if name == edge.ENTRY
            else {'uid': 'a' * 36, 'generation': 10, 'spec_sha256': 'c' * 64} for name in edge.PROTECTED},
        runtime={'jar_sha256': edge.SERVER_JAR, 'image': edge.SERVER_IMAGE, 'node': 'datax-01',
                 'pod': 'skillhub-server-abcde-abcde', 'uid': 'a' * 36})


class ReceiptTests(unittest.TestCase):
    def test_unbound_draft_refuses_before_any_io(self):
        with patch.object(edge.Controller, 'call', side_effect=AssertionError('no kubectl')), \
             patch.object(edge, 'private_read', side_effect=AssertionError('no file read')), \
             patch.object(edge.os, 'mkdir', side_effect=AssertionError('no marker')):
            result = edge.Controller().run()
        self.assertEqual(result['error_code'], 'NATIVE_RECEIPT_NOT_BOUND_NO_CHANGE')
        self.assertEqual(result['commands'], 0)
        self.assertFalse(result['once_claimed'])

    def test_pin_format_is_exact(self):
        edge.validate_pin(PIN)
        for value in (None, {}, dict(PIN, extra=True), dict(PIN, bytes=True), dict(PIN, bytes=999999),
                      dict(PIN, sha256='x' * 64), dict(PIN, operation='../bad')):
            with self.subTest(value=value), self.assertRaises(edge.Refused):
                edge.validate_pin(value)

    def test_complete_native_receipt_accepted(self):
        self.assertEqual(edge.validate_receipt(receipt(), PIN), receipt())

    def test_rejects_each_incomplete_acceptance(self):
        for key in ('image_only_patch_verified', 'stable_readback', 'service_endpoints_verified',
                    'unrelated_deployments_preserved', 'temporary_pod_absent'):
            row = receipt()
            row[key] = False
            with self.subTest(key=key), self.assertRaises(edge.Refused):
                edge.validate_receipt(row, PIN)
        for key, value in (('patch_request_count', 2), ('installed_arm64_jar_check', 'SKIPPED'),
                           ('operation', 'c' * 32), ('status', 'INCOMPLETE')):
            row = receipt()
            row[key] = value
            with self.subTest(key=key), self.assertRaises(edge.Refused):
                edge.validate_receipt(row, PIN)

    def test_native_http_missing_success_or_fake_bearer_rejected(self):
        for mutation in ('missing', 'public', 'bearer'):
            row = receipt()
            if mutation == 'missing':
                row['native_http_checks'].pop()
            elif mutation == 'public':
                row['native_http_checks'][1]['status'] = 200
            else:
                row['native_http_checks'][4]['fabricated_bearer'] = False
            with self.subTest(mutation=mutation), self.assertRaises(edge.Refused):
                edge.validate_receipt(row, PIN)

    def test_wrong_image_jar_node_or_entry_binding_rejected(self):
        for key, value in (('image', 'unreviewed:tag'), ('jar_sha256', '0' * 64), ('node', 'datax-02')):
            row = receipt()
            row['runtime'][key] = value
            with self.subTest(key=key), self.assertRaises(edge.Refused):
                edge.validate_receipt(row, PIN)
        row = receipt()
        row['protected_before'][edge.ENTRY]['generation'] += 1
        with self.assertRaises(edge.Refused):
            edge.validate_receipt(row, PIN)

    def test_fixed_receipt_read_hash_checked(self):
        raw = edge.canonical(receipt())
        pin = dict(PIN, bytes=len(raw), sha256=edge.sha(raw))
        with patch.object(edge, 'NATIVE_RECEIPT_BINDING', pin), patch.object(edge, 'private_read', return_value=raw):
            self.assertEqual(edge.read_native_receipt(), receipt())
        with patch.object(edge, 'NATIVE_RECEIPT_BINDING', pin), patch.object(edge, 'private_read', return_value=raw+b' '):
            with self.assertRaisesRegex(edge.Refused, 'HASH_CHANGED'):
                edge.read_native_receipt()


class PolicyBoundaryTests(unittest.TestCase):
    def test_stale_mount_never_used_as_template(self):
        import base64
        raw = b'LIVE\n' + base64.b64encode(b'live') + b'\nMOUNTED_SHA\n' + edge.policy.STALE_MOUNT_SHA.encode() + b'\n'
        self.assertEqual(edge.split_live(raw), b'live')
        with self.assertRaises(edge.Refused):
            edge.split_live(raw.replace(edge.policy.STALE_MOUNT_SHA.encode(), b'0' * 64))

    def test_process_identity_requires_one_master(self):
        self.assertEqual(edge.process_identity(b'MASTER 1:10\nWORKER 2:20\n'), {'master': '1:10', 'workers': ['2:20']})
        for raw in (b'MASTER 1:10\n', b'WORKER 2:20\n', b'MASTER 1:10\nMASTER 2:20\nWORKER 3:30\n', b'MASTER x:1\nWORKER 2:20\n'):
            with self.subTest(raw=raw), self.assertRaises(edge.Refused):
                edge.process_identity(raw)

    def test_endpoint_set_rejects_unready_terminating_duplicate(self):
        row = {'metadata': {'labels': {'kubernetes.io/service-name': 'nginx'}},
               'endpoints': [{'conditions': {'ready': True}, 'targetRef': {'kind': 'Pod', 'name': edge.POD, 'uid': edge.POD_UID, 'namespace': edge.NS}}]}
        self.assertEqual(edge.endpoint_refs({'items': [row]}, 'nginx'), [(edge.POD, edge.POD_UID)])
        for kind in ('unready', 'terminating', 'duplicate', 'namespace'):
            value = copy.deepcopy(row)
            if kind == 'unready': value['endpoints'][0]['conditions']['ready'] = False
            if kind == 'terminating': value['endpoints'][0]['conditions']['terminating'] = True
            if kind == 'duplicate': value['endpoints'] *= 2
            if kind == 'namespace': value['endpoints'][0]['targetRef']['namespace'] = 'other'
            with self.subTest(kind=kind), self.assertRaises(edge.Refused):
                edge.endpoint_refs({'items': [value]}, 'nginx')

    def test_fixed_script_and_path_guard(self):
        controller = edge.Controller()
        with patch.object(controller, 'call', side_effect=AssertionError('must refuse')):
            for script, args in (('rm -rf /tmp', ()), (edge.STAGE, ('/tmp/other', 'a'*64, 'b'*64)),
                                 (edge.PPT_PROBE, ('arbitrary',))):
                with self.subTest(script=script), self.assertRaises(edge.Refused):
                    controller.execute(script, 'test', args)

    def test_patch_has_cas_and_only_one_replace(self):
        controller = edge.Controller()
        cm = {'metadata': {'name': edge.CM, 'uid': edge.CM_UID, 'resourceVersion': '4'}, 'data': {'nginx.conf': 'before', 'keep': 'same'}}
        with patch.object(controller, 'call', return_value=b'{}') as call:
            controller.patch(cm, b'after')
            operations = json.loads(call.call_args.args[2])
            self.assertEqual([x['op'] for x in operations], ['test', 'test', 'test', 'replace'])
            self.assertEqual(operations[2]['value'], cm['data'])
            self.assertEqual(operations[3], {'op': 'replace', 'path': '/data/nginx.conf', 'value': 'after'})
            with self.assertRaisesRegex(edge.Refused, 'SINGLE_PATCH'):
                controller.patch(cm, b'after')
            self.assertEqual(call.call_count, 1)

    def test_patch_failure_keeps_uncertainty_flag(self):
        controller = edge.Controller()
        with patch.object(controller, 'call', side_effect=edge.Refused('timeout')):
            with self.assertRaises(edge.Refused):
                controller.patch({'metadata': {'resourceVersion': '1'}, 'data': {}}, b'candidate')
        self.assertEqual(controller.report['configmap_patch_requests'], 1)
        self.assertTrue(controller.report['configmap_patch_may_have_applied'])

    def test_no_redirect_follow_or_real_credentials_in_http(self):
        controller = edge.Controller()
        with patch.object(controller, 'call', return_value=b'401\n') as call:
            self.assertEqual(controller.http(edge.POD, '/platform/skillhub/api/web/labels', bearer=True), (401, ''))
            args = call.call_args.args[0]
            self.assertNotIn('-L', args)
            self.assertNotIn('--location', args)
            self.assertIn(edge.INVALID_BEARER, args)
            self.assertFalse(any(x.startswith('Cookie:') for x in args))
        with self.assertRaises(edge.Refused):
            controller.http(edge.POD, 'https://outside.example')

    def test_private_writes_use_exclusive_fsync_and_dirfd(self):
        source = (ROOT / 'deploy/enterprise/skillhub_edge_activation_20261106.py').read_text(encoding='utf-8')
        self.assertIn('os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW', source)
        self.assertIn('os.fsync(self.fd)', source)
        self.assertIn('src_dir_fd=self.fd, dst_dir_fd=self.fd', source)
        self.assertIn('mv -T "$1/candidate.conf" /tmp/nginx.conf', edge.INSTALL)
        self.assertIn('sync -f', edge.INSTALL)
        self.assertLess(edge.INSTALL.index('nginx -t'), edge.INSTALL.index('mv -T'))
        self.assertLess(edge.INSTALL.index('LIVE_HASH'), edge.INSTALL.index('mv -T'))
        self.assertNotIn('restart', edge.INSTALL)

    def test_only_required_dependencies_need_convergence(self):
        source = (ROOT / 'deploy/enterprise/skillhub_edge_activation_20261106.py').read_text(encoding='utf-8')
        self.assertIn('if name in (SERVER, ENTRY):', source)
        self.assertIn("binding(deployments[name]) == expected[name]", source)

    def test_python37_and_embedded_draft(self):
        spec = importlib.util.spec_from_file_location('edge_package_test', ROOT / 'deploy/enterprise/package-skillhub-edge-activation-20261106.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        raw = module.generate()
        ast.parse(raw, feature_version=(3, 7))
        self.assertNotIn(b'import skillhub_nginx_auth_transform_20261106', raw)
        scope = {'__name__': 'synthetic_import'}
        exec(compile(raw, '<embedded>', 'exec'), scope)
        self.assertEqual(scope['Controller']().run()['error_code'], 'NATIVE_RECEIPT_NOT_BOUND_NO_CHANGE')


class FakeController(edge.Controller):
    """Control-flow mock only; pure transform and real Nginx checked separately."""
    def __init__(self, fail=None):
        super().__init__()
        self.events, self.fail, self.changed = [], fail, False
        self.initial = {'pod': {'metadata': {'name': edge.POD}}, 'serverpod': {'metadata': {'name': 'server'}},
            'container': {}, 'cm': {'metadata': {'resourceVersion': '1', 'uid': edge.CM_UID}, 'data': {'nginx.conf': 'template'}}}

    def snapshot(self, native, expected_cm=None):
        self.events.append('snapshot')
        return self.initial

    def execute(self, script, label, params=(), data=None):
        self.events.append(label)
        if label == self.fail:
            raise edge.Refused('injected_' + label)
        if script == edge.READ_LIVE:
            return b'candidate' if self.changed else b'live'
        if script == edge.READ_PROCESSES:
            return b'MASTER 1:1\nWORKER 3:3\n' if self.changed else b'MASTER 1:1\nWORKER 2:2\n'
        if script == edge.STAGE: return b'CANDIDATE_STAGED_AND_NGINX_VALIDATED'
        if script == edge.INSTALL:
            self.changed = True
            return b'LIVE_INSTALLED_RELOAD_SIGNAL_SENT'
        if script == edge.PPT_PROBE: return b'PPT_ADMISSION_BLOCKED_409'
        raise AssertionError(script)

    def replay(self, c, raw, label): return b'live' if label == 'replay_current' else b'candidate'
    def check_same_pods(self, initial): self.events.append('same_pods')
    def native_http(self, native, label): self.events.append(label)
    def claim(self, initial, plan): self.report['once_claimed'] = True; self.events.append('claim')
    def save(self): pass
    def patch(self, cm, raw):
        self.events.append('patch')
        self.report.update(configmap_patch_requests=1, configmap_patch_may_have_applied=True)
        return {'metadata': {'uid': edge.CM_UID}, 'data': {'nginx.conf': 'newtemplate'}}
    def http(self, pod, path, native=False, bearer=False):
        for row in edge.EDGE_CHECKS:
            if row['path'] == path and bool(row.get('fabricated_bearer')) == bearer:
                return row['status'], 'http://cnoocclaw.cnooc.com.cn/login' if row.get('redirect') else ''
        raise AssertionError(path)


class ControllerFlowTests(unittest.TestCase):
    def setUp(self):
        plan = {'current_live': b'live', 'current_template': b'template', 'candidate_live': b'candidate',
                'candidate_template': b'newtemplate', 'summary': {}}
        replacements = (
            (edge, 'NATIVE_RECEIPT_BINDING', PIN), (edge, 'read_native_receipt', lambda: receipt()),
            (edge.sys, 'platform', 'linux'), (edge.sys, 'flags', SimpleNamespace(isolated=1, dont_write_bytecode=1)),
            (edge.os, 'geteuid', lambda: 0), (edge.os, 'uname', lambda: SimpleNamespace(machine='aarch64')),
            (edge.socket, 'gethostname', lambda: 'datax-01'), (edge.os.path, 'lexists', lambda p: False),
            (edge.shutil, 'which', lambda *a, **k: '/usr/bin/kubectl'), (edge, 'split_live', lambda b: b),
            (edge, 'pod_identity', lambda p: p['metadata']),
            (edge.policy, 'plan_production_pair', lambda *a: plan),
            (edge.policy, 'verify_startup_replay', lambda *a: None),
        )
        for obj, name, value in replacements:
            p = patch.object(obj, name, value, create=True)
            p.start()
            self.addCleanup(p.stop)

    def test_sequence_requires_native_then_stage_then_single_cas(self):
        c = FakeController()
        result = c.run()
        self.assertEqual(result['status'], edge.SUCCESS, result)
        self.assertLess(c.events.index('native_http_before'), c.events.index('claim'))
        self.assertLess(c.events.index('stage_and_nginx_test'), c.events.index('patch'))
        self.assertLess(c.events.index('patch'), c.events.index('atomic_install_and_reload'))
        self.assertEqual(c.events.count('patch'), 1)
        self.assertEqual(c.events.count('atomic_install_and_reload'), 1)
        self.assertEqual(result['ppt_new_run_admission'], 'BLOCKED_409')

    def test_nginx_test_failure_does_not_patch_or_reload(self):
        c = FakeController('stage_and_nginx_test')
        result = c.run()
        self.assertEqual(result['status'], 'INCOMPLETE')
        self.assertNotIn('patch', c.events)
        self.assertFalse(result['runtime_install_may_have_applied'])

    def test_install_uncertainty_never_retried_or_rolled_back(self):
        c = FakeController('atomic_install_and_reload')
        result = c.run()
        self.assertEqual(result['status'], 'INCOMPLETE')
        self.assertTrue(result['configmap_patch_may_have_applied'])
        self.assertTrue(result['runtime_install_may_have_applied'])
        self.assertTrue(result['reload_may_have_been_requested'])
        self.assertEqual(c.events.count('patch'), 1)
        self.assertEqual(c.events.count('atomic_install_and_reload'), 1)
        self.assertFalse(result['automatic_rollback'])

    def test_wrong_host_or_once_directory_prevents_cluster_reads(self):
        for host, existing, code in (('wrong-host', False, 'ROOT_ISOLATED_DATAX01_REQUIRED'),
                                     ('datax-01', True, 'ONCE_EXISTS_REVIEW_REQUIRED')):
            with self.subTest(host=host), patch.object(edge.socket, 'gethostname', return_value=host), \
                 patch.object(edge.os.path, 'lexists', return_value=existing):
                c = FakeController()
                self.assertEqual(c.run()['error_code'], code)
                self.assertEqual(c.events, [])


if __name__ == '__main__':
    unittest.main()
