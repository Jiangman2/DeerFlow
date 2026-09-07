"""PoC admission guards: never let a mistaken target run schema setup."""
import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("kingbase_probe", Path(__file__).parents[1] / "scripts" / "kingbase" / "probe.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class TargetTests(unittest.TestCase):
    def test_release_banner(self):
        for banner in ("KingbaseES V9R1C10", "KingbaseES V009R001C010B0004", "KingbaseES V9.1.0", "KingbaseES 9.3.11"):
            self.assertTrue(probe.is_kingbase_v9(banner))
        for banner in ("PostgreSQL 9.6", "KingbaseES V8R6", "KingbaseES V10R1"):
            self.assertFalse(probe.is_kingbase_v9(banner))

    def test_scratch_target(self):
        name = "deerflow_kingbase_poc_ci"
        self.assertEqual(probe.validate_target("postgresql://tester:secret@localhost/" + name, name), name)

    def test_rejects_wrong_or_overridden_database(self):
        for dsn, confirmation in [
            ("postgresql://localhost/deerflow", "deerflow"),
            ("postgresql://localhost/deerflow_kingbase_poc_ci", "other"),
            ("postgresql://localhost/deerflow_kingbase_poc_ci?dbname=production", "deerflow_kingbase_poc_ci"),
            ("postgresql://localhost/deerflow_kingbase_poc_ci#production", "deerflow_kingbase_poc_ci"),
            ("postgresql:///deerflow_kingbase_poc_ci", "deerflow_kingbase_poc_ci"),
            ("sqlite:///deerflow_kingbase_poc_ci", "deerflow_kingbase_poc_ci"),
        ]:
            with self.subTest(dsn=dsn), self.assertRaises(ValueError):
                probe.validate_target(dsn, confirmation)


if __name__ == "__main__":
    unittest.main()
