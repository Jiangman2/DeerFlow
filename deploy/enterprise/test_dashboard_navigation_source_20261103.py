"""The candidate cannot bypass historical provenance or drift outside its seal."""

from pathlib import Path
import importlib.util
import io
import tarfile
import subprocess
import unittest
from unittest.mock import patch

import dashboard_navigation_source_20261103 as navigation
import work_integration_source_20261106 as work_followup
from work_integration_source_20261106 import historical_view

ROOT = Path(__file__).resolve().parents[2]


class NavigationSourceTests(unittest.TestCase):
    def setUp(self):
        self.read, _ = historical_view(lambda path: (ROOT / path).read_bytes())

    def changed(self, target):
        return lambda path: (
            self.read(path) + (b"\n// drift\n" if path == target else b"")
        )

    def test_actual_ref_qualifies_work_successors_before_navigation(self):
        path = ROOT / "deploy/enterprise/verify-dashboard-navigation-source-20261103.py"
        spec = importlib.util.spec_from_file_location("dashboard_ref_regression", path)
        verifier = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(verifier)
        ref = (
            subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT)
            .decode()
            .strip()
        )
        actual = verifier.verify(ref)
        working = verifier.verify()
        self.assertEqual(actual["status"], working["status"])
        self.assertEqual(
            actual["changed_paths_verified"], working["changed_paths_verified"]
        )
        self.assertEqual(
            actual["work_integration_followup"], working["work_integration_followup"]
        )
        self.assertIsNotNone(
            actual["work_integration_followup"]["work_cloud_routing"][
                "work_search_candidates"
            ]
        )
        self.assertFalse(actual["production_commands_executed"])

    def test_all_work_ref_readers_archive_every_successor_input(self):
        class ArchiveReached(Exception):
            pass

        required = {
            work_followup.cloud.ARCHIVE,
            work_followup.cloud.search.ARCHIVE,
            *work_followup.cloud.PARENT_MANIFESTS,
            *work_followup.cloud.search.PARENT_MANIFESTS,
            *work_followup.cloud.DOCUMENTATION_PATHS,
            *work_followup.cloud.search.DOCUMENTATION_PATHS,
            "backend/tests",
        }
        for filename in [
            "verify-dashboard-navigation-source-20261103.py",
            "verify-release-source-20261023.py",
            "verify-nov01-released-source-20261102.py",
            "verify-nov02-released-source-20261103.py",
        ]:
            with self.subTest(filename=filename):
                spec = importlib.util.spec_from_file_location(
                    "archive_" + filename, ROOT / "deploy/enterprise" / filename
                )
                verifier = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(verifier)

                def git(args, **kwargs):
                    if args[1] == "show":
                        return (ROOT / args[-1].split(":", 1)[1]).read_bytes()
                    if args[1] == "ls-tree":
                        return (args[-1] + "\n").encode()
                    if args[1] == "archive":
                        self.assertTrue(
                            required <= set(args),
                            (filename, sorted(required - set(args))),
                        )
                        raise ArchiveReached
                    raise AssertionError(args)

                with (
                    patch.object(verifier.subprocess, "check_output", side_effect=git),
                    self.assertRaises(ArchiveReached),
                ):
                    verifier.verify("synthetic-ref")

    def test_actual_ref_rejects_payload_drift_and_missing_successor_seals(self):
        spec = importlib.util.spec_from_file_location(
            "dashboard_ref_tamper",
            ROOT / "deploy/enterprise/verify-dashboard-navigation-source-20261103.py",
        )
        verifier = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(verifier)
        ref = (
            subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT)
            .decode()
            .strip()
        )
        native = subprocess.check_output
        cases = [
            (
                "backend/app/integrations/work/file_search.py",
                False,
                "WORK_SEARCH_CANDIDATES_CURRENT_CHANGED",
            ),
            (
                "backend/tests/test_work_search_cards.py",
                False,
                "WORK_SEARCH_CANDIDATES_CURRENT_CHANGED",
            ),
            ("backend/README.md", False, "WORK_SEARCH_CANDIDATES_CURRENT_CHANGED"),
            (
                work_followup.cloud.search.MANIFEST,
                False,
                "WORK_SEARCH_CANDIDATES_SEAL_CHANGED",
            ),
            (
                work_followup.cloud.search.MANIFEST,
                True,
                "WORK_SEARCH_CANDIDATES_MANIFEST_REQUIRED",
            ),
            (
                work_followup.cloud.MANIFEST,
                False,
                "WORK_SEARCH_CANDIDATES_PARENT_MANIFEST_CHANGED",
            ),
            (
                work_followup.cloud.MANIFEST,
                True,
                "WORK_SEARCH_CANDIDATES_PARENT_MANIFEST_REQUIRED",
            ),
        ]
        for target, remove, error in cases:
            with self.subTest(target=target, remove=remove):

                def altered(args, **kwargs):
                    raw = native(args, **kwargs)
                    if args[1] != "archive":
                        return raw
                    output = io.BytesIO()
                    found = False
                    with (
                        tarfile.open(fileobj=io.BytesIO(raw)) as original,
                        tarfile.open(fileobj=output, mode="w") as archive,
                    ):
                        for member in original:
                            payload = (
                                original.extractfile(member).read()
                                if member.isfile()
                                else None
                            )
                            if member.name == target:
                                found = True
                                if remove:
                                    continue
                                payload += b"\n# synthetic drift\n"
                                member.size = len(payload)
                            archive.addfile(
                                member,
                                io.BytesIO(payload) if payload is not None else None,
                            )
                    self.assertTrue(found, target)
                    return output.getvalue()

                with (
                    patch.object(
                        verifier.subprocess, "check_output", side_effect=altered
                    ),
                    self.assertRaisesRegex(AssertionError, error),
                ):
                    verifier.verify(ref)

    def test_candidate_is_not_a_production_receipt(self):
        manifest = navigation.qualify(self.read)
        self.assertEqual(
            len(navigation.summary(manifest)["frontend_runtime_paths"]), 10
        )
        self.assertFalse(manifest["production_acceptance_claimed"])
        self.assertFalse(manifest["gateway_modified"])
        self.assertFalse(manifest["database_modified"])

    def test_every_runtime_and_test_file_is_bound(self):
        for path in navigation.qualify(self.read)["changes"]:
            with (
                self.subTest(path=path),
                self.assertRaisesRegex(
                    AssertionError, "DASHBOARD_NAVIGATION_CURRENT_CHANGED"
                ),
            ):
                navigation.qualify(self.changed(path))

    def test_every_baseline_is_bound(self):
        for row in navigation.qualify(self.read)["changes"].values():
            if row["baseline_path"]:
                with (
                    self.subTest(path=row["baseline_path"]),
                    self.assertRaisesRegex(
                        AssertionError, "DASHBOARD_NAVIGATION_BASELINE_CHANGED"
                    ),
                ):
                    navigation.qualify(self.changed(row["baseline_path"]))

    def test_seal_changes_are_rejected(self):
        with self.assertRaisesRegex(
            AssertionError, "DASHBOARD_NAVIGATION_SEAL_CHANGED"
        ):
            navigation.qualify(self.changed(navigation.MANIFEST))

    def test_projection_matches_exact_enterprise_parent(self):
        manifest = navigation.qualify(self.read)
        reader = navigation.historical_read(self.read)
        self.assertIs(navigation.historical_read(reader), reader)
        for path, row in manifest["changes"].items():
            with self.subTest(path=path):
                result = subprocess.run(
                    ["git", "show", navigation.BASE_COMMIT + ":" + path],
                    cwd=ROOT,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                )
                if row["baseline_path"] is None:
                    self.assertNotEqual(result.returncode, 0)
                    with self.assertRaises(KeyError):
                        reader(path)
                else:
                    self.assertEqual(result.returncode, 0)
                    self.assertEqual(reader(path), result.stdout)

    def test_other_sources_pass_through_unchanged(self):
        path = "backend/app/gateway/routers/model_monitor.py"
        self.assertEqual(navigation.historical_read(self.read)(path), self.read(path))


if __name__ == "__main__":
    unittest.main()
