"""Local API boundaries for the unsealed Skill-only installer skeleton."""

import ast
import copy
import importlib.util
import json
import io
import contextlib
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent


def load(name):
    spec = importlib.util.spec_from_file_location('delivery_install_' + name, ROOT / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


host, publisher, verifier = (load(name) for name in ('operator', 'publisher', 'verify_child'))
ASSETS = {'publisher': b'print("publisher")', 'verifier': b'print("verifier")', 'package': b'zip', 'test_files': b'{}'}


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.operation = host.Operation(ASSETS)

    def test_operator_is_python37_compatible(self):
        ast.parse((ROOT / 'operator.py').read_text(encoding='utf-8'), feature_version=(3, 7))

    def test_unsealed_assets_stop_before_any_external_command(self):
        with patch.object(host.subprocess, 'Popen', side_effect=AssertionError('must not run')):
            with self.assertRaisesRegex(host.Refused, 'CANDIDATE_NOT_SEALED'):
                host.load_assets()

    def test_exact_operator_receipt_shape_keeps_runtime_path_under_publication(self):
        runtime = {'manifest_sha256': host.RUNTIME_SHA, 'source_image_id': host.IMAGE_ID}
        report = {'status': 'ENERGY_PUBLIC_RUNTIME_REPAIR_INSTALLED_VISIBILITY_PENDING', 'operation': '2677be7ae78e4339b7df3628e0ffa1c9',
                  'skill_updated': True, 'maintenance_pod_absent': True, 'maintenance_configmap_absent': True,
                  'runtime': dict(runtime),
                  'publication': {'after_tree_sha256': host.OLD_TREE, 'after_files': 107,
                                  'runtime': dict(runtime, path='/mnt/skills/public/.energy-browser-runtime-20261112-r1/linux-arm64')},
                  'shared_runtime_qualification': {'uid': 1000, 'gid': 1000, 'png_sha256': host.EXPECTED_FIXED_PNG_SHA}}
        raw = host.canonical(report)
        self.assertNotIn('path', report['runtime'])
        with patch.object(host, 'read', return_value=raw), patch.object(host, 'OLD_INSTALL_BYTES', len(raw)), patch.object(host, 'OLD_INSTALL_SHA', host.sha(raw)):
            actual = host.qualification_binding()
        self.assertEqual(actual[2], host.RUNTIME_SHA)
        self.assertEqual(actual[3]['operation'], report['operation'])
        report['publication']['runtime']['path'] = '/mnt/skills/.wrong-runtime'
        raw = host.canonical(report)
        with patch.object(host, 'read', return_value=raw), patch.object(host, 'OLD_INSTALL_BYTES', len(raw)), patch.object(host, 'OLD_INSTALL_SHA', host.sha(raw)):
            with self.assertRaisesRegex(host.Refused, 'ACCEPTED_PUBLIC_RUNTIME_CHANGED'):
                host.qualification_binding()

    def test_report_write_io_failure_still_emits_final_json(self):
        operation = self.operation
        operation.claimed = True
        operation.out['status'] = 'ENERGY_DELIVERY_SKILL_INSTALLED'
        output = io.StringIO()
        with patch.object(host.sys, 'argv', ['operator.py', '--install-delivery-skill-once']), patch.object(host.os, 'name', 'posix'), patch.object(host.os, 'uname', return_value=SimpleNamespace(nodename='datax-01'), create=True), patch.object(host.os, 'geteuid', return_value=0, create=True), patch.object(host, 'load_assets', return_value=ASSETS), patch.object(host, 'Operation', return_value=operation), patch.object(operation, 'run'), patch.object(operation, 'cleanup'), patch.object(host, 'private_write', side_effect=OSError('synthetic private path unavailable')), contextlib.redirect_stdout(output):
            code = host.main()
        row = json.loads(output.getvalue())
        self.assertEqual(code, 1)
        self.assertEqual(row['status'], 'INCOMPLETE')
        self.assertEqual(row['report_write_error_class'], 'OSError')
        self.assertTrue(row['report_write_incomplete'])
        self.assertNotIn('synthetic private path', output.getvalue())

    def test_mutation_scope_excludes_platform_and_business_resources(self):
        for command in (
            ['patch', 'deployment', 'gateway'], ['patch', 'deployment', 'provisioner'],
            ['create', 'secret', 'credential'], ['get', 'secret', 'deer-flow-env'],
            ['delete', 'pod', 'sandbox-business'], ['rollout', 'restart', 'deployment/gateway'],
        ):
            with self.assertRaisesRegex(host.Refused, 'FIXED_OPERATION_REQUIRED'):
                self.operation.command(command)

    def test_qualification_matches_real_public_only_mount_shape(self):
        spec = self.operation.requested['spec']
        qualification = spec['containers'][1]
        mounts = [row for row in qualification['volumeMounts'] if row['name'] == 'skills']
        self.assertEqual(mounts[0], {'name': 'skills', 'mountPath': '/mnt/skills/public', 'subPath': 'public', 'readOnly': True})
        self.assertEqual(mounts[1]['mountPath'], '/mnt/skills/public/energy-market-daily')
        self.assertEqual(mounts[1]['subPath'], '.energy-delivery-skill-install-20261112-r1-' + self.operation.operation + '/candidate')
        self.assertTrue(mounts[1]['readOnly'])
        self.assertFalse(any('.energy-browser-runtime' in row['mountPath'] or row['mountPath'] == '/mnt/skills' for row in mounts))
        self.assertEqual(qualification['securityContext']['runAsUser'], 1000)
        self.assertEqual(qualification['securityContext']['runAsGroup'], 1000)

    def test_no_credentials_or_service_account_and_no_image_pull(self):
        spec = self.operation.requested['spec']
        self.assertFalse(spec['automountServiceAccountToken'])
        self.assertFalse(any('secret' in volume for volume in spec['volumes']))
        for container in spec['containers'] + spec['initContainers']:
            self.assertEqual(container['imagePullPolicy'], 'Never')
            self.assertFalse(container['securityContext']['allowPrivilegeEscalation'])
            self.assertEqual(container['securityContext']['capabilities'], {'drop': ['ALL']})
            self.assertNotIn('envFrom', container)

    def test_owned_pod_uid_change_is_not_cleaned(self):
        self.operation.create_pod_requested = True
        self.operation.pod_uid = 'original-uid'
        pod = copy.deepcopy(self.operation.requested)
        pod['metadata']['uid'] = 'replacement-uid'
        calls = []
        def command(args, *unused, **kwargs):
            calls.append(args)
            return pod
        with patch.object(self.operation, 'command', side_effect=command):
            with self.assertRaisesRegex(host.Refused, 'OWN_POD_IDENTITY'):
                self.operation.cleanup()
        self.assertFalse(any(args[0] == 'delete' for args in calls))

    def test_owned_configmap_uid_change_is_not_cleaned(self):
        self.operation.create_cm_requested = True
        self.operation.cm_uid = 'original-uid'
        self.operation.cm = {'data': {'payload': 'fixed'}}
        cm = {'metadata': {'uid': 'replacement-uid', 'labels': {host.LABEL: self.operation.operation}}, 'data': self.operation.cm['data']}
        calls = []
        def command(args, *unused, **kwargs):
            calls.append(args)
            return cm
        with patch.object(self.operation, 'command', side_effect=command):
            with self.assertRaisesRegex(host.Refused, 'OWN_CONFIGMAP_UID_CHANGED'):
                self.operation.cleanup()
        self.assertFalse(any(args[0] == 'delete' for args in calls))

    def test_budget415_must_not_precede_skill_install(self):
        rows = [{'metadata': {'name': name, 'uid': uid, 'generation': 415}, 'spec': {'name': name}}
                for name, uid in host.DEPLOYMENT_UIDS.items()]
        with patch.object(self.operation, 'command', return_value={'items': rows}):
            with self.assertRaisesRegex(host.Refused, 'GATEWAY_414_BEFORE_BUDGET_415_REQUIRED'):
                self.operation.baseline()

    def test_publisher_has_no_browser_install_or_copy_routine(self):
        self.assertFalse(hasattr(publisher, 'install_runtime'))
        tree = ast.parse((ROOT / 'publisher.py').read_text(encoding='utf-8'))
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'file_hash')
        calls = [node.func.attr for node in ast.walk(function) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)]
        self.assertFalse(set(calls) & {'write', 'write_bytes', 'write_text', 'mkdir', 'copy', 'copytree', 'chmod'})
        self.assertNotIn('install_runtime', publisher.stage.__code__.co_names)

    def test_unsealed_stage_refuses_before_backup_or_runtime_read(self):
        payload = {'action': 'stage', 'operation': 'a' * 32, 'package': '', 'package_sha256': 'a' * 64,
                   'runtime_manifest_sha256': publisher.RUNTIME_SHA}
        with patch.object(publisher, 'read', side_effect=AssertionError('no read before seal')):
            with self.assertRaisesRegex(publisher.Refused, 'CANDIDATE_NOT_SEALED'):
                publisher.stage(payload)

    def test_publication_requires_both_compose_cases(self):
        receipt = {'runtime': {'manifest_sha256': 'b' * 64}, 'after_tree_sha256': 'c' * 64}
        acceptance = {'status': 'NONROOT_ARM64_DELIVERY_SKILL_QUALIFIED', 'uid': 1000, 'gid': 1000,
                      'package_sha256': publisher.PACKAGE_SHA, 'runtime_manifest_sha256': 'b' * 64,
                      'after_tree_sha256': 'c' * 64, 'png_sha256': 'd' * 64,
                      'final_text': {'complete_artifacts_accepted': True, 'missing_artifact_rejected': False,
                                     'messages_sent': 0, 'network_requests': 0}}
        with patch.object(publisher, 'staged', return_value=(Path('/unexecuted'), receipt)), patch.object(publisher.os, 'rename', side_effect=AssertionError('no switch')):
            with self.assertRaisesRegex(publisher.Refused, 'NONROOT_RENDER_ACCEPTANCE_REQUIRED'):
                publisher.publish({'action': 'publish', 'operation': 'a' * 32, 'acceptance': acceptance})

    def test_unsealed_compose_probe_cannot_be_executed(self):
        with self.assertRaisesRegex(verifier.Refused, 'FINAL_TEXT_PROBE_NOT_SEALED'):
            verifier.qualify_final_text(None, None, None, None)

    def test_compose_probe_cannot_open_a_socket(self):
        code = 'def qualify(*args):\n import socket\n socket.socket()\n'
        with patch.object(verifier, 'FINAL_TEXT_PROBE', code), patch.object(verifier, 'FINAL_TEXT_PROBE_SHA', verifier.sha(code.encode())):
            with self.assertRaisesRegex(verifier.Refused, 'COMPOSE_ONLY_EXTERNAL_OPERATION_FORBIDDEN'):
                verifier.qualify_final_text(None, None, None, None)

    def test_compose_probe_partial_acceptance_fails_closed(self):
        code = "def qualify(*args):\n return {'complete_artifacts_accepted':True,'missing_artifact_rejected':False,'messages_sent':0,'network_requests':0}\n"
        with patch.object(verifier, 'FINAL_TEXT_PROBE', code), patch.object(verifier, 'FINAL_TEXT_PROBE_SHA', verifier.sha(code.encode())):
            with self.assertRaisesRegex(verifier.Refused, 'COMPOSE_ONLY_CASES_NOT_ACCEPTED'):
                verifier.qualify_final_text(None, None, None, None)


if __name__ == '__main__':
    unittest.main()
