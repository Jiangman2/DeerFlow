"""Historical V4.1 receipts must coexist with strictly qualified later sources."""

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location(
    "v41_archive", Path(__file__).with_name("verify-v41-preparation-20261029.py")
)
ARCHIVE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ARCHIVE)
ADAPTER = "backend/packages/harness/deerflow/models/cnooc_deepseek_v41.py"
LOCK = "deploy/enterprise/production-source-lock-20261029.json"
CANDIDATE = "deploy/enterprise/work-source-candidate-20261026.json"
INCIDENT = "deploy/enterprise/incident-source-candidate-20261031.json"


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


class AdapterArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.root_patch = patch.object(ARCHIVE, "ROOT", self.root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        self.original = b"value = 'original'\n"
        self.intermediate = b"value = 'intermediate'\n"
        self.current = b"value = 'guarded'\n"
        self.provider = {"installed_adapter_sha256": sha(self.original)}
        self.prepared = {"adapter_sha256": sha(self.original)}
        self.write(ADAPTER, self.original)
        self.lock = self.write_json(LOCK, {
            "source_sets": {"gateway": {ADAPTER: sha(self.original)}, "frontend": {}}
        })

    def write(self, name, raw):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        return raw

    def write_json(self, name, value):
        return self.write(name, json.dumps(value, sort_keys=True).encode())

    def chain(self):
        candidate = self.write_json(CANDIDATE, {
            "schema": "production-source-candidate.v1", "status": "NOT_DEPLOYED",
            "base_commit": "a" * 40, "base_lock_sha256": sha(self.lock),
            "changes": {ADAPTER: {
                "base_sha256": sha(self.original), "candidate_sha256": sha(self.intermediate)
            }},
        })
        self.write_json(INCIDENT, {
            "schema": "incident-source-candidate.v1", "status": "NOT_DEPLOYED",
            "base_commit": "b" * 40, "base_lock_sha256": sha(self.lock),
            "parent_candidate_sha256": sha(candidate),
            "changes": {"frontend": {}, "gateway": {ADAPTER: {
                "base_sha256": sha(self.intermediate), "candidate_sha256": sha(self.current)
            }}},
        })
        self.write(ADAPTER, self.current)

    def verify(self):
        ARCHIVE.verify_adapter(self.provider, self.prepared)

    def test_original_adapter_still_matches_historical_receipts(self):
        self.verify()

    def test_qualified_later_adapter_keeps_original_receipts_immutable(self):
        self.chain()
        before = dict(self.provider), dict(self.prepared)
        self.verify()
        self.assertEqual(before, (self.provider, self.prepared))
        self.assertNotEqual(sha(self.current), self.provider["installed_adapter_sha256"])

    def test_current_checkout_line_endings_match_declared_lf_hash(self):
        self.chain()
        self.write(ADAPTER, self.current.replace(b"\n", b"\r\n"))
        self.verify()

    def test_unqualified_change_is_rejected(self):
        self.write(ADAPTER, self.current)
        with self.assertRaisesRegex(ValueError, "Installed adapter changed"):
            self.verify()

    def test_changed_current_source_is_rejected_even_with_valid_chain(self):
        self.chain()
        self.write(ADAPTER, self.current + b"unreviewed = True\n")
        with self.assertRaisesRegex(ValueError, "Installed adapter changed"):
            self.verify()

    def test_matching_but_rewritten_receipts_are_rejected(self):
        self.chain()
        self.provider["installed_adapter_sha256"] = sha(self.current)
        self.prepared["adapter_sha256"] = sha(self.current)
        with self.assertRaisesRegex(ValueError, "Historical adapter receipt changed"):
            self.verify()

    def test_changed_parent_or_source_binding_is_rejected(self):
        self.chain()
        raw = (self.root / INCIDENT).read_bytes()
        for field in ("base_lock_sha256", "parent_candidate_sha256", "source_parent"):
            with self.subTest(field=field):
                manifest = json.loads(raw)
                if field == "source_parent":
                    manifest["changes"]["gateway"][ADAPTER]["base_sha256"] = "0" * 64
                else:
                    manifest[field] = "0" * 64
                self.write_json(INCIDENT, manifest)
                with self.assertRaises(AssertionError):
                    self.verify()

    def test_incident_without_parent_is_rejected(self):
        self.chain()
        (self.root / CANDIDATE).unlink()
        with self.assertRaisesRegex(ValueError, "Incident adapter parent missing"):
            self.verify()

    def test_changed_candidate_parent_is_rejected(self):
        self.chain()
        manifest = json.loads((self.root / CANDIDATE).read_bytes())
        manifest["base_lock_sha256"] = "0" * 64
        self.write_json(CANDIDATE, manifest)
        with self.assertRaisesRegex(AssertionError, "CANDIDATE_BASE_LOCK_MISMATCH"):
            self.verify()


if __name__ == "__main__":
    unittest.main()
