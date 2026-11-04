"""Reject provenance drift without contacting production."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SPEC = importlib.util.spec_from_file_location('release_verifier', HERE / 'verify-release-source-20261023.py')
V = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(V)


class ArtifactDirectoryHistoryTests(unittest.TestCase):
    def test_only_verified_middleware_is_projected(self):
        q = V._nov02.followup
        read = lambda path: (ROOT / path).read_bytes()
        projected = q.artifact_directory_historical_read(read)
        self.assertEqual(projected(q.ARTIFACT_SOURCE), read(q.ARTIFACT_PARENT))
        self.assertNotEqual(read(q.ARTIFACT_SOURCE), projected(q.ARTIFACT_SOURCE))
        self.assertEqual(projected(q.ARTIFACT_TEST), read(q.ARTIFACT_TEST))
        for path in (q.ARTIFACT_SOURCE, q.ARTIFACT_TEST, q.ARTIFACT_PARENT):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "ARTIFACT_DIRECTORY_SOURCE_CHANGED"):
                q.artifact_directory_historical_read(lambda name: b"changed" if name == path else read(name))

    def test_unqualified_historical_ref_keeps_original_reader(self):
        read = {}.__getitem__
        self.assertIs(V._nov02.followup.artifact_directory_historical_read(read), read)


class SourceDeltaTests(unittest.TestCase):
    def setUp(self):
        self.read = lambda name: (ROOT / name).read_bytes()
        sha = lambda name: hashlib.sha256(self.read(name).replace(b'\r\n', b'\n')).hexdigest()
        self.lock = json.loads(self.read(V.LOCK))
        candidate = json.loads(self.read(V.CANDIDATE))
        self.prior = V.candidate_overrides(self.lock, candidate, sha(V.LOCK))
        self.incident = V.incident_overrides(self.lock, self.prior, json.loads(self.read(V.INCIDENT_DELTA)), sha(V.LOCK), sha(V.CANDIDATE))
        self.delta = json.loads(self.read(V.RELEASED_DELTA))

    def verify(self, delta):
        return V.released_overrides(self.lock, self.prior, self.incident, delta, self.read)

    def test_sealed_release_is_accepted(self):
        self.assertEqual(len(self.verify(self.delta)['gateway']), 11)

    def test_runtime_digest_cannot_diverge_from_release_seal(self):
        changed = copy.deepcopy(self.delta)
        next(iter(changed['changes']['gateway'].values()))['released_sha256'] = '0' * 64
        with self.assertRaisesRegex(AssertionError, 'RELEASE_DELTA_SOURCE_CHANGED'):
            self.verify(changed)

    def test_unreleased_runtime_path_cannot_be_added(self):
        changed = copy.deepcopy(self.delta)
        changed['changes']['gateway']['backend/app/gateway/auth/data_lake_work.py'] = {'base_sha256': None, 'released_sha256': '0' * 64}
        with self.assertRaisesRegex(AssertionError, 'RELEASE_DELTA_SCOPE_CHANGED'):
            self.verify(changed)

    def test_historical_inventory_drift_is_rejected(self):
        changed = copy.deepcopy(self.delta)
        changed['parent_sha256'][next(iter(changed['parent_sha256']))] = '0' * 64
        with self.assertRaisesRegex(AssertionError, 'RELEASE_PARENT_CHANGED'):
            self.verify(changed)


if __name__ == '__main__':
    unittest.main()
