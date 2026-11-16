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
        report = {'status': host.OLD_INSTALL_STATUS, 'operation': host.OLD_INSTALL_OPERATION,
                  'skill_updated': True, 'maintenance_pod_absent': True, 'maintenance_configmap_absent': True,
                  'runtime': dict(runtime),
                  'publication': {'after_tree_sha256': host.OLD_TREE, 'after_files': host.OLD_INSTALL_FILES,
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

    def test_archived_actual_prior_install_receipt_shape_is_accepted(self):
        archive=ROOT.parents[2] / 'release/energy-daily-freight-links-20261114-r1/OPERATOR-INSTALL-REPORT.json'
        raw=archive.read_bytes()
        with patch.object(host,'read',return_value=raw),patch.object(host,'OLD_INSTALL_BYTES',len(raw)),patch.object(host,'OLD_INSTALL_SHA',host.sha(raw)):
            result=host.qualification_binding()
        self.assertEqual(result[0]['status'],host.OLD_INSTALL_STATUS)
        self.assertEqual(result[3]['operation'],'daeceae4165e4e1d87050bd97e6bf933')

    def test_report_write_io_failure_still_emits_final_json(self):
        operation = self.operation
        operation.claimed = True
        operation.out['status'] = 'ENERGY_DELIVERY_SKILL_INSTALLED'
        output = io.StringIO()
        with patch.object(host.sys, 'argv', ['operator.py', '--install-delivery-context-skill-once']), patch.object(host.os, 'name', 'posix'), patch.object(host.os, 'uname', return_value=SimpleNamespace(nodename='datax-01'), create=True), patch.object(host.os, 'geteuid', return_value=0, create=True), patch.object(host, 'load_assets', return_value=ASSETS), patch.object(host, 'Operation', return_value=operation), patch.object(operation, 'run'), patch.object(operation, 'cleanup'), patch.object(host, 'private_write', side_effect=OSError('synthetic private path unavailable')), contextlib.redirect_stdout(output):
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
        self.assertEqual(mounts[1]['subPath'], '.energy-delivery-context-skill-install-20261114-r1-' + self.operation.operation + '/candidate')
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

    def test_current_protected_baseline_accepts_new_generation_and_preserves_spec(self):
        rows = [{'metadata': {'name': name, 'uid': uid, 'generation': 417}, 'spec': {'name': name, 'template': {'image': 'current'}}}
                for name, uid in host.DEPLOYMENT_UIDS.items()]
        with patch.object(self.operation, 'command', return_value={'items': rows}):
            result = self.operation.baseline()
        self.assertEqual(result['gateway']['generation'], 417)
        for row in rows:
            self.assertEqual(result[row['metadata']['name']]['spec_sha256'], host.sha(host.canonical(row['spec'])))

    def test_current_protected_baseline_refuses_replaced_deployment(self):
        rows = [{'metadata': {'name': name, 'uid': uid, 'generation': 417}, 'spec': {'name': name}}
                for name, uid in host.DEPLOYMENT_UIDS.items()]
        rows[0]['metadata']['uid'] = 'replacement-uid'
        with patch.object(self.operation, 'command', return_value={'items': rows}):
            with self.assertRaisesRegex(host.Refused, 'DEPLOYMENT_IDENTITY_CHANGED'):
                self.operation.baseline()

    def test_current_protected_baseline_refuses_missing_deployment(self):
        rows = [{'metadata': {'name': name, 'uid': uid, 'generation': 417}, 'spec': {'name': name}}
                for name, uid in host.DEPLOYMENT_UIDS.items()]
        with patch.object(self.operation, 'command', return_value={'items': rows[1:]}):
            with self.assertRaisesRegex(host.Refused, 'DEPLOYMENT_SET_CHANGED'):
                self.operation.baseline()

    def test_ordinary_visibility_uses_current_ready_matching_runtime(self):
        name, uid = 'sandbox-live01', '12345678-1234-1234-1234-123456789abc'
        pod = {'metadata': {'uid': uid}, 'spec': {
            'containers': [{'name': 'sandbox', 'volumeMounts': [{'name': 'skills-public',
                'mountPath': '/mnt/skills/public', 'subPath': 'public', 'readOnly': True}]}],
            'volumes': [{'name': 'skills-public', 'persistentVolumeClaim': {'claimName': host.CLAIM}}]}}
        def command(args, *unused, **kwargs):
            if args == host.ORDINARY_LIST:
                return ('sandbox-old 12345678-1234-1234-1234-123456789000 node Pending <none> false <none>\n'
                        + name + ' ' + uid + ' ecs-zdz-pro-0017 Running <none> true ' + host.IMAGE_ID + '\n')
            if args[:2] == ['get', 'pod']:
                self.assertEqual(args[2], name)
                return pod
            if args[:1] == ['exec']:
                self.assertEqual(args[1], name)
                return {'status': 'ORDINARY_SANDBOX_PUBLIC_RUNTIME_VISIBLE'}
            raise AssertionError(args)
        with patch.object(self.operation, 'command', side_effect=command):
            result = self.operation.ordinary_visibility('a' * 64)
        self.assertEqual(result['sample']['pod'], name)
        self.assertEqual(result['sample']['uid'], uid)

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

    def test_publication_requires_new_link_and_freight_acceptance(self):
        receipt = {'runtime': {'manifest_sha256': 'b' * 64}, 'after_tree_sha256': 'c' * 64}
        valid = {'status': 'NONROOT_ARM64_DELIVERY_SKILL_QUALIFIED', 'uid': 1000, 'gid': 1000,
                 'package_sha256': publisher.PACKAGE_SHA, 'runtime_manifest_sha256': 'b' * 64,
                 'after_tree_sha256': 'c' * 64, 'png_sha256': 'd' * 64,
                 'final_text': {'complete_artifacts_accepted': True, 'missing_artifact_rejected': True,
                                'single_text_ready_before_publication': True, 'freight_fallback_date_marked': True,
                                'missing_host_context_blocked':True,'synthetic_host_environment_used':True,
                                'real_host_context_injection_proven':False,
                                'messages_sent': 0, 'network_requests': 0},
                 'offline_tests':{'run':55,'errors':0,'failures':0},
                 'freight_render': {'status': 'FREIGHT_FALLBACK_RENDER_QUALIFIED', 'actual_dates_verified': True}}
        for section, key in (('final_text', 'single_text_ready_before_publication'),
                             ('final_text','missing_host_context_blocked'),('final_text','synthetic_host_environment_used'),
                             ('final_text', 'freight_fallback_date_marked'), ('freight_render', 'actual_dates_verified')):
            acceptance = copy.deepcopy(valid)
            acceptance[section][key] = False
            with self.subTest(section=section, key=key), patch.object(publisher, 'staged', return_value=(Path('/unexecuted'), receipt)), patch.object(publisher.os, 'rename', side_effect=AssertionError('no switch')):
                with self.assertRaisesRegex(publisher.Refused, 'NONROOT_RENDER_ACCEPTANCE_REQUIRED'):
                    publisher.publish({'action': 'publish', 'operation': 'a' * 32, 'acceptance': acceptance})

    def test_gateway_context_contract_unbound_stops_before_any_read_or_command(self):
        with patch.object(host,'read',side_effect=AssertionError('no read')):
            with self.assertRaisesRegex(host.Refused,'GATEWAY_CONTEXT_RECEIPT_NOT_BOUND'):
                host.gateway_context_binding()
        with patch.object(host,'qualification_binding',side_effect=AssertionError('no old receipt')), patch.object(self.operation,'command',side_effect=AssertionError('no kubectl')):
            with self.assertRaisesRegex(host.Refused,'GATEWAY_CONTEXT_RECEIPT_NOT_BOUND'):
                self.operation.run()
        self.assertFalse(self.operation.claimed)

    def test_gateway_context_receipt_and_live_identity_are_required(self):
        after={'uid':host.DEPLOYMENT_UIDS['gateway'],'generation':417,'spec_sha256':host.GATEWAY_CONTEXT_ACTUAL_SPEC}
        row={'schema':host.GATEWAY_CONTEXT_RECEIPT['schema'],'status':host.GATEWAY_CONTEXT_RECEIPT['status'],
             'operation':host.GATEWAY_CONTEXT_FAILURE['operation'],'all_gateway_pods_verified':True,'context_injection_runtime_accepted':True,
             'accepted_source':host.GATEWAY_CONTEXT_SOURCE,'verified_pods':70,'verified_source_files_per_pod':14,
             'service_backends_verified':True,'future_scheduled_runs_budget':10000,'global_max_recursion_limit':10000,
             'plan_sha256':host.GATEWAY_CONTEXT_FAILURE['plan_sha256'],'after':after,
             'tool_revision':'postcheck-r2','readonly':True,'workload_modified':False,'original_receipts_modified':False,
             'patch_request_count':0,'configmap_create_requests':0,'original_patch_not_replayed':True,
             'only_observed_api_default_accepted':True,'accepted_original_failure':host.GATEWAY_CONTEXT_FAILURE,
             'requested_spec_sha256':host.GATEWAY_CONTEXT_REQUESTED_SPEC,'observed_spec_sha256':host.GATEWAY_CONTEXT_ACTUAL_SPEC,
             'accepted_defaulting':{'schema':'task-context-single-defaulting-field-proof.v1','gateway_uid':after['uid'],
                 'generation':417,'requested_spec_sha256':host.GATEWAY_CONTEXT_REQUESTED_SPEC,
                 'actual_spec_sha256':host.GATEWAY_CONTEXT_ACTUAL_SPEC,'difference_path':'/template/spec/volumes/11/configMap/defaultMode',
                 'actual_default_mode':420,'difference_count':1,'field_report_sha256':'f'*64}}
        raw=host.canonical(row)
        contract=dict(host.GATEWAY_CONTEXT_RECEIPT,bytes=len(raw),sha256=host.sha(raw),operation=row['operation'])
        with patch.object(host,'GATEWAY_CONTEXT_RECEIPT',contract),patch.object(host,'read',return_value=raw):
            result=host.gateway_context_binding(after)
            self.assertEqual(result['gateway'],after)
            with self.assertRaisesRegex(host.Refused,'GATEWAY_CONTEXT_LIVE_IDENTITY_CHANGED'):
                host.gateway_context_binding(dict(after,generation=418))
        for key,value in (('all_gateway_pods_verified',False),('context_injection_runtime_accepted',False),
                          ('accepted_source',{}),('verified_pods',69),('verified_source_files_per_pod',13),
                          ('service_backends_verified',False),('plan_sha256',''),('future_scheduled_runs_budget',300)):
            bad=host.canonical(dict(row,**{key:value}))
            changed=dict(contract,bytes=len(bad),sha256=host.sha(bad))
            with self.subTest(key=key),patch.object(host,'GATEWAY_CONTEXT_RECEIPT',changed),patch.object(host,'read',return_value=bad):
                with self.assertRaisesRegex(host.Refused,'GATEWAY_CONTEXT_RUNTIME_ACCEPTANCE_REQUIRED'):
                    host.gateway_context_binding(after)

        for key,value in (('original_patch_not_replayed',False),('accepted_original_failure',{}),('patch_request_count',1),('tool_revision','r1')):
            bad=host.canonical(dict(row,**{key:value}))
            changed=dict(contract,bytes=len(bad),sha256=host.sha(bad))
            with self.subTest(postcheck_key=key),patch.object(host,'GATEWAY_CONTEXT_RECEIPT',changed),patch.object(host,'read',return_value=bad):
                with self.assertRaisesRegex(host.Refused,'GATEWAY_CONTEXT_POSTCHECK_PROOF_REQUIRED'):
                    host.gateway_context_binding(after)

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

    def test_unsealed_freight_probe_cannot_be_executed(self):
        with self.assertRaisesRegex(verifier.Refused, 'FINAL_TEXT_PROBE_NOT_SEALED'):
            verifier.qualify_freight_render(None, None, None)

    def test_freight_render_rejects_missing_dates_or_tiny_png(self):
        result = {'status': 'FREIGHT_FALLBACK_RENDER_QUALIFIED', 'actual_dates_verified': True,
                  'png_sha256': 'a' * 64, 'png_bytes': 1200, 'png': {'width': 780, 'height': 5250, 'browser_fallback_count': 0}}
        for key, value in (('actual_dates_verified', False), ('png_bytes', 1000), ('png_sha256', 'invalid')):
            invalid = dict(result, **{key: value})
            code = 'def qualify_freight_render(*args):\n return ' + repr(invalid) + '\n'
            with self.subTest(key=key), patch.object(verifier, 'FINAL_TEXT_PROBE', code), patch.object(verifier, 'FINAL_TEXT_PROBE_SHA', verifier.sha(code.encode())):
                with self.assertRaisesRegex(verifier.Refused, 'FREIGHT_RENDER_CASES_NOT_ACCEPTED'):
                    verifier.qualify_freight_render(None, None, None)

    def test_freight_probe_preserves_success_receipt(self):
        result = {'status': 'FREIGHT_FALLBACK_RENDER_QUALIFIED', 'actual_dates_verified': True,
                  'png_sha256': 'a' * 64, 'png_bytes': 1200, 'png': {'width': 780, 'height': 5250, 'browser_fallback_count': 0}}
        code = 'def qualify_freight_render(*args):\n return ' + repr(result) + '\n'
        with patch.object(verifier, 'FINAL_TEXT_PROBE', code), patch.object(verifier, 'FINAL_TEXT_PROBE_SHA', verifier.sha(code.encode())):
            self.assertEqual(verifier.qualify_freight_render(None, None, None), result)


if __name__ == '__main__':
    unittest.main()
