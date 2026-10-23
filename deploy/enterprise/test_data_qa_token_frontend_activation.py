"""Actual receipt-bound package against simulated cluster responses; no cluster access."""
import ast
import copy
import json
import tempfile
import types
import unittest
from pathlib import Path

import test_data_qa_progress_frontend_activation as base

HERE = Path(__file__).resolve().parent
PACKAGE = HERE / 'data-qa-token-frontend-package-20261022-r1.json'
ASSETS = HERE / 'data-qa-token-frontend-assets-20261022-r1.json'
BACKEND = HERE / 'oa-feature-activation-field-20261023-r1.json'
PRELOADS = HERE / 'data-qa-token-frontend-preloads-field-20261023-r1.json'


class CombinedActivationTests(base.ActivationTests):
    @classmethod
    def setUpClass(cls):
        cls.pack = base.load('package-data-qa-token-frontend-activation.py')
        cls.package = json.loads(PACKAGE.read_text(encoding='utf-8'))
        cls.assets = json.loads(ASSETS.read_text(encoding='utf-8'))
        cls.host = cls.pack.module_for(cls.pack.template(cls.package))
        cls.proven = cls.pack.prior.prior_common(base.PRIOR)
        cls.backend_source = base.load('package-oa-feature-activation.py').build()
        cls.backend_pin, unused = cls.pack.prior.backend_evidence(BACKEND)
        cls.preloads = {r['hostname']: r for r in json.loads(PRELOADS.read_text(encoding='utf-8'))['receipts']}
        cls.source = cls.pack.build(PACKAGE, ASSETS, PRELOADS, BACKEND, base.PRIOR)
        cls.mod = types.ModuleType('combined_offline_test_only')
        exec(compile(cls.source, '<combined-package-test>', 'exec'), cls.mod.__dict__)

    def test_actual_preloads_pass_and_are_embedded_exactly(self):
        self.mod.validate_preloads(self.preloads)
        self.assertEqual(self.mod.PRELOADS, self.preloads)
        self.assertEqual(self.mod.BACKEND, self.backend_pin)
        self.assertEqual(self.mod.SOURCE_PINS, self.assets['source_pins'])
        self.assertEqual(self.mod.MANIFEST, self.package['manifest'])
        self.assertEqual(self.mod.CONFIG, self.package['config'])
        self.assertIn('--activate-frontend-data-qa-token-once', self.source)
        self.assertNotIn('--activate-frontend-data-qa-progress-once', self.source)
        self.assertEqual(self.mod.ACCEPTED, 'FRONTEND_DATA_QA_TOKEN_RUNTIME_ACCEPTED_BROWSER_PENDING')
        self.assertIn('data-qa-token', self.mod.ONCE)

    def test_unknown_or_failed_backend_refuses_packaging_without_output(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / 'missing.json'
            with self.assertRaisesRegex(ValueError, 'accepted_gateway_receipt_not_yet_available'):
                self.pack.build(PACKAGE, ASSETS, missing, missing, base.PRIOR)
            bad = Path(directory) / 'failed.json'
            bad.write_text(json.dumps({'status': 'INCOMPLETE'}), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'accepted_gateway_receipt_required'):
                self.pack.build(PACKAGE, ASSETS, missing, bad, base.PRIOR)
            self.assertEqual([p.name for p in Path(directory).iterdir()], ['failed.json'])

    def test_superseded_candidate_cannot_be_packaged_even_with_backend_receipt(self):
        with self.assertRaisesRegex(ValueError, 'candidate_superseded'):
            self.pack.prior.build(HERE / 'data-qa-progress-frontend-preloads-field-20261022-r2.json', BACKEND, base.PRIOR)

    def test_native_proven_functions_are_ast_identical(self):
        original = ast.parse(base.PRIOR.read_text(encoding='utf-8'))
        selected = ast.parse(self.proven)
        for name in self.pack.prior.PRIOR_NAMES:
            a = next(n for n in original.body if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name == name)
            b = next(n for n in selected.body if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name == name)
            self.assertEqual(ast.dump(a), ast.dump(b), name)
        ast.parse(self.source, feature_version=(3, 7))

    def test_actual_gateway_summary_bound_to_full_private_report(self):
        self.assertEqual(self.backend_pin['bytes'], 302782)
        self.assertEqual(self.backend_pin['sha256'], '9748159776fc431cdbaedb21da8c96eda68534532bf5a97581d03a8dd51669b0')
        self.assertEqual(self.mod.BACKEND, self.backend_pin)

    def test_new_preloads_must_arrive_before_operator_packaging(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, 'new_frontend_preload_receipts_required'):
                self.pack.build(PACKAGE, ASSETS, Path(directory) / 'missing.json', BACKEND, base.PRIOR)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_old_actual_preloads_cannot_authorize_new_candidate(self):
        old_path = HERE / 'data-qa-progress-frontend-preloads-field-20261022-r2.json'
        with self.assertRaisesRegex(ValueError, 'combined_preload_evidence_required'):
            self.pack.build(PACKAGE, ASSETS, old_path, BACKEND, base.PRIOR)
        old, unused = self.pack.prior.preload_evidence(old_path)
        with self.assertRaises(self.mod.Refused):
            self.mod.validate_preloads(old)

    def test_compiled_features_must_belong_to_combined_image(self):
        bad = copy.deepcopy(self.assets)
        bad['image'] = self.mod.OLD
        with self.assertRaisesRegex(ValueError, 'compiled_assets_required'):
            self.pack.assemble(self.package, bad, self.preloads, self.backend_pin, self.proven, self.backend_source, {})
        bad = copy.deepcopy(self.assets)
        del bad['feature_markers']['token_pending']
        with self.assertRaisesRegex(ValueError, 'compiled_features_missing'):
            self.pack.assemble(self.package, bad, self.preloads, self.backend_pin, self.proven, self.backend_source, {})

    def test_generated_host_and_helpers_have_python37_syntax(self):
        # The packager uses the local modern Python; the emitted operator script
        # and its embedded host helpers must remain Python 3.7-compatible.
        for source in (self.source, self.proven, self.backend_source):
            ast.parse(source, feature_version=(3, 7))
        self.assertEqual(self.mod.PROVENANCE['preload_summaries'], self.pack.pin(PRELOADS.read_bytes()))

    def test_actual_receipts_generate_only_the_qualified_combined_candidate(self):
        source = self.pack.build(PACKAGE, ASSETS, PRELOADS, BACKEND, base.PRIOR)
        module = self.pack.module_for(source)
        module.validate_preloads(module.PRELOADS)
        self.assertEqual(module.NEW, 'deerflow.local/deer-flow-frontend@' + self.package['manifest'])
        self.assertEqual({r['report_bytes'] for r in module.PRELOADS.values()}, {61396, 55785})
        self.assertEqual(module.PRELOADS['datax-02']['report_sha256'], '1bb0fce747b5b2236928926380de8a56def6b8fba605c791bd10d2be72ce50fd')
        self.assertEqual(module.PRELOADS['ecs-zdz-pro-0015']['report_sha256'], 'a1dd0a727ab5d24838295d4237c58dd627f7b9a14112d5a1e0139e0b0ff5a6ca')
        self.assertEqual(module.BACKEND, self.backend_pin)
        self.assertEqual(module.PROVENANCE['preload_summaries'], self.pack.pin(PRELOADS.read_bytes()))
        self.assertEqual(module.SOURCE_PINS, self.assets['source_pins'])
        front, helper, backend = module.load_modules()
        self.assertEqual(front.NEW, module.NEW)
        self.assertEqual(backend.EXPECTED['ppt-master-runner']['generation'], 55)
        self.assertEqual(module.FRONT['generation'], 225)

    def test_pasted_summary_does_not_replace_full_server_report(self):
        summary = json.loads(BACKEND.read_text(encoding='utf-8'))['observation']
        with self.assertRaises(self.mod.Refused):
            self.mod.backend_gate(summary, self.backend)


if __name__ == '__main__':
    unittest.main(verbosity=2)
