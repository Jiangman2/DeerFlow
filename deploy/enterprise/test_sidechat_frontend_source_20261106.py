"""Reject unregistered changes and forged parent projections for Frontend 242."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

import sidechat_frontend_source_20261106 as source
from released_source_20261102 import followup, load

ROOT = Path(__file__).resolve().parents[2]


class SidechatSourceTests(unittest.TestCase):
    def read(self, path):
        return (ROOT / path).read_bytes()

    def replace(self, target, data):
        def read(path):
            if path == target:
                if data is None:
                    raise KeyError(path)
                return data
            return self.read(path)
        return read

    def changed(self, path):
        return self.replace(path, self.read(path) + b'\n// drift\n')

    def resealed(self, change):
        manifest = copy.deepcopy(source.qualify(self.read))
        change(manifest)
        raw = json.dumps(manifest).encode()
        with patch.object(source, 'MANIFEST_SHA256', source.digest(raw)):
            return source.qualify(self.replace(source.MANIFEST, raw))

    def test_current_and_historical_sources_both_verify(self):
        manifest = source.qualify(self.read)
        self.assertEqual(set(manifest['changes']), source.PATHS)
        self.assertEqual(source.summary(manifest)['runtime_sources_verified'], 1)
        self.assertEqual(len(load(self.read)[1]['sources']), 41)
        projected = followup.historical_read(self.read)
        baseline = self.read(manifest['changes'][source.RUNTIME]['baseline_path'])
        self.assertEqual(projected(source.RUNTIME), baseline)
        self.assertIs(followup.historical_read(projected), projected)

    def test_every_current_source_and_test_is_checked_before_projection(self):
        for path in source.PATHS:
            with self.subTest(path=path), self.assertRaisesRegex(AssertionError, 'SIDECHAT_SOURCE_CURRENT_CHANGED'):
                load(self.changed(path))

    def test_parent_snapshot_is_checked(self):
        path = source.ARCHIVE + 'baseline/' + source.RUNTIME
        with self.assertRaisesRegex(AssertionError, 'SIDECHAT_SOURCE_BASELINE_CHANGED'):
            load(self.changed(path))

    def test_manifest_cannot_be_changed_or_removed(self):
        with self.assertRaisesRegex(AssertionError, 'SIDECHAT_SOURCE_SEAL_CHANGED'):
            load(self.changed(source.MANIFEST))
        with self.assertRaisesRegex(AssertionError, 'SIDECHAT_SOURCE_MANIFEST_REQUIRED'):
            load(self.replace(source.MANIFEST, None))

    def test_build_evidence_is_checked(self):
        for path in source.EVIDENCE_PATHS:
            with self.subTest(path=path), self.assertRaisesRegex(AssertionError, 'SIDECHAT_SOURCE_BUILD_EVIDENCE_CHANGED'):
                load(self.changed(path))

    def test_reader_is_idempotent_and_rechecks_current_data(self):
        values = {}
        reader = source.historical_read(lambda path: values.get(path, self.read(path)))
        self.assertIs(source.historical_read(reader), reader)
        with self.assertRaises(KeyError):
            reader(source.TEST)
        values[source.RUNTIME] = self.read(source.RUNTIME) + b'\n// changed after qualification\n'
        with self.assertRaisesRegex(AssertionError, 'SIDECHAT_SOURCE_CURRENT_CHANGED'):
            source.historical_read(reader)

    def test_forged_reader_cannot_expand_projection(self):
        qualification = source.qualify(self.read)
        qualification['changes']['backend/app/gateway/app.py'] = {}
        with self.assertRaisesRegex(AssertionError, 'SIDECHAT_SOURCE_READER_CHANGED'):
            source.HistoricalReader(self.read, qualification)

    def test_scope_cannot_expand_or_shrink_even_if_manifest_is_resealed(self):
        for mutate in (lambda m: m['changes'].update({'backend/app/gateway/app.py': {}}),
                       lambda m: m['changes'].pop(source.TEST)):
            with self.assertRaisesRegex(AssertionError, 'SIDECHAT_SOURCE_SCOPE'):
                self.resealed(mutate)

    def test_parent_path_cannot_escape_fixed_archive(self):
        for target in ('../../app.py', '/tmp/app.py', source.RUNTIME):
            with self.subTest(target=target), self.assertRaisesRegex(AssertionError, 'SIDECHAT_SOURCE_BASELINE_PATH'):
                self.resealed(lambda m: m['changes'][source.RUNTIME].update(baseline_path=target))

    def test_new_test_cannot_invent_parent(self):
        with self.assertRaisesRegex(AssertionError, 'SIDECHAT_SOURCE_NEW_TEST_PARENT'):
            self.resealed(lambda m: m['changes'][source.TEST].update(base_sha256='0' * 64))

    def test_no_fresh_deployment_or_registration_mutation_claim(self):
        for key in ('fresh_production_acceptance_claimed', 'runtime_modified_by_registration'):
            with self.subTest(key=key), self.assertRaisesRegex(AssertionError, 'SIDECHAT_SOURCE_OVERCLAIM'):
                self.resealed(lambda m: m.update({key: True}))

    def test_image_parent_and_evidence_scope_remain_fixed(self):
        for mutate, reason in (
            (lambda m: m.update(frontend_image='sha256:' + '0' * 64), 'SIDECHAT_SOURCE_IMAGE'),
            (lambda m: m.update(base_commit='0' * 40), 'SIDECHAT_SOURCE_PARENT'),
            (lambda m: m['build_evidence_sha256_lf'].pop(source.EVIDENCE_PATHS[0]), 'SIDECHAT_SOURCE_EVIDENCE_SCOPE'),
        ):
            with self.assertRaisesRegex(AssertionError, reason):
                self.resealed(mutate)

    def test_unrelated_runtime_still_passes_through_and_is_checked(self):
        path = 'backend/app/gateway/routers/model_monitor.py'
        self.assertEqual(source.historical_read(self.read)(path), self.read(path))
        with self.assertRaisesRegex(AssertionError, 'NOV02_RUNTIME_LOGIC_CHANGED|NOV02_CURRENT_SOURCE_CHANGED'):
            load(self.changed(path))

    def test_pre_registration_parent_is_not_mislabelled_as_successor(self):
        manifest = source.qualify(self.read)
        def read(path):
            if path.startswith(source.ARCHIVE) or path == source.TEST:
                raise KeyError(path)
            if path == source.RUNTIME:
                return self.read(manifest['changes'][path]['baseline_path'])
            return self.read(path)
        self.assertFalse(source.qualify(read))
        self.assertIsNone(source.summary(source.qualify(read)))
        self.assertEqual(len(load(read)[1]['sources']), 41)

    def test_parent_snapshot_matches_the_actual_build_base_git_object(self):
        original = subprocess.check_output(['git', 'show', source.BASE_COMMIT + ':' + source.RUNTIME], cwd=ROOT)
        manifest = source.qualify(self.read)
        row = manifest['changes'][source.RUNTIME]
        self.assertEqual(source.digest(original), row['base_sha256'])
        self.assertEqual(original, self.read(row['baseline_path']).replace(b'\r\n', b'\n'))
        self.assertEqual(subprocess.check_output(['git', 'ls-tree', '--name-only', source.BASE_COMMIT, source.TEST], cwd=ROOT), b'')

    def test_standalone_verifier_does_not_accept_missing_registration(self):
        spec = importlib.util.spec_from_file_location('sidechat_verifier', ROOT / 'deploy/enterprise/verify-sidechat-frontend-source-20261106.py')
        verifier = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(verifier)
        self.assertEqual(verifier.verify()['status'], 'SIDECHAT_FRONTEND_SOURCE_VERIFIED')
        with patch.object(source, 'qualify', return_value={}):
            with self.assertRaisesRegex(AssertionError, 'SIDECHAT_SOURCE_MANIFEST_REQUIRED'):
                verifier.verify()


if __name__ == '__main__':
    unittest.main()
