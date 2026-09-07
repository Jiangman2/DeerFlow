"""Exercise divergence classification against a real temporary Git history."""
import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("upstream_inventory", Path(__file__).parents[1] / "scripts" / "upstream" / "inventory.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class InventoryTests(unittest.TestCase):
    def test_deleted_core_and_upstream_added_name_are_shared(self):
        with tempfile.TemporaryDirectory() as folder:
            repo = Path(folder)
            def run(*args):
                return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()
            run("init")
            run("config", "user.name", "Inventory Test")
            run("config", "user.email", "test@example.invalid")
            (repo / "core.txt").write_text("base")
            run("add", ".")
            run("commit", "-m", "base")
            base = run("rev-parse", "HEAD")
            (repo / "collision.txt").write_text("official")
            run("add", ".")
            run("commit", "-m", "upstream")
            upstream = run("rev-parse", "HEAD")
            run("checkout", "--detach", base)
            (repo / "core.txt").unlink()
            (repo / "collision.txt").write_text("enterprise")
            (repo / "enterprise.txt").write_text("enterprise")
            run("add", "-A")
            run("commit", "-m", "enterprise")
            result = module.inventory(repo, upstream, "HEAD")
            rows = {row["path"]: row for row in result["shared_files_requiring_review"]}
            self.assertEqual(rows["core.txt"]["change"], "D")
            self.assertIn("collision.txt", rows)
            self.assertNotIn("enterprise.txt", rows)
            self.assertEqual(result["enterprise_added_file_count"], 1)
            self.assertFalse(json.loads(json.dumps(result))["certifies_compatibility"])


if __name__ == "__main__":
    unittest.main()
