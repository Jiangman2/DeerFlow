"""Resume only the same live Host's cleaned-up, officially confirmed session.

A committed SQL claim precedes the single HTTP effect. A lost POST/launch ACK
is read-only reconciliation, never a second start. Host process restarts remain
an explicit recovery boundary; stored PIDs/session names cannot prove liveness.
"""

from __future__ import annotations

import asyncio

from deerflow.ppt_confirmation import lifecycle as core


class ConfirmationContinuation:
    def __init__(self, repository, owner):
        self.repository, self.owner = repository, owner

    async def _act(self, scope, snapshot, action, **evidence):
        gate = snapshot["gate"]
        return await self.repository.transition(scope, expected_revision=snapshot["revision"], gate_id=gate["gate_id"],
            expected_gate_revision=gate["revision"], action=action, **evidence)

    async def tick(self, scope, snapshot, task):
        gate = snapshot.get("gate") if snapshot else None
        if not gate or snapshot["cancelled"]:
            return snapshot
        if gate["state"] == "confirmed":
            core.require(isinstance(task, dict) and task.get("task_id") == scope.task_id and task.get("thread_id") == scope.thread_id,
                         "continuation_task_scope_mismatch")
            # A live CLI continues by consuming the native receipt itself.
            if task.get("status") != "completed" or task.get("process_cleanup_complete") is not True or task.get("artifacts"):
                return snapshot
            session = core.identifier(task.get("session_id"))
            core.require(task.get("confirmation_protocol") == "ppt-confirmation-v1" and not task.get("cancel_requested"),
                         "continuation_protocol_unavailable")
            snapshot = await self._act(scope, snapshot, "claim_continuation", original_turn="waiting_user", run_id=scope.task_id)
            gate = snapshot["gate"]
            current = await self.repository.get(scope)
            if current is None or current["cancelled"] or current["revision"] != snapshot["revision"]:
                return current
            try:
                await asyncio.wait_for(self.owner.continue_guarded(scope, gate, session), timeout=10)
            except (OSError, TimeoutError, core.ConsentConflict):
                pass
            current = await self.repository.get(scope)
            if current is None or current["cancelled"]:
                return current
            if current["gate"]["state"] == "continuation_pending":
                snapshot = await self._act(scope, current, "recover")
            else:
                snapshot = current
            gate = snapshot["gate"]
        if gate["state"] not in {"continuation_pending", "continuation_unknown"}:
            return snapshot
        try:
            proof = await asyncio.wait_for(self.owner.continuation_receipt(scope, gate["gate_id"]), timeout=5)
        except (OSError, TimeoutError):
            return snapshot
        core.require(isinstance(proof, dict), "invalid_continuation_receipt")
        if proof.get("state") != "dispatched":
            return snapshot
        core.require(proof.get("task_id") == scope.task_id and proof.get("thread_id") == scope.thread_id
            and proof.get("gate_id") == gate["gate_id"] and proof.get("receipt_digest") == gate["receipt_digest"]
            and proof.get("retry_permitted") is False, "continuation_receipt_mismatch")
        return await self._act(scope, snapshot, "record_continuation", run_id=scope.task_id)
