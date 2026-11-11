"""Narrow rejection tests; no app imports, models, network or production access."""

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import subprocess
import sys

import work_search_candidates_source_20261111 as source

MODIFIED = "backend/tests/test_work_file_search_service.py"


class WorkSearchCandidatesSourceTests(unittest.TestCase):
    def fixture(self):
        parents = {path: None for path in source.REQUIRED_PATHS}
        parents[MODIFIED] = b"x = 1\n"
        data = {path: b"x = 2\n" for path in parents}
        data.update(
            {
                path: (source.ROOT / path).read_bytes()
                for path in source.PARENT_MANIFESTS
            }
        )
        manifest = {
            "schema": "work-search-candidates.v1",
            "status": "NOT_DEPLOYED",
            "base_commit": source.BASE_COMMIT,
            "production_acceptance_claimed": False,
            "schema_migration_requested": False,
            "parent_manifests": source.PARENT_MANIFESTS,
            "changes": {
                path: {
                    "base_sha256": source.digest(parent) if parent else None,
                    "current_sha256": source.digest(data[path]),
                }
                for path, parent in parents.items()
            },
        }
        data[source.MANIFEST] = json.dumps(manifest, sort_keys=True).encode()
        return data, parents

    def qualify(self, data, parents, *, reseal=False):
        expected = (
            source.digest(data[source.MANIFEST])
            if reseal
            else source.digest(self.fixture()[0][source.MANIFEST])
        )
        with (
            patch.object(source, "MANIFEST_SHA256", expected),
            patch.object(source, "_parent_sources", return_value=parents),
        ):
            return source.historical_view(data.__getitem__)

    def test_spec_loaded_main_verifier_does_not_require_deploy_pythonpath(self):
        path = source.ROOT / "deploy/enterprise/verify-release-source-20261023.py"
        code = (
            "import importlib.util; "
            f"spec=importlib.util.spec_from_file_location('isolated_verifier',{str(path)!r}); "
            "module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module); "
            "assert callable(module.verify); print('ISOLATED_SPEC_IMPORT_OK')"
        )
        result = subprocess.run(
            [sys.executable, "-I", "-c", code],
            cwd=source.ROOT / "backend",
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "ISOLATED_SPEC_IMPORT_OK")

    def test_historical_ref_archives_include_all_three_parent_manifests(self):
        class ArchiveReached(Exception):
            pass

        for filename in [
            "verify-release-source-20261023.py",
            "verify-nov01-released-source-20261102.py",
            "verify-nov02-released-source-20261103.py",
        ]:
            with self.subTest(filename=filename):
                spec = importlib.util.spec_from_file_location(
                    "archive_" + filename, source.ROOT / "deploy/enterprise" / filename
                )
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)

                def git(args, **kwargs):
                    if args[1] == "show":
                        return (source.ROOT / args[-1].split(":", 1)[1]).read_bytes()
                    if args[1] == "ls-tree":
                        return (args[-1] + "\n").encode()
                    if args[1] == "archive":
                        self.assertTrue(
                            set(source.PARENT_MANIFESTS) <= set(args),
                            (
                                filename,
                                sorted(set(source.PARENT_MANIFESTS) - set(args)),
                            ),
                        )
                        self.assertIn(source.ARCHIVE, args)
                        raise ArchiveReached
                    raise AssertionError(args)

                with (
                    patch.object(module.subprocess, "check_output", side_effect=git),
                    self.assertRaises(ArchiveReached),
                ):
                    module.verify("synthetic-ref")

    def test_verified_parent_projection_and_added_paths(self):
        data, parents = self.fixture()
        data["unrelated"] = b"untouched"
        read, result = self.qualify(data, parents)
        self.assertEqual(read(MODIFIED), parents[MODIFIED])
        self.assertEqual(read("unrelated"), b"untouched")
        with self.assertRaises(KeyError):
            read(source.REQUIRED_PATHS[0])
        self.assertIn(source.REQUIRED_PATHS[0], result["added_gateway_runtime"])
        self.assertFalse(result["production_acceptance_claimed"])
        self.assertFalse(result["schema_migration_requested"])

    def test_explicit_documentation_is_qualified_but_not_counted_as_runtime(self):
        data, parents = self.fixture()
        path = "backend/docs/WORK_SEARCH_CANDIDATES_20261111.md"
        data[path] = b"Candidate only; no deployed acceptance.\n"
        parents[path] = None
        manifest = json.loads(data[source.MANIFEST])
        manifest["changes"][path] = {
            "base_sha256": None,
            "current_sha256": source.digest(data[path]),
        }
        data[source.MANIFEST] = json.dumps(manifest).encode()
        _, result = self.qualify(data, parents, reseal=True)
        self.assertEqual(result["documentation_paths_verified"], 1)
        self.assertNotIn(path, result["added_gateway_runtime"])
        self.assertNotIn(path, result["added_frontend_runtime"])

    def test_cloud_predecessor_qualifies_new_current_before_projecting_shared_docs(
        self,
    ):
        spec = importlib.util.spec_from_file_location(
            "cloud_predecessor",
            source.ROOT / "deploy/enterprise/work_cloud_routing_source_20261111.py",
        )
        cloud = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cloud)
        self.assertEqual(cloud.MANIFEST_SHA256, source.PARENT_MANIFESTS[cloud.MANIFEST])
        data, parents = self.fixture()
        cache = {}

        def parent(path):
            if path not in cache:
                cache[path] = subprocess.check_output(
                    ["git", "show", source.BASE_COMMIT + ":" + path], cwd=source.ROOT
                )
            return cache[path]

        shared = "backend/README.md"
        parents[shared] = parent(shared)
        data[shared] = parents[shared] + b"\nScoped search note.\n"
        manifest = json.loads(data[source.MANIFEST])
        manifest["changes"][shared] = {
            "base_sha256": source.digest(parents[shared]),
            "current_sha256": source.digest(data[shared]),
        }
        data[source.MANIFEST] = json.dumps(manifest).encode()

        def read(path):
            return data[path] if path in data else parent(path)

        with (
            patch.object(
                cloud.search, "MANIFEST_SHA256", source.digest(data[source.MANIFEST])
            ),
            patch.object(cloud.search, "_parent_sources", return_value=parents),
        ):
            historical, result = cloud.historical_view(read)
            self.assertEqual(result["changed_paths_verified"], 8)
            self.assertIsNotNone(result["work_search_candidates"])
            ced_readme = subprocess.check_output(
                ["git", "show", cloud.BASE_COMMIT + ":" + shared], cwd=source.ROOT
            )
            self.assertEqual(historical(shared), ced_readme)
            data[shared] += b"# altered after qualification\n"
            with self.assertRaisesRegex(
                AssertionError, "WORK_SEARCH_CANDIDATES_CURRENT_CHANGED"
            ):
                cloud.historical_view(read)

    def test_cloud_inventory_keeps_shared_changes_and_rejects_unknown_deltas(self):
        spec = importlib.util.spec_from_file_location(
            "cloud_inventory",
            source.ROOT
            / "deploy/enterprise/verify-work-cloud-routing-source-20261111.py",
        )
        verifier = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(verifier)
        shared = "backend/README.md"
        search_only = source.RUNTIME_PATHS[0]
        added = source.DOCUMENTATION_PATHS[-1]
        unknown = "backend/app/unlisted.py"
        parents = {
            shared: b"ced7 doc",
            search_only: b"old file",
            added: None,
            unknown: None,
        }
        projected = {
            shared: b"28b cloud doc",
            search_only: b"old file",
            unknown: b"not declared",
        }
        with patch.object(verifier.source, "_parent_sources", return_value=parents):
            self.assertEqual(
                verifier.effective_parent_delta(projected.__getitem__, set(parents)),
                {shared, unknown},
            )

    def test_current_manifest_and_parent_changes_rejected(self):
        original, _ = self.fixture()
        for path in original:
            with self.subTest(path=path):
                data, parents = self.fixture()
                data[path] += b"\n"
                with self.assertRaises(AssertionError):
                    self.qualify(data, parents)
        data, parents = self.fixture()
        parents[MODIFIED] += b"# changed\n"
        with self.assertRaisesRegex(
            AssertionError, "WORK_SEARCH_CANDIDATES_PARENT_CHANGED"
        ):
            self.qualify(data, parents)

    def test_missing_manifest_cannot_hide_new_files(self):
        data, parents = self.fixture()
        del data[source.MANIFEST]
        data[source.ARCHIVE + "README.md"] = b"Candidate registry exists.\n"
        with self.assertRaisesRegex(
            AssertionError, "WORK_SEARCH_CANDIDATES_MANIFEST_REQUIRED"
        ):
            source.historical_view(data.__getitem__)
        read = {}.__getitem__
        self.assertEqual(source.historical_view(read), (read, None))

    def test_archive_presence_without_manifest_is_rejected(self):
        data = {source.ARCHIVE + "README.md": b"Candidate registry exists.\n"}
        with self.assertRaisesRegex(
            AssertionError, "WORK_SEARCH_CANDIDATES_MANIFEST_REQUIRED"
        ):
            source.historical_view(data.__getitem__)

    def test_missing_declared_source_is_rejected(self):
        data, parents = self.fixture()
        del data[MODIFIED]
        with self.assertRaises(KeyError):
            self.qualify(data, parents)

    def test_resealed_but_invalid_scope_and_authority_rejected(self):
        mutations = [
            ("status", "DEPLOYED"),
            ("base_commit", "0" * 40),
            ("production_acceptance_claimed", True),
            ("schema_migration_requested", True),
            ("schema", "different.v1"),
            ("parent_manifests", {}),
        ]
        for key, value in mutations:
            with self.subTest(key=key):
                data, parents = self.fixture()
                manifest = json.loads(data[source.MANIFEST])
                manifest[key] = value
                data[source.MANIFEST] = json.dumps(manifest).encode()
                with self.assertRaises(AssertionError):
                    self.qualify(data, parents, reseal=True)
        for path in [
            "../escape.py",
            "frontend/../config.ts",
            "frontend/.env",
            "backend/config.yaml",
            "release/old/SOURCE.json",
            "C:/private.py",
            "frontend/src/.env",
            "frontend/src/components/workspace/scheduled-task-work-delivery-status.tsx",
            "backend/app/channels/artifact_delivery.py",
            "backend/app/scheduler/work_notifications.py",
            "backend/packages/harness/deerflow/persistence/migrations/versions/0023_bad.py",
        ]:
            with self.subTest(path=path):
                data, parents = self.fixture()
                manifest = json.loads(data[source.MANIFEST])
                manifest["changes"][path] = {
                    "base_sha256": None,
                    "current_sha256": source.digest(b"x = 2\n"),
                }
                data[path] = b"x = 2\n"
                parents[path] = None
                data[source.MANIFEST] = json.dumps(manifest).encode()
                with self.assertRaisesRegex(
                    AssertionError, "WORK_SEARCH_CANDIDATES_SCOPE"
                ):
                    self.qualify(data, parents, reseal=True)

    def test_reader_marker_does_not_bypass_revalidation(self):
        data, parents = self.fixture()
        fake = source.HistoricalReader(data.__getitem__, {MODIFIED: b"forged"})
        with (
            patch.object(
                source, "MANIFEST_SHA256", source.digest(data[source.MANIFEST])
            ),
            patch.object(source, "_parent_sources", return_value=parents),
        ):
            read, _ = source.historical_view(fake)
            self.assertEqual(read(MODIFIED), parents[MODIFIED])
            data[MODIFIED] += b"# stale\n"
            with self.assertRaisesRegex(
                AssertionError, "WORK_SEARCH_CANDIDATES_CURRENT_CHANGED"
            ):
                source.historical_view(read)

    def test_independently_loaded_helpers_revalidate_original_source(self):
        data, parents = self.fixture()
        modules = []
        for name in ["independent_work_search_a", "independent_work_search_b"]:
            spec = importlib.util.spec_from_file_location(
                name,
                source.ROOT
                / "deploy/enterprise/work_search_candidates_source_20261111.py",
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            modules.append(module)
        a, b = modules
        with (
            patch.object(a, "MANIFEST_SHA256", source.digest(data[source.MANIFEST])),
            patch.object(b, "MANIFEST_SHA256", source.digest(data[source.MANIFEST])),
            patch.object(a, "_parent_sources", return_value=parents),
            patch.object(b, "_parent_sources", return_value=parents) as b_parent,
        ):
            first, _ = a.historical_view(data.__getitem__)
            second, _ = b.historical_view(first)
            b_parent.assert_called_once()
            self.assertEqual(second(MODIFIED), parents[MODIFIED])
            data[MODIFIED] += b"# drift after first qualification\n"
            with self.assertRaisesRegex(
                AssertionError, "WORK_SEARCH_CANDIDATES_CURRENT_CHANGED"
            ):
                b.historical_view(first)

    def test_git_archive_includes_candidate_and_refuses_missing_manifest(self):
        data, parents = self.fixture()
        spec = importlib.util.spec_from_file_location(
            "cloud_verifier",
            source.ROOT
            / "deploy/enterprise/verify-work-search-candidates-source-20261111.py",
        )
        verifier = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(verifier)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def git(*args):
                return subprocess.check_output(
                    ["git", *args], cwd=root, stderr=subprocess.DEVNULL
                )

            git("init", "-q")
            for path, content in data.items():
                target = root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
            for path in [
                "backend/app/synthetic.py",
                "backend/packages/harness/synthetic.py",
                "backend/tests/test_synthetic.py",
                "frontend/src/synthetic.ts",
                "frontend/tests/synthetic.test.ts",
            ]:
                target = root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"# fixture only\n")
            git("add", ".")
            git(
                "-c",
                "user.name=Synthetic",
                "-c",
                "user.email=synthetic@example.invalid",
                "commit",
                "-qm",
                "synthetic candidate",
            )
            with (
                patch.object(verifier, "ROOT", root),
                patch.object(
                    source, "MANIFEST_SHA256", source.digest(data[source.MANIFEST])
                ),
                patch.object(source, "_parent_sources", return_value=parents),
            ):
                read, result = source.historical_view(verifier.snapshot("HEAD"))
                self.assertEqual(result["changed_paths_verified"], len(parents))
                self.assertEqual(read(MODIFIED), parents[MODIFIED])
                (root / source.MANIFEST).unlink()
                (root / source.ARCHIVE / "README.md").write_bytes(
                    b"Candidate registry exists.\n"
                )
                git("add", ".")
                git(
                    "-c",
                    "user.name=Synthetic",
                    "-c",
                    "user.email=synthetic@example.invalid",
                    "commit",
                    "-qm",
                    "missing seal",
                )
                with self.assertRaisesRegex(
                    AssertionError, "WORK_SEARCH_CANDIDATES_MANIFEST_REQUIRED"
                ):
                    source.historical_view(verifier.snapshot("HEAD"))
                # A directory/README is also evidence of the new layer, even
                # if a ref hides the new product test along with its seal.
                (root / source.REQUIRED_PATHS[0]).unlink()
                archive_readme = root / source.ARCHIVE / "README.md"
                archive_readme.parent.mkdir(parents=True, exist_ok=True)
                archive_readme.write_bytes(b"Candidate registry exists.\n")
                git("add", ".")
                git(
                    "-c",
                    "user.name=Synthetic",
                    "-c",
                    "user.email=synthetic@example.invalid",
                    "commit",
                    "-qm",
                    "archive without seal",
                )
                with self.assertRaisesRegex(
                    AssertionError, "WORK_SEARCH_CANDIDATES_MANIFEST_REQUIRED"
                ):
                    source.historical_view(verifier.snapshot("HEAD"))

    def test_old_git_ref_without_new_layers_is_compatible(self):
        spec = importlib.util.spec_from_file_location(
            "legacy_cloud_verifier",
            source.ROOT
            / "deploy/enterprise/verify-work-search-candidates-source-20261111.py",
        )
        verifier = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(verifier)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.check_call(["git", "init", "-q"], cwd=root)
            for directory in verifier.SOURCE_ROOTS:
                path = root / directory / "synthetic.txt"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("old unchanged fixture")
            subprocess.check_call(["git", "add", "."], cwd=root)
            subprocess.check_call(
                [
                    "git",
                    "-c",
                    "user.name=Synthetic",
                    "-c",
                    "user.email=synthetic@example.invalid",
                    "commit",
                    "-qm",
                    "old synthetic tree",
                ],
                cwd=root,
            )
            with patch.object(verifier, "ROOT", root):
                result = verifier.verify("HEAD")
                self.assertEqual(
                    result["status"],
                    "HISTORICAL_TREE_WITHOUT_WORK_SEARCH_CANDIDATES_CANDIDATE",
                )
                self.assertFalse(result["production_commands_executed"])

    def test_standalone_rejects_undeclared_runtime_and_test_deltas(self):
        data, parents = self.fixture()
        spec = importlib.util.spec_from_file_location(
            "scope_cloud_verifier",
            source.ROOT
            / "deploy/enterprise/verify-work-search-candidates-source-20261111.py",
        )
        verifier = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(verifier)
        changed = ("\n".join([*parents, "frontend/src/undeclared.ts"]) + "\n").encode()
        with (
            patch.object(verifier, "snapshot", return_value=data.__getitem__),
            patch.object(verifier.subprocess, "check_output", return_value=changed),
            patch.object(
                source, "MANIFEST_SHA256", source.digest(data[source.MANIFEST])
            ),
            patch.object(source, "_parent_sources", return_value=parents),
        ):
            with self.assertRaisesRegex(
                AssertionError, "WORK_SEARCH_CANDIDATES_UNDECLARED_SOURCE_DELTA"
            ):
                verifier.verify("synthetic")


if __name__ == "__main__":
    unittest.main()
