"""Stdlib-only provenance guards for the single-file authority-tag security fix."""

import copy
import importlib.util
import json
import shutil
import subprocess
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("sanitizer_alignment_fixture", Path(__file__).with_name("test_work_release_alignment_source_manifest.py"))
FIXTURE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FIXTURE)
V = FIXTURE.VERIFIER
digest = FIXTURE.digest
serialized = FIXTURE.serialized
PARENT = b"VALUE = 10\n"
CURRENT = b"VALUE = 11\n"


@contextmanager
def evidence(*, include_runner=False):
    """Extend the existing synthetic lineage; never touch application or Git data."""
    with FIXTURE.evidence() as (root, alignment, records):
        lock = json.loads(records[V.LOCK])
        lock["source_sets"]["gateway"][V.WORK_AUTHORITY_SANITIZER_PATH] = digest(PARENT)
        if include_runner:
            lock["source_sets"]["ppt-master-runner"] = {V.WORK_AUTHORITY_SANITIZER_PATH: digest(PARENT)}
            records[V.RUNNER + V.WORK_AUTHORITY_SANITIZER_PATH] = PARENT
        records[V.LOCK] = serialized(lock)
        previous = V.CANDIDATE
        for name in V.WORK_PARENT_MANIFESTS[1:]:
            row = json.loads(records[name])
            row["base_lock_sha256"] = digest(records[V.LOCK])
            if "parent_candidate_sha256" in row:
                row["parent_candidate_sha256"] = digest(records[previous])
            records[name] = serialized(row)
            previous = name
        alignment["parent_manifests"] = {p: digest(records[p]) for p in V.WORK_PARENT_MANIFESTS}
        records[V.WORK_RELEASE_ALIGNMENT] = serialized(alignment)
        manifest = {
            "schema": "work-authority-sanitizer-source-candidate.v1",
            "status": "NOT_DEPLOYED",
            "base_commit": V.WORK_AUTHORITY_SANITIZER_BASE_COMMIT,
            "base_lock_sha256": digest(records[V.LOCK]),
            "parent_candidate_sha256": digest(records[V.WORK_RELEASE_ALIGNMENT]),
            "scope": "Synthetic source qualification only.",
            "changes": {"gateway": {V.WORK_AUTHORITY_SANITIZER_PATH: {"base_sha256": digest(PARENT), "candidate_sha256": digest(CURRENT)}}, "frontend": {}},
        }
        records[V.WORK_AUTHORITY_SANITIZER_DELTA] = serialized(manifest)
        records[V.WORK_AUTHORITY_SANITIZER_PATH] = CURRENT
        for name, value in records.items():
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(value)
        with patch.object(V._nov02.followup.alignment, "MANIFEST_SHA256", digest(records[V.WORK_RELEASE_ALIGNMENT])), patch.object(V, "WORK_AUTHORITY_SANITIZER_SHA256", digest(records[V.WORK_AUTHORITY_SANITIZER_DELTA])):
            yield root, lock, manifest, records


class AuthoritySanitizerSourceTests(unittest.TestCase):
    def test_single_gateway_source_is_qualified_without_changing_old_seals(self):
        with evidence() as (root, _, _, records):
            result = V.verify()
            self.assertEqual(result["candidate"]["work_authority_sanitizer_delta"], {"status": "NOT_DEPLOYED", "base_commit": V.WORK_AUTHORITY_SANITIZER_BASE_COMMIT, "changed_sources": 1})
            self.assertFalse(result["production_modified"])
            for name in (*V.WORK_PARENT_MANIFESTS, V.WORK_RELEASE_ALIGNMENT):
                self.assertEqual((root / name).read_bytes(), records[name])

    def test_strict_protocol_scope_and_parent_hashes(self):
        cases = ("null", "schema", "status", "base", "lock", "parent", "unknown", "scope_type", "component", "frontend", "path", "extra_path", "row", "base_hash", "candidate_hash")
        with evidence() as (_, lock, original, records):
            for tamper in cases:
                manifest = copy.deepcopy(original)
                changes = manifest["changes"]
                row = changes["gateway"][V.WORK_AUTHORITY_SANITIZER_PATH]
                if tamper == "null":
                    manifest = None
                elif tamper in {"schema", "status", "base", "lock", "parent"}:
                    key = {"schema": "schema", "status": "status", "base": "base_commit", "lock": "base_lock_sha256", "parent": "parent_candidate_sha256"}[tamper]
                    manifest[key] = "unapproved"
                elif tamper == "unknown":
                    manifest["skip_checks"] = True
                elif tamper == "scope_type":
                    manifest["scope"] = None
                elif tamper == "component":
                    changes["ppt-master-runner"] = {}
                elif tamper == "frontend":
                    changes["frontend"]["frontend/src/forged.ts"] = row
                elif tamper == "path":
                    changes["gateway"]["backend/app/forged.py"] = changes["gateway"].pop(V.WORK_AUTHORITY_SANITIZER_PATH)
                elif tamper == "extra_path":
                    changes["gateway"]["backend/app/forged.py"] = row
                elif tamper == "row":
                    row["skip"] = True
                elif tamper == "base_hash":
                    row["base_sha256"] = "0" * 64
                else:
                    row["candidate_sha256"] = "invalid"
                with self.subTest(tamper=tamper), self.assertRaisesRegex(AssertionError, "WORK_AUTHORITY_SANITIZER_"):
                    V.work_authority_sanitizer_overrides(lock, {}, manifest, digest(records[V.LOCK]), digest(records[V.WORK_RELEASE_ALIGNMENT]))

    def test_actual_current_drift_missing_manifest_and_seal_drift_are_rejected(self):
        for tamper in ("current", "missing", "manifest", "syntax", "undeclared"):
            with self.subTest(tamper=tamper), evidence() as (root, _, manifest, _):
                if tamper == "current":
                    (root / V.WORK_AUTHORITY_SANITIZER_PATH).write_bytes(b"VALUE = 99\n")
                elif tamper == "missing":
                    (root / V.WORK_AUTHORITY_SANITIZER_DELTA).unlink()
                elif tamper == "manifest":
                    target = root / V.WORK_AUTHORITY_SANITIZER_DELTA
                    target.write_bytes(target.read_bytes() + b"\n")
                elif tamper == "undeclared":
                    (root / "backend/app/forged.py").write_bytes(b"VALUE = 99\n")
                else:
                    raw = b"def malformed(\n"
                    (root / V.WORK_AUTHORITY_SANITIZER_PATH).write_bytes(raw)
                    manifest["changes"]["gateway"][V.WORK_AUTHORITY_SANITIZER_PATH]["candidate_sha256"] = digest(raw)
                    (root / V.WORK_AUTHORITY_SANITIZER_DELTA).write_bytes(serialized(manifest))
                with self.assertRaises((AssertionError, SyntaxError)):
                    if tamper == "syntax":
                        with patch.object(V, "WORK_AUTHORITY_SANITIZER_SHA256", digest(serialized(manifest))):
                            V.verify()
                    else:
                        V.verify()

    def test_runner_source_cannot_use_gateway_override(self):
        with evidence(include_runner=True) as (root, _, _, _):
            self.assertEqual(len(V.verify()["results"]), 3)
            runner = root / V.RUNNER / V.WORK_AUTHORITY_SANITIZER_PATH
            runner.write_bytes(CURRENT)
            with self.assertRaisesRegex(AssertionError, "SOURCE_HASH_MISMATCH"):
                V.verify()

    @unittest.skipUnless(shutil.which("git"), "Git is required for offline committed-tree verification")
    def test_git_archive_keeps_the_parent_valid_and_rejects_a_missing_delta(self):
        with evidence() as (root, _, _, records):
            for prefix in (V.RUNNER, V.FORMAT_BASELINE, "frontend/"):
                target = root / prefix / ".keep"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"synthetic\n")

            def git(*args):
                return subprocess.check_output(["git", "-c", "user.name=Synthetic Test", "-c", "user.email=synthetic@example.test", *args], cwd=root, stderr=subprocess.STDOUT)

            (root / V.WORK_AUTHORITY_SANITIZER_DELTA).unlink()
            (root / V.WORK_AUTHORITY_SANITIZER_PATH).write_bytes(PARENT)
            git("init", "--quiet")
            git("add", ".")
            git("commit", "--quiet", "-m", "synthetic pre-security parent")
            old = git("rev-parse", "HEAD").decode().strip()
            (root / V.WORK_AUTHORITY_SANITIZER_DELTA).write_bytes(records[V.WORK_AUTHORITY_SANITIZER_DELTA])
            (root / V.WORK_AUTHORITY_SANITIZER_PATH).write_bytes(CURRENT)
            git("add", ".")
            git("commit", "--quiet", "-m", "synthetic security candidate")
            new = git("rev-parse", "HEAD").decode().strip()
            self.assertNotIn("work_authority_sanitizer_delta", V.verify(old)["candidate"])
            self.assertEqual(V.verify(new)["candidate"]["work_authority_sanitizer_delta"]["changed_sources"], 1)
            (root / V.WORK_AUTHORITY_SANITIZER_PATH).write_bytes(b"VALUE = 99\n")
            self.assertEqual(V.verify(new)["git_ref"], new)
            with self.assertRaises(AssertionError):
                V.verify()
            (root / V.WORK_AUTHORITY_SANITIZER_DELTA).unlink()
            git("add", ".")
            git("commit", "--quiet", "-m", "synthetic missing security delta")
            with self.assertRaises(AssertionError):
                V.verify(git("rev-parse", "HEAD").decode().strip())


if __name__ == "__main__":
    unittest.main()
