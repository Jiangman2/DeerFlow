"""Short-transaction, optimistic-CAS repository for PPT confirmation transport.

NOT RUNTIME-WIRED. Callers must first verify thread/run ownership and the actual
isolated PPT runtime. Scope is explicit: ambient context and unscoped admin writes
are deliberately unsupported. Never expose ``transition`` as arbitrary HTTP RPC.

The task revision serializes gate changes, disable and cancel across processes.
A successful claim is committed BEFORE the caller sends an official request.
No network call, timer, Agent start or external callback runs in a transaction.
A failed/uncertain DB commit returns no dispatch permission. Reload/reconcile;
never infer permission to repeat an effect from an elapsed lease or deadline.
"""

from __future__ import annotations

import copy
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import and_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deerflow.persistence.ppt_confirmation.model import ConfirmationGateRow as Gate
from deerflow.persistence.ppt_confirmation.model import ConfirmationTaskRow as Task
from deerflow.ppt_confirmation import lifecycle as core


@dataclass(frozen=True)
class Scope:
    owner_id: str
    thread_id: str
    task_id: str

    def __post_init__(self):
        for value in (self.owner_id, self.thread_id, self.task_id):
            core.identifier(value)


# This allowlist contains only pure transitions, never user-supplied callbacks.
_ACTIONS = {
    "observe_ready": (core.observe_ready, {"version", "payload_digest", "source_digest"}),
    "take_over": (core.take_over, set()),
    "recover": (core.recover, set()),
    "claim_submission": (core.claim_submission, {"operation_id", "mode", "version", "payload_digest", "source_digest"}),
    "record_confirmation": (core.record_confirmation, {"version", "payload_digest", "source_digest", "receipt_digest"}),
    "adopt_official_confirmation": (core.adopt_official_confirmation, {"owner_version", "operation_id", "receipt_payload_digest", "receipt_digest"}),
    "claim_continuation": (core.claim_continuation, {"original_turn", "run_id"}),
    "record_continuation": (core.record_continuation, {"run_id"}),
}


def _where(scope: Scope):
    core.require(isinstance(scope, Scope), "explicit_scope_required")
    return and_(Task.task_id == scope.task_id, Task.owner_id == scope.owner_id, Task.thread_id == scope.thread_id)


def _policy(task: Task) -> dict:
    return core.new_policy(task.owner_id, task.thread_id, task.task_id, runtime_profile="ppt-master-runner", skill_name="ppt-master", opted_in=task.auto_enabled)


async def _first_statement(session: AsyncSession, statement):
    """Known SQLite contention BEFORE any write/commit grants no permission.

    Never wrap later writes or commit with this helper: a lost commit ACK is an
    uncertain result, not a stale-revision rejection or permission to retry.
    Classify native error codes only; don't parse potentially private messages.
    """
    try:
        return await session.execute(statement)
    except OperationalError as exc:
        code = getattr(exc.orig, "sqlite_errorcode", None)
        if session.get_bind().dialect.name == "sqlite" and isinstance(exc.orig, sqlite3.OperationalError) and type(code) is int and (code & 0xFF) in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
            raise core.ConsentConflict("confirmation_store_busy") from None
        raise


class ConfirmationRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession], *, clock: Callable[[], float] = time.time):
        self._sf = session_factory
        self._clock = clock

    def _now(self) -> int:
        return core.timestamp(int(self._clock()))

    async def get(self, scope: Scope) -> dict | None:
        # One SELECT is important: separate reads can mix task/gate revisions.
        statement = select(Task, Gate).outerjoin(Gate, and_(Gate.gate_id == Task.current_gate_id, Gate.task_id == Task.task_id)).where(_where(scope))
        async with self._sf() as session:
            row = (await _first_statement(session, statement)).one_or_none()
            if row is None:
                return None
            task, gate = row
            core.require(task.current_gate_id is None or gate is not None, "missing_current_gate")
            return {"policy": _policy(task), "cancelled": task.cancelled, "revision": task.revision, "created_at": task.created_at, "updated_at": task.updated_at, "gate": copy.deepcopy(gate.document) if gate else None}

    async def get_gate(self, scope: Scope, gate_id: str) -> dict | None:
        core.fingerprint(gate_id)
        async with self._sf() as session:
            statement = select(Gate.document).join(Task, Task.task_id == Gate.task_id).where(_where(scope), Gate.gate_id == gate_id)
            return copy.deepcopy((await _first_statement(session, statement)).scalar_one_or_none())

    async def admit(self, scope: Scope, *, runtime_profile: str, skill_name: str, opted_in: bool = False, new_task: bool = True) -> dict:
        _where(scope)
        policy = core.new_policy(scope.owner_id, scope.thread_id, scope.task_id, runtime_profile=runtime_profile, skill_name=skill_name, opted_in=opted_in, new_task=new_task)
        now = self._now()
        async with self._sf() as session:
            dialect = session.get_bind().dialect.name
            core.require(dialect in {"sqlite", "postgresql"}, "unsupported_database")
            insert = pg_insert if dialect == "postgresql" else sqlite_insert
            await _first_statement(
                session,
                insert(Task)
                .values(task_id=scope.task_id, owner_id=scope.owner_id, thread_id=scope.thread_id, auto_enabled=policy["auto_enabled"], cancelled=False, revision=0, current_gate_id=None, created_at=now, updated_at=now)
                .on_conflict_do_nothing(index_elements=["task_id"]),
            )
            await session.commit()
        result = await self.get(scope)
        core.require(result is not None, "admission_conflict")
        # Retried admission never changes original consent, including false -> true.
        return result

    async def _snapshot(self, scope: Scope, expected_revision: int) -> dict:
        core.require(type(expected_revision) is int and expected_revision >= 0, "invalid_revision")
        snapshot = await self.get(scope)
        core.require(snapshot is not None, "task_not_found")
        core.require(snapshot["revision"] == expected_revision, "stale_task_revision")
        core.require(not snapshot["cancelled"], "task_cancelled")
        return snapshot

    async def _commit(self, scope: Scope, snapshot: dict, gate: dict | None, *, insert_gate: bool = False, auto_enabled: bool | None = None, cancelled: bool = False) -> dict:
        now = self._now()
        values = {"revision": snapshot["revision"] + 1, "updated_at": now, "current_gate_id": gate["gate_id"] if gate else None, "cancelled": cancelled}
        if auto_enabled is not None:
            values["auto_enabled"] = auto_enabled
        async with self._sf() as session:
            async with session.begin():
                # First statement is the CAS write, not a stale read -> lock upgrade.
                changed = await _first_statement(session, update(Task).where(_where(scope), Task.revision == snapshot["revision"], Task.cancelled.is_(False)).values(**values))
                core.require(changed.rowcount == 1, "stale_task_revision")
                if gate is not None:
                    gate_values = dict(revision=gate["revision"], state=gate["state"], due_at=gate["due_at"], document=gate)
                    if insert_gate:
                        previous = snapshot["gate"]
                        if previous is not None and previous["state"] == "waiting_user":
                            superseded = core.observe_ready(previous, now=now, version=gate["version"], payload_digest=gate["payload_digest"], source_digest=gate["source_digest"])
                            core.require(superseded["state"] == "superseded", "previous_gate_still_actionable")
                            changed = await session.execute(
                                update(Gate)
                                .where(Gate.task_id == scope.task_id, Gate.gate_id == previous["gate_id"], Gate.revision == previous["revision"])
                                .values(revision=superseded["revision"], state="superseded", due_at=None, document=superseded)
                            )
                            core.require(changed.rowcount == 1, "stale_gate_revision")
                        session.add(Gate(gate_id=gate["gate_id"], task_id=scope.task_id, created_at=gate["created_at"], **gate_values))
                    else:
                        changed = await session.execute(update(Gate).where(Gate.task_id == scope.task_id, Gate.gate_id == gate["gate_id"], Gate.revision == snapshot["gate"]["revision"]).values(**gate_values))
                        core.require(changed.rowcount == 1, "stale_gate_revision")
        # Return only OUR committed transition, never a later peer's claim.
        result = copy.deepcopy(snapshot)
        result.update(revision=values["revision"], updated_at=now, cancelled=cancelled, gate=copy.deepcopy(gate))
        if auto_enabled is not None:
            result["policy"]["auto_enabled"] = auto_enabled
        return result

    async def register_gate(self, scope: Scope, *, expected_revision: int, project_id: str, stage: str, version: str, payload_digest: str, source_digest: str, owner_version: str | None = None) -> dict:
        snapshot = await self._snapshot(scope, expected_revision)
        candidate = core.new_gate(snapshot["policy"], project_id=project_id, stage=stage, version=version, payload_digest=payload_digest, source_digest=source_digest, owner_version=owner_version, now=self._now())
        previous = snapshot["gate"]
        if previous:
            if previous["gate_id"] == candidate["gate_id"]:
                return snapshot
            candidate = core.new_gate(snapshot["policy"], project_id=project_id, stage=stage, version=version, payload_digest=payload_digest, source_digest=source_digest, owner_version=owner_version, now=self._now(), previous=previous)
        return await self._commit(scope, snapshot, candidate, insert_gate=True)

    async def transition(self, scope: Scope, *, expected_revision: int, gate_id: str, expected_gate_revision: int, action: str, **evidence) -> dict:
        core.require(isinstance(action, str) and action in _ACTIONS, "unsupported_transition")
        function, keys = _ACTIONS[action]
        core.require(set(evidence) == keys, "invalid_transition_evidence")
        snapshot = await self._snapshot(scope, expected_revision)
        gate = snapshot["gate"]
        core.require(gate is not None and gate["gate_id"] == core.fingerprint(gate_id), "not_current_gate")
        core.require(type(expected_gate_revision) is int and gate["revision"] == expected_gate_revision, "stale_gate_revision")
        if action == "claim_submission" and evidence["mode"] == "automatic":
            core.require(snapshot["policy"]["auto_enabled"], "automatic_disabled")
        if action in {"observe_ready", "claim_submission"}:
            evidence["now"] = self._now()
        if action in {"take_over", "claim_submission", "claim_continuation"}:
            evidence.update(actor_id=scope.owner_id, expected_revision=expected_gate_revision)
        next_gate = function(gate, **evidence)
        if next_gate == gate:
            # A read/no-op is never an authorization to dispatch an effect.
            return snapshot
        return await self._commit(scope, snapshot, next_gate)

    async def disable_auto(self, scope: Scope, *, expected_revision: int) -> dict:
        snapshot = await self._snapshot(scope, expected_revision)
        gate = snapshot["gate"]
        if gate is not None and gate["state"] == "waiting_user":
            gate = core.take_over(gate, actor_id=scope.owner_id, expected_revision=gate["revision"])
        return await self._commit(scope, snapshot, gate, auto_enabled=False)

    async def cancel_task(self, scope: Scope, *, expected_revision: int) -> dict:
        snapshot = await self._snapshot(scope, expected_revision)
        gate = core.cancel(snapshot["gate"], actor_id=scope.owner_id) if snapshot["gate"] is not None else None
        return await self._commit(scope, snapshot, gate, auto_enabled=False, cancelled=True)
