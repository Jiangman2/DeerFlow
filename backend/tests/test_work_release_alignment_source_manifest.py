"""Stdlib-only rebase provenance tests, runnable without Docker or app imports."""

import copy
import hashlib
import importlib.util
import json
import shutil
import subprocess
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[2] / "deploy/enterprise/verify-release-source-20261023.py"
SPEC = importlib.util.spec_from_file_location("work_release_alignment_verifier", SOURCE)
VERIFIER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFIER)
AGENT = "backend/app/integrations/work/agent.py"


def digest(raw):
    return hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()


def serialized(value):
    return json.dumps(value, indent=2).encode() + b"\n"


@contextmanager
def evidence():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        assert root.resolve().is_relative_to(Path(tempfile.gettempdir()).resolve())
        paths = sorted(VERIFIER.WORK_RELEASE_ALIGNMENT_PATHS)
        raw = {i: f"VALUE = {i}\n".encode() for i in range(8)}
        lock = {"source_sets": {"gateway": {p: digest(raw[0]) for p in paths}, "frontend": {}}, "scope": {"test": "synthetic"}}
        records = {VERIFIER.LOCK: serialized(lock)}
        work = {
            "schema": "production-source-candidate.v1",
            "status": "NOT_DEPLOYED",
            "base_commit": "a" * 40,
            "base_lock_sha256": digest(records[VERIFIER.LOCK]),
            "changes": {p: {"base_sha256": digest(raw[0]), "candidate_sha256": digest(raw[1])} for p in paths},
        }
        records[VERIFIER.CANDIDATE] = serialized(work)
        layers = [
            (VERIFIER.INCIDENT_DELTA, "incident-source-candidate.v1", VERIFIER.CANDIDATE, {}),
            (VERIFIER.WORK_ROUTING_DELTA, "work-routing-source-candidate.v1", VERIFIER.INCIDENT_DELTA, {p: {"base_sha256": digest(raw[1]), "candidate_sha256": digest(raw[3])} for p in paths}),
            (VERIFIER.WORK_TASK_AGENT_DELTA, "work-task-agent-source-candidate.v1", VERIFIER.WORK_ROUTING_DELTA, {}),
            (VERIFIER.WORK_TASK_AGENT_FIX_DELTA, "work-task-agent-fix-source-candidate.v1", VERIFIER.WORK_TASK_AGENT_DELTA, {AGENT: {"base_sha256": None, "candidate_sha256": digest(raw[5])}}),
            (VERIFIER.WORK_TASK_AGENT_MODEL_FIX_DELTA, "work-task-agent-model-fix-source-candidate.v1", VERIFIER.WORK_TASK_AGENT_FIX_DELTA, {AGENT: {"base_sha256": digest(raw[5]), "candidate_sha256": digest(raw[6])}}),
        ]
        for path, schema, parent, changes in layers:
            records[path] = serialized(
                {
                    "schema": schema,
                    "status": "NOT_DEPLOYED",
                    "base_commit": VERIFIER.WORK_ROUTING_BASE_COMMIT,
                    "base_lock_sha256": digest(records[VERIFIER.LOCK]),
                    "parent_candidate_sha256": digest(records[parent]),
                    "changes": {"gateway": changes, "frontend": {}},
                }
            )
        alignment = {
            "schema": "work-release-alignment-source-candidate.v1",
            "status": "NOT_DEPLOYED",
            "base_commit": VERIFIER.WORK_RELEASE_BASE_COMMIT,
            "scope": "Synthetic rebase; no deployment acceptance.",
            "parent_manifests": {p: digest(records[p]) for p in VERIFIER.WORK_PARENT_MANIFESTS},
            "changes": {},
        }
        for path in paths:
            baseline = VERIFIER.WORK_RELEASE_ALIGNMENT_ARCHIVE + "production-base/" + path
            alignment["changes"][path] = {"base_sha256": digest(raw[1]), "work_candidate_sha256": digest(raw[3]), "candidate_sha256": digest(raw[7]), "baseline_path": baseline}
            records[baseline], records[path] = raw[1], raw[7]
        records[AGENT] = raw[6]
        records[VERIFIER.WORK_RELEASE_ALIGNMENT] = serialized(alignment)
        for path, value in records.items():
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(value)
        with patch.object(VERIFIER, "ROOT", root):
            yield root, alignment, records


class WorkReleaseAlignmentTests(unittest.TestCase):
    def test_rebase_verifies_both_lineages_without_rewriting_the_six_old_layers(self):
        with evidence() as (root, _, original):
            result = VERIFIER.verify()
            self.assertEqual(result["status"], "CANDIDATE_SOURCE_VERIFIED")
            self.assertFalse(result["production_modified"])
            self.assertEqual(result["candidate"]["work_release_alignment"], {"status": "NOT_DEPLOYED", "base_commit": VERIFIER.WORK_RELEASE_BASE_COMMIT, "changed_sources": 2})
            self.assertEqual(result["candidate"]["work_task_agent_model_fix_delta"]["changed_sources"], 1)
            for name in VERIFIER.WORK_PARENT_MANIFESTS:
                self.assertEqual((root / name).read_bytes(), original[name])

    def test_rebase_rejects_missing_tampered_or_out_of_scope_evidence(self):
        cases = (
            "null",
            "schema",
            "status",
            "base_commit",
            "unknown",
            "parent_scope",
            "parent_bytes",
            "parent_digest",
            "change_scope",
            "change_schema",
            "current",
            "current_and_bad_syntax",
            "production_base",
            "forged_production_parent",
            "production_parent",
            "work_parent",
            "baseline_path",
            "missing_manifest",
            "unlisted",
        )
        for tamper in cases:
            with self.subTest(tamper=tamper), evidence() as (root, original, records):
                alignment = copy.deepcopy(original)
                path = sorted(alignment["changes"])[0]
                if tamper == "null":
                    alignment = None
                elif tamper in {"schema", "status", "base_commit"}:
                    alignment[tamper] = {"schema": "unreviewed.v1", "status": "DEPLOYED", "base_commit": "b" * 40}[tamper]
                elif tamper == "unknown":
                    alignment["skip_production_checks"] = True
                elif tamper == "parent_scope":
                    alignment["parent_manifests"].pop(VERIFIER.WORK_TASK_AGENT_FIX_DELTA)
                elif tamper == "parent_bytes":
                    (root / VERIFIER.WORK_TASK_AGENT_FIX_DELTA).write_bytes(records[VERIFIER.WORK_TASK_AGENT_FIX_DELTA] + b" ")
                elif tamper == "parent_digest":
                    alignment["parent_manifests"][VERIFIER.WORK_TASK_AGENT_FIX_DELTA] = "0" * 64
                elif tamper == "change_scope":
                    alignment["changes"]["backend/app/arbitrary.py"] = alignment["changes"].pop(path)
                elif tamper == "change_schema":
                    alignment["changes"][path]["skip"] = True
                elif tamper == "current":
                    (root / path).write_bytes(b"VALUE = 99\n")
                elif tamper == "current_and_bad_syntax":
                    raw = b"def invalid(\n"
                    (root / path).write_bytes(raw)
                    alignment["changes"][path]["candidate_sha256"] = digest(raw)
                elif tamper == "production_base":
                    (root / alignment["changes"][path]["baseline_path"]).write_bytes(b"VALUE = 99\n")
                elif tamper == "forged_production_parent":
                    raw = b"VALUE = 99\n"
                    (root / alignment["changes"][path]["baseline_path"]).write_bytes(raw)
                    alignment["changes"][path]["base_sha256"] = digest(raw)
                elif tamper in {"production_parent", "work_parent"}:
                    alignment["changes"][path]["base_sha256" if tamper == "production_parent" else "work_candidate_sha256"] = "f" * 64
                elif tamper == "baseline_path":
                    alignment["changes"][path]["baseline_path"] = "../../outside.py"
                elif tamper == "missing_manifest":
                    (root / VERIFIER.WORK_RELEASE_ALIGNMENT).unlink()
                else:
                    (root / "backend/app/unlisted.py").write_bytes(b"VALUE = 99\n")
                if tamper != "missing_manifest":
                    (root / VERIFIER.WORK_RELEASE_ALIGNMENT).write_bytes(serialized(alignment))
                with self.assertRaises((AssertionError, SyntaxError)):
                    VERIFIER.verify()

    def test_only_verified_sources_can_be_projected_for_the_production_qualifier(self):
        with evidence() as (_, alignment, _):
            original = VERIFIER._nov02.followup.qualify
            inspected = []

            def qualify(read):
                for path, row in alignment["changes"].items():
                    self.assertEqual(digest(read(path)), row["base_sha256"])
                inspected.append(True)
                return original(read)

            with patch.object(VERIFIER._nov02.followup, "qualify", qualify):
                VERIFIER.verify()
            self.assertTrue(inspected)

    @unittest.skipUnless(shutil.which("git"), "Git is required for committed-tree archive verification")
    def test_git_archive_includes_rebase_and_supports_the_six_layer_predecessor(self):
        with evidence() as (root, alignment, _):
            for prefix in (VERIFIER.RUNNER, VERIFIER.FORMAT_BASELINE, "frontend/"):
                target = root / prefix / ".keep"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"synthetic\n")

            def git(*args):
                return subprocess.check_output(["git", "-c", "user.name=Synthetic Test", "-c", "user.email=synthetic@example.test", *args], cwd=root, stderr=subprocess.STDOUT)

            raw = (root / VERIFIER.WORK_RELEASE_ALIGNMENT).read_bytes()
            (root / VERIFIER.WORK_RELEASE_ALIGNMENT).unlink()
            for path in alignment["changes"]:
                (root / path).write_bytes(b"VALUE = 3\n")
            git("init", "--quiet")
            git("add", ".")
            git("commit", "--quiet", "-m", "synthetic sixth layer")
            old = git("rev-parse", "HEAD").decode().strip()
            (root / VERIFIER.WORK_RELEASE_ALIGNMENT).write_bytes(raw)
            for path in alignment["changes"]:
                (root / path).write_bytes(b"VALUE = 7\n")
            git("add", ".")
            git("commit", "--quiet", "-m", "synthetic rebased candidate")
            new = git("rev-parse", "HEAD").decode().strip()
            self.assertNotIn("work_release_alignment", VERIFIER.verify(old)["candidate"])
            self.assertEqual(VERIFIER.verify(new)["candidate"]["work_release_alignment"]["changed_sources"], 2)
            (root / sorted(alignment["changes"])[0]).write_bytes(b"VALUE = 99\n")
            self.assertEqual(VERIFIER.verify(new)["git_ref"], new)
            with self.assertRaises(AssertionError):
                VERIFIER.verify()


if __name__ == "__main__":
    unittest.main()
