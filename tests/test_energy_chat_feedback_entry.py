"""Chat-feedback regressions: edition identity, sample isolation and clear blockers.

No real HTTP requests, credentials, browser rendering or Work messages are used.
"""

import contextlib
import datetime
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch


BASE = Path(__file__).resolve().parents[1]
SKILL = BASE / "release/post44-source-sync-20261113/parent/skills/public/energy-market-daily"
SPEC = importlib.util.spec_from_file_location("energy_chat_feedback_entry", SKILL / "scripts/entry.py")
entry = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(entry)
PRIVATE = "PRIVATE_SENTINEL_NOT_FOR_REPORT_72109"


class EntryFeedbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.expected = entry.original_files()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="energy-chat-feedback-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.project = self.root / "session"
        entry.init(self.project, "2026-11-07", self.expected)

    def meta(self):
        return entry.project_meta(self.project, self.expected)

    def replace_meta(self, **changes):
        path = self.project / "PROJECT.json"
        value = json.loads(path.read_bytes())
        value.update(changes)
        path.write_text(json.dumps(value), encoding="utf-8")
        return self.meta()

    def input(self, name):
        return self.project / "engine/app/data" / name

    def seed_output(self):
        output = self.project / "engine/app/output"
        output.mkdir(parents=True, exist_ok=True)
        for suffix in (".png", ".html", ".json"):
            sealed = self.project / "engine/reference" / ("approved-2026-11-07" + suffix)
            (output / ("油气与金融市场日报_2026-11-07" + suffix)).write_bytes(sealed.read_bytes())
        return output

    def invoke(self, *args):
        output = io.StringIO()
        with patch.object(sys, "argv", ["entry.py", *args]), contextlib.redirect_stdout(output):
            code = entry.main()
        return code, json.loads(output.getvalue())

    def test_default_mode_is_generate_even_for_explicit_past_date(self):
        result = entry.edition(self.meta())
        self.assertEqual(result["mode"], "generate")
        self.assertEqual(result["publication_date"], "2026-11-07")
        self.assertEqual(result["data_date"], "2026-11-06")

    def test_no_date_uses_beijing_today_and_records_distinct_data_date(self):
        class FixedClock(datetime.datetime):
            @classmethod
            def now(cls, tz=None):
                instant = cls(2026, 11, 9, 16, 30, tzinfo=datetime.timezone.utc)
                return instant.astimezone(tz)

        project = self.root / "beijing-today"
        with patch.object(entry.dt, "datetime", FixedClock):
            result = entry.init(project, None, self.expected)
        meta = json.loads((project / "PROJECT.json").read_bytes())
        for value in (result, entry.edition(meta)):
            self.assertEqual(value["publication_date"], "2026-11-10")
            self.assertEqual(value["data_date"], "2026-11-09")
            self.assertEqual(value["mode"], "generate")

    def test_reproduction_requires_explicit_date_before_creating_project(self):
        project = self.root / "implicit-reproduction"
        with self.assertRaisesRegex(ValueError, "REPRODUCTION_DATE_REQUIRED"):
            entry.init(project, None, self.expected, mode="reproduce")
        self.assertFalse(project.exists())

    def test_empty_explicit_reproduction_date_cannot_default_to_today(self):
        project = self.root / "empty-reproduction-date"
        with self.assertRaisesRegex(ValueError, "REPRODUCTION_DATE_REQUIRED"):
            entry.init(project, "", self.expected, mode="reproduce")
        self.assertFalse(project.exists())

    def test_explicit_reproduction_is_persisted(self):
        project = self.root / "reproduction"
        result = entry.init(project, "2026-11-07", self.expected, mode="reproduce")
        meta = entry.project_meta(project, self.expected)
        self.assertEqual(result["mode"], "reproduce")
        self.assertEqual(meta["mode"], "reproduce")
        self.assertEqual(entry.edition(meta)["data_date"], "2026-11-06")
        entry.require_report_inputs(project, meta, self.expected)

    def test_legacy_metadata_mode_is_explicitly_unknown(self):
        path = self.project / "PROJECT.json"
        meta = json.loads(path.read_bytes())
        meta.pop("mode", None)
        path.write_text(json.dumps(meta), encoding="utf-8")
        self.assertEqual(entry.edition(self.meta())["mode"], "legacy_unspecified")
        with self.assertRaises(entry.WorkflowBlocked) as caught:
            entry.require_report_inputs(self.project, self.meta(), self.expected)
        self.assertEqual(caught.exception.report["error_code"], "SAMPLE_INPUTS_REQUIRE_REPRODUCTION")

    def test_null_legacy_mode_does_not_become_generate_or_reproduce(self):
        meta = self.replace_meta(mode=None)
        self.assertEqual(entry.edition(meta)["mode"], "legacy_unspecified")
        with self.assertRaises(entry.WorkflowBlocked):
            entry.require_report_inputs(self.project, meta, self.expected)

    def test_metadata_data_date_mismatch_is_refused(self):
        with self.assertRaisesRegex(ValueError, "PROJECT_DATA_DATE"):
            self.replace_meta(data_date="2026-11-07")

    def test_status_identifies_all_three_seed_snapshots_without_claiming_ready(self):
        result = entry.inputs_status(self.project, self.meta(), self.expected)
        self.assertEqual(result["status"], "INPUTS_OBSERVED")
        self.assertEqual({row["kind"] for row in result["input_states"]}, {"energy", "financial", "news"})
        self.assertEqual({row["state"] for row in result["input_states"]}, {"bundled_sample_identical"})
        self.assertTrue(result["all_inputs_present"])
        self.assertTrue(result["all_inputs_bundled_sample_identical"])
        self.assertFalse(result["formal_report_ready"])
        self.assertEqual(result["publication_date"], "2026-11-07")
        self.assertEqual(result["data_date"], "2026-11-06")

    def test_new_edition_never_uses_previous_sample_as_date_fallback(self):
        project = self.root / "new-edition"
        entry.init(project, "2026-11-10", self.expected)
        result = entry.inputs_status(project, entry.project_meta(project, self.expected), self.expected)
        self.assertEqual({row["state"] for row in result["input_states"]}, {"missing"})
        self.assertFalse(result["all_inputs_present"])
        self.assertFalse(result["all_inputs_bundled_sample_identical"])
        self.assertEqual(result["publication_date"], "2026-11-10")
        self.assertEqual(result["data_date"], "2026-11-09")
        for row in result["input_states"]:
            expected_date = "2026-11-10" if row["kind"] == "news" else "2026-11-09"
            self.assertIn(expected_date, row["path"])
        self.assertTrue((project / "engine/app/data/news_2026-11-07.json").is_file())

    def test_missing_one_input_has_fixed_dated_blocker_summary(self):
        self.input("financial_2026-11-06.json").unlink()
        with self.assertRaises(entry.WorkflowBlocked) as caught:
            entry.require_report_inputs(self.project, self.meta(), self.expected)
        report = caught.exception.report
        self.assertEqual(report["error_code"], "INPUTS_MISSING")
        self.assertEqual(report["publication_date"], "2026-11-07")
        self.assertEqual(report["data_date"], "2026-11-06")
        states = {row["kind"]: row["state"] for row in report["input_states"]}
        self.assertEqual(states["financial"], "missing")
        self.assertEqual(states["energy"], "bundled_sample_identical")

    def test_generate_with_only_bundled_samples_is_blocked(self):
        with self.assertRaises(entry.WorkflowBlocked) as caught:
            entry.require_report_inputs(self.project, self.meta(), self.expected)
        self.assertEqual(caught.exception.report["error_code"], "SAMPLE_INPUTS_REQUIRE_REPRODUCTION")
        self.assertEqual(caught.exception.report["mode"], "generate")

    def test_revised_target_snapshot_can_reuse_valid_historical_market_cache(self):
        path = self.input("news_2026-11-07.json")
        payload = json.loads(path.read_bytes())
        payload["retrieved_at"] = "2026-11-10T08:00:00+08:00"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        result = entry.inputs_status(self.project, self.meta(), self.expected)
        self.assertFalse(result["all_inputs_bundled_sample_identical"])
        states = {row["kind"]: row["state"] for row in result["input_states"]}
        self.assertEqual(states["news"], "present")
        self.assertEqual(states["energy"], "bundled_sample_identical")
        with patch.object(entry, "request_market", side_effect=AssertionError("must not require fresh network receipt")):
            entry.require_report_inputs(self.project, self.meta(), self.expected)

    def test_status_is_readonly_and_does_not_create_lock_or_request_market(self):
        before = {str(path.relative_to(self.project)): path.read_bytes() for path in self.project.rglob("*") if path.is_file()}
        with patch.object(entry, "request_market", side_effect=AssertionError("status must not call collector")), patch.object(entry, "original_command", side_effect=AssertionError("status must not execute originals")):
            code, result = self.invoke("status", "--project", str(self.project))
        self.assertEqual(code, 0)
        self.assertEqual(result["status"], "INPUTS_OBSERVED")
        self.assertFalse((self.project / ".entry.lock").exists())
        after = {str(path.relative_to(self.project)): path.read_bytes() for path in self.project.rglob("*") if path.is_file()}
        self.assertEqual(before, after)

    def test_status_does_not_touch_an_existing_owner_lock(self):
        lock = self.project / ".entry.lock"
        lock.write_text("87654", encoding="ascii")
        with patch.object(entry, "request_market", side_effect=AssertionError("status must not call collector")):
            code, result = self.invoke("status", "--project", str(self.project))
        self.assertEqual(code, 0)
        self.assertEqual(result["status"], "INPUTS_OBSERVED")
        self.assertEqual(lock.read_text(encoding="ascii"), "87654")

    def test_existing_project_date_and_mode_cannot_be_overridden_by_cli(self):
        before = (self.project / "PROJECT.json").read_bytes()
        for option in ("--date", "--mode"):
            value = "2026-11-10" if option == "--date" else "reproduce"
            code, result = self.invoke("status", "--project", str(self.project), option, value)
            self.assertEqual(code, 1)
            self.assertEqual(result["error_code"], "EDITION_OPTIONS_INIT_ONLY")
            self.assertEqual((self.project / "PROJECT.json").read_bytes(), before)
            self.assertFalse((self.project / ".entry.lock").exists())

    def test_missing_input_blocks_build_before_original_or_browser(self):
        self.input("financial_2026-11-06.json").unlink()
        with patch.object(entry, "original_command") as original, patch.object(entry, "report_browser") as browser:
            with self.assertRaises(entry.WorkflowBlocked) as caught:
                entry.build_report(self.project, self.meta(), self.expected, None)
        self.assertEqual(caught.exception.report["error_code"], "INPUTS_MISSING")
        original.assert_not_called()
        browser.assert_not_called()

    def test_sample_only_generate_blocks_build_before_original(self):
        with patch.object(entry, "original_command") as original:
            with self.assertRaises(entry.WorkflowBlocked) as caught:
                entry.build_report(self.project, self.meta(), self.expected, None)
        self.assertEqual(caught.exception.report["error_code"], "SAMPLE_INPUTS_REQUIRE_REPRODUCTION")
        original.assert_not_called()

    def test_cli_check_missing_input_keeps_structured_report_and_nonzero_exit(self):
        self.input("energy_2026-11-06.json").unlink()
        with patch.object(entry, "original_command") as original:
            code, result = self.invoke("check", "--project", str(self.project))
        self.assertEqual(code, 1)
        self.assertEqual(result["error_code"], "INPUTS_MISSING")
        self.assertEqual(result["publication_date"], "2026-11-07")
        self.assertEqual(result["data_date"], "2026-11-06")
        self.assertEqual(result["mode"], "generate")
        self.assertTrue(result["input_states"])
        self.assertFalse((self.project / ".entry.lock").exists())
        original.assert_not_called()

    def test_cli_reproduction_does_not_swallow_original_validator_failure(self):
        self.replace_meta(mode="reproduce")
        with patch.object(entry, "original_command", side_effect=ValueError("ORIGINAL_COMMAND_FAILED_VALIDATE_INPUTS")):
            code, result = self.invoke("check", "--project", str(self.project))
        self.assertEqual(code, 1)
        self.assertEqual(result["error_code"], "ORIGINAL_COMMAND_FAILED_VALIDATE_INPUTS")

    def test_cli_refresh_failure_preserves_collector_details_and_nonzero_exit(self):
        observed = {
            "status": "INCOMPLETE", "data_date": "2026-11-06",
            "error_code": "PROVIDER_REQUEST_FAILED", "failure_stage": "original_fetch",
            "connections": 4, "target_response_validated": False,
        }
        with patch.object(entry, "request_market", return_value=observed), patch.object(entry, "import_market") as imported:
            code, result = self.invoke("refresh-market", "--project", str(self.project))
        self.assertEqual(code, 1)
        self.assertEqual(result["error_code"], "COLLECTOR_FAILED")
        self.assertEqual(result["publication_date"], "2026-11-07")
        self.assertEqual(result["data_date"], "2026-11-06")
        self.assertEqual(result["collector"]["error_code"], "PROVIDER_REQUEST_FAILED")
        self.assertEqual(result["collector"]["connections"], 4)
        self.assertTrue(result["blocked_outputs"])
        self.assertFalse((self.project / ".entry.lock").exists())
        imported.assert_not_called()

    def test_reproduction_delivery_keeps_historical_identity_through_acceptance(self):
        meta = self.replace_meta(mode="reproduce")
        # Place the sealed reference in this test project's mutable output
        # directory. Mock execution to test wrapper identity without a browser.
        self.seed_output()
        def original(project, script, args, **kwargs):
            if script != "workflow_gate.py":
                return b"PASS"
            lock = project / "engine/app/state/2026-11-07.lock.json"
            if args[0] == "claim":
                lock.parent.mkdir(parents=True, exist_ok=True)
                lock.write_text('{"owner":"synthetic-owner"}', encoding="utf-8")
                return b'{"status":"CLAIMED","owner":"synthetic-owner"}'
            if args[0] == "complete":
                return b'{"status":"COMPLETE"}'
            if args[0] == "release":
                lock.unlink()
                return b'{"status":"RELEASED"}'
            raise AssertionError("unexpected original command")

        with patch.object(entry, "original_command", side_effect=original), patch.object(entry, "report_browser", return_value="synthetic-browser"):
            built = entry.build_report(self.project, meta, self.expected, None)
            inspected = entry.inspect(self.project, self.expected)
            accepted = entry.complete(self.project, self.expected, meta, True)
        for result in (built, inspected, accepted):
            self.assertEqual(result["publication_date"], "2026-11-07")
            self.assertEqual(result["data_date"], "2026-11-06")
            self.assertEqual(result["mode"], "reproduce")
            self.assertFalse(result["formal_report_ready"])
        self.assertEqual(accepted["status"], "ORIGINAL_REPORT_ACCEPTED")

    def test_old_delivery_without_mode_or_data_date_remains_readable_and_immutable(self):
        self.seed_output()
        meta_path = self.project / "PROJECT.json"
        meta = json.loads(meta_path.read_bytes())
        for field in ("mode", "data_date", "entry_revision", "date_selection"):
            meta.pop(field, None)
        meta_path.write_text(json.dumps(meta), encoding="utf-8")
        run = "a" * 32
        output = self.project / "deliveries" / run
        output.mkdir(parents=True)
        artifacts = {}
        for suffix in (".png", ".html", ".json"):
            name = "油气与金融市场日报_2026-11-07" + suffix
            raw = (self.project / "engine/app/output" / name).read_bytes()
            (output / name).write_bytes(raw)
            artifacts[name] = entry.sha(raw)
        receipt = {
            "schema": "energy-daily-original-run.v1", "publication_date": "2026-11-07",
            "archive_sha256": entry.ARCHIVE_SHA, "input_sha256": entry.input_hashes(self.project / "engine"),
            "artifacts": artifacts, "original_files_verified": 158, "business_code_modified": False,
            "provider_requests": 0, "work_messages_sent": 0,
        }
        entry.save(output / "RECEIPT.json", receipt)
        digest = entry.sha((output / "RECEIPT.json").read_bytes())
        entry.save(self.project / "LATEST.json", {"build_id": run, "receipt_sha256": digest})
        entry.save(output / "VISUAL-ACCEPTANCE.json", {"receipt_sha256": digest, "visual_reviewed": True})
        before = {str(path.relative_to(self.project)): path.read_bytes() for path in self.project.rglob("*") if path.is_file()}
        with patch.object(entry, "original_command", side_effect=AssertionError("already accepted legacy output must not be rerun")):
            inspected = entry.inspect(self.project, self.expected)
            completed = entry.complete(self.project, self.expected, self.meta(), True)
        for result in (inspected, completed):
            self.assertEqual(result["status"], "ORIGINAL_REPORT_ACCEPTED")
            self.assertEqual(result["mode"], "legacy_unspecified")
            self.assertEqual(result["publication_date"], "2026-11-07")
            self.assertEqual(result["data_date"], "2026-11-06")
            self.assertFalse(result["formal_report_ready"])
        after = {str(path.relative_to(self.project)): path.read_bytes() for path in self.project.rglob("*") if path.is_file()}
        self.assertEqual(before, after)

    def test_original_output_date_mismatch_cannot_be_delivered(self):
        meta = self.replace_meta(mode="reproduce")
        output = self.seed_output()
        path = output / "油气与金融市场日报_2026-11-07.json"
        snapshot = json.loads(path.read_bytes())
        snapshot["data_date"] = "2026-11-05"
        path.write_text(json.dumps(snapshot), encoding="utf-8")
        with patch.object(entry, "original_command", return_value=b"PASS"), patch.object(entry, "report_browser", return_value="synthetic-browser"):
            with self.assertRaisesRegex(ValueError, "OUTPUT_DATE"):
                entry.build_report(self.project, meta, self.expected, None)
        self.assertFalse((self.project / "LATEST.json").exists())
        self.assertFalse((self.project / "deliveries").exists())


class CollectorFeedbackTests(unittest.TestCase):
    def setUp(self):
        self.meta = {"publication_date": "2026-11-10", "mode": "generate"}
        self.project = Path("unused-synthetic-project")

    def incomplete(self, **extra):
        result = {
            "status": "INCOMPLETE", "data_date": "2026-11-09",
            "error_code": "PROVIDER_REQUEST_FAILED", "failure_stage": "original_fetch",
            "connections": 4, "target_response_validated": False,
            "error_class": "ValueError", "credentials_exported": False,
        }
        result.update(extra)
        return result

    def test_connection_failure_retains_facts_instead_of_claiming_missing_credential(self):
        with patch.object(entry, "request_market", return_value=self.incomplete()) as request, patch.object(entry, "import_market") as import_market:
            with self.assertRaises(entry.WorkflowBlocked) as caught:
                entry.refresh_market(self.project, self.meta)
        report = caught.exception.report
        self.assertEqual(report["error_code"], "COLLECTOR_FAILED")
        self.assertTrue(report["blocked_outputs"])
        self.assertEqual(report["publication_date"], "2026-11-10")
        self.assertEqual(report["data_date"], "2026-11-09")
        self.assertEqual(report["mode"], "generate")
        self.assertEqual(report["collector"]["error_code"], "PROVIDER_REQUEST_FAILED")
        self.assertEqual(report["collector"]["failure_stage"], "original_fetch")
        self.assertEqual(report["collector"]["connections"], 4)
        self.assertFalse(report["collector"]["target_response_validated"])
        request.assert_called_once_with("2026-11-09")
        import_market.assert_not_called()
        self.assertNotIn("CREDENTIAL_MISSING", json.dumps(report))

    def test_untrusted_error_fields_are_not_copied_to_agent_output(self):
        observed = self.incomplete(error_code=PRIVATE, failure_stage=PRIVATE, error_class=PRIVATE, connections=PRIVATE,
                                   target_response_validated=PRIVATE, raw_error=PRIVATE, password=PRIVATE)
        with patch.object(entry, "request_market", return_value=observed):
            with self.assertRaises(entry.WorkflowBlocked) as caught:
                entry.refresh_market(self.project, self.meta)
        report = caught.exception.report
        self.assertNotIn(PRIVATE, json.dumps(report))
        self.assertEqual(report["collector"]["error_code"], "UNKNOWN")
        self.assertEqual(report["collector"]["failure_stage"], "UNKNOWN")
        self.assertNotIn("password", report["collector"])
        self.assertNotIn("raw_error", report["collector"])

    def test_collector_connection_timeout_keeps_date_and_redacts_exception_text(self):
        with patch.object(entry, "request_market", side_effect=TimeoutError("url?p=" + PRIVATE)):
            with self.assertRaises(entry.WorkflowBlocked) as caught:
                entry.refresh_market(self.project, self.meta)
        report = caught.exception.report
        self.assertEqual(report["error_code"], "COLLECTOR_UNREACHABLE")
        self.assertEqual(report["publication_date"], "2026-11-10")
        self.assertEqual(report["data_date"], "2026-11-09")
        self.assertTrue(report["blocked_outputs"])
        self.assertNotIn(PRIVATE, json.dumps(report))

    def test_connection_count_is_bounded_and_not_a_boolean(self):
        for value in (-1, 189, True, 10**30):
            with self.subTest(value=value), patch.object(entry, "request_market", return_value=self.incomplete(connections=value)):
                with self.assertRaises(entry.WorkflowBlocked) as caught:
                    entry.refresh_market(self.project, self.meta)
            seen = caught.exception.report["collector"].get("connections")
            self.assertTrue(seen is None or (type(seen) is int and 0 <= seen <= 188))

    def test_wait_for_data_is_distinct_from_transport_failure(self):
        observed = {"status": "WAIT_DATA", "data_date": "2026-11-09", "connections": 2, "target_response_validated": True}
        with patch.object(entry, "request_market", return_value=observed) as request, patch.object(entry, "import_market") as import_market:
            result = entry.refresh_market(self.project, self.meta)
        self.assertEqual(result["status"], "WAIT_PROVIDER_DATA")
        self.assertTrue(result["blocked_outputs"])
        self.assertEqual(result["publication_date"], "2026-11-10")
        self.assertEqual(result["data_date"], "2026-11-09")
        self.assertEqual(result["mode"], "generate")
        self.assertNotEqual(result.get("error_code"), "COLLECTOR_FAILED")
        request.assert_called_once()
        import_market.assert_not_called()

    def test_ready_complete_cache_needs_no_new_network_receipt(self):
        observed = {"status": "READY", "data_date": "2026-11-09", "connections": 0, "target_response_validated": True}
        with patch.object(entry, "request_market", return_value=observed), patch.object(entry, "import_market", return_value={"status": "ORIGINAL_MARKET_SCRIPT_COMPLETED", "cache_days": 93}) as import_market:
            result = entry.refresh_market(self.project, self.meta)
        self.assertEqual(result["status"], "ORIGINAL_MARKET_SCRIPT_COMPLETED")
        self.assertEqual(result["publication_date"], "2026-11-10")
        self.assertEqual(result["data_date"], "2026-11-09")
        self.assertEqual(result["mode"], "generate")
        import_market.assert_called_once_with(self.project, entry.ROOT.parent / "energy-market-data", self.meta)

    def test_collecting_then_incomplete_stops_without_repeated_failure_requests(self):
        states = [{"status": "COLLECTING", "data_date": "2026-11-09"}, self.incomplete()]
        with patch.object(entry, "request_market", side_effect=states) as request, patch.object(entry.time, "sleep") as sleep:
            with self.assertRaises(entry.WorkflowBlocked):
                entry.refresh_market(self.project, self.meta)
        self.assertEqual(request.call_count, 2)
        sleep.assert_called_once_with(5)


if __name__ == "__main__":
    unittest.main()
