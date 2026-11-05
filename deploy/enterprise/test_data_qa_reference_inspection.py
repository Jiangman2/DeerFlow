"""Python 3.7 bounded read-only diagnostic qualification."""

import importlib.util
import pathlib
import tempfile
import unittest

FOLDER = pathlib.Path(__file__).parent
spec = importlib.util.spec_from_file_location("builder", FOLDER / "package-data-qa-agent-reference-inspection.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class ReferenceInspectionTests(unittest.TestCase):
    def setUp(self):
        self.package = {"__name__": "test"}
        exec(compile(builder.build(FOLDER), "<package>", "exec"), self.package)
        probe = self.package["PROBE"]
        self.scope = {}
        exec(compile(probe[:probe.index("\nimport contextlib,io,json")], "<probe>", "exec"), self.scope)

    def scan(self, root):
        return self.scope["inspect_references"](root, self.scope["agent_references"])

    def test_only_closed_vocabulary_no_prompt_secret_or_user_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            p = pathlib.Path(folder) / "users/private-user/agents/private-name"
            p.mkdir(parents=True)
            (p / "SOUL.md").write_text("明细查询调用 datalake_detail_qa 私密词条 password=synthetic-secret-123\n禁止外部搜索；指标调用 datalake_metrics_qa\n", encoding="utf-8")
            result = self.scan(folder)
            item = result["affected_file_details"][0]
            self.assertEqual(item["scope"], "personal")
            self.assertEqual(item["filename"], "SOUL.md")
            self.assertEqual(item["affected_lines"], 2)
            self.assertIn("禁止", item["lines"][1]["vocabulary"])
            for value in ("synthetic-secret-123", "private-user", "private-name", "私密词条"):
                self.assertNotIn(value, str(result))

    def test_config_fields_distinguished_from_prose(self):
        with tempfile.TemporaryDirectory() as folder:
            p = pathlib.Path(folder) / "team_agents/a"
            p.mkdir(parents=True)
            (p / "config.yaml").write_text("excluded_tools: [datalake_detail_qa]\n", encoding="utf-8")
            item = self.scan(folder)["affected_file_details"][0]
            self.assertEqual(item["scope"], "team")
            self.assertEqual(item["lines"][0]["known_fields"], ["excluded_tools"])

    def test_scan_does_not_change_content_and_preserves_inventory_hash(self):
        with tempfile.TemporaryDirectory() as folder:
            p = pathlib.Path(folder) / "agents/a"
            p.mkdir(parents=True)
            file = p / "SOUL.md"
            file.write_bytes(b"datalake_metrics_qa")
            before = file.stat()
            original = self.scope["agent_references"](folder)
            detailed = self.scan(folder)
            self.assertEqual(original["inventory_sha256"], detailed["inventory_sha256"])
            self.assertEqual(file.read_bytes(), b"datalake_metrics_qa")
            self.assertEqual(file.stat().st_mtime_ns, before.st_mtime_ns)

    def test_symlink_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            p = pathlib.Path(folder) / "agents/a"
            p.mkdir(parents=True)
            (p / "SOUL.md").symlink_to(p / "missing")
            with self.assertRaises(OSError):
                self.scan(folder)

    def test_output_line_limit_is_explicit(self):
        with tempfile.TemporaryDirectory() as folder:
            p = pathlib.Path(folder) / "agents/a"
            p.mkdir(parents=True)
            (p / "SOUL.md").write_text("datalake_detail_qa\n" * 40)
            item = self.scan(folder)["affected_file_details"][0]
            self.assertEqual(item["affected_lines"], 40)
            self.assertEqual(len(item["lines"]), 24)
            self.assertTrue(item["lines_truncated"])

    def test_host_identity_refuses_replaced_container_or_pod(self):
        p = dict(metadata=dict(name=self.package["POD"], uid="changed"))
        with self.assertRaisesRegex(ValueError, "POD_IDENTITY_CHANGED"):
            self.package["pod_identity"](p, {})

    def test_packaged_probe_and_host_compile(self):
        compile(self.package["PROBE"], "<probe>", "exec")
        self.assertLess(len(builder.build(FOLDER)), 20000)
        self.assertNotIn("[\"patch\"", self.package["PROBE"])


if __name__ == "__main__":
    unittest.main()
