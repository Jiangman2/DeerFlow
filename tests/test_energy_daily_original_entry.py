"""Behavioral tests for original-source preservation and the outside-only launcher."""

import importlib.util
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
SKILL = REPO / "skills/public/energy-market-daily"
spec = importlib.util.spec_from_file_location(
    "energy_entry", SKILL / "scripts/entry.py"
)
entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entry)


class EntryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.expected = entry.original_files()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="energy-original-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / "session"
        entry.init(self.project, "2026-11-07", self.expected)

    def test_all_original_files_match_pinned_archive(self):
        self.assertEqual(len(self.expected), 158)
        self.assertEqual(entry.original_files(), self.expected)

    def test_working_copy_starts_byte_identical(self):
        entry.verify_tree(self.project / "engine", self.expected, False)

    def test_two_sessions_do_not_share_writable_inputs(self):
        other = self.root / "second"
        entry.init(other, "2026-11-07", self.expected)
        path = self.project / "engine/app/data/news_2026-11-07.json"
        path.write_text("{}", encoding="utf-8")
        self.assertNotEqual(
            path.read_bytes(),
            (other / "engine/app/data/news_2026-11-07.json").read_bytes(),
        )
        self.assertEqual(entry.original_files(), self.expected)

    def test_mutable_data_is_allowed_but_programs_are_not(self):
        (self.project / "engine/app/data/new.json").write_text("{}")
        entry.project_meta(self.project, self.expected)
        (self.project / "engine/app/report_renderer.py").write_text('print("changed")')
        with self.assertRaisesRegex(ValueError, "ORIGINAL_FILE_CHANGED"):
            entry.project_meta(self.project, self.expected)

    def test_original_document_and_config_changes_rejected(self):
        for rel in ("docs/03-ACQUISITION.md", "config/schedule.json"):
            path = self.project / "engine" / rel
            original = path.read_bytes()
            path.write_bytes(original + b"\nchanged")
            with self.assertRaisesRegex(ValueError, "ORIGINAL_FILE_CHANGED"):
                entry.project_meta(self.project, self.expected)
            path.write_bytes(original)

    def test_added_importable_module_rejected(self):
        (self.project / "engine/app/json.py").write_text("pass")
        with self.assertRaisesRegex(ValueError, "ORIGINAL_FILE_CHANGED"):
            entry.project_meta(self.project, self.expected)

    def test_missing_program_rejected(self):
        (self.project / "engine/app/report_data.py").unlink()
        with self.assertRaisesRegex(ValueError, "ORIGINAL_FILE_MISSING"):
            entry.project_meta(self.project, self.expected)

    def test_no_overwrite_existing_session(self):
        with self.assertRaisesRegex(ValueError, "PROJECT_EXISTS"):
            entry.init(self.project, "2026-11-07", self.expected)

    def test_date_normalization_and_future_editions_rejected(self):
        for day in ("2099-01-01", "20261107"):
            with self.assertRaises(ValueError):
                entry.init(self.root / day, day, self.expected)

    def test_omitted_date_is_beijing_today_and_persisted(self):
        import datetime
        import json

        class FixedClock(datetime.datetime):
            @classmethod
            def now(cls, tz=None):
                instant = cls(2026, 11, 9, 16, 30, tzinfo=datetime.timezone.utc)
                return instant.astimezone(tz)

        project = self.root / "default-date"
        with patch.object(entry.dt, "datetime", FixedClock):
            result = entry.init(project, None, self.expected)
        self.assertEqual(result["publication_date"], "2026-11-10")
        self.assertEqual(
            json.loads((project / "PROJECT.json").read_bytes())["publication_date"],
            "2026-11-10",
        )

    def test_original_validator_runs_successfully(self):
        before = entry.input_hashes(self.project / "engine")
        output = entry.original_command(
            self.project, "validate_inputs.py", ["--date", "2026-11-07"]
        )
        self.assertIn(b"PASS:", output)
        self.assertEqual(before, entry.input_hashes(self.project / "engine"))
        entry.verify_tree(self.project / "engine", self.expected, True)

    def test_original_nonzero_exit_blocks_wrapper(self):
        (self.project / "engine/app/data/financial_2026-11-06.json").unlink()
        with self.assertRaisesRegex(
            ValueError, "ORIGINAL_COMMAND_FAILED_VALIDATE_INPUTS"
        ):
            entry.original_command(
                self.project, "validate_inputs.py", ["--date", "2026-11-07"]
            )

    def test_incomplete_market_cache_does_not_call_original_fetch(self):
        cache = self.root / "empty-cache"
        cache.mkdir()
        with patch.object(entry, "original_command") as run:
            with self.assertRaises(FileNotFoundError):
                entry.import_market(
                    self.project, cache, entry.project_meta(self.project, self.expected)
                )
            run.assert_not_called()

    def test_original_market_assembly_uses_complete_local_cache(self):
        result = entry.import_market(
            self.project,
            SKILL / "upstream/app/sources",
            entry.project_meta(self.project, self.expected),
        )
        self.assertEqual(result["cache_days"], 93)
        self.assertEqual(result["status"], "ORIGINAL_MARKET_SCRIPT_COMPLETED")
        entry.verify_tree(self.project / "engine", self.expected, True)

    def test_original_archive_tamper_rejected(self):
        copy = self.root / "skill"
        shutil.copytree(SKILL, copy)
        with (copy / "assets/energy-daily-v1.0.original.zip").open("ab") as f:
            f.write(b"x")
        with patch.object(entry, "ROOT", copy):
            with self.assertRaisesRegex(ValueError, "ORIGINAL_ARCHIVE_CHANGED"):
                entry.original_files()

    def test_shared_original_tamper_rejected(self):
        copy = self.root / "skill"
        shutil.copytree(SKILL, copy)
        (copy / "upstream/app/report_renderer.py").write_text("pass")
        with patch.object(entry, "ROOT", copy):
            with self.assertRaisesRegex(ValueError, "ORIGINAL_FILE_CHANGED"):
                entry.original_files()

    def test_visual_review_is_required_before_original_complete(self):
        with self.assertRaisesRegex(ValueError, "VISUAL_REVIEW_REQUIRED"):
            entry.complete(
                self.project,
                self.expected,
                entry.project_meta(self.project, self.expected),
                False,
            )

    def test_private_environment_is_not_forwarded(self):
        seen = {}

        class Child:
            returncode = 0

            def __init__(self, *args, **kwargs):
                seen.update(kwargs["env"])

            def wait(self, **kwargs):
                return 0

        with patch.dict(
            os.environ,
            {
                "JKX_API_PASSWORD": "synthetic-private",
                "GH_TOKEN": "synthetic-private",
                "HTTPS_PROXY": "synthetic-private",
            },
        ):
            with patch.object(entry.subprocess, "Popen", Child):
                entry.original_command(self.project, "validate_inputs.py", [])
        self.assertNotIn("JKX_API_PASSWORD", seen)
        self.assertNotIn("GH_TOKEN", seen)
        self.assertNotIn("HTTPS_PROXY", seen)


if __name__ == "__main__":
    unittest.main()
