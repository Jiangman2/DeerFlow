"""Reject incomplete, unregistered or overclaimed history-composer successors."""
import copy
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

from released_source_20261102 import composer as source, load

ROOT = Path(__file__).resolve().parents[2]


class HistoryComposerSourceTests(unittest.TestCase):
    def read(self, path):
        return (ROOT / path).read_bytes()

    def replace(self, target, value):
        def read(path):
            if path == target:
                if value is None:
                    raise KeyError(path)
                return value
            return self.read(path)
        return read

    def changed(self, path):
        return self.replace(path, self.read(path) + b"\n// drift\n")

    def resealed(self, mutate):
        manifest = copy.deepcopy(source.qualify(self.read))
        mutate(manifest)
        raw = json.dumps(manifest).encode()
        with patch.object(source, "MANIFEST_SHA256", source.digest(raw)):
            return source.qualify(self.replace(source.MANIFEST, raw))

    def test_candidate_is_scoped_and_not_deployed(self):
        manifest = source.qualify(self.read)
        self.assertEqual(set(manifest["changes"]), source.PATHS)
        summary = source.summary(manifest)
        self.assertEqual((summary["runtime_sources_verified"], summary["tests_verified"]), (3, 2))
        self.assertEqual(summary["status"], "NOT_DEPLOYED")
        self.assertFalse(summary["production_acceptance_claimed"])
        self.assertEqual(len(load(self.read)[1]["sources"]), 41)

    def test_every_current_source_checked_before_historical_projection(self):
        for path in source.PATHS:
            with self.subTest(path=path), self.assertRaisesRegex(AssertionError, "HISTORY_COMPOSER_CURRENT_CHANGED"):
                load(self.changed(path))

    def test_every_current_source_is_required(self):
        for path in source.PATHS:
            with self.subTest(path=path), self.assertRaises((KeyError, FileNotFoundError)):
                source.qualify(self.replace(path, None))

    def test_baselines_are_exact_merged_enterprise_parent(self):
        reader = source.historical_read(self.read)
        for path in source.EXISTING:
            expected = subprocess.check_output(["git", "show", source.BASE_COMMIT + ":" + path], cwd=ROOT)
            self.assertEqual(reader(path), expected)

    def test_every_baseline_is_checked(self):
        for path in source.EXISTING:
            target = source.ARCHIVE + "baseline/" + path
            with self.subTest(path=path), self.assertRaisesRegex(AssertionError, "HISTORY_COMPOSER_BASELINE_CHANGED"):
                source.historical_read(self.changed(target))

    def test_manifest_is_sealed_and_required(self):
        with self.assertRaisesRegex(AssertionError, "HISTORY_COMPOSER_SEAL_CHANGED"):
            source.qualify(self.changed(source.MANIFEST))
        with self.assertRaisesRegex(AssertionError, "HISTORY_COMPOSER_MANIFEST_REQUIRED"):
            source.qualify(self.replace(source.MANIFEST, None))

    def test_each_partial_archive_requires_manifest(self):
        for path in (*source.NEW, *(source.ARCHIVE + "baseline/" + p for p in source.EXISTING)):
            with self.subTest(path=path), self.assertRaisesRegex(AssertionError, "HISTORY_COMPOSER_MANIFEST_REQUIRED"):
                source.qualify({path: b"partial"}.__getitem__)

    def test_historical_refs_without_candidate_are_unchanged(self):
        read = {}.__getitem__
        self.assertEqual(source.qualify(read), {})
        self.assertIs(source.historical_read(read), read)

    def test_projection_is_idempotent_and_rechecks_live_data(self):
        values = {}
        reader = source.historical_read(lambda path: values.get(path, self.read(path)))
        self.assertIs(source.historical_read(reader), reader)
        for path in (*source.NEW, source.MANIFEST):
            with self.assertRaises(KeyError):
                reader(path)
        nested = lambda path: reader(path)
        self.assertIs(source.historical_read(nested), nested)
        values[source.HOOK] = self.read(source.HOOK) + b"\n// drift"
        with self.assertRaisesRegex(AssertionError, "HISTORY_COMPOSER_CURRENT_CHANGED"):
            source.historical_read(reader)

    def test_forged_reader_cannot_expand_scope(self):
        qualification = source.qualify(self.read)
        qualification["changes"]["backend/app/gateway/app.py"] = {}
        with self.assertRaisesRegex(AssertionError, "HISTORY_COMPOSER_READER_CHANGED"):
            source.HistoricalReader(self.read, qualification)

    def test_resealed_scope_parent_and_new_paths_remain_fixed(self):
        for mutate, reason in (
            (lambda m: m["changes"].pop(source.HOOK), "HISTORY_COMPOSER_SCOPE"),
            (lambda m: m.update(base_commit="0" * 40), "HISTORY_COMPOSER_PARENT"),
            (lambda m: m["changes"][source.INPUT].update(baseline_path="../../unsafe"), "HISTORY_COMPOSER_BASELINE_PATH"),
            (lambda m: m["changes"][source.HOOK].update(base_sha256="0" * 64), "HISTORY_COMPOSER_NEW_PARENT"),
        ):
            with self.subTest(reason=reason), self.assertRaisesRegex(AssertionError, reason):
                self.resealed(mutate)

    def test_deployment_claims_are_rejected(self):
        for key in ("production_acceptance_claimed", "runtime_modified_by_registration"):
            with self.subTest(key=key), self.assertRaisesRegex(AssertionError, "HISTORY_COMPOSER_OVERCLAIM"):
                self.resealed(lambda m: m.update({key: True}))
        with self.assertRaisesRegex(AssertionError, "HISTORY_COMPOSER_STATUS"):
            self.resealed(lambda m: m.update(status="DEPLOYED"))

    def test_raw_baseline_cannot_masquerade_as_candidate(self):
        for path in source.EXISTING:
            with self.subTest(path=path), self.assertRaisesRegex(AssertionError, "HISTORY_COMPOSER_CURRENT_CHANGED"):
                source.qualify(self.replace(path, self.read(source.ARCHIVE + "baseline/" + path)))

    def test_unrelated_sources_remain_checked(self):
        path = "backend/app/gateway/routers/model_monitor.py"
        self.assertEqual(source.historical_read(self.read)(path), self.read(path))
        with self.assertRaisesRegex(AssertionError, "NOV02_RUNTIME_LOGIC_CHANGED|NOV02_CURRENT_SOURCE_CHANGED"):
            load(self.changed(path))


if __name__ == "__main__":
    unittest.main()
