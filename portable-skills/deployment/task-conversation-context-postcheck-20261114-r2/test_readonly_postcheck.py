"""Synthetic scope tests only: real field-diff evidence remains required."""
import copy
import contextlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


post = load('context_readonly_postcheck', HERE / 'verify-task-conversation-context-20261114-r2.py')
parent = load('immutable_context_r1', HERE.parents[2] / 'release/task-conversation-context-20261114-r1/activate-task-conversation-context-20261114-r1.py')
sealer = load('readonly_postcheck_sealer', HERE / 'seal_readonly_postcheck.py')


def fixture():
    requested = {'replicas': 70, 'template': {'spec': {'volumes': [
        {'name': 'existing', 'configMap': {'name': 'existing', 'defaultMode': 420}},
        {'name': parent.TOOLS_VOLUME, 'configMap': {'name': 'gateway-task-context-' + post.OPERATION[:12]}},
    ]}}}
    expected = copy.deepcopy(requested)
    expected['template']['spec']['volumes'][-1]['configMap']['defaultMode'] = 420
    proof = {'schema': 'task-context-single-defaulting-field-proof.v1', 'field_report_sha256': 'a' * 64,
        'gateway_uid': parent.DEPLOYMENT_UID, 'generation': 417, 'requested_spec_sha256': post.digest(post.canonical(requested)),
        'actual_spec_sha256': post.digest(post.canonical(expected)), 'difference_path': '/template/spec/volumes/1/configMap/defaultMode',
        'actual_default_mode': 420, 'difference_count': 1}
    return requested, expected, proof


class ScopeTests(unittest.TestCase):
    def test_only_proven_single_default_and_original_expected_not_changed(self):
        requested, expected, proof = fixture()
        prior = copy.deepcopy(requested)
        with mock.patch.object(post, 'REQUESTED_SPEC_SHA', proof['requested_spec_sha256']):
            self.assertEqual(post.defaulted_expected(parent, requested, proof), expected)
        self.assertEqual(requested, prior)

    def test_unbound_proof_rejected(self):
        with self.assertRaisesRegex(post.Refused, 'FIELD_PROOF_KEYS'):
            post.defaulted_expected(parent, fixture()[0], {})
        with self.assertRaisesRegex(post.Refused, 'PARENT_ASSET_NOT_SEALED'):
            post.load_parent()

    def test_other_field_path_mode_multiple_differences_and_generation_refused(self):
        for key, value in [('difference_path', '/template/spec/volumes/0/configMap/defaultMode'), ('actual_default_mode', 493),
                           ('actual_default_mode', True), ('difference_count', 2), ('generation', 418), ('gateway_uid', 'other')]:
            requested, _, proof = fixture()
            requested_sha = proof['requested_spec_sha256']
            proof[key] = value
            with mock.patch.object(post, 'REQUESTED_SPEC_SHA', requested_sha), self.assertRaises(post.Refused):
                post.defaulted_expected(parent, requested, proof)

    def test_any_extra_expected_mutation_wrong_actual_hash_or_existing_default_refused(self):
        requested, _, proof = fixture()
        changed = copy.deepcopy(requested)
        changed['replicas'] = 71
        with mock.patch.object(post, 'REQUESTED_SPEC_SHA', proof['requested_spec_sha256']), self.assertRaisesRegex(post.Refused, 'ORIGINAL_REQUESTED_SPEC_HASH'):
            post.defaulted_expected(parent, changed, proof)
        proof['actual_spec_sha256'] = '0' * 64
        with mock.patch.object(post, 'REQUESTED_SPEC_SHA', proof['requested_spec_sha256']), self.assertRaisesRegex(post.Refused, 'OBSERVED_DEFAULTING_SPEC_HASH'):
            post.defaulted_expected(parent, requested, proof)
        requested['template']['spec']['volumes'][-1]['configMap']['defaultMode'] = 420
        proof['requested_spec_sha256'] = post.digest(post.canonical(requested))
        with mock.patch.object(post, 'REQUESTED_SPEC_SHA', proof['requested_spec_sha256']), self.assertRaisesRegex(post.Refused, 'ORIGINAL_CONTEXT_VOLUME_SHAPE'):
            post.defaulted_expected(parent, requested, proof)

    def test_only_owned_last_added_volume_can_default(self):
        for mutation in (
            lambda vols: vols.append({'name': 'later', 'emptyDir': {}}),
            lambda vols: vols[-1]['configMap'].update(optional=False),
            lambda vols: vols[-1]['configMap'].update(name='unrelated'),
            lambda vols: vols.append(copy.deepcopy(vols[-1])),
        ):
            requested, _, proof = fixture()
            mutation(requested['template']['spec']['volumes'])
            proof['requested_spec_sha256'] = post.digest(post.canonical(requested))
            with mock.patch.object(post, 'REQUESTED_SPEC_SHA', proof['requested_spec_sha256']), self.assertRaises(post.Refused):
                post.defaulted_expected(parent, requested, proof)

    def test_no_mutating_command_or_activation_path_is_callable(self):
        op = post.verifier_class(parent)(parent.load_child())
        with mock.patch.object(parent.subprocess, 'Popen') as popen:
            for args in (['patch', 'deployment', 'gateway'], ['create', '-f', '-'], ['delete', 'pod', 'x']):
                with self.assertRaisesRegex(post.Refused, 'POSTCHECK_MUTATION_COMMAND_REFUSED'):
                    op.command(args)
            for method in ('preflight', 'claim', 'cutover', 'rollout'):
                with self.assertRaises(post.Refused):
                    getattr(op, method)()
            popen.assert_not_called()

    def test_wrong_original_failure_hash_refuses_before_kubernetes(self):
        op = post.verifier_class(parent)(parent.load_child())
        with mock.patch.object(parent, 'private_read', return_value=b'{}'), mock.patch.object(op, 'get') as get:
            with self.assertRaisesRegex(post.Refused, 'ORIGINAL_FAILURE_RECEIPT_HASH'):
                op.run_postcheck()
            get.assert_not_called()

    def prepared_run(self, converged):
        requested, expected, proof = fixture()
        old = {'operation': post.OPERATION, 'plan_sha256': 'b' * 64, 'status': 'INCOMPLETE', 'error_code': 'GATEWAY_SPEC_DRIFT', 'patch_request_count': 1, 'patch_may_have_applied': True}
        old_plan = b'accepted-fixed-plan'
        old['plan_sha256'] = post.digest(old_plan)
        raw = post.canonical(old)
        op = post.verifier_class(parent)(parent.load_child())
        op.operation = post.OPERATION
        op.expected = requested
        op.plan = {'candidate_spec_sha256': proof['requested_spec_sha256']}
        current = {'metadata': {'uid': parent.DEPLOYMENT_UID, 'generation': 417}, 'spec': expected,
            'status': {'observedGeneration': 417 if converged else 416, 'replicas': 70, 'updatedReplicas': 70, 'readyReplicas': 70, 'availableReplicas': 70, 'terminatingReplicas': 0}}
        def read(path, *args):
            if path.name == 'REPORT.json':
                return raw
            if path.name == 'PLAN.json':
                return old_plan
            raise AssertionError('unexpected file')
        with mock.patch.object(post, 'REPORT_BYTES', len(raw)), mock.patch.object(post, 'REPORT_SHA', post.digest(raw)), mock.patch.object(post, 'PLAN_SHA', post.digest(old_plan)), mock.patch.object(post, 'REQUESTED_SPEC_SHA', proof['requested_spec_sha256']), mock.patch.object(post, 'FIELD_DEFAULTING', proof), mock.patch.object(parent, 'private_read', side_effect=read), mock.patch.object(parent, 'private_write') as write, mock.patch.object(op, 'load_plan'), mock.patch.object(op, 'get', return_value=current), mock.patch.object(op, 'verify') as verify:
            op.run_postcheck()
            write.assert_not_called()
            self.assertEqual(verify.call_count, int(converged))
        self.assertEqual(op.expected, expected)
        self.assertEqual(op.report['patch_request_count'], 0)
        self.assertEqual(op.report['configmap_create_requests'], 0)
        self.assertFalse(op.report['old_release_directory_modified'])
        return op

    def test_in_progress_returns_without_wait_write_or_verification(self):
        op = self.prepared_run(False)
        self.assertEqual(op.report['status'], 'GATEWAY_417_ROLLOUT_IN_PROGRESS_NO_CHANGE')

    def test_only_converged_matches_enter_inherited_fleet_verifier(self):
        self.prepared_run(True)

    def test_save_writes_only_new_directory_and_preserves_old_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old_dir, new_dir = root / 'old', root / 'new'
            old_dir.mkdir()
            old_file = old_dir / 'REPORT.json'
            old_file.write_bytes(b'immutable-old-receipt')
            _, expected, proof = fixture()
            op = SimpleNamespace(commands=297, report={'schema': 'task-conversation-context-activation.v1',
                'status': 'TASK_CONVERSATION_CONTEXT_DEPLOYED_RUNTIME_ACCEPTED', 'operation': post.OPERATION,
                'plan_sha256': post.PLAN_SHA, 'after': {'generation': 417, 'spec_sha256': post.digest(post.canonical(expected))},
                'accepted_source': dict(parent.TOOLS_CANDIDATE), 'accepted_defaulting': proof,
                'all_gateway_pods_verified': True, 'context_injection_runtime_accepted': True})
            original_write = parent.private_write
            paths = []
            def record(path, raw):
                paths.append(path)
                return original_write(path, raw)
            # Windows directory permissions are not POSIX mode bits; actual
            # 0700/0600 ownership checks remain unmodified in the operator.
            mode = mock.patch.object(post.stat, 'S_IMODE', return_value=0o700) if os.name == 'nt' else contextlib.nullcontext()
            with mock.patch.object(post, 'OLD_CONTROL', old_dir), mock.patch.object(post, 'NEW_CONTROL', new_dir), mock.patch.object(parent, 'private_write', side_effect=record), mode:
                post.finalize_observation(op.report, op, post.time.monotonic())
                post.save_new_acceptance(parent, op)
                saved = (new_dir / 'RUNTIME-ACCEPTANCE.json').read_bytes()
                metadata = op.report['runtime_acceptance_receipt']
                without_metadata = {key: value for key, value in op.report.items() if key != 'runtime_acceptance_receipt'}
                self.assertEqual(saved, post.canonical(without_metadata))
                self.assertEqual(post.digest(saved), metadata['sha256'])
                self.assertEqual(len(saved), metadata['bytes'])
                self.assertEqual(json.loads(saved)['commands'], 297)
                self.assertTrue(metadata['stdout_without_receipt_metadata_matches_saved'])
                fresh = SimpleNamespace(report=copy.deepcopy(without_metadata))
                fresh.report.update(persistent_files_modified=False, commands=301, observed_at='new-observation')
                post.save_new_acceptance(parent, fresh)
                self.assertTrue(fresh.report['runtime_acceptance_receipt']['existing_acceptance_reused'])
                self.assertFalse(fresh.report['runtime_acceptance_receipt']['stdout_without_receipt_metadata_matches_saved'])
                self.assertEqual((new_dir / 'RUNTIME-ACCEPTANCE.json').read_bytes(), saved)
            self.assertEqual(paths, [new_dir / 'RUNTIME-ACCEPTANCE.json'])
            self.assertEqual(old_file.read_bytes(), b'immutable-old-receipt')
            self.assertEqual(op.report['runtime_acceptance_receipt']['path'], str(new_dir / 'RUNTIME-ACCEPTANCE.json'))

    def synthetic_field_report(self):
        return {'schema': 'task-context-spec-drift-readonly.v1', 'status': 'RECORDED_PATCH_SPEC_DIFF_OBSERVED_NO_CHANGE',
            'accepted_operation': post.OPERATION, 'only_new_volume_default_mode_420_difference': True,
            'new_volume_present_once': True, 'new_volume_configmap_matches_recorded': True,
            'new_readonly_source_mount_exact': True, 'stable_deployment_spec': True,
            'workload_modified': False, 'files_modified': False, 'original_receipts_modified': False, 'diff_limit_reached': False,
            'new_volume_default_mode': 420, 'deployment': {'uid': parent.DEPLOYMENT_UID, 'generation': 417, 'desired': 70, 'spec_sha256': 'c' * 64},
            'differences': [{'path': '/template/spec/volumes/9/configMap/defaultMode',
                'expected': {'present': False, 'type': 'missing', 'sha256': None},
                'actual': {'present': True, 'type': 'int', 'sha256': post.digest(post.canonical(420))}}],
            'new_configmap': {key: True for key in ('present', 'immutable', 'operation_label_matches', 'only_tools_source', 'source_matches_candidate')}}

    def test_sealer_requires_matching_field_value_and_complete_proof(self):
        report = self.synthetic_field_report()
        proof = sealer.proven_defaulting(report)
        self.assertEqual(proof['difference_count'], 1)
        self.assertEqual(proof['actual_default_mode'], 420)
        for mutate in (
            lambda row: row.update(only_new_volume_default_mode_420_difference=False),
            lambda row: row['new_configmap'].update(source_matches_candidate=False),
            lambda row: row['differences'][0]['actual'].update(sha256='0' * 64),
            lambda row: row['deployment'].update(generation=418),
            lambda row: row['differences'].append(copy.deepcopy(row['differences'][0])),
        ):
            bad = copy.deepcopy(report)
            mutate(bad)
            with self.assertRaises(ValueError):
                sealer.proven_defaulting(bad)


def main():
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ScopeTests))
    payload = {'schema': 'task-context-postcheck-synthetic-tests.v1', 'tests': result.testsRun, 'failures': len(result.failures), 'errors': len(result.errors),
        'passed': result.wasSuccessful(), 'production_requests': 0, 'field_defaulting_proof_bound': bool(post.FIELD_DEFAULTING),
        'sealed': bool(post.PARENT_ASSET), 'template_sha256': post.digest((HERE / 'verify-task-conversation-context-20261114-r2.py').read_bytes())}
    (HERE / 'VALIDATION.json').write_text(json.dumps(payload, sort_keys=True, indent=2) + '\n', encoding='utf-8')
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    sys.exit(main())
