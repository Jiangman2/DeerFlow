"""Reject edits outside the exact default-model source successor."""
import copy
import io
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

import new_chat_model_source_20261105 as source
from released_source_20261102 import followup, load
from work_integration_source_20261106 import historical_view

ROOT = Path(__file__).resolve().parents[2]


class NewChatSourceTests(unittest.TestCase):
    def setUp(self):
        self.read, _ = historical_view(lambda path: (ROOT / path).read_bytes())

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

    def test_current_source_and_historical_release_both_verify(self):
        manifest = source.qualify(self.read)
        self.assertEqual(len(manifest['changes']), 9)
        self.assertEqual(source.summary(manifest)['runtime_sources_verified'], 6)
        self.assertFalse(manifest['production_acceptance_claimed'])
        self.assertEqual(len(load(self.read)[1]['sources']), 41)

    def test_existing_work_projection_is_not_applied_twice(self):
        aligned = followup.alignment.historical_read(self.read)
        self.assertEqual(len(followup.qualify(aligned)['changes']), 18)
        historical = followup.historical_read(aligned)
        self.assertIs(followup.historical_read(historical), historical)
        self.assertEqual(len(load(historical)[1]['sources']), 41)

    def test_every_current_source_test_and_guide_is_checked(self):
        for path in source.PATHS:
            with self.subTest(path=path), self.assertRaisesRegex(AssertionError, 'NEW_CHAT_SOURCE_CURRENT_CHANGED'):
                load(self.changed(path))

    def test_every_parent_snapshot_is_checked(self):
        for path, row in source.qualify(self.read)['changes'].items():
            if row['baseline_path']:
                with self.subTest(path=path), self.assertRaisesRegex(AssertionError, 'NEW_CHAT_SOURCE_BASELINE_CHANGED'):
                    load(self.changed(row['baseline_path']))

    def test_manifest_seal_cannot_change(self):
        with self.assertRaisesRegex(AssertionError, 'NEW_CHAT_SOURCE_SEAL_CHANGED'):
            load(self.changed(source.MANIFEST))

    def test_manifest_cannot_disappear_with_parent_snapshots(self):
        with self.assertRaisesRegex(AssertionError, 'NEW_CHAT_SOURCE_MANIFEST_REQUIRED'):
            load(self.replace(source.MANIFEST, None))

    def test_removing_whole_registration_cannot_hide_current_changes(self):
        def read(path):
            if path.startswith(source.ARCHIVE):
                raise KeyError(path)
            return self.read(path)
        with self.assertRaisesRegex(AssertionError, 'NOV02_PROTECTED_SOURCE_CHANGED'):
            load(read)

    def test_historical_parent_without_registration_still_verifies(self):
        manifest = source.qualify(self.read)
        def read(path):
            if path.startswith(source.ARCHIVE):
                raise KeyError(path)
            row = manifest['changes'].get(path)
            if row:
                if row['baseline_path'] is None:
                    raise KeyError(path)
                return self.read(row['baseline_path'])
            return self.read(path)
        self.assertFalse(source.qualify(read))
        self.assertEqual(len(load(read)[1]['sources']), 41)

    def test_unrelated_runtime_remains_checked(self):
        path = 'backend/app/gateway/routers/model_monitor.py'
        self.assertEqual(source.historical_read(self.read)(path), self.read(path))
        with self.assertRaisesRegex(AssertionError, 'NOV02_RUNTIME_LOGIC_CHANGED|NOV02_CURRENT_SOURCE_CHANGED'):
            load(self.changed(path))

    def test_reader_is_idempotent(self):
        reader = source.historical_read(self.read)
        self.assertIs(source.historical_read(reader), reader)
        for path in source.TESTS:
            with self.assertRaises(KeyError):
                reader(path)

    def test_reader_rechecks_current_data(self):
        values = {}
        reader = source.historical_read(lambda p: values[p] if p in values else self.read(p))
        path = next(iter(source.RUNTIME))
        values[path] = self.read(path) + b'\n// changed later\n'
        with self.assertRaisesRegex(AssertionError, 'NEW_CHAT_SOURCE_CURRENT_CHANGED'):
            source.historical_read(reader)

    def test_forged_projection_is_rejected(self):
        manifest = source.qualify(self.read)
        manifest['changes']['backend/app/gateway/app.py'] = {}
        with self.assertRaisesRegex(AssertionError, 'NEW_CHAT_SOURCE_READER_CHANGED'):
            source.HistoricalReader(self.read, manifest)

    def test_registration_scope_cannot_expand_or_shrink(self):
        for mutate in (lambda m: m['changes'].update({'backend/app/gateway/app.py': {}}),
                       lambda m: m['changes'].pop(next(iter(source.RUNTIME)))):
            with self.assertRaisesRegex(AssertionError, 'NEW_CHAT_SOURCE_SCOPE'):
                self.resealed(mutate)

    def test_acceptance_and_runtime_change_overclaims_rejected(self):
        for key in ('production_acceptance_claimed', 'runtime_modified_by_registration'):
            with self.subTest(key=key), self.assertRaisesRegex(AssertionError, 'NEW_CHAT_SOURCE_OVERCLAIM'):
                self.resealed(lambda m: m.update({key: True}))

    def test_commit_bindings_cannot_change(self):
        for key in ('base_commit', 'source_commit'):
            with self.subTest(key=key), self.assertRaisesRegex(AssertionError, 'NEW_CHAT_SOURCE_COMMITS'):
                self.resealed(lambda m: m.update({key: '0' * 40}))

    def test_parent_path_cannot_escape_archive(self):
        for value in ('../../app.py', '/tmp/app.py', 'frontend/src/core/settings/store.ts'):
            with self.subTest(value=value), self.assertRaisesRegex(AssertionError, 'NEW_CHAT_SOURCE_BASELINE_PATH'):
                self.resealed(lambda m: m['changes'][source.GUIDE].update(baseline_path=value))

    def test_new_tests_do_not_invent_historical_inputs(self):
        with self.assertRaisesRegex(AssertionError, 'NEW_CHAT_SOURCE_NEW_TEST_PARENT'):
            self.resealed(lambda m: m['changes'][next(iter(source.TESTS))].update(base_sha256='0' * 64))

    def test_fixed_git_commit_and_parent_match_every_registered_file(self):
        changed = subprocess.check_output(['git', 'diff-tree', '--no-commit-id', '--name-only', '-r', source.SOURCE_COMMIT], cwd=ROOT).decode().splitlines()
        self.assertEqual(set(changed), source.PATHS)
        manifest = source.qualify(self.read)
        requests = [(source.SOURCE_COMMIT, path, row['current_sha256']) for path, row in manifest['changes'].items()]
        requests += [(source.BASE_COMMIT, path, row['base_sha256']) for path, row in manifest['changes'].items() if row['baseline_path']]
        raw = subprocess.check_output(['git', 'cat-file', '--batch'], cwd=ROOT,
            input=''.join(f'{ref}:{path}\n' for ref, path, _ in requests).encode())
        stream = io.BytesIO(raw)
        for ref, path, expected in requests:
            header = stream.readline().split()
            self.assertEqual(header[1], b'blob')
            data = stream.read(int(header[2]))
            self.assertEqual(stream.read(1), b'\n')
            with self.subTest(ref=ref, path=path):
                self.assertEqual(source.digest(data), expected)
                if ref == source.BASE_COMMIT:
                    self.assertEqual(source.digest(self.read(manifest['changes'][path]['baseline_path'])), expected)


if __name__ == '__main__':
    unittest.main()
