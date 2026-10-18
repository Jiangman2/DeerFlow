"""Actual monitor/controller/SQL transitions; owner is a disposable fake.

No user browser, model, production DB or Agent is involved. Runtime continuation
and real container qualification are separate acceptance requirements.
"""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import test_ppt_confirmation_controller as fixtures

from deerflow.ppt_confirmation.monitor import ConfirmationMonitor, public_status


class MonitorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixtures.ControllerTests.asyncSetUp(self)
        await self.repo.admit(self.scope, runtime_profile="ppt-master-runner", skill_name="ppt-master", opted_in=True)
        self.monitor = ConfirmationMonitor(self.controller, self.scope)

    async def asyncTearDown(self):
        await fixtures.ControllerTests.asyncTearDown(self)

    async def test_original_run_observer_confirms_once_without_opening_a_browser(self):
        for now in range(1000, 1060, 5):
            self.now = now
            await self.monitor.step()
        self.assertEqual(self.owner.posts, [])
        waiting = self.monitor.take_notice()
        self.assertEqual(waiting["state"], "waiting_user")
        self.assertEqual(waiting["due_at"], 1060)
        self.assertNotIn("payload", waiting)
        self.now = 1060
        await self.monitor.step()
        self.assertEqual(len(self.owner.posts), 1)
        self.assertEqual(self.monitor.take_notice()["state"], "confirmed")
        self.now = 1065
        await self.monitor.step()
        self.assertEqual(len(self.owner.posts), 1)

    async def test_unknown_ack_keeps_original_claim_no_repeated_submit(self):
        self.owner.unknown = True
        for now in range(1000, 1100, 5):
            self.now = now
            await self.monitor.step()
        self.assertEqual(len(self.owner.posts), 1)
        self.assertEqual(self.monitor.take_notice()["state"], "submission_unknown")

    async def test_user_disable_revokes_countdown_before_effect(self):
        await self.monitor.step()
        initial = await self.repo.get(self.scope)
        await self.repo.disable_auto(self.scope, expected_revision=initial["revision"])
        for now in range(1005, 1070, 5):
            self.now = now
            await self.monitor.step()
        self.assertEqual(self.owner.posts, [])
        notice = self.monitor.take_notice()
        self.assertEqual(notice["auto_block_reason"], "user_takeover")
        self.assertIsNone(notice["due_at"])

    async def test_cancelled_task_does_not_observe_or_dispatch(self):
        initial = await self.repo.get(self.scope)
        await self.repo.cancel_task(self.scope, expected_revision=initial["revision"])
        self.owner.observe = AsyncMock(side_effect=AssertionError("must not observe"))
        await self.monitor.step()
        self.assertEqual(self.monitor.take_notice()["state"], "cancelled")
        self.owner.observe.assert_not_awaited()

    async def test_start_recovers_old_gate_before_observation(self):
        await self.monitor.step()
        seen = asyncio.Event()
        original_tick = self.controller.tick

        async def tick(scope):
            result = await original_tick(scope)
            seen.set()
            return result

        self.controller.tick = tick
        async with self.monitor:
            await asyncio.wait_for(seen.wait(), 2)
        row = await self.repo.get(self.scope)
        self.assertEqual(row["gate"]["auto_block_reason"], "observer_recovered")
        self.assertTrue(self.monitor.operation.done())

    async def test_slow_observer_does_not_block_host_polling_and_is_joined_on_exit(self):
        started = asyncio.Event()
        exited = asyncio.Event()

        async def stalled(_scope):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                exited.set()

        controller = SimpleNamespace(recover=AsyncMock(), tick=stalled)
        async with ConfirmationMonitor(controller, self.scope, interval=0.01) as monitor:
            await asyncio.wait_for(started.wait(), 1)
            self.assertIsNone(monitor.take_notice())
            self.assertFalse(exited.is_set())
        self.assertTrue(exited.is_set())
        self.assertTrue(monitor.operation.done())

    async def test_errors_are_redacted_and_coalesced(self):
        self.controller.tick = AsyncMock(side_effect=OSError("PRIVATE_FIXTURE_NO_OUTPUT"))
        await self.monitor.step()
        notice = self.monitor.take_notice()
        self.assertEqual(notice["state"], "unavailable")
        self.assertNotIn("PRIVATE_FIXTURE", str(notice))
        await self.monitor.step()
        self.assertIsNone(self.monitor.take_notice())

    async def test_no_gate_does_not_claim_confirmation_readiness(self):
        self.assertIsNone(public_status(await self.repo.get(self.scope)))

    async def test_completed_cli_is_not_a_completed_deck_while_confirmation_waits(self):
        await self.monitor.step()
        self.monitor.observe_task({"task_id": self.scope.task_id, "thread_id": self.scope.thread_id, "status": "completed", "artifacts": [], "process_cleanup_complete": True})
        with patch("deerflow.ppt_confirmation.monitor.time.monotonic", return_value=100):
            self.assertTrue(self.monitor.hold_completed())
        with patch("deerflow.ppt_confirmation.monitor.time.monotonic", return_value=10000):
            self.assertTrue(self.monitor.hold_completed())

    async def test_missing_verified_gate_fails_after_bounded_grace(self):
        self.monitor.observe_task({"task_id": self.scope.task_id, "thread_id": self.scope.thread_id, "status": "completed", "artifacts": []})
        with patch("deerflow.ppt_confirmation.monitor.time.monotonic", return_value=100):
            self.assertTrue(self.monitor.hold_completed())
        with patch("deerflow.ppt_confirmation.monitor.time.monotonic", return_value=146), self.assertRaises(RuntimeError):
            self.monitor.hold_completed()

    async def test_delivered_or_running_task_is_not_held(self):
        for extra in ({"status": "running", "artifacts": []}, {"status": "completed", "artifacts": ["accepted.pptx"]}):
            self.monitor.observe_task({"task_id": self.scope.task_id, "thread_id": self.scope.thread_id, **extra})
            self.assertFalse(self.monitor.hold_completed())


if __name__ == "__main__":
    unittest.main()
