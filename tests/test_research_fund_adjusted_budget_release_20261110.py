"""Offline release binding and zero-action template guard checks."""
import ast
import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT/'release/research-fund-adjusted-budget-20261110-r1'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


b = load('labels_builder', RELEASE/'adjusted_budget_release_builder.py')
preflight = load('labels_preflight_fixture', ROOT/'tests/test_research_fund_adjusted_budget_preflight_20261110.py')
host = load('labels_host_template', RELEASE/'adjusted_budget_updater_template.py')
remote = load('labels_remote_template', RELEASE/'adjusted_budget_remote.py')
BEFORE = json.loads((RELEASE/'baseline-manifest.json').read_bytes())
BOUND = RELEASE/'bound-package/update-portal-research-fund-adjusted-budget-20261110-r1.py'


class Checks(unittest.TestCase):
    def test_bound_package_matches_actual_operator_baseline_and_reviewed_skill(self):
        target = load('bound_expense_updater', BOUND)
        target.validate_bundle()
        actual = json.loads((RELEASE/'operator-preflight-20261110.json').read_bytes())
        self.assertEqual(target.BUNDLE['deployments'], actual['deployments'])
        self.assertEqual(target.BUNDLE['frontend_observation'], actual['frontend_observation'])
        self.assertEqual(target.BUNDLE['before'], BEFORE)
        self.assertEqual(target.BUNDLE['after'], b.manifest(ROOT/'skills/public/research-fund-settlement'))
        self.assertEqual(target.BUNDLE['baseline_receipt_sha256'], b.sha((RELEASE/'operator-preflight-20261110.json').read_bytes()))
        self.assertIsNone(target.BUNDLE['runtime_review'])
        self.assertEqual(b.literal_binding(target.BUNDLE['remote_source'], 'QUALIFIER_EXPECTED'), 325)

    def test_bound_package_wrong_host_has_zero_reads_and_mutations(self):
        target = load('wrong_host_expense_updater', BOUND)
        with patch.object(sys, 'argv', ['tool', '--update-research-fund-skill-once']), patch.object(target.socket, 'gethostname', return_value='offline-synthetic'), patch.object(target, 'command') as command, patch.object(target, 'verify_prior_release') as read, contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(target.main(), 1)
        report = json.loads(output.getvalue())
        self.assertEqual(report['error_code'], 'WRONG_HOST')
        self.assertEqual(report['read_and_maintenance_commands'], 0)
        self.assertFalse(report['once_claimed'])
        self.assertFalse(report['skill_switch_requested'])
        command.assert_not_called()
        read.assert_not_called()

    def test_bound_package_tampered_payload_or_source_is_refused(self):
        for key in ('archive_b64', 'inspector_source', 'readonly_child', 'remote_source'):
            target = load('tampered_expense_updater', BOUND)
            value = copy.deepcopy(target.BUNDLE)
            value[key] = 'AAAA' if key == 'archive_b64' else value[key]+'\n'
            with self.subTest(key=key), patch.object(target, 'BUNDLE', value), self.assertRaises(target.Refused):
                target.validate_bundle()

    def test_bound_package_python37_syntax_including_embedded_programs(self):
        source = BOUND.read_text(encoding='utf8')
        ast.parse(source, feature_version=(3, 7))
        value = b.literal_binding(source, 'BUNDLE')
        for key in ('inspector_source', 'readonly_child', 'remote_source'):
            ast.parse(value[key], feature_version=(3, 7))






    def baseline(self):
        report = preflight.Fixture().inspect()
        self.assertEqual(report['exitcode'], 0)
        return report

    def test_current_three_changed_no_added_skill_files(self):
        after = b.manifest(ROOT/'skills/public/research-fund-settlement')
        b.validate_scope(BEFORE, after)
        self.assertEqual(after['file_count'], 31)

    def test_financial_rule_or_template_change_is_rejected(self):
        after = b.manifest(ROOT/'skills/public/research-fund-settlement')
        for name in ('scripts/report_model.py', 'assets/templates/group-company-template.docx'):
            value = copy.deepcopy(after)
            target = next((row for row in value['files'] if row['path'] == name), None)
            if target is None:
                name = next(row['path'] for row in value['files'] if row['path'].endswith('.docx'))
                target = next(row for row in value['files'] if row['path'] == name)
            target['sha256'] = '0'*64
            value['tree_sha256'] = b.sha(b.canonical(value['files']))
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'SKILL_CHANGED_FILE_SCOPE'):
                b.validate_scope(BEFORE, value)

    def test_stable_current_versions_are_bound_without_old_generation(self):
        report = self.baseline()
        self.assertEqual(b.validate_baseline(b.canonical(report), BEFORE), report)
        self.assertEqual(report['deployments']['gateway']['generation'], 408)
        self.assertEqual(report['deployments']['provisioner']['generation'], 86)

    def test_current_snapshot_with_nonconverged_gateway_cannot_bind_writer(self):
        value = self.baseline()
        value['convergence_after']['gateway']['updated'] -= 1
        raw = b.canonical(value)
        with self.assertRaisesRegex(ValueError, 'WRITABLE_BASELINE_NOT_CONVERGED_gateway'):
            b.validate_baseline(raw, BEFORE)

    def test_before_and_after_convergence_required(self):
        for side in ('convergence_before', 'convergence_after'):
            for role in ('gateway', 'provisioner', 'frontend'):
                value = self.baseline(); value[side][role]['updated'] -= 1
                with self.subTest(side=side, role=role), self.assertRaisesRegex(ValueError, 'WRITABLE_BASELINE_NOT_CONVERGED_'+role):
                    b.validate_baseline(b.canonical(value), BEFORE)

    def test_partial_ready_replicas_and_five_node_coverage_required(self):
        value = self.baseline(); value['final_pod_selection_diagnostics']['gateway']['complete_current_replica_set'] = False
        with self.assertRaisesRegex(ValueError, 'CURRENT_REPLICA_COVERAGE_REQUIRED_gateway'):
            b.validate_baseline(b.canonical(value), BEFORE)
        value = self.baseline(); value['gateway_node_coverage']['missing'] = ['ecs-zdz-pro-0022']
        with self.assertRaisesRegex(ValueError, 'GATEWAY_NODE_COVERAGE_REQUIRED'):
            b.validate_baseline(b.canonical(value), BEFORE)

    def test_no_maintainable_shared_sandbox_refused(self):
        value = self.baseline(); value['sandbox_samples'][0]['mount']['read_only'] = False
        with self.assertRaisesRegex(ValueError, 'SHARED_SANDBOX_SAMPLE_REQUIRED'):
            b.validate_baseline(b.canonical(value), BEFORE)

    def test_prior_release_pin_cannot_be_substituted(self):
        value = self.baseline(); value['prior_release']['sha256'] = '0'*64
        with self.assertRaisesRegex(ValueError, 'PRIOR_RELEASE_RECEIPT_BINDING_CHANGED'):
            b.validate_baseline(b.canonical(value), BEFORE)

    def test_runtime_changes_require_independent_exact_review_record(self):
        f = preflight.Fixture(); f.ob['runtime_sources']['activation']['sha256'] = '0'*64
        report = f.inspect(); raw = b.canonical(report)
        self.assertEqual(report['exitcode'], 2)
        with self.assertRaisesRegex(ValueError, 'EXPLICIT_RUNTIME_COMPATIBILITY_REVIEW_REQUIRED'):
            b.validate_baseline(raw, BEFORE)
        review = {'schema': 'research-fund-runtime-source-review.v1', 'status': 'REVIEWED_COMPATIBLE_FOR_SHARED_SKILL_UPDATE',
                  'baseline_sha256': b.sha(raw), 'runtime_sources_sha256': b.sha(b.canonical(report['samples'][0]['runtime_sources'])),
                  'reviewed_reasons': report['review_reasons'], 'compatibility_evidence_sha256': 'a'*64}
        self.assertEqual(b.validate_baseline(raw, BEFORE, review), report)
        changed = dict(review, baseline_sha256='0'*64)
        with self.assertRaisesRegex(ValueError, 'EXPLICIT_RUNTIME_COMPATIBILITY_REVIEW_REQUIRED'):
            b.validate_baseline(raw, BEFORE, changed)
        self.assertEqual(report['status'], 'READONLY_ADJUSTED_BUDGET_BASELINE_REVIEW_REQUIRED')

    def test_non_runtime_review_cannot_be_overridden(self):
        value = self.baseline(); value['review_reasons'] = ['NO_READY_SANDBOX_SAMPLE']
        with self.assertRaisesRegex(ValueError, 'BASELINE_REVIEW_PENDING'):
            b.validate_baseline(b.canonical(value), BEFORE, {'status':'arbitrary'})

    def test_unbound_host_refuses_before_any_reads_or_actions(self):
        with patch.object(sys, 'argv', ['tool', '--update-research-fund-skill-once']), patch.object(host, 'command') as command, patch.object(host, 'verify_prior_release') as read, contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(host.main(), 1)
        report = json.loads(output.getvalue())
        self.assertEqual(report['error_code'], 'UNBOUND_TEMPLATE_NO_PRODUCTION_ACTION')
        self.assertEqual(report['read_and_maintenance_commands'], 0)
        self.assertFalse(report['once_claimed']); command.assert_not_called(); read.assert_not_called()
        self.assertEqual(report['schema'], 'portal-research-fund-adjusted-budget-update.v1')
        self.assertEqual(report['tool_revision'], '20261110-r1')

    def test_new_once_and_backup_paths_never_reuse_old_release(self):
        self.assertEqual(str(host.REPORT_ROOT).replace('\\', '/'), '/var/lib/portal-research-fund-skill-update-adjusted-budget-20261110-r1')
        source = (RELEASE/'adjusted_budget_remote.py').read_text(encoding='utf8')
        self.assertIn('adjusted-budget-20261110-r1', source)
        self.assertNotIn('report-labels-20261106-r1', source)
        self.assertIs(remote.QUALIFIER_SOURCE, None)
        self.assertIs(remote.QUALIFIER_EXPECTED, None)

    def test_python37_host_remote_and_readonly_syntax(self):
        for path in (RELEASE/'adjusted_budget_updater_template.py', RELEASE/'adjusted_budget_remote.py', ROOT/'deploy/enterprise/inspect-portal-research-fund-adjusted-budget-20261110-r1.py'):
            ast.parse(path.read_text(encoding='utf8'), feature_version=(3, 7))

    def test_no_mutation_command_except_exact_maintenance_manifest(self):
        host.AUTH.clear()
        for args in (['patch', 'deployment', 'gateway'], ['delete', 'pod', 'existing-sandbox'], ['create', '-f', '-', '-o', 'json']):
            with self.subTest(args=args), self.assertRaises(host.Refused):
                host.authorize_command(args, b'{}')

    def delayed_publication(self, succeeds):
        clock = [0.0]; rounds = [0]; calls = []
        def sleep(seconds):
            self.assertGreater(seconds, 0); self.assertLessEqual(seconds, 15)
            clock[0] += seconds
        def process(argv, **kwargs):
            self.assertEqual(argv[len(host.BASE):], ['get', 'pod', 'synthetic', '-o', 'json'])
            calls.append(argv)
            return types.SimpleNamespace(returncode=0, stdout=b'{}', stderr=b'')
        def verify(lib, deployment, expected):
            rounds[0] += 1
            for _ in range(12 if succeeds and rounds[0] == 4 else 11):
                host.command(['get', 'pod', 'synthetic', '-o', 'json'])
            if succeeds and rounds[0] == 4:
                return [{'status':'PASS'}]
            raise host.Refused('GATEWAY_SKILL_MANIFEST_CHANGED')
        # Includes conservative normal pre-publication work and a full Pod-ready wait.
        with patch.object(host, 'COMMANDS', 95), patch.object(host, 'STARTED', 0), \
             patch.object(host.time, 'monotonic', side_effect=lambda: clock[0]), \
             patch.object(host.time, 'sleep', side_effect=sleep), \
             patch.object(host.subprocess, 'run', side_effect=process), \
             patch.object(host, 'verify_consumers', side_effect=verify):
            if succeeds:
                self.assertEqual(host.verify_after_publication(None, None, None), [{'status':'PASS'}])
                # Eight final guards, three cleanup base calls and nineteen polling calls.
                for _ in range(30):host.command(['get', 'pod', 'synthetic', '-o', 'json'])
                self.assertEqual(host.COMMANDS, 170)
            else:
                with self.assertRaisesRegex(host.Refused, '^GATEWAY_SKILL_MANIFEST_CHANGED$'):
                    host.verify_after_publication(None, None, None)
                self.assertEqual(host.COMMANDS, 139)
            self.assertEqual(rounds[0], 4); self.assertEqual(clock[0], 45)
        self.assertEqual(len(calls), 75 if succeeds else 44)

    def test_last_node_delayed_then_succeeds_leaves_final_cleanup_budget(self):
        self.delayed_publication(True)

    def test_last_node_stale_all_window_preserves_original_error_not_command_limit(self):
        self.delayed_publication(False)


if __name__ == '__main__':
    unittest.main()
