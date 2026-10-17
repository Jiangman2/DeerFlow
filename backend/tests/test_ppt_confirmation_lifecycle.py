"""Transport-independent consent tests; no browser, model, SQL or production calls.

The record must be committed by a trusted owner-scoped CAS repository BEFORE
an effect is dispatched. These unit tests are not end-to-end acceptance.
"""

import importlib.util
import json
import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "packages/harness/deerflow/ppt_confirmation/lifecycle.py"
spec = importlib.util.spec_from_file_location("confirmation_lifecycle_test", SOURCE)
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)


class ConfirmationLifecycleTests(unittest.TestCase):
    def policy(self, enabled=True, fresh=True):
        return core.new_policy("owner-1", "thread-1", "task-1", runtime_profile="ppt-master-runner", skill_name="ppt-master", opted_in=enabled, new_task=fresh)

    def gate(self, policy=None, **overrides):
        values = dict(project_id="project-1", stage="stage1", version="a" * 64, payload_digest="b" * 64, source_digest="c" * 64, now=1000)
        values.update(overrides)
        return core.new_gate(policy or self.policy(), **values)

    def claim(self, row, now=1060, mode="automatic", operation_id="submit-1", **overrides):
        args = dict(actor_id="owner-1", expected_revision=row["revision"], operation_id=operation_id, mode=mode, now=now, version=row["version"], payload_digest=row["payload_digest"], source_digest=row["source_digest"])
        args.update(overrides)
        return core.claim_submission(row, **args)

    def ready_at(self, row, now):
        return core.observe_ready(row, now=now, version=row["version"], payload_digest=row["payload_digest"], source_digest=row["source_digest"])

    def tick(self, row):
        # A server observer, not a browser, supplies each fresh owner receipt.
        for now in range(1005, 1061, 5):
            row = self.ready_at(row, now)
        return row

    def confirmed(self):
        row = self.claim(self.tick(self.gate()))
        return core.record_confirmation(row, version="a" * 64, payload_digest="b" * 64, source_digest="c" * 64, receipt_digest="d" * 64)

    def test_default_and_legacy_are_manual(self):
        for policy in (self.policy(False), self.policy(True, False)):
            row = self.tick(self.gate(policy))
            self.assertIsNone(row["due_at"])
            with self.assertRaises(core.ConsentConflict):
                self.claim(row)

    def test_optin_is_strict_boolean_not_truthy(self):
        for value in ("true", 1, {}, None):
            with self.assertRaises(core.ConsentConflict):
                self.policy(value)

    def test_new_tasks_do_not_inherit_optin(self):
        self.assertTrue(self.policy()["auto_enabled"])
        self.assertFalse(core.new_policy("owner-1", "thread-2", "task-2", runtime_profile="ppt-master-runner", skill_name="ppt-master")["auto_enabled"])

    def test_other_skills_and_ordinary_runtime_cannot_opt_in(self):
        for profile, skill in (("lead_agent", "ppt-master"), ("ppt-master-runner", "another-skill"), ("", "")):
            with self.assertRaises(core.ConsentConflict):
                core.new_policy("owner-1", "thread-1", "task-1", runtime_profile=profile, skill_name=skill, opted_in=True)
        policy = self.policy()
        policy["runtime_profile"] = "lead_agent"
        with self.assertRaises(core.ConsentConflict):
            self.gate(policy)

    def test_countdown_works_without_any_page_open_event(self):
        row = self.tick(self.gate())
        self.assertEqual(row["due_at"], 1060)
        claimed = self.claim(row)
        self.assertEqual(claimed["state"], "submission_pending")
        self.assertEqual(claimed["submission"]["mode"], "automatic")
        self.assertEqual(row["state"], "waiting_user")  # pure transition

    def test_not_one_second_early(self):
        row = self.tick(self.gate())
        with self.assertRaises(core.ConsentConflict):
            self.claim(row, now=1059)

    def test_periodic_observation_does_not_postpone_deadline(self):
        self.assertEqual(self.tick(self.gate())["due_at"], 1060)

    def test_page_recovery_does_not_restore_expired_auto_authority(self):
        row = core.recover(self.tick(self.gate()))
        row = self.ready_at(row, 2000)
        self.assertEqual(row["state"], "waiting_user")
        self.assertIsNone(row["due_at"])
        self.assertEqual(row["auto_block_reason"], "observer_recovered")
        with self.assertRaises(core.ConsentConflict):
            self.claim(row, now=2000)

    def test_long_observation_gap_or_backward_clock_disarms(self):
        for now in (999, 1021):
            row = self.ready_at(self.gate(), now)
            self.assertIsNone(row["due_at"])
            self.assertEqual(row["auto_block_reason"], "observation_gap")

    def test_recommendation_or_actual_default_payload_change_supersedes(self):
        for field in ("version", "payload_digest", "source_digest"):
            args = {name: self.gate()[name] for name in ("version", "payload_digest", "source_digest")}
            args[field] = "e" * 64
            row = core.observe_ready(self.gate(), now=1005, **args)
            self.assertEqual(row["state"], "superseded")
            with self.assertRaises(core.ConsentConflict):
                self.claim(row)

    def test_new_version_of_same_gate_requires_manual_confirmation(self):
        previous = self.gate()
        row = self.gate(previous=previous, version="e" * 64, now=1060)
        self.assertIsNone(row["due_at"])
        self.assertEqual(row["auto_block_reason"], "recommendation_changed")

    def test_subsequent_stage_can_use_same_explicit_task_policy(self):
        row = self.gate(previous=self.confirmed(), stage="stage2", now=1100)
        self.assertEqual(row["due_at"], 1160)

    def test_manual_takeover_disarms_and_preserves_gate(self):
        row = core.take_over(self.gate(), actor_id="owner-1", expected_revision=0)
        self.assertIsNone(row["due_at"])
        with self.assertRaises(core.ConsentConflict):
            self.claim(self.tick(row))
        self.assertEqual(self.claim(self.tick(row), mode="manual")["state"], "submission_pending")

    def test_stale_revision_and_other_user_cannot_claim(self):
        row = self.tick(self.gate())
        for kw in ({"expected_revision": 0}, {"actor_id": "owner-2"}):
            with self.assertRaises(core.ConsentConflict):
                self.claim(row, **kw)

    def test_uncertain_post_is_not_retried_even_after_restart(self):
        row = core.recover(self.claim(self.tick(self.gate())))
        self.assertEqual(row["state"], "submission_unknown")
        for operation in ("submit-1", "submit-2"):
            with self.assertRaises(core.ConsentConflict):
                self.claim(row, operation_id=operation)

    def test_receipt_not_http_success_controls_confirmation(self):
        row = self.claim(self.tick(self.gate()))
        self.assertEqual(row["state"], "submission_pending")
        with self.assertRaises(core.ConsentConflict):
            core.record_confirmation(row, version="e" * 64, payload_digest="b" * 64, source_digest="c" * 64, receipt_digest="d" * 64)
        self.assertEqual(self.confirmed()["state"], "confirmed")

    def test_unknown_submission_can_be_reconciled_without_reposting(self):
        row = core.recover(self.claim(self.tick(self.gate())))
        row = core.record_confirmation(row, version="a" * 64, payload_digest="b" * 64, source_digest="c" * 64, receipt_digest="d" * 64)
        self.assertEqual(row["state"], "confirmed")

    def test_running_original_agent_requires_no_second_start(self):
        row = core.claim_continuation(self.confirmed(), actor_id="owner-1", expected_revision=self.confirmed()["revision"], original_turn="running", run_id="run-original")
        self.assertEqual(row["continuation"]["action"], "observe_existing")

    def test_clean_handoff_is_reserved_once_and_not_blindly_replayed(self):
        row = self.confirmed()
        row = core.claim_continuation(row, actor_id="owner-1", expected_revision=row["revision"], original_turn="waiting_user", run_id="run-resume")
        self.assertEqual(row["continuation"]["action"], "resume_same_session")
        row = core.recover(row)
        self.assertEqual(row["state"], "continuation_unknown")
        with self.assertRaises(core.ConsentConflict):
            core.claim_continuation(row, actor_id="owner-1", expected_revision=row["revision"], original_turn="waiting_user", run_id="run-duplicate")

    def test_failed_cancelled_unknown_or_delivered_turn_does_not_auto_resume(self):
        for status in ("failed", "cancelled", "unknown", "completed", "cleanup_pending"):
            row = self.confirmed()
            with self.assertRaises(core.ConsentConflict):
                core.claim_continuation(row, actor_id="owner-1", expected_revision=row["revision"], original_turn=status, run_id="run-resume")

    def test_cancel_is_terminal_even_when_late_receipt_arrives(self):
        row = core.cancel(self.claim(self.tick(self.gate())), actor_id="owner-1")
        with self.assertRaises(core.ConsentConflict):
            core.record_confirmation(row, version="a" * 64, payload_digest="b" * 64, source_digest="c" * 64, receipt_digest="d" * 64)
        self.assertEqual(row["state"], "cancelled")

    def test_serialization_preserves_deadline_and_pending_operation(self):
        row = self.claim(self.tick(self.gate()))
        loaded = json.loads(json.dumps(row))
        self.assertEqual(loaded, row)
        self.assertNotIn("prompt", loaded)
        self.assertNotIn("payload", loaded)


if __name__ == "__main__":
    unittest.main()
