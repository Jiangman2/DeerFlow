"""Regression checks for missing evidence and changed stream-recovery sources."""
import json
from pathlib import Path
import unittest

import terminal_stream_source_20261106 as source

ROOT = Path(__file__).resolve().parents[2]


class TerminalStreamSourceTests(unittest.TestCase):
    def fixture(self):
        manifest = (ROOT / source.MANIFEST).read_bytes()
        paths = list(json.loads(manifest)["files"])
        paths += [source.ARCHIVE + "baseline/" + source.CLIENT]
        return {source.MANIFEST: manifest, **{p: (ROOT / p).read_bytes() for p in paths}}

    def test_historical_fixture_without_delta_is_unchanged(self):
        read = {}.__getitem__
        self.assertIs(source.historical_read(read), read)

    def test_valid_delta_projects_only_client_and_is_idempotent(self):
        data = self.fixture()
        read = source.historical_read(data.__getitem__)
        self.assertEqual(read(source.CLIENT), data[source.ARCHIVE + "baseline/" + source.CLIENT])
        self.assertEqual(read(source.HELPER), data[source.HELPER])
        self.assertEqual(source.historical_read(read)(source.CLIENT), read(source.CLIENT))

    def test_changed_or_missing_sources_and_evidence_are_rejected(self):
        for path in self.fixture():
            for missing in (True, False):
                with self.subTest(path=path, missing=missing):
                    data = self.fixture()
                    if missing:
                        del data[path]
                    else:
                        data[path] = b"changed"
                    with self.assertRaises((AssertionError, KeyError, ValueError)):
                        source.historical_read(data.__getitem__)


if __name__ == "__main__":
    unittest.main()
