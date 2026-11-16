"""Offline operator scope and recovery tests; no Kubernetes or provider calls."""
import base64
import copy
import contextlib
import io
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


host = load('task_context_host', HERE / 'activate-task-conversation-context-20261114-r1.py')
child = load('task_context_child', HERE / 'qualify_context_child.py')
prior = load('accepted_operator_regression_tests', HERE.parent / 'scheduler-budget-20261113-r5/test_scheduler_budget.py')
prior.host = host
CODE = (HERE / 'backend/packages/harness/deerflow/sandbox/tools.py').read_bytes()
HELPER = (HERE / 'backend/packages/harness/deerflow/utils/conversation_url.py').read_bytes()
CM = 'gateway-task-context-0123456789ab'
OPERATION = '0123456789abcdef0123456789abcdef'


def fixture():
    obj = prior.fixture()
    obj['metadata']['generation'] = 416
    obj['status']['observedGeneration'] = 416
    return obj


def build(obj):
    with mock.patch.object(host, 'BEFORE_SPEC_SHA', host.sha(host.canonical(obj['spec']))):
        return host.build_spec(obj, CM)


class ContextOperatorTests(unittest.TestCase):
    def test_only_one_volume_and_mount_added_everything_else_exact(self):
        before = fixture()
        snapshot = copy.deepcopy(before)
        after = build(before)
        self.assertEqual(before, snapshot)
        self.assertEqual(len(after['template']['spec']['volumes']), len(before['spec']['template']['spec']['volumes']) + 1)
        volume = after['template']['spec']['volumes'].pop()
        mount = after['template']['spec']['containers'][0]['volumeMounts'].pop()
        self.assertEqual(volume, {'name': host.TOOLS_VOLUME, 'configMap': {'name': CM}})
        self.assertEqual(mount, {'name': host.TOOLS_VOLUME, 'mountPath': host.TOOLS_PATH, 'subPath': 'tools.py', 'readOnly': True})
        self.assertEqual(after, before['spec'])

    def test_patch_has_four_cas_tests_and_exact_two_append_actions(self):
        before = fixture()
        after = build(before)
        patch = host.cas_patch(before, after)
        self.assertEqual([row['op'] for row in patch], ['test', 'test', 'test', 'test', 'add', 'add'])
        self.assertEqual([row['path'] for row in patch[:4]], ['/metadata/uid', '/metadata/resourceVersion', '/metadata/generation', '/spec'])
        self.assertEqual(patch[3]['value'], before['spec'])
        applied = copy.deepcopy(before['spec'])
        applied['template']['spec']['volumes'].append(patch[4]['value'])
        applied['template']['spec']['containers'][0]['volumeMounts'].append(patch[5]['value'])
        self.assertEqual(applied, after)

    def test_real_baseline_required_and_invalid_generation_or_image_rejected(self):
        with self.assertRaisesRegex(host.Refused, 'BASELINE_CHANGED'):
            host.build_spec(fixture(), CM)
        for mutate in (lambda obj: obj['metadata'].update(generation=417), lambda obj: obj['metadata'].update(uid='other'),
                       lambda obj: obj['spec']['template']['spec']['containers'][0].update(image='other')):
            before = fixture()
            mutate(before)
            with self.assertRaises(host.Refused):
                build(before)

    def test_shadow_mount_duplicate_volume_and_variable_binding_rejected(self):
        for mutate in (
            lambda pod: pod['volumes'].append({'name': host.TOOLS_VOLUME, 'emptyDir': {}}),
            lambda pod: pod['containers'][0]['volumeMounts'].append({'name': 'shadow', 'mountPath': '/app/backend/packages/harness'}),
            lambda pod: pod['containers'][0]['volumeMounts'].append({'name': 'shadow', 'mountPath': host.TOOLS_PATH}),
            lambda pod: pod['containers'][0]['volumeMounts'][0].update(subPathExpr='variable'),
        ):
            before = fixture()
            mutate(before['spec']['template']['spec'])
            with self.assertRaises(host.Refused):
                build(before)

    def test_configmap_is_immutable_and_contains_only_candidate_tools(self):
        cm = host.new_configmap(CM, OPERATION, CODE)
        self.assertEqual(cm['data'], {'tools.py': CODE.decode()})
        self.assertTrue(cm['immutable'])
        self.assertNotIn('binaryData', cm)
        self.assertNotIn('config.yaml', cm['data'])
        with self.assertRaisesRegex(host.Refused, 'CANDIDATE_SOURCE_HASH'):
            host.new_configmap(CM, OPERATION, CODE + b'\n')

    def test_template_refuses_unsealed_code_and_child(self):
        with self.assertRaisesRegex(host.Refused, 'CODE_ASSET_NOT_SEALED'):
            host.load_code()
        with self.assertRaisesRegex(host.Refused, 'CHILD_ASSET_NOT_SEALED'):
            host.load_child()

    def test_sealed_code_loader_checks_hash_and_does_not_compile_on_operator(self):
        asset = {'base64': base64.b64encode(CODE).decode(), 'sha256': host.sha(CODE)}
        with mock.patch.object(host, 'CODE_ASSET', asset), mock.patch('builtins.compile', side_effect=AssertionError('operator must not compile Gateway source')):
            self.assertEqual(host.load_code().encode(), CODE)
        asset['sha256'] = '0' * 64
        with mock.patch.object(host, 'CODE_ASSET', asset), self.assertRaisesRegex(host.Refused, 'CODE_ASSET_HASH'):
            host.load_code()

    def test_old_budget_plan_must_be_exact_and_only_read(self):
        plan = {'schema': 'scheduler-budget-private-plan.v1', 'operation': host.BUDGET_OPERATION,
                'candidate_spec_sha256': host.BEFORE_SPEC_SHA, 'new_configmap': host.OLD_CM,
                'new_config_bytes': 15992, 'new_config_sha256': 'b160057a087d8483' + '1' * 48}
        raw = host.canonical(plan)
        with mock.patch.object(host, 'private_read', return_value=raw), mock.patch.object(host, 'BUDGET_PLAN_SHA', host.sha(raw)), mock.patch.object(host, 'private_write') as write:
            result = host.accepted_budget_profile()
            self.assertEqual(result['scheduler_recursion_limit'], 10000)
            write.assert_not_called()
        with mock.patch.object(host, 'private_read', return_value=raw), self.assertRaisesRegex(host.Refused, 'ACCEPTED_BUDGET_PLAN_HASH'):
            host.accepted_budget_profile()

    def test_child_qualifies_exact_functions_transport_without_network(self):
        self.assertEqual(child.synthetic_context_checks(CODE, HELPER), 5)
        self.assertEqual(len(child.SOURCE_PATHS), 14)

    def test_child_refuses_parent_without_context_function(self):
        old = (HERE / 'parent/backend/packages/harness/deerflow/sandbox/tools.py').read_bytes()
        with self.assertRaisesRegex(child.Refused, 'CONTEXT_SOURCE_FUNCTIONS_REQUIRED'):
            child.synthetic_context_checks(old, HELPER)

    def test_child_audit_prevents_network_writes_and_subprocess(self):
        for event, args in [('socket.__new__', ()), ('subprocess.Popen', ()), ('open', ('unused', 'w', os.O_WRONLY)), ('os.rename', ())]:
            with self.assertRaises(child.Refused):
                child.audit(event, args)

    def test_readonly_command_scope_cannot_mutate_or_read_secrets(self):
        op = host.Operator('child', readonly=True)
        op.report['once_claimed'] = True
        with mock.patch.object(host.subprocess, 'Popen') as launch, mock.patch.object(host, 'private_write') as write:
            for args in (['get', 'secret', 'x', '-o', 'json'], ['delete', 'pod', 'x'], ['create', '-f', '-', '-o', 'json'], ['patch', 'deployment', 'gateway']):
                with self.assertRaises(host.Refused):
                    op.command(args)
            op.journal('READ_ONLY')
            launch.assert_not_called()
            write.assert_not_called()

    def test_preserved_maps_include_extensions_and_budget_without_source_edit(self):
        op = host.Operator('child', readonly=True)
        with mock.patch.object(op, 'get', side_effect=lambda kind, name: name) as get:
            result = op.snapshot_maps()
        self.assertEqual(set(result), set(host.OVERLAYS) | {host.OLD_CM, host.EXTRA_CM})
        self.assertEqual(get.call_count, 5)

    def test_readonly_rollout_returns_progress_without_wait_or_patch(self):
        before = fixture()
        op = host.Operator('child', readonly=True)
        op.before, op.expected = before, build(before)
        op.plan = {'before': host.identity(before)}
        current = copy.deepcopy(before)
        current['metadata']['generation'] = 417
        current['spec'] = op.expected
        current['status']['updatedReplicas'] = 20
        with mock.patch.object(op, 'load_plan'), mock.patch.object(op, 'get', return_value=current), mock.patch.object(op, 'verify') as verify, mock.patch.object(op, 'cutover') as patch, mock.patch.object(host, 'private_write') as write:
            op.run()
        self.assertEqual(op.report['status'], 'GATEWAY_417_ROLLOUT_IN_PROGRESS_NO_CHANGE')
        verify.assert_not_called(); patch.assert_not_called(); write.assert_not_called()

    def test_readonly_missing_patch_never_replays(self):
        op = host.Operator('child', readonly=True)
        op.before = fixture()
        op.plan = {'before': host.identity(op.before)}
        with mock.patch.object(op, 'load_plan'), mock.patch.object(op, 'get', return_value=op.before), mock.patch.object(op, 'cutover') as patch:
            op.run()
        self.assertEqual(op.report['status'], 'TASK_CONTEXT_PATCH_NOT_OBSERVED_REVIEW_REQUIRED')
        patch.assert_not_called()

    def test_rollout_does_not_accept_old_observed_generation_with_seventy_updated(self):
        before = fixture()
        op = host.Operator('child')
        op.expected = build(before)
        current = copy.deepcopy(before)
        current['spec'] = op.expected
        current['metadata']['generation'] = 417
        self.assertFalse(host.is_converged(current, 417))
        advanced = copy.deepcopy(current)
        advanced['status']['observedGeneration'] = 417
        output = io.StringIO()
        with mock.patch.object(op, 'get', side_effect=[current, advanced]), mock.patch.object(host.time, 'sleep') as sleep, contextlib.redirect_stdout(output):
            self.assertEqual(op.rollout(), advanced)
        sleep.assert_called_once()
        progress = json.loads(output.getvalue().splitlines()[0])
        self.assertEqual(progress['generation'], 417)
        self.assertEqual(progress['observed_generation'], 416)

    def test_wrong_host_refuses_all_actions_before_resource_loading(self):
        with mock.patch.object(host.os, 'name', 'nt'), mock.patch.object(host, 'load_child') as child_load, mock.patch.object(host, 'load_code') as code_load, mock.patch.object(host.Operator, 'run') as run, mock.patch('builtins.print') as output:
            self.assertEqual(host.main(['--activate-task-context-once']), 1)
        child_load.assert_not_called(); code_load.assert_not_called(); run.assert_not_called()
        report = json.loads(output.call_args[0][0])
        self.assertEqual(report['commands'], 0)
        self.assertEqual(report['error_code'], 'DATAX01_REQUIRED')

    def accepted(self):
        op = host.Operator('child', readonly=True)
        op.operation = OPERATION
        op.report.update(schema='task-conversation-context-activation.v1', status='TASK_CONVERSATION_CONTEXT_DEPLOYED_RUNTIME_ACCEPTED', operation=OPERATION,
            plan_sha256='1' * 64, after={'uid': host.DEPLOYMENT_UID, 'generation': 417, 'spec_sha256': '2' * 64},
            accepted_source={'parent_sha256': host.TOOLS_PARENT['sha256'], 'candidate_sha256': host.TOOLS_CANDIDATE['sha256']},
            all_gateway_pods_verified=True, context_injection_runtime_accepted=True)
        return op

    def test_acceptance_saved_local_once_and_later_read_without_overwrite(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(host, 'CONTROL', Path(directory)):
            op = self.accepted()
            host.save_acceptance(op)
            first = (Path(directory) / 'RUNTIME-ACCEPTANCE.json').read_bytes()
            self.assertEqual(op.report['runtime_acceptance_receipt']['sha256'], host.sha(first))
            repeated = self.accepted()
            with mock.patch.object(host, 'private_read', return_value=first), mock.patch.object(host, 'private_write') as write:
                host.save_acceptance(repeated)
            write.assert_not_called()
            self.assertEqual((Path(directory) / 'RUNTIME-ACCEPTANCE.json').read_bytes(), first)

    def test_acceptance_rejects_mismatch_or_incomplete(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(host, 'CONTROL', Path(directory)):
            op = self.accepted()
            host.save_acceptance(op)
            raw = (Path(directory) / 'RUNTIME-ACCEPTANCE.json').read_bytes()
            op.report['after']['spec_sha256'] = '3' * 64
            with mock.patch.object(host, 'private_read', return_value=raw), self.assertRaisesRegex(host.Refused, 'EXISTING_ACCEPTANCE_MISMATCH'):
                host.save_acceptance(op)
        op.report['status'] = 'INCOMPLETE'
        with self.assertRaisesRegex(host.Refused, 'ACCEPTANCE_NOT_COMPLETE'):
            host.save_acceptance(op)


class FleetTests(prior.FleetTests):
    def test_source_closure_requires_all_eight_ro_projections_and_openssl(self):
        deployment = fixture()
        maps = {name: {'kind': 'ConfigMap', 'metadata': {'name': name, 'namespace': host.NS, 'uid': name, 'resourceVersion': '1'},
            'data': {key: 'x' * 189 if name == host.OPENSSL_CM else 'pass\n' for key in paths}} for name, paths in host.OVERLAYS.items()}
        with mock.patch.object(host, 'OPENSSL_SHA', host.sha(b'x' * 189)), mock.patch.object(host, 'EXPECTED_SOURCES', prior.synthetic_source_baseline(maps)):
            self.assertEqual(len(host.source_binding(deployment['spec'], maps)), 14)
            deployment['spec']['template']['spec']['containers'][0]['volumeMounts'][1]['readOnly'] = False
            with self.assertRaisesRegex(host.Refused, 'SOURCE_CONFIGMAP_MOUNT'):
                host.source_binding(deployment['spec'], maps)


class AdmissionTests(prior.AdmissionTests):
    def test_failure_report_contains_field_path_but_no_compared_value(self):
        error = host.Refused('INIT_CONTAINER_ADMISSION_CHANGED')
        error.field, error.path = 'initContainers', '/spec/initContainers/0/env/0/value'
        output = io.StringIO()
        original_mask = os.umask(0o077)
        try:
            with mock.patch.object(host.os, 'name', 'posix'), mock.patch.object(host.os, 'uname', create=True, return_value=type('Uname', (), {'nodename': 'datax-01'})()), mock.patch.object(host.os, 'geteuid', create=True, return_value=0), mock.patch.object(host, 'load_child', return_value='fixed-child'), mock.patch.object(host, 'load_code', return_value='fixed-code'), mock.patch.object(host.Operator, 'run', side_effect=error), contextlib.redirect_stdout(output):
                self.assertEqual(host.main(['--activate-task-context-once']), 1)
        finally:
            os.umask(original_mask)
        value = json.loads(output.getvalue())
        self.assertEqual(value['error_field'], 'initContainers')
        self.assertEqual(value['error_path'], error.path)
        self.assertEqual(value['commands'], 0)
        self.assertNotIn('DO_NOT_PRINT', output.getvalue())


def main():
    suite = unittest.TestSuite()
    loader = unittest.TestLoader()
    for tests in (ContextOperatorTests, FleetTests, AdmissionTests, prior.SparseListTests):
        suite.addTests(loader.loadTestsFromTestCase(tests))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    payload = {'schema': 'task-context-operator-offline-tests.v1', 'tests': result.testsRun, 'failures': len(result.failures), 'errors': len(result.errors),
        'skipped': len(result.skipped), 'passed': result.wasSuccessful(), 'production_requests': 0, 'provider_requests': 0,
        'operator_template_sha256': host.sha((HERE / 'activate-task-conversation-context-20261114-r1.py').read_bytes()),
        'qualification_child_sha256': host.sha((HERE / 'qualify_context_child.py').read_bytes()), 'sealed': False}
    (HERE / 'OPERATOR-VALIDATION.json').write_text(json.dumps(payload, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    sys.exit(main())
