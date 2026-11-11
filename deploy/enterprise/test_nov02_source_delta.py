"""Reject source and release-evidence drift without production access."""
import copy
import importlib.util
import json
from pathlib import Path
import unittest

from released_source_20261102 import CI_FOLLOWUP, CI_MANIFEST, DELTA, SOURCE, digest, historical_read, load
from work_integration_source_20261106 import historical_view

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SPEC = importlib.util.spec_from_file_location("release_verifier", HERE / "verify-release-source-20261023.py")
V = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(V)


class SourceDeltaTests(unittest.TestCase):
    def setUp(self):
        self.read, _ = historical_view(lambda path: (ROOT / path).read_bytes())
        self.delta, self.manifest = load(self.read)

    def replace(self, path, raw):
        return lambda name: raw if name == path else self.read(name)

    def test_sealed_release_is_accepted(self):
        self.assertEqual(len(self.manifest["sources"]), 41)

    def test_current_runtime_change_rejected(self):
        path = "backend/app/gateway/routers/model_monitor.py"
        with self.assertRaisesRegex(AssertionError, "NOV02_RUNTIME_LOGIC_CHANGED|NOV02_CURRENT_SOURCE_CHANGED"):
            load(self.replace(path, self.read(path) + b"\n# drift\n"))

    def test_parent_source_change_rejected(self):
        path = next(row["parent_source"] for row in self.manifest["sources"].values() if row.get("parent_source"))
        with self.assertRaisesRegex(AssertionError, "NOV02_PARENT_SOURCE_CHANGED"):
            load(self.replace(path, self.read(path) + b"\n# drift\n"))

    def test_evidence_change_rejected(self):
        path = next(iter(self.manifest["evidence"]))
        with self.assertRaisesRegex(AssertionError, "NOV02_EVIDENCE_CHANGED"):
            load(self.replace(path, self.read(path) + b"\n"))

    def test_parent_delta_change_rejected(self):
        path = self.delta["parent_delta"]
        with self.assertRaisesRegex(AssertionError, "NOV02_PARENT_DELTA_CHANGED"):
            load(self.replace(path, self.read(path) + b"\n"))

    def test_delta_scope_change_rejected(self):
        delta = copy.deepcopy(self.delta)
        delta["changes"]["gateway"]["backend/app/gateway/auth/data_lake_work.py"] = {"base_sha256": None, "released_sha256": "0" * 64, "kind": "released-runtime"}
        with self.assertRaisesRegex(AssertionError, "NOV02_DELTA_SCOPE_CHANGED"):
            load(self.replace(DELTA, json.dumps(delta).encode()))

    def test_source_seal_change_rejected(self):
        with self.assertRaisesRegex(AssertionError, "NOV02_SOURCE_SEAL_CHANGED"):
            load(self.replace(SOURCE, self.read(SOURCE) + b"\n"))

    def test_ci_qualification_seal_change_rejected(self):
        with self.assertRaisesRegex(AssertionError, "PR26_CI_SEAL_CHANGED"):
            load(self.replace(CI_MANIFEST, self.read(CI_MANIFEST) + b"\n"))

    def test_formatter_output_drift_rejected(self):
        path = "frontend/src/styles/cnooc-concept.css"
        with self.assertRaisesRegex(AssertionError, "PR26_CI_CURRENT_CHANGED|HISTORY_COMPOSER_CURRENT_CHANGED"):
            load(self.replace(path, self.read(path) + b"\n/* drift */\n"))

    def test_followup_seal_change_rejected(self):
        with self.assertRaisesRegex(AssertionError, "PR26_CI_FOLLOWUP_SEAL_CHANGED"):
            load(self.replace(CI_FOLLOWUP, self.read(CI_FOLLOWUP) + b"\n"))

    def test_followup_baseline_change_rejected(self):
        followup = json.loads(self.read(CI_FOLLOWUP))
        path = next(iter(followup["changes"].values()))["baseline_path"]
        with self.assertRaisesRegex(AssertionError, "PR26_CI_FOLLOWUP_BASELINE_CHANGED"):
            load(self.replace(path, self.read(path) + b"\n"))

    def test_followup_current_test_change_rejected(self):
        path = "backend/tests/test_tool_output_budget_middleware.py"
        with self.assertRaisesRegex(AssertionError, "PR26_CI_CURRENT_CHANGED"):
            load(self.replace(path, self.read(path) + b"\n# drift\n"))

    def test_ci_baseline_drift_rejected(self):
        qualification = json.loads(self.read(CI_MANIFEST))
        path = next(iter(qualification["changes"].values()))["baseline_path"]
        with self.assertRaisesRegex(AssertionError, "PR26_CI_BASELINE_CHANGED"):
            load(self.replace(path, self.read(path) + b"\n"))

    def test_historical_reader_restores_exact_parent(self):
        historical = historical_read(self.read)
        for path, row in self.manifest["sources"].items():
            if row.get("parent_source"):
                self.assertEqual(digest(historical(path)), row["base_sha256"])

    def test_historical_chain_accepts_only_declared_delta(self):
        lock = json.loads(self.read(V.LOCK))
        candidate = json.loads(self.read(V.CANDIDATE))
        prior = V.candidate_overrides(lock, candidate, digest(self.read(V.LOCK)))
        incident = V.incident_overrides(lock, prior, json.loads(self.read(V.INCIDENT_DELTA)), digest(self.read(V.LOCK)), digest(self.read(V.CANDIDATE)))
        released = V.released_overrides(lock, prior, incident, json.loads(self.read(V.RELEASED_DELTA)), self.read)
        from released_source_20261102 import overrides
        result = overrides(lock, prior, incident, released, self.read)
        self.assertEqual(len(result["gateway"]), 22)


if __name__ == "__main__":
    unittest.main()
