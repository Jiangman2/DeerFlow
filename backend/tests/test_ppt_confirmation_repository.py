"""Durable transport tests, not official UI/CLI or production acceptance.

Runs standalone in the release venv. Each case uses a new SQLite file. Optional
Postgres qualification must use a disposable database, never the service DB.
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy import event, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages/harness"))

from deerflow.persistence.ppt_confirmation.model import ConfirmationGateRow, ConfirmationTaskRow
from deerflow.persistence.ppt_confirmation.sql import ConfirmationRepository, Scope
from deerflow.ppt_confirmation.lifecycle import ConsentConflict


class ConfirmationRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        asyncio.get_running_loop().set_debug(False)
        self.directory = tempfile.TemporaryDirectory(prefix="ppt-confirmation-sql-")
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{Path(self.directory.name) / 'test.db'}")
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(lambda db: ConfirmationTaskRow.metadata.create_all(db, tables=[ConfirmationTaskRow.__table__, ConfirmationGateRow.__table__]))
        self.now = 1000
        self.repo = ConfirmationRepository(self.factory, clock=lambda: self.now)
        self.scope = Scope("owner-1", "thread-1", "task-1")

    async def asyncTearDown(self):
        await self.engine.dispose()
        self.directory.cleanup()

    async def admit(self, enabled=True, scope=None, **extra):
        return await self.repo.admit(scope or self.scope, runtime_profile="ppt-master-runner", skill_name="ppt-master", opted_in=enabled, **extra)

    async def gate(self, **changes):
        snapshot = await self.repo.get(self.scope)
        values = dict(project_id="project-1", stage="stage1", version="a" * 64, payload_digest="b" * 64, source_digest="c" * 64)
        values.update(changes)
        return await self.repo.register_gate(self.scope, expected_revision=snapshot["revision"], **values)

    async def act(self, action, snapshot=None, **evidence):
        snapshot = snapshot or await self.repo.get(self.scope)
        return await self.repo.transition(self.scope, expected_revision=snapshot["revision"], gate_id=snapshot["gate"]["gate_id"], expected_gate_revision=snapshot["gate"]["revision"], action=action, **evidence)

    def binding(self):
        return dict(version="a" * 64, payload_digest="b" * 64, source_digest="c" * 64)

    async def due(self):
        await self.admit()
        await self.gate()
        for now in range(1005, 1061, 5):
            self.now = now
            snapshot = await self.act("observe_ready", **self.binding())
        return snapshot

    async def claim(self, snapshot=None):
        return await self.act("claim_submission", snapshot, operation_id="submit-1", mode="automatic", **self.binding())

    async def test_missing_read_does_not_create_task_or_gate(self):
        self.assertIsNone(await self.repo.get(self.scope))
        async with self.factory() as session:
            self.assertEqual(list((await session.execute(select(ConfirmationTaskRow))).scalars()), [])

    async def test_admission_retry_cannot_change_consent(self):
        original = await self.admit(False)
        retry = await self.admit(True)
        self.assertEqual(retry, original)
        self.assertFalse(retry["policy"]["auto_enabled"])

    async def test_scope_cannot_be_omitted_or_cross_owner(self):
        for bad in (None, "", "../owner"):
            with self.assertRaises(ConsentConflict):
                Scope(bad, "thread-1", "task-1")
        await self.admit()
        for scope in (Scope("owner-2", "thread-1", "task-1"), Scope("owner-1", "thread-2", "task-1")):
            self.assertIsNone(await self.repo.get(scope))
            with self.assertRaises(ConsentConflict):
                await self.admit(scope=scope)

    async def test_legacy_task_never_inherits_optin(self):
        snapshot = await self.admit(True, new_task=False)
        self.assertFalse(snapshot["policy"]["auto_enabled"])

    async def test_new_repository_recovers_persisted_claim_not_new_submission(self):
        claimed = await self.claim(await self.due())
        self.repo = ConfirmationRepository(self.factory, clock=lambda: self.now)
        loaded = await self.repo.get(self.scope)
        self.assertEqual(loaded, claimed)
        recovered = await self.act("recover")
        self.assertEqual(recovered["gate"]["state"], "submission_unknown")
        with self.assertRaises(ConsentConflict):
            await self.claim()

    async def test_simultaneous_claims_have_exactly_one_winner(self):
        ready = await self.due()
        results = await asyncio.gather(*(self.claim(ready) for _ in range(12)), return_exceptions=True)
        self.assertEqual(sum(isinstance(result, dict) for result in results), 1)
        self.assertTrue(all(isinstance(result, (dict, ConsentConflict)) for result in results), results)

    async def test_sqlite_busy_before_first_write_refuses_without_retry(self):
        if self.engine.dialect.name != "sqlite":
            self.skipTest("SQLite-specific native lock classification")
        ready = await self.due()
        contender = create_async_engine(self.engine.url, connect_args={"timeout": 0.05})
        attempts = []

        def count_update(connection, cursor, statement, parameters, context, executemany):
            if statement.startswith("UPDATE"):
                attempts.append(True)

        event.listen(contender.sync_engine, "before_cursor_execute", count_update)
        repo = ConfirmationRepository(async_sessionmaker(contender, expire_on_commit=False), clock=lambda: self.now)
        try:
            async with self.engine.begin() as blocker:
                await blocker.execute(update(ConfirmationTaskRow).values(updated_at=self.now))
                with self.assertRaisesRegex(ConsentConflict, "confirmation_store_busy"):
                    await repo.transition(
                        self.scope,
                        expected_revision=ready["revision"],
                        gate_id=ready["gate"]["gate_id"],
                        expected_gate_revision=ready["gate"]["revision"],
                        action="claim_submission",
                        operation_id="blocked-claim",
                        mode="automatic",
                        **self.binding(),
                    )
        finally:
            await contender.dispose()
        self.assertEqual(len(attempts), 1)
        self.assertEqual((await self.repo.get(self.scope))["gate"], ready["gate"])

    async def test_uncertain_commit_is_not_downgraded_to_a_retryable_conflict(self):
        ready = await self.due()

        class LostCommitAcknowledgement(AsyncSession):
            @asynccontextmanager
            async def begin(self):
                async with super().begin():
                    yield
                # Simulates a lost ACK AFTER a durable write; no HTTP effect is
                # authorized by an exception, even if reload finds the claim.
                raise OperationalError(None, None, OSError("synthetic lost commit ACK"))

        repo = ConfirmationRepository(async_sessionmaker(self.engine, class_=LostCommitAcknowledgement, expire_on_commit=False), clock=lambda: self.now)
        with self.assertRaises(OperationalError):
            await repo.transition(
                self.scope,
                expected_revision=ready["revision"],
                gate_id=ready["gate"]["gate_id"],
                expected_gate_revision=ready["gate"]["revision"],
                action="claim_submission",
                operation_id="uncertain-claim",
                mode="automatic",
                **self.binding(),
            )
        stored = await self.repo.get(self.scope)
        self.assertEqual(stored["gate"]["state"], "submission_pending")
        self.assertEqual(stored["gate"]["submission"]["id"], "uncertain-claim")
        with self.assertRaises(ConsentConflict):
            await self.claim(stored)

    async def test_disabling_task_invalidates_previously_prepared_claim(self):
        ready = await self.due()
        disabled = await self.repo.disable_auto(self.scope, expected_revision=ready["revision"])
        self.assertFalse(disabled["policy"]["auto_enabled"])
        self.assertIsNone(disabled["gate"]["due_at"])
        with self.assertRaises(ConsentConflict):
            await self.claim(ready)
        with self.assertRaises(ConsentConflict):
            await self.claim(disabled)

    async def test_cancel_race_cannot_be_overwritten_by_stale_observer(self):
        ready = await self.due()
        cancelled = await self.repo.cancel_task(self.scope, expected_revision=ready["revision"])
        self.assertTrue(cancelled["cancelled"])
        self.assertEqual(cancelled["gate"]["state"], "cancelled")
        with self.assertRaises(ConsentConflict):
            await self.claim(ready)
        with self.assertRaises(ConsentConflict):
            await self.act("recover", cancelled)

    async def test_current_gate_is_unique_and_history_stays_readable(self):
        await self.claim(await self.due())
        first = await self.act("record_confirmation", receipt_digest="d" * 64, **self.binding())
        second = await self.gate(stage="stage2")
        self.assertNotEqual(first["gate"]["gate_id"], second["gate"]["gate_id"])
        history = await self.repo.get_gate(self.scope, first["gate"]["gate_id"])
        self.assertEqual(history, first["gate"])
        with self.assertRaises(ConsentConflict):
            await self.act("claim_continuation", first, original_turn="running", run_id="run-1")

    async def test_registration_retry_does_not_restart_countdown(self):
        await self.admit()
        first = await self.gate()
        self.now = 1010
        second = await self.gate()
        self.assertEqual(first, second)

    async def test_two_initial_gates_cannot_replace_each_other(self):
        start = await self.admit()

        async def register(version):
            return await self.repo.register_gate(self.scope, expected_revision=start["revision"], project_id="project-1", stage="stage1", version=version, payload_digest="b" * 64, source_digest="c" * 64)

        results = await asyncio.gather(register("a" * 64), register("d" * 64), return_exceptions=True)
        self.assertEqual(sum(isinstance(result, dict) for result in results), 1)
        async with self.factory() as session:
            self.assertEqual(len(list((await session.execute(select(ConfirmationGateRow))).scalars())), 1)

    async def test_unknown_confirmation_receipt_and_continuation_are_durable(self):
        await self.claim(await self.due())
        await self.act("recover")
        await self.act("record_confirmation", receipt_digest="d" * 64, **self.binding())
        await self.act("claim_continuation", original_turn="waiting_user", run_id="resume-1")
        await self.act("recover")
        continued = await self.act("record_continuation", run_id="resume-1")
        self.assertEqual(continued["gate"]["state"], "continuing")
        with self.assertRaises(ConsentConflict):
            await self.act("claim_continuation", original_turn="waiting_user", run_id="resume-2")

    async def test_repository_does_not_accept_arbitrary_transition_or_actor(self):
        await self.due()
        for action, evidence in (("__init__", {}), ("observe_ready", {"actor_id": "owner-2"}), ("observe_ready", {"now": 999999})):
            with self.assertRaises(ConsentConflict):
                await self.act(action, **evidence)

    async def test_no_consent_leaks_into_next_task(self):
        await self.admit(True)
        new = await self.admit(False, scope=Scope("owner-1", "thread-1", "task-2"))
        self.assertFalse(new["policy"]["auto_enabled"])

    async def test_gate_read_is_owner_scoped(self):
        await self.admit()
        first = await self.gate()
        self.assertIsNone(await self.repo.get_gate(Scope("owner-2", "thread-1", "task-1"), first["gate"]["gate_id"]))

    async def test_replaced_version_is_persisted_as_non_actionable_history(self):
        await self.admit()
        original = await self.gate()
        replacement = await self.gate(version="e" * 64)
        history = await self.repo.get_gate(self.scope, original["gate"]["gate_id"])
        self.assertEqual(history["state"], "superseded")
        self.assertIsNone(history["due_at"])
        self.assertIsNone(replacement["gate"]["due_at"])

    async def test_repository_keeps_no_database_connection_while_effect_is_pending(self):
        await self.claim(await self.due())
        self.assertEqual(self.engine.pool.checkedout(), 0)

    async def test_two_new_tasks_can_progress_independently(self):
        first = await self.due()
        other = Scope("owner-2", "thread-2", "task-2")
        second = await self.admit(scope=other)
        await self.repo.cancel_task(other, expected_revision=second["revision"])
        self.assertEqual((await self.claim(first))["gate"]["state"], "submission_pending")


if __name__ == "__main__":
    unittest.main()
