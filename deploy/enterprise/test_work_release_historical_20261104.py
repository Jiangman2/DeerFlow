"""Refuse undeclared Work merge drift at every historical verification entry."""

import copy
import json
from pathlib import Path
import unittest

from released_source_20261102 import followup, historical_read, load

ROOT = Path(__file__).resolve().parents[2]
ALIGNMENT = followup.alignment


class WorkReleaseHistoricalTests(unittest.TestCase):
    def read(self, path):
        return (ROOT / path).read_bytes()

    def replace(self, target, raw):
        return lambda path: raw if path == target else self.read(path)

    def test_shared_qualification_and_reader_are_idempotent(self):
        manifest = ALIGNMENT.qualify(self.read)
        self.assertEqual(
            set(manifest["changes"]), ALIGNMENT.WORK_RELEASE_ALIGNMENT_PATHS
        )
        reader = ALIGNMENT.historical_read(self.read)
        self.assertIs(ALIGNMENT.historical_read(reader), reader)
        for path, row in manifest["changes"].items():
            self.assertEqual(reader(path), self.read(row["baseline_path"]))
        self.assertEqual(
            reader("backend/app/gateway/routers/model_monitor.py"),
            self.read("backend/app/gateway/routers/model_monitor.py"),
        )
        self.assertEqual(len(load(self.read)[1]["sources"]), 41)
        self.assertEqual(len(load(reader)[1]["sources"]), 41)

    def test_each_current_file_is_enforced_at_all_direct_entries(self):
        for path in ALIGNMENT.WORK_RELEASE_ALIGNMENT_PATHS:
            for entry in (
                load,
                historical_read,
                followup.qualify,
                followup.historical_read,
            ):
                with (
                    self.subTest(path=path, entry=entry.__name__),
                    self.assertRaisesRegex(
                        AssertionError, "WORK_RELEASE_ALIGNMENT_CURRENT_CHANGED"
                    ),
                ):
                    entry(self.replace(path, self.read(path) + b"\n# unsealed edit\n"))

    def test_each_production_parent_snapshot_is_enforced(self):
        for row in ALIGNMENT.qualify(self.read)["changes"].values():
            path = row["baseline_path"]
            with (
                self.subTest(path=path),
                self.assertRaisesRegex(
                    AssertionError, "WORK_RELEASE_ALIGNMENT_BASELINE_CHANGED"
                ),
            ):
                load(self.replace(path, self.read(path) + b"\n# unsealed snapshot\n"))

    def test_all_sealed_parent_manifests_are_enforced(self):
        for path in ALIGNMENT.WORK_PARENT_MANIFESTS:
            with (
                self.subTest(path=path),
                self.assertRaisesRegex(
                    AssertionError, "WORK_RELEASE_ALIGNMENT_PARENT_CHANGED"
                ),
            ):
                load(self.replace(path, self.read(path) + b"\n"))

    def test_unqualified_snapshot_reader_cannot_impersonate_verified_current(self):
        changes = ALIGNMENT.qualify(self.read)["changes"]

        def reader(path):
            return self.read(
                changes[path]["baseline_path"] if path in changes else path
            )

        with self.assertRaisesRegex(
            AssertionError, "WORK_RELEASE_ALIGNMENT_CURRENT_CHANGED"
        ):
            load(reader)

    def test_marker_constructors_reject_unverified_qualifications(self):
        for module in (ALIGNMENT, followup):
            qualification = copy.deepcopy(module.qualify(self.read))
            qualification["unverified"] = True
            with (
                self.subTest(module=module.__name__),
                self.assertRaisesRegex(AssertionError, "READER_CHANGED"),
            ):
                module.HistoricalReader(self.read, qualification)

    def test_existing_markers_recheck_current_bytes_and_their_qualification(self):
        path = sorted(ALIGNMENT.WORK_RELEASE_ALIGNMENT_PATHS)[0]
        for module in (ALIGNMENT, followup):
            for tamper in ("current", "qualification"):
                overrides = {}

                def reader(name):
                    return overrides.get(name, self.read(name))

                verified = module.historical_read(reader)
                if tamper == "current":
                    overrides[path] = self.read(path) + b"\n# late drift\n"
                else:
                    verified.qualification["unverified"] = True
                with (
                    self.subTest(module=module.__name__, tamper=tamper),
                    self.assertRaisesRegex(
                        AssertionError, "CURRENT_CHANGED|READER_CHANGED"
                    ),
                ):
                    module.historical_read(verified)

    def test_self_consistent_manifest_and_parent_replacement_is_not_approved(self):
        manifest = copy.deepcopy(ALIGNMENT.qualify(self.read))
        parent = next(iter(manifest["parent_manifests"]))
        replacement = self.read(parent) + b"\n"
        manifest["parent_manifests"][parent] = followup.digest(replacement)

        def reader(path):
            if path == ALIGNMENT.WORK_RELEASE_ALIGNMENT:
                return json.dumps(manifest).encode()
            return replacement if path == parent else self.read(path)

        with self.assertRaisesRegex(
            AssertionError, "WORK_RELEASE_ALIGNMENT_SEAL_CHANGED"
        ):
            load(reader)

    def test_manifest_removal_cannot_hide_merged_current_files(self):
        def reader(path):
            if path == ALIGNMENT.WORK_RELEASE_ALIGNMENT:
                raise FileNotFoundError(path)
            return self.read(path)

        with self.assertRaisesRegex(
            AssertionError, "WORK_RELEASE_ALIGNMENT_MISSING_MANIFEST"
        ):
            load(reader)

    def test_manifest_scope_protocol_parent_and_hash_refusals(self):
        original = ALIGNMENT.qualify(self.read)
        for tamper in (
            "schema",
            "status",
            "base",
            "unknown",
            "scope",
            "parent",
            "path",
            "hash",
        ):
            manifest = copy.deepcopy(original)
            path = sorted(manifest["changes"])[0]
            if tamper == "schema":
                manifest["schema"] = "unapproved.v1"
            elif tamper == "status":
                manifest["status"] = "DEPLOYED"
            elif tamper == "base":
                manifest["base_commit"] = "0" * 40
            elif tamper == "unknown":
                manifest["skip_verification"] = True
            elif tamper == "scope":
                manifest["changes"]["backend/app/arbitrary.py"] = manifest[
                    "changes"
                ].pop(path)
            elif tamper == "parent":
                manifest["parent_manifests"].pop(
                    next(iter(manifest["parent_manifests"]))
                )
            elif tamper == "path":
                manifest["changes"][path]["baseline_path"] = "../../outside.py"
            else:
                manifest["changes"][path]["candidate_sha256"] = "invalid-hash"
            with (
                self.subTest(tamper=tamper),
                self.assertRaisesRegex(AssertionError, "WORK_RELEASE_ALIGNMENT_"),
            ):
                load(
                    self.replace(
                        ALIGNMENT.WORK_RELEASE_ALIGNMENT, json.dumps(manifest).encode()
                    )
                )

    def test_no_projection_for_an_undeclared_runtime_change(self):
        path = "backend/app/gateway/routers/model_monitor.py"
        with self.assertRaisesRegex(
            AssertionError, "NOV02_RUNTIME_LOGIC_CHANGED|NOV02_CURRENT_SOURCE_CHANGED"
        ):
            load(self.replace(path, self.read(path) + b"\n# unlisted edit\n"))

    def test_hidden_manifest_with_projected_current_and_new_archive_is_rejected(self):
        manifest = ALIGNMENT.qualify(self.read)

        def projected(path):
            if path == ALIGNMENT.WORK_RELEASE_ALIGNMENT:
                raise KeyError(path)
            row = manifest["changes"].get(path)
            return self.read(row["baseline_path"] if row else path)

        for entry in (load, followup.qualify, followup.historical_read):
            with (
                self.subTest(entry=entry.__name__),
                self.assertRaisesRegex(
                    AssertionError, "WORK_RELEASE_ALIGNMENT_MISSING_MANIFEST"
                ),
            ):
                entry(projected)

    def test_missing_optional_manifest_preserves_the_old_reader_contract(self):
        manifest = ALIGNMENT.qualify(self.read)

        def previous(path):
            if path == ALIGNMENT.WORK_RELEASE_ALIGNMENT or path.startswith(
                ALIGNMENT.WORK_RELEASE_ALIGNMENT_ARCHIVE
            ):
                raise KeyError(path)
            row = manifest["changes"].get(path)
            return self.read(row["baseline_path"] if row else path)

        self.assertIs(ALIGNMENT.historical_read(previous), previous)
        self.assertEqual(len(load(previous)[1]["sources"]), 41)


if __name__ == "__main__":
    unittest.main()
