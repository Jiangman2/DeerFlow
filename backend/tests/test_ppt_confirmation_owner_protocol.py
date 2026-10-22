"""Versioned native-owner transactions; no provider, Agent or production data."""

from __future__ import annotations

import copy
import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from test_ppt_confirmation_proposal import OfficialFixture


class OwnerProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ppt-owner-transaction-")
        self.addCleanup(self.temp.cleanup)
        # Explicit opt-in is only the OWNER protocol test, not user consent.
        with patch.dict(os.environ, {"PPT_MASTER_CONFIRMATION_PROTOCOL": "1"}):
            self.fixture = OfficialFixture(self.temp.name)

    def payload(self, operation="op-one"):
        rec = self.fixture.get("/api/recommendations")
        return {
            "stage": "stage1",
            "primary_language": "zh-CN",
            "canvas": "ppt169",
            "template_selection": {"mode": "free_design", "selection_keys": []},
            "options_sha256": rec["template_options"]["options_sha256"],
            "_confirmation_request": {"protocol": 1, "version": rec["confirmation_protocol"]["version"], "operation_id": operation},
        }

    def test_receipt_by_version_observes_manual_winner_without_reposting(self):
        payload = self.payload("manual-winner")
        version = payload["_confirmation_request"]["version"]
        path = "/api/confirmation-receipts/by-version/" + version
        self.assertEqual(self.fixture.get(path)["state"], "not_found")
        payload["canvas"] = "ppt43"
        self.assertEqual(self.fixture.submit(payload).status_code, 200)
        before = {p.name: p.read_bytes() for p in self.fixture.confirm.iterdir() if p.is_file()}
        proof = self.fixture.get(path)
        self.assertEqual(proof["operation_id"], "manual-winner")
        self.assertEqual(proof["state"], "committed")
        self.assertEqual(proof["version"], version)
        self.assertFalse(proof["retry_permitted"])
        self.assertNotIn("canvas", json.dumps(proof))
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.fixture.confirm.iterdir() if p.is_file()})
        self.assertEqual(self.fixture.get("/api/confirmation-receipts/by-version/" + "f" * 64)["state"], "not_found")

    def test_receipt_by_version_never_accepts_stale_native_result(self):
        payload = self.payload()
        self.assertEqual(self.fixture.submit(payload).status_code, 200)
        self.fixture.stage1["audience"]["value"] = "changed"
        self.fixture.write("recommendations.stage1.json", self.fixture.stage1)
        reply = self.fixture.client.get("/api/confirmation-receipts/by-version/" + payload["_confirmation_request"]["version"])
        self.assertEqual(reply.status_code, 409)
        self.assertEqual(reply.json["state"], "unconfirmed")

    def test_strict_owner_requires_version_and_has_no_write_on_missing_version(self):
        rec = self.fixture.get("/api/recommendations")
        reply = self.fixture.submit({"stage": "stage1", "template_selection": {"mode": "free_design", "selection_keys": []}, "options_sha256": rec["template_options"]["options_sha256"]})
        self.assertEqual(reply.status_code, 409)
        self.assertFalse((self.fixture.confirm / "result.json").exists())
        self.assertFalse((self.fixture.confirm / "template_selection.json").exists())

    def test_success_is_bound_and_duplicate_is_read_only(self):
        payload = self.payload()
        reply = self.fixture.submit(payload)
        self.assertEqual(reply.status_code, 200, reply.json)
        files = {name: (self.fixture.confirm / name).read_bytes() for name in ("result.json", "template_selection.json")}
        repeat = self.fixture.submit(payload)
        self.assertEqual(repeat.status_code, 200, repeat.json)
        self.assertTrue(repeat.json["duplicate"])
        for name, raw in files.items():
            self.assertEqual((self.fixture.confirm / name).read_bytes(), raw)
        record = json.loads(files["result.json"])
        self.assertNotIn("_confirmation_request", record)
        self.assertEqual(record["_confirmation_receipt"]["operation_id"], "op-one")
        self.assertEqual(self.fixture.module._wait_result_status(self.fixture.confirm / "result.json", "stage1"), 0)

    def test_changed_recommendation_refuses_stale_submission(self):
        payload = self.payload()
        self.fixture.stage1["audience"]["value"] = "updated audience"
        self.fixture.write("recommendations.stage1.json", self.fixture.stage1)
        reply = self.fixture.submit(payload)
        self.assertEqual(reply.status_code, 409)
        self.assertEqual(reply.json["error"], "confirmation_version_changed")
        self.assertFalse((self.fixture.confirm / "result.json").exists())

    def test_same_operation_with_different_payload_is_not_a_duplicate(self):
        payload = self.payload()
        self.assertEqual(self.fixture.submit(payload).status_code, 200)
        payload["canvas"] = "ppt43"
        reply = self.fixture.submit(payload)
        self.assertEqual(reply.status_code, 409)
        self.assertEqual(reply.json["error"], "confirmation_operation_conflict")

    def test_duplicate_after_content_change_is_not_success(self):
        payload = self.payload()
        self.assertEqual(self.fixture.submit(payload).status_code, 200)
        self.fixture.stage1["audience"]["value"] = "another round"
        self.fixture.write("recommendations.stage1.json", self.fixture.stage1)
        reply = self.fixture.submit(payload)
        self.assertEqual(reply.status_code, 409)
        self.assertIsNone(self.fixture.module._wait_result_status(self.fixture.confirm / "result.json", "stage1"))

    def test_two_simultaneous_different_submissions_have_one_winner(self):
        first = self.payload("manual")
        second = copy.deepcopy(first)
        second["_confirmation_request"]["operation_id"] = "automatic"

        def submit(value):
            with self.fixture.app.test_client() as client:
                return client.post("/api/confirm", json=value).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(submit, [first, second]))
        self.assertEqual(sorted(results), [200, 409])

    def test_change_inside_validator_cannot_create_accepted_receipt(self):
        payload = self.payload()
        original = self.fixture.module._custom_selection_error

        def concurrent_edit(value):
            self.fixture.stage1["audience"]["value"] = "changed while validating"
            self.fixture.write("recommendations.stage1.json", self.fixture.stage1)
            return original(value)

        with patch.object(self.fixture.module, "_custom_selection_error", side_effect=concurrent_edit):
            reply = self.fixture.submit(payload)
        self.assertEqual(reply.status_code, 409)
        self.assertFalse((self.fixture.confirm / "result.json").exists())

    def test_partial_commit_may_not_release_waiter_or_replay(self):
        payload = self.payload()
        writer = self.fixture.module._write_json_atomic

        def fail_result(path, data):
            if path.name == "result.json":
                raise OSError("simulated disk failure private detail")
            return writer(path, data)

        with patch.object(self.fixture.module, "_write_json_atomic", side_effect=fail_result):
            reply = self.fixture.submit(payload)
        self.assertEqual(reply.status_code, 409)
        self.assertNotIn("private detail", str(reply.json))
        self.assertIsNone(self.fixture.module._wait_result_status(self.fixture.confirm / "result.json", "stage1"))
        self.assertEqual(self.fixture.submit(payload).status_code, 409)

    def test_unenabled_official_client_preserves_manual_contract(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"PPT_MASTER_CONFIRMATION_PROTOCOL": "0"}):
            fixture = OfficialFixture(tmp)
            rec = fixture.get("/api/recommendations")
            self.assertNotIn("confirmation_protocol", rec)
            reply = fixture.submit({"stage": "stage1", "template_selection": {"mode": "free_design", "selection_keys": []}, "options_sha256": rec["template_options"]["options_sha256"]})
            self.assertEqual(reply.status_code, 200, reply.json)

    def test_receipt_tampering_is_not_completion(self):
        payload = self.payload()
        self.assertEqual(self.fixture.submit(payload).status_code, 200)
        path = self.fixture.confirm / "result.json"
        record = json.loads(path.read_bytes())
        record["canvas"] = "ppt43"
        self.fixture.write("result.json", record)
        self.assertIsNone(self.fixture.module._wait_result_status(path, "stage1"))

    def test_protocol_has_no_auto_timer_or_agent_dispatch(self):
        # Enabling conditional submission alone never gives permission to click.
        self.payload()
        self.assertFalse((self.fixture.confirm / "result.json").exists())

    def test_owner_restart_invalidates_unsent_version_but_reconciles_completed_operation(self):
        payload = self.payload()
        self.assertEqual(self.fixture.submit(payload).status_code, 200)
        with patch.dict(os.environ, {"PPT_MASTER_CONFIRMATION_PROTOCOL": "1"}):
            app = self.fixture.module.create_app(str(self.fixture.project), idle_timeout=0, server_port=5050)
        client = app.test_client()
        reply = client.post("/api/confirm", json=payload)
        self.assertEqual(reply.status_code, 200, reply.json)
        self.assertTrue(reply.json["duplicate"])
        payload["_confirmation_request"]["operation_id"] = "another-operation"
        self.assertEqual(client.post("/api/confirm", json=payload).status_code, 409)

    def test_native_invalid_choices_cannot_create_journal_or_result(self):
        payload = self.payload()
        payload["template_selection"]["mode"] = "invented-mode"
        self.assertEqual(self.fixture.submit(payload).status_code, 400)
        self.assertFalse((self.fixture.confirm / ".confirmation-transaction.json").exists())
        self.assertFalse((self.fixture.confirm / "result.json").exists())

    def test_concurrent_edit_after_result_write_keeps_wait_blocked(self):
        payload = self.payload()
        original = self.fixture.module._write_json_atomic

        def edit_after_write(path, data):
            original(path, data)
            if path.name == "result.json":
                self.fixture.stage1["audience"]["value"] = "concurrent edit before commit acknowledgement"
                self.fixture.write("recommendations.stage1.json", self.fixture.stage1)

        with patch.object(self.fixture.module, "_write_json_atomic", side_effect=edit_after_write):
            reply = self.fixture.submit(payload)
        self.assertEqual(reply.status_code, 409)
        self.assertIsNone(self.fixture.module._wait_result_status(self.fixture.confirm / "result.json", "stage1"))
        self.assertEqual(self.fixture.submit(payload).status_code, 409)

    def receipt(self, operation="op-one"):
        return self.fixture.client.get("/api/confirmation-receipts/" + operation)

    def test_readonly_reconciliation_returns_bound_receipt_without_post_replay(self):
        payload = self.payload()
        self.assertEqual(self.fixture.submit(payload).status_code, 200)
        before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.fixture.confirm.iterdir() if p.is_file()}
        reply = self.receipt()
        self.assertEqual(reply.status_code, 200, reply.json)
        self.assertEqual(reply.json["state"], "committed")
        self.assertEqual(reply.json["version"], payload["_confirmation_request"]["version"])
        self.assertEqual(reply.json["operation_id"], "op-one")
        self.assertEqual(reply.json["stage"], "stage1")
        self.assertEqual(len(reply.json["receipt_sha256"]), 64)
        self.assertEqual(reply.headers["Cache-Control"], "no-store")
        after = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.fixture.confirm.iterdir() if p.is_file()}
        self.assertEqual(before, after)
        self.assertNotIn("本地验收用户", str(reply.json))
        self.assertNotIn(str(self.fixture.project), str(reply.json))

    def test_unknown_operation_is_not_permission_to_replay_and_writes_no_file(self):
        before = set(self.fixture.confirm.iterdir())
        reply = self.receipt("never-submitted")
        self.assertEqual(reply.status_code, 200)
        self.assertEqual(reply.json, {"protocol": 1, "state": "not_found", "retry_permitted": False})
        self.assertEqual(set(self.fixture.confirm.iterdir()), before)

    def test_prepared_and_stale_receipts_are_not_reported_as_committed(self):
        payload = self.payload()
        self.assertEqual(self.fixture.submit(payload).status_code, 200)
        for invalidation in ("recommendations", "journal"):
            if invalidation == "recommendations":
                self.fixture.stage1["audience"]["value"] = "changed input"
                self.fixture.write("recommendations.stage1.json", self.fixture.stage1)
            else:
                path = self.fixture.confirm / ".confirmation-transaction.json"
                journal = json.loads(path.read_bytes())
                journal["state"] = "prepared"
                self.fixture.write(path.name, journal)
            reply = self.receipt()
            self.assertEqual(reply.status_code, 409)
            self.assertEqual(reply.json, {"protocol": 1, "state": "unconfirmed", "retry_permitted": False})

    def test_readonly_receipt_survives_owner_process_epoch_change(self):
        self.assertEqual(self.fixture.submit(self.payload()).status_code, 200)
        with patch.dict(os.environ, {"PPT_MASTER_CONFIRMATION_PROTOCOL": "1"}):
            app = self.fixture.module.create_app(str(self.fixture.project), idle_timeout=0, server_port=5050)
        response = app.test_client().get("/api/confirmation-receipts/op-one")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["state"], "committed")

    def test_legacy_owner_does_not_expose_conditional_receipt(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"PPT_MASTER_CONFIRMATION_PROTOCOL": "0"}):
            fixture = OfficialFixture(tmp)
            self.assertEqual(fixture.client.get("/api/confirmation-receipts/op-one").status_code, 404)

    def test_protocol_task_cannot_release_wait_with_unbound_legacy_result(self):
        self.assertEqual(self.fixture.submit(self.payload()).status_code, 200)
        path = self.fixture.confirm / "result.json"
        result = json.loads(path.read_bytes())
        del result["_confirmation_receipt"]
        self.fixture.write(path.name, result)
        with patch.dict(os.environ, {"PPT_MASTER_CONFIRMATION_PROTOCOL": "1"}):
            self.assertIsNone(self.fixture.module._wait_result_status(path, "stage1"))


if __name__ == "__main__":
    unittest.main()
