"""Watch additions/deletions, SQL changes, and cross-platform checkout endings."""
import importlib.util
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("drift", Path(__file__).parents[1] / "scripts/upstream/check_persistence_drift.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class DriftTests(unittest.TestCase):
    def test_detects_additions_deletions_and_changes(self):
        self.assertEqual(module.changes({"deleted": "a", "same": "x", "changed": "a"}, {"added": "b", "same": "x", "changed": "b"}), ["added", "changed", "deleted"])

    def test_new_migration_watched_and_line_endings_normalized(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / module.DIRECTORIES[0] / "migrations/versions/new.py"
            path.parent.mkdir(parents=True)
            before = module.snapshot(root)
            path.write_bytes(b"SQL = 'SELECT 1'\r\n")
            after = module.snapshot(root)
            self.assertEqual(module.changes(before, after), [path.relative_to(root).as_posix()])
            path.write_bytes(b"SQL = 'SELECT 1'\n")
            self.assertEqual(module.snapshot(root), after)
            path.write_bytes(b"SQL = 'SELECT 2'\n")
            self.assertTrue(module.changes(after, module.snapshot(root)))


if __name__ == "__main__":
    unittest.main()
