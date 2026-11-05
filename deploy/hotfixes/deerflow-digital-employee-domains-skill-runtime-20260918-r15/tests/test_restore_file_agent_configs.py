#!/usr/bin/env python3
from __future__ import print_function

import importlib.util
import tempfile
import unittest
import zipfile
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "restore-file-agent-configs.py"
SPEC = importlib.util.spec_from_file_location("restore_file_agent_configs", str(SCRIPT))
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class RestoreFileAgentConfigsTests(unittest.TestCase):
    def _archive(self, root, members):
        path = root / "backup.zip"
        with zipfile.ZipFile(str(path), "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, data in members:
                archive.writestr(name, data)
        return path

    def test_restores_three_layouts_exactly_and_preserves_extra_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            live = base / "live"
            live.mkdir()
            members = [
                ("users/u1/agents/a/config.yaml", b"name: a\ncategory_id: old-a\n"),
                ("team_agents/b/config.yaml", b"name: b\ncategory_id: old-b\n"),
                ("agents/c/config.yaml", b"name: c\ncategory_id: old-c\n"),
            ]
            archive = self._archive(base, members)
            for name, _ in members:
                target = live / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"changed\n")
            extra = live / "users/u2/agents/new/config.yaml"
            extra.parent.mkdir(parents=True)
            extra.write_bytes(b"new-row\n")
            self.assertEqual(MODULE.restore_archive(archive, live), 3)
            for name, data in members:
                self.assertEqual((live / name).read_bytes(), data)
            self.assertEqual(extra.read_bytes(), b"new-row\n")

    def test_path_traversal_and_unsupported_member_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            live = base / "live"
            live.mkdir()
            for name in ("../escape/config.yaml", "unrelated/config.yaml"):
                archive = self._archive(base, [(name, b"x")])
                with self.assertRaisesRegex(ValueError, "unsafe"):
                    MODULE.restore_archive(archive, live)

    def test_duplicate_member_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            live = base / "live"
            live.mkdir()
            archive = base / "duplicate.zip"
            with zipfile.ZipFile(str(archive), "w") as handle:
                handle.writestr("agents/a/config.yaml", b"first")
                handle.writestr("agents/a/config.yaml", b"second")
            with self.assertRaisesRegex(ValueError, "duplicate"):
                MODULE.restore_archive(archive, live)


if __name__ == "__main__":
    unittest.main()
