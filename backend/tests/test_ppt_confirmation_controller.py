"""Server-tick integration with disposable SQL. No browser events or Agent runs.

The owner below is synthetic: real authenticated Host wiring and task cancellation
serialization remain separate release gates. These tests do not approve rollout.
"""

import asyncio
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages/harness"))

from deerflow.persistence.ppt_confirmation.model import ConfirmationGateRow, ConfirmationTaskRow
from deerflow.persistence.ppt_confirmation.sql import ConfirmationRepository, Scope
from deerflow.ppt_confirmation.controller import ConfirmationController, ObservedProposal
from deerflow.ppt_confirmation.lifecycle import ConsentConflict
from deerflow.ppt_confirmation.proposal import CapturedProposal


def make_proposal(version="a", owner_version="e"):
    raw = json.dumps({"stage": "stage1"}, separators=(",", ":"), sort_keys=True).encode()
    return CapturedProposal(version * 64, "c" * 64, hashlib.sha256(raw).hexdigest(), raw, "Official", 0, 1, owner_version=owner_version * 64)


class Owner:
    def __init__(self, scope):
        self.scope = scope
        self.proposal = make_proposal()
        self.posts = []
        self.lookups = []
        self.saved = {}
        self.lose_ack = False
        self.unknown = False
        self.receipt_override = None
        self.before_effect = None
        self.native_receipt = {"state": "not_found", "retry_permitted": False}

    async def observe(self, scope):
        return ObservedProposal(self.scope, "project-one", "stage1", self.proposal)

    async def submit_guarded(self, scope, claim, proposal):
        # The production adapter must hold the same Host task lock as cancel
        # through fresh version validation + one native conditional dispatch.
        if self.before_effect:
            await self.before_effect()
        self.posts.append(claim["submission"]["id"])
        if not self.unknown:
            self.saved[claim["submission"]["id"]] = {
                "protocol": 1,
                "state": "committed",
                "operation_id": claim["submission"]["id"],
                "version": proposal.owner_version,
                "payload_sha256": proposal.payload_digest,
                "receipt_sha256": "d" * 64,
                "stage": "stage1",
                "retry_permitted": False,
            }
        if self.lose_ack or self.unknown:
            raise TimeoutError("private upstream detail must not leak")

    async def receipt(self, scope, operation):
        self.lookups.append(operation)
        return self.receipt_override or self.saved.get(operation, {"state": "not_found", "retry_permitted": False})

    async def receipt_by_version(self, scope, version):
        return self.native_receipt


class ControllerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        asyncio.get_running_loop().set_debug(False)
        self.tmp = tempfile.TemporaryDirectory(prefix="ppt-confirmation-controller-")
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{Path(self.tmp.name) / 'test.db'}")
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(lambda db: ConfirmationTaskRow.metadata.create_all(db, tables=[ConfirmationTaskRow.__table__, ConfirmationGateRow.__table__]))
        self.now = 1000
        self.repo = ConfirmationRepository(self.factory, clock=lambda: self.now)
        self.scope = Scope("owner-one", "thread-one", "task-one")
        self.owner = Owner(self.scope)
        self.controller = ConfirmationController(self.repo, self.owner, clock=lambda: self.now)

    async def asyncTearDown(self):
        await self.engine.dispose()
        self.tmp.cleanup()

    async def admit(self, enabled=True, new=True):
        await self.repo.admit(self.scope, runtime_profile="ppt-master-runner", skill_name="ppt-master", opted_in=enabled, new_task=new)

    async def until(self, end=1060):
        for now in range(self.now, end + 1, 5):
            self.now = now
            result = await self.controller.tick(self.scope)
        return result

    async def test_new_optin_runs_after_60_seconds_without_a_browser_event(self):
        await self.admit()
        await self.until(1055)
        self.assertEqual(self.owner.posts, [])
        self.now = 1060
        result = await self.controller.tick(self.scope)
        self.assertEqual(result["gate"]["state"], "confirmed")
        self.assertEqual(len(self.owner.posts), 1)
        self.assertEqual(result["gate"]["owner_version"], "e" * 64)
        self.assertNotEqual(result["gate"]["version"], result["gate"]["owner_version"])

    async def test_not_admitted_or_cross_owner_does_not_observe_or_submit(self):
        self.assertIsNone(await self.controller.tick(self.scope))
        await self.admit()
        self.assertIsNone(await self.controller.tick(Scope("other", "thread-one", "task-one")))
        self.assertEqual(self.owner.posts, [])

    async def test_default_and_legacy_remain_manual(self):
        await self.admit(enabled=False)
        result = await self.until(1080)
        self.assertEqual(result["gate"]["state"], "waiting_user")
        self.assertIsNone(result["gate"]["due_at"])
        self.assertEqual(self.owner.posts, [])

    async def test_restart_does_not_rearm_old_countdown(self):
        await self.admit()
        await self.until(1020)
        await self.controller.recover(self.scope)
        self.controller = ConfirmationController(self.repo, self.owner, clock=lambda: self.now)
        result = await self.until(1100)
        self.assertEqual(result["gate"]["auto_block_reason"], "observer_recovered")
        self.assertEqual(self.owner.posts, [])

    async def test_lost_ack_is_reconciled_by_read_only_receipt(self):
        await self.admit()
        self.owner.lose_ack = True
        result = await self.until()
        self.assertEqual(result["gate"]["state"], "confirmed")
        await self.until(1080)
        self.assertEqual(len(self.owner.posts), 1)
        self.assertTrue(self.owner.lookups)

    async def test_unknown_outcome_never_retries(self):
        await self.admit()
        self.owner.unknown = True
        result = await self.until(1100)
        self.assertEqual(result["gate"]["state"], "submission_unknown")
        self.assertEqual(len(self.owner.posts), 1)

    async def test_content_update_disarms_countdown(self):
        await self.admit()
        await self.until(1020)
        self.owner.proposal = make_proposal(version="b", owner_version="f")
        result = await self.until(1100)
        self.assertEqual(result["gate"]["auto_block_reason"], "recommendation_changed")
        self.assertEqual(self.owner.posts, [])

    async def test_cancelled_task_is_never_dispatched(self):
        await self.admit()
        await self.until(1055)
        state = await self.repo.get(self.scope)
        await self.repo.cancel_task(self.scope, expected_revision=state["revision"])
        await self.until(1100)
        self.assertEqual(self.owner.posts, [])

    async def test_disable_prevents_dispatch(self):
        await self.admit()
        await self.until(1055)
        state = await self.repo.get(self.scope)
        await self.repo.disable_auto(self.scope, expected_revision=state["revision"])
        await self.until(1100)
        self.assertEqual(self.owner.posts, [])

    def manual_proof(self, **changes):
        return {"protocol": 1, "state": "committed", "operation_id": "manual-native-op", "version": "e" * 64, "payload_sha256": "f" * 64, "receipt_sha256": "d" * 64, "stage": "stage1", "retry_permitted": False, **changes}

    async def test_manual_click_with_different_choices_completes_gate_without_auto_post(self):
        await self.admit()
        await self.until(1055)
        self.owner.native_receipt = self.manual_proof()
        self.now = 1060
        result = await self.controller.tick(self.scope)
        self.assertEqual(result["gate"]["state"], "confirmed")
        self.assertEqual(result["gate"]["official_submission"]["payload_digest"], "f" * 64)
        self.assertEqual(self.owner.posts, [])

    async def test_manual_wins_after_auto_claim_is_reconciled_without_second_post(self):
        await self.admit()

        async def manual_wins():
            self.owner.native_receipt = self.manual_proof()
            raise ConsentConflict("owner_version_changed")

        self.owner.before_effect = manual_wins
        result = await self.until()
        self.assertEqual(result["gate"]["state"], "confirmed")
        self.assertEqual(result["gate"]["official_submission"]["id"], "manual-native-op")
        await self.until(1080)
        self.assertEqual(self.owner.posts, [])

    async def test_manual_native_receipt_cannot_cross_version_stage_or_cancel(self):
        await self.admit(enabled=False)
        await self.until(1000)
        for changes in ({"version": "b" * 64}, {"stage": "final"}, {"protocol": True}, {"retry_permitted": True}):
            self.owner.native_receipt = self.manual_proof(**changes)
            with self.assertRaises(ConsentConflict):
                await self.controller.tick(self.scope)
            self.assertEqual((await self.repo.get(self.scope))["gate"]["state"], "waiting_user")
        state = await self.repo.get(self.scope)
        await self.repo.cancel_task(self.scope, expected_revision=state["revision"])
        self.owner.native_receipt = self.manual_proof()
        self.assertTrue((await self.controller.tick(self.scope))["cancelled"])
        self.assertEqual(self.owner.posts, [])

    async def test_wrong_task_observation_is_rejected(self):
        await self.admit()
        self.owner.scope = Scope("owner-two", "thread-two", "task-two")
        with self.assertRaises(ConsentConflict):
            await self.controller.tick(self.scope)
        self.assertIsNone((await self.repo.get(self.scope))["gate"])

    async def test_wrong_operation_version_or_digest_never_confirms(self):
        await self.admit()
        self.owner.unknown = True
        result = await self.until()
        operation = result["gate"]["submission"]["id"]
        original = {"protocol": 1, "state": "committed", "operation_id": operation, "version": "e" * 64, "payload_sha256": self.owner.proposal.payload_digest, "receipt_sha256": "d" * 64, "stage": "stage1", "retry_permitted": False}
        for key, value in (("operation_id", "other"), ("version", "f" * 64), ("payload_sha256", "b" * 64), ("stage", "final")):
            self.owner.receipt_override = {**original, key: value}
            with self.assertRaises(ConsentConflict):
                await self.controller.tick(self.scope)
            self.assertEqual((await self.repo.get(self.scope))["gate"]["state"], "submission_unknown")
        self.assertEqual(len(self.owner.posts), 1)

    async def test_competing_ticks_have_at_most_one_post(self):
        await self.admit()
        await self.until(1055)
        self.now = 1060
        results = await asyncio.gather(*(self.controller.tick(self.scope) for _ in range(8)), return_exceptions=True)
        self.assertEqual(len(self.owner.posts), 1, results)
        self.assertTrue(all(isinstance(row, (dict, ConsentConflict)) for row in results), results)

    async def test_host_dispatch_guard_can_refuse_cancel_race(self):
        await self.admit()

        async def cancel_race():
            state = await self.repo.get(self.scope)
            await self.repo.cancel_task(self.scope, expected_revision=state["revision"])
            raise ConsentConflict("task_cancelled")

        self.owner.before_effect = cancel_race
        result = await self.until()
        self.assertTrue(result["cancelled"])
        self.assertEqual(self.owner.posts, [])

    async def test_readonly_peer_cannot_revoke_the_live_dispatchers_claim(self):
        await self.admit()
        await self.until(1055)
        self.now = 1060
        committed = asyncio.Event()
        release = asyncio.Event()
        original = self.repo._commit

        async def pause_after_claim(scope, snapshot, gate, **kwargs):
            value = await original(scope, snapshot, gate, **kwargs)
            if gate and gate["state"] == "submission_pending":
                committed.set()
                await release.wait()
            return value

        self.repo._commit = pause_after_claim
        dispatch = asyncio.create_task(self.controller.tick(self.scope))
        try:
            await asyncio.wait_for(committed.wait(), 5)
            read = await self.controller.tick(self.scope)
            self.assertEqual(read["gate"]["state"], "submission_pending")
        finally:
            release.set()
            result = await dispatch
        self.assertEqual(result["gate"]["state"], "confirmed")
        self.assertEqual(len(self.owner.posts), 1)


if __name__ == "__main__":
    unittest.main()
