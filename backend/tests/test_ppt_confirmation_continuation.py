"""Disposable SQL + exact-session continuation; no production/model launch."""

import unittest
from unittest.mock import AsyncMock

import test_ppt_confirmation_controller as fixtures

from deerflow.ppt_confirmation.continuation import ConfirmationContinuation
from deerflow.ppt_confirmation.lifecycle import ConsentConflict


class ContinuationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixtures.ControllerTests.asyncSetUp(self)
        await self.repo.admit(self.scope, runtime_profile="ppt-master-runner", skill_name="ppt-master", opted_in=True)
        for self.now in range(1000, 1065, 5):
            self.snapshot = await self.controller.tick(self.scope)
        self.task = {
            "task_id": self.scope.task_id,
            "thread_id": self.scope.thread_id,
            "session_id": "original-session",
            "status": "completed",
            "process_cleanup_complete": True,
            "artifacts": [],
            "confirmation_protocol": "ppt-confirmation-v1",
        }
        self.proof = {"state": "dispatched", "task_id": self.scope.task_id, "thread_id": self.scope.thread_id, "gate_id": self.snapshot["gate"]["gate_id"], "receipt_digest": "d" * 64, "retry_permitted": False}
        self.owner.continue_guarded = AsyncMock()
        self.owner.continuation_receipt = AsyncMock(side_effect=lambda *_: self.proof)
        self.flow = ConfirmationContinuation(self.repo, self.owner)

    async def asyncTearDown(self):
        await fixtures.ControllerTests.asyncTearDown(self)

    async def test_committed_confirmation_resumes_only_original_session_once(self):
        state = await self.flow.tick(self.scope, self.snapshot, self.task)
        self.assertEqual(state["gate"]["state"], "continuing")
        args = self.owner.continue_guarded.call_args.args
        self.assertEqual(args[2], "original-session")
        self.assertEqual(args[1]["continuation"], {"run_id": self.scope.task_id, "action": "resume_same_session"})
        await self.flow.tick(self.scope, state, self.task)
        self.owner.continue_guarded.assert_awaited_once()

    async def test_running_failed_cancelled_delivered_or_unclean_task_never_restarted(self):
        for change in ({"status": "running"}, {"status": "failed"}, {"status": "cancelled"}, {"artifacts": ["deck.pptx"]}, {"process_cleanup_complete": False}):
            state = await self.flow.tick(self.scope, self.snapshot, {**self.task, **change})
            self.assertEqual(state["gate"]["state"], "confirmed")
        self.owner.continue_guarded.assert_not_awaited()

    async def test_unknown_launch_ack_reconciles_without_second_post(self):
        self.owner.continue_guarded.side_effect = TimeoutError("fixture-private-detail")
        self.proof["state"] = "dispatch_intent"
        state = await self.flow.tick(self.scope, self.snapshot, self.task)
        self.assertEqual(state["gate"]["state"], "continuation_unknown")
        for _ in range(3):
            state = await self.flow.tick(self.scope, state, self.task)
        self.owner.continue_guarded.assert_awaited_once()
        self.proof["state"] = "dispatched"
        state = await self.flow.tick(self.scope, state, self.task)
        self.assertEqual(state["gate"]["state"], "continuing")

    async def test_wrong_task_or_legacy_protocol_never_grants_resume(self):
        for change in ({"task_id": "another"}, {"thread_id": "another"}, {"confirmation_protocol": None}, {"cancel_requested": True}):
            with self.subTest(change=change), self.assertRaises(ConsentConflict):
                await self.flow.tick(self.scope, self.snapshot, {**self.task, **change})
        self.owner.continue_guarded.assert_not_awaited()

    async def test_foreign_launch_receipt_is_not_accepted(self):
        self.proof["task_id"] = "another"
        with self.assertRaises(ConsentConflict):
            await self.flow.tick(self.scope, self.snapshot, self.task)
        self.assertEqual((await self.repo.get(self.scope))["gate"]["state"], "continuation_unknown")

    async def test_cancel_between_claim_and_dispatch_ack_never_marks_continuing(self):
        async def cancel(*_):
            row = await self.repo.get(self.scope)
            await self.repo.cancel_task(self.scope, expected_revision=row["revision"])

        self.owner.continue_guarded.side_effect = cancel
        state = await self.flow.tick(self.scope, self.snapshot, self.task)
        self.assertTrue(state["cancelled"])
        self.owner.continuation_receipt.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
