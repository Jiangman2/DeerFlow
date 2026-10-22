"""Bounded server-side tick over durable consent and an exact native owner.

NOT CONNECTED TO THE PRODUCTION SCHEDULER/HTTP ROUTES. This component orchestrates
real SQL CAS and conditional owner effects but never admits consent, interprets
model text, starts Codex or schedules itself. Only an authenticated runtime may
construct Scope, admit a NEW task or provide an Owner adapter.

The production Owner adapter MUST serialize submit_guarded with Host cancellation,
verify the current task/project/process + committed claim under that guard and
send at most one conditional POST. A repository re-read alone cannot close that
cross-system cancellation race. No effect runs inside a database transaction.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
import uuid
from dataclasses import dataclass
from typing import Protocol

from deerflow.persistence.ppt_confirmation.sql import ConfirmationRepository, Scope
from deerflow.ppt_confirmation import lifecycle as core
from deerflow.ppt_confirmation.proposal import CapturedProposal


@dataclass(frozen=True, repr=False)
class ObservedProposal:
    scope: Scope
    project_id: str
    stage: str
    proposal: CapturedProposal

    def validate(self, scope: Scope):
        core.require(self.scope == scope, "confirmation_owner_scope_mismatch")
        core.identifier(self.project_id)
        core.require(self.stage in {"stage1", "stage2"}, "unsupported_confirmation_stage")
        core.require(isinstance(self.proposal, CapturedProposal), "invalid_official_proposal")
        for value in (self.proposal.version, self.proposal.owner_version, self.proposal.source_digest, self.proposal.payload_digest):
            core.fingerprint(value)
        core.require(hashlib.sha256(self.proposal.payload_json).hexdigest() == self.proposal.payload_digest, "proposal_payload_changed")
        core.require(not self.proposal.confirmation_submitted and self.proposal.console_errors == 0, "invalid_official_proposal")
        core.require(self.proposal.payload.get("stage") == ("stage1" if self.stage == "stage1" else "final"), "proposal_stage_mismatch")
        return self

    def binding(self):
        return {name: getattr(self.proposal, name) for name in ("version", "payload_digest", "source_digest")}


class Owner(Protocol):
    async def observe(self, scope: Scope) -> ObservedProposal | None: ...

    async def submit_guarded(self, scope: Scope, claim: dict, proposal: CapturedProposal) -> None: ...

    async def receipt(self, scope: Scope, operation: str) -> dict: ...

    async def receipt_by_version(self, scope: Scope, version: str) -> dict: ...


class ConfirmationController:
    def __init__(self, repository: ConfirmationRepository, owner: Owner, *, clock=time.time):
        self.repository = repository
        self.owner = owner
        self.clock = clock

    async def _act(self, scope, snapshot, action, **evidence):
        gate = snapshot["gate"]
        return await self.repository.transition(scope, expected_revision=snapshot["revision"], gate_id=gate["gate_id"], expected_gate_revision=gate["revision"], action=action, **evidence)

    async def recover(self, scope: Scope):
        """Call once on observer takeover; downtime never counts as consent."""
        snapshot = await self.repository.get(scope)
        if snapshot is None or snapshot["cancelled"] or snapshot["gate"] is None:
            return snapshot
        return await self._act(scope, snapshot, "recover")

    async def _reconcile(self, scope, snapshot, *, effect_returned=False):
        gate = snapshot["gate"]
        if gate["state"] not in {"submission_pending", "submission_unknown"}:
            return snapshot
        # A polling peer must not revoke a live dispatcher's committed claim.
        # Only the dispatcher after its attempt, or explicit observer recovery,
        # may label pending as unknown. Receipt lookup itself is read-only.
        if effect_returned and gate["state"] == "submission_pending":
            snapshot = await self._act(scope, snapshot, "recover")
            gate = snapshot["gate"]
        try:
            proof = await asyncio.wait_for(self.owner.receipt(scope, gate["submission"]["id"]), timeout=5)
        except (OSError, TimeoutError):
            return snapshot  # Unknown is durable; no POST, reset or launch.
        core.require(isinstance(proof, dict), "invalid_confirmation_receipt")
        if proof.get("state") != "committed":
            return await self._adopt_official(scope, snapshot)
        core.require(
            type(proof.get("protocol")) is int
            and proof["protocol"] == 1
            and proof.get("retry_permitted") is False
            and proof.get("operation_id") == gate["submission"]["id"]
            and proof.get("version") == gate["owner_version"]
            and proof.get("payload_sha256") == gate["payload_digest"]
            and proof.get("stage") == ("stage1" if gate["stage"] == "stage1" else "final"),
            "confirmation_receipt_binding_mismatch",
        )
        core.fingerprint(proof.get("receipt_sha256"))
        return await self._act(scope, snapshot, "record_confirmation", receipt_digest=proof["receipt_sha256"], **{key: gate[key] for key in ("version", "payload_digest", "source_digest")})

    async def _adopt_official(self, scope, snapshot):
        gate = snapshot["gate"]
        if not gate or gate["state"] not in {"waiting_user", "submission_pending", "submission_unknown"}:
            return snapshot
        try:
            proof = await asyncio.wait_for(self.owner.receipt_by_version(scope, gate["owner_version"]), timeout=5)
        except (OSError, TimeoutError):
            # Without this read a manual winner is unknown: stop this tick,
            # never proceed into an automatic effect after failed reconciliation.
            raise core.ConsentConflict("official_receipt_unavailable") from None
        core.require(isinstance(proof, dict), "invalid_confirmation_receipt")
        if proof.get("state") != "committed":
            core.require(proof.get("state") == "not_found", "official_receipt_unconfirmed")
            return snapshot
        core.require(
            type(proof.get("protocol")) is int
            and proof["protocol"] == 1
            and proof.get("retry_permitted") is False
            and proof.get("version") == gate["owner_version"]
            and proof.get("stage") == ("stage1" if gate["stage"] == "stage1" else "final"),
            "confirmation_receipt_binding_mismatch",
        )
        return await self._act(
            scope, snapshot, "adopt_official_confirmation", owner_version=proof["version"], operation_id=proof.get("operation_id"), receipt_payload_digest=proof.get("payload_sha256"), receipt_digest=proof.get("receipt_sha256")
        )

    async def tick(self, scope: Scope):
        """One bounded observation/claim/effect, with no retry or browser event.

        The scheduler must tick within the observation gap and call recover on
        takeover. All DB conflicts propagate as no-dispatch outcomes. A commit
        exception never grants permission even if the claim was stored remotely.
        """
        snapshot = await self.repository.get(scope)
        if snapshot is None or snapshot["cancelled"]:
            return snapshot
        gate = snapshot["gate"]
        if gate and gate["state"] in {"submission_pending", "submission_unknown"}:
            return await self._reconcile(scope, snapshot)
        if gate and gate["state"] == "waiting_user":
            snapshot = await self._adopt_official(scope, snapshot)
            gate = snapshot["gate"]
            if gate["state"] == "confirmed":
                return snapshot
        if gate and gate["state"] not in {"waiting_user", "confirmed", "continuing"}:
            return snapshot
        observation = await asyncio.wait_for(self.owner.observe(scope), timeout=25)
        if observation is None:
            return snapshot
        core.require(isinstance(observation, ObservedProposal), "invalid_official_observation")
        observation.validate(scope)
        proposal = observation.proposal
        snapshot = await self.repository.register_gate(scope, expected_revision=snapshot["revision"], project_id=observation.project_id, stage=observation.stage, owner_version=proposal.owner_version, **observation.binding())
        if snapshot["gate"]["state"] != "waiting_user":
            return snapshot
        snapshot = await self._act(scope, snapshot, "observe_ready", **observation.binding())
        gate = snapshot["gate"]
        now = core.timestamp(int(self.clock()))
        if not snapshot["policy"]["auto_enabled"] or gate["auto_block_reason"] is not None or gate["due_at"] is None or now < gate["due_at"]:
            return snapshot
        claimed = await self._act(scope, snapshot, "claim_submission", operation_id=uuid.uuid4().hex, mode="automatic", **observation.binding())
        # A changed/cancelled row cannot dispatch merely because WE once held it.
        current = await self.repository.get(scope)
        if current is None or current["revision"] != claimed["revision"] or current["cancelled"]:
            return current
        try:
            await asyncio.wait_for(self.owner.submit_guarded(scope, claimed["gate"], proposal), timeout=10)
        except (OSError, TimeoutError, core.ConsentConflict):
            # Guard refusal or uncertain ACK: only a subsequent bound receipt
            # can establish confirmation; do not log raw transport information.
            pass
        current = await self.repository.get(scope)
        if current is None or current["cancelled"]:
            return current
        return await self._reconcile(scope, current, effect_returned=True)
