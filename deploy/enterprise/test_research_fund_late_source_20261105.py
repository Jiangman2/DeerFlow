"""Offline rejection tests for the two accepted November 5 successors."""
import copy
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    'finance_verifier', ROOT / 'deploy/enterprise/verify-research-fund-production-source.py'
)
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)
source = verifier.later


class LateSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.paths = [p.relative_to(ROOT).as_posix() for p in (ROOT / source.SKILL).rglob('*')
                     if p.is_file() and '__pycache__' not in p.parts]
        _, cls.parent_projection = verifier.current.qualify(lambda path: (ROOT/path).read_bytes(), cls.paths)
        if cls.parent_projection:
            cls.paths = [p for p in cls.parent_projection if p.startswith(source.SKILL)]
        cls.manifest = json.loads((ROOT / source.MANIFEST).read_bytes())

    def reader(self, overrides):
        def read(path):
            if path in overrides:
                if overrides[path] is None:
                    raise KeyError(path)
                return overrides[path]
            if path == verifier.current.MANIFEST:
                raise KeyError(path)
            if self.parent_projection and path in self.parent_projection:
                return self.parent_projection[path]
            return (ROOT / path).read_bytes()
        return read

    def qualify(self, overrides=None, paths=None):
        return source.qualify(self.reader(overrides or {}), self.paths if paths is None else paths)

    def resealed(self, changes=None, mutate=None):
        changes = changes or {}
        manifest = copy.deepcopy(self.manifest)
        for path, raw in changes.items():
            for key in ('evidence_files', 'support_files'):
                if path in manifest[key]:
                    manifest[key][path] = {'bytes': len(raw), 'sha256': source.sha(raw)}
        if mutate:
            mutate(manifest)
        raw = json.dumps(manifest).encode()
        with patch.object(source, 'MANIFEST_SHA256', source.sha(raw)):
            return self.qualify({**changes, source.MANIFEST: raw})

    def test_full_chain_preserves_historical_verification(self):
        with patch.object(verifier.subprocess, 'run', side_effect=AssertionError('no processes')):
            result = verifier.verify(self.reader({}), self.paths)
        self.assertEqual(result['tree_sha256'], source.AFTER)
        self.assertEqual(result['intermediate_tree_sha256'], source.MIDDLE)
        self.assertEqual(result['skill_files'], 30)
        self.assertEqual(result['historical_skill_files'], 29)
        self.assertTrue(result['business_acceptance_pending'])
        self.assertFalse(result['production_commands_executed'])

    def test_latest_bytes_cannot_hide_successor_manifest(self):
        with self.assertRaises((ValueError, AssertionError)):
            verifier.verify(self.reader({source.MANIFEST: None}), self.paths)

    def test_changed_runtime_rejected(self):
        path = source.SKILL + 'scripts/group_template.py'
        with self.assertRaisesRegex(ValueError, 'LATE_SOURCE_RUNTIME_CHANGED'):
            self.qualify({path: (ROOT / path).read_bytes() + b'\n# change\n'})

    def test_extra_and_missing_runtime_files_rejected(self):
        for paths in (self.paths[1:], self.paths + [source.SKILL + 'extra.py']):
            with self.subTest(paths=len(paths)), self.assertRaisesRegex(ValueError, 'LATE_SOURCE_FILE_SET_CHANGED'):
                self.qualify(paths=paths)

    def test_source_seal_and_parent_seal_rejected(self):
        for path, code in ((source.MANIFEST, 'LATE_SOURCE_SEAL_CHANGED'),
                           (source.PARENT_SOURCE, 'LATE_SOURCE_PARENT_CHANGED')):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, code):
                self.qualify({path: (ROOT / path).read_bytes() + b' '})

    def test_all_evidence_and_support_bytes_bound(self):
        for key in ('evidence_files', 'support_files'):
            for path in self.manifest[key]:
                with self.subTest(path=path), self.assertRaisesRegex(ValueError, 'LATE_SOURCE_FILE_CHANGED'):
                    self.qualify({path: b'changed'})

    def test_parent_snapshots_bound(self):
        for row in self.manifest['parent_snapshots'].values():
            with self.subTest(path=row['snapshot']), self.assertRaisesRegex(ValueError, 'LATE_SOURCE_FILE_CHANGED'):
                self.qualify({row['snapshot']: b'changed'})

    def test_acceptance_and_execution_overclaims_rejected(self):
        for key, value in (('business_acceptance_pending', False), ('production_commands_executed_by_verifier', True)):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'LATE_SOURCE_ACCEPTANCE_OVERCLAIM'):
                self.resealed(mutate=lambda m: m.update({key: value}))

    def test_support_registration_required(self):
        with self.assertRaisesRegex(ValueError, 'LATE_SOURCE_SUPPORT_SET_CHANGED'):
            self.resealed(mutate=lambda m: m['support_files'].pop(next(iter(source.SUPPORT_PATHS))))

    def test_evidence_scope_cannot_expand_to_runtime(self):
        with self.assertRaisesRegex(ValueError, 'LATE_SOURCE_EVIDENCE_SCOPE'):
            self.resealed(mutate=lambda m: m['evidence_files'].update({'backend/app/gateway/app.py': {}}))

    def test_receipt_registration_required(self):
        with self.assertRaisesRegex(ValueError, 'LATE_SOURCE_RECEIPT_REGISTRATION_MISSING'):
            self.resealed(mutate=lambda m: m['evidence_files'].pop(source.ROUND3 + 'operator-update-20261105.json'))

    def test_receipt_semantics_checked_after_seal(self):
        mutations = [
            ('LATE_SOURCE_RUNTIME_NOT_ACCEPTED', lambda r: r.update(maintenance_pod_absent=False)),
            ('LATE_SOURCE_RECEIPT_TREE_CHANGED', lambda r: r.update(after_tree_sha256=source.BEFORE)),
            ('LATE_SOURCE_ACCEPTANCE_OVERCLAIM', lambda r: r.update(browser_business_acceptance_pending=False)),
            ('LATE_SOURCE_ACCEPTANCE_OVERCLAIM', lambda r: r.update(provider_requests=1)),
            ('LATE_SOURCE_DEPLOYMENT_SCOPE', lambda r: r.update(checked_deployments_unchanged=[])),
            ('LATE_SOURCE_NODE_COVERAGE', lambda r: r['after_consumers'].pop()),
            ('LATE_SOURCE_PUBLICATION_CHANGED', lambda r: r['publication'].update(backup_verified=False)),
            ('LATE_SOURCE_TEST_RECEIPT_CHANGED', lambda r: r['staging']['synthetic_checks'][-1].update(passed=0)),
        ]
        for directory in (source.ROUND3, source.PROJECT_TYPE):
            path = directory + 'operator-update-20261105.json'
            for code, mutate in mutations:
                receipt = json.loads((ROOT / path).read_bytes())
                mutate(receipt)
                with self.subTest(directory=directory, code=code), self.assertRaisesRegex(ValueError, code):
                    self.resealed({path: json.dumps(receipt).encode()})

    def test_candidate_must_match_embedded_package(self):
        path = source.PROJECT_TYPE + 'candidate-manifest.json'
        with self.assertRaisesRegex(ValueError, 'LATE_SOURCE_CANDIDATE_CHANGED'):
            self.resealed({path: b'{}'})

    def test_snapshot_cannot_reference_runtime(self):
        with self.assertRaisesRegex(ValueError, 'LATE_SOURCE_SNAPSHOT_SCOPE'):
            self.resealed(mutate=lambda m: next(iter(m['parent_snapshots'].values())).update(snapshot='backend/app.py'))

    def test_original_evidence_remains_authoritative(self):
        path = verifier.successor.NOV04 + 'production-update-20261104.json'
        with self.assertRaisesRegex(ValueError, 'FINANCE_EVIDENCE_CHANGED'):
            verifier.verify(self.reader({path: b'{}'}), self.paths)


if __name__ == '__main__':
    unittest.main()
