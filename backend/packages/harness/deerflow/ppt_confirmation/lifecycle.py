"""Consent/continuation transitions, independent of Codex and browser lifetime.

NOT WIRED INTO PRODUCTION. This shared module does not implement a durable repository,
authorize a request, run a timer, submit HTTP or launch an Agent. The controller
must authenticate the owner, derive observations from the pinned official owner
(never model text), and commit each transition with an owner/task-scoped database
compare-and-swap before executing its effect. A failed CAS executes nothing.

The Agent Host image must ship this exact source rather than a second copy.
The official package owns fields, defaults, validators and receipt construction.
Only opaque identities/digests and transport state live here. Unknown effects
stay unknown across restarts; time passing never grants permission to retry.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re

AUTO_DELAY_SECONDS = 60
MAX_OBSERVATION_GAP_SECONDS = 20
MAX_SUBMISSION_OBSERVATION_AGE_SECONDS = 10
_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_DIGEST = re.compile(r"[a-f0-9]{64}\Z")


class ConsentConflict(ValueError):
    """Safe fixed labels only; do not attach raw project/owner payloads."""


def require(condition: bool, label: str) -> None:
    if not condition:
        raise ConsentConflict(label)


def identifier(value: object) -> str:
    require(isinstance(value, str) and _ID.fullmatch(value) is not None, "invalid_identity")
    return value


def fingerprint(value: object) -> str:
    require(isinstance(value, str) and _DIGEST.fullmatch(value) is not None, "invalid_digest")
    return value


def timestamp(value: object) -> int:
    require(type(value) is int and 0 <= value <= 253402300739, "invalid_timestamp")
    return value


def _changed(row: dict, **updates) -> dict:
    result = copy.deepcopy(row)
    result.update(updates)
    result["revision"] = row["revision"] + 1
    return result


def _actor(row: dict, actor_id: str, expected_revision: int | None = None) -> None:
    require(identifier(actor_id) == row["owner_id"], "owner_mismatch")
    if expected_revision is not None:
        require(type(expected_revision) is int and expected_revision == row["revision"], "stale_revision")


def _binding(row: dict, version: str, payload_digest: str, source_digest: str) -> bool:
    return all(row[key] == fingerprint(value) for key, value in (("version", version), ("payload_digest", payload_digest), ("source_digest", source_digest)))


def new_policy(owner_id: str, thread_id: str, task_id: str, *, runtime_profile: str, skill_name: str, opted_in: bool = False, new_task: bool = True) -> dict:
    """Called from authenticated initial-task admission, never from Agent prose.

    Existing tasks do not inherit new UI defaults. A continuation loads its
    ORIGINAL stored policy instead of calling this function again.
    """
    require(runtime_profile == "ppt-master-runner" and skill_name == "ppt-master", "unsupported_confirmation_scope")
    require(type(opted_in) is bool and type(new_task) is bool, "invalid_optin")
    return {"schema": 1, "owner_id": identifier(owner_id), "thread_id": identifier(thread_id), "task_id": identifier(task_id), "runtime_profile": runtime_profile, "skill_name": skill_name, "auto_enabled": opted_in and new_task}


def new_gate(policy: dict, *, project_id: str, stage: str, version: str, payload_digest: str, source_digest: str, now: int, previous: dict | None = None, owner_version: str | None = None) -> dict:
    """Register only a complete, validated official page/default proposal.

    project_id is a server-resolved opaque identity, not a client-selected path.
    source_digest pins the official adapter/Skill contract. payload_digest binds
    the actual UI-generated choices, NOT just the recommendations file.
    """
    require(policy.get("schema") == 1 and type(policy.get("auto_enabled")) is bool, "invalid_policy")
    require(policy.get("runtime_profile") == "ppt-master-runner" and policy.get("skill_name") == "ppt-master", "unsupported_confirmation_scope")
    binding = {key: identifier(policy[key]) for key in ("owner_id", "thread_id", "task_id")}
    binding.update(project_id=identifier(project_id), stage=identifier(stage), version=fingerprint(version), payload_digest=fingerprint(payload_digest), source_digest=fingerprint(source_digest))
    # The owner's conditional token is distinct from the full page/asset/default
    # fingerprint. Persist both so an ACK can be reconciled after process death.
    binding["owner_version"] = fingerprint(version if owner_version is None else owner_version)
    now = timestamp(now)
    reason = None if policy["auto_enabled"] else "manual_policy"
    if previous is not None:
        require(all(previous[key] == binding[key] for key in ("owner_id", "thread_id", "task_id", "project_id")), "previous_gate_mismatch")
        require(previous["state"] not in {"cancelled", "submission_pending", "submission_unknown", "continuation_pending", "continuation_unknown"}, "previous_effect_unresolved")
        if previous["stage"] == stage:
            require(any(previous[key] != binding[key] for key in ("version", "payload_digest", "source_digest")), "existing_gate_must_be_reused")
            reason = "recommendation_changed"
        else:
            require(previous["state"] in {"confirmed", "continuing"}, "previous_stage_not_confirmed")
    gate_id = hashlib.sha256(json.dumps(binding, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {
        "schema": 1,
        **binding,
        "gate_id": gate_id,
        "revision": 0,
        "state": "waiting_user",
        "created_at": now,
        "last_observed_at": now,
        "due_at": now + AUTO_DELAY_SECONDS if reason is None else None,
        "auto_block_reason": reason,
        "submission": None,
        "receipt_digest": None,
        "continuation": None,
    }


def observe_ready(row: dict, *, now: int, version: str, payload_digest: str, source_digest: str) -> dict:
    """Observer is server-side. No page-open/visibility event is required."""
    now = timestamp(now)
    if row["state"] != "waiting_user":
        return copy.deepcopy(row)
    if not _binding(row, version, payload_digest, source_digest):
        return _changed(row, state="superseded", due_at=None, auto_block_reason="recommendation_changed")
    if not 0 <= now - row["last_observed_at"] <= MAX_OBSERVATION_GAP_SECONDS:
        return _changed(row, due_at=None, auto_block_reason=row["auto_block_reason"] or "observation_gap", last_observed_at=now)
    if now == row["last_observed_at"]:
        return copy.deepcopy(row)
    return _changed(row, last_observed_at=now)


def take_over(row: dict, *, actor_id: str, expected_revision: int) -> dict:
    _actor(row, actor_id, expected_revision)
    require(row["state"] == "waiting_user", "submission_already_claimed")
    return _changed(row, due_at=None, auto_block_reason="user_takeover")


def recover(row: dict) -> dict:
    """A new observer cannot treat elapsed downtime as a reviewed countdown."""
    state = row["state"]
    if state == "waiting_user":
        return _changed(row, due_at=None, auto_block_reason=row["auto_block_reason"] or "observer_recovered")
    if state == "submission_pending":
        return _changed(row, state="submission_unknown")
    if state == "continuation_pending":
        return _changed(row, state="continuation_unknown")
    return copy.deepcopy(row)


def claim_submission(row: dict, *, actor_id: str, expected_revision: int, operation_id: str, mode: str, now: int, version: str, payload_digest: str, source_digest: str) -> dict:
    """Commit returned record BEFORE sending one conditional official POST.

    Caller must hold the owner transaction across version validation and owner
    dispatch. A pre/post hash check without that owner's concurrency boundary
    is insufficient: it detects stale writes only after they have occurred.
    """
    _actor(row, actor_id, expected_revision)
    timestamp(now)
    require(row["state"] == "waiting_user", "submission_not_permitted")
    require(_binding(row, version, payload_digest, source_digest), "owner_version_changed")
    require(0 <= now - row["last_observed_at"] <= MAX_SUBMISSION_OBSERVATION_AGE_SECONDS, "fresh_owner_required")
    require(mode in {"manual", "automatic"}, "invalid_submission_mode")
    if mode == "automatic":
        require(row["auto_block_reason"] is None and row["due_at"] is not None and now >= row["due_at"], "automatic_not_due_or_disabled")
    return _changed(row, state="submission_pending", due_at=None, submission={"id": identifier(operation_id), "mode": mode, "claimed_at": now})


def record_confirmation(row: dict, *, version: str, payload_digest: str, source_digest: str, receipt_digest: str) -> dict:
    """The trusted official adapter supplies a receipt bound to THIS submission.

    HTTP 200, an Agent message, or a generic result.json from another round is
    not this proof. The adapter must reconcile uncertain requests read-only.
    """
    require(_binding(row, version, payload_digest, source_digest), "receipt_binding_mismatch")
    fingerprint(receipt_digest)
    if row["state"] in {"confirmed", "continuation_pending", "continuation_unknown", "continuing"}:
        require(row["receipt_digest"] == receipt_digest, "receipt_changed")
        return copy.deepcopy(row)
    require(row["state"] in {"submission_pending", "submission_unknown"}, "no_current_submission")
    return _changed(row, state="confirmed", receipt_digest=receipt_digest, due_at=None)


def adopt_official_confirmation(row: dict, *, owner_version: str, operation_id: str, receipt_payload_digest: str, receipt_digest: str) -> dict:
    """Observe a native page confirmation that won before/beside automation.

    Only a trusted owner adapter may supply this evidence; never an HTTP body
    from the user/model. The native version AND validated commit proof must be
    checked there. Keep actual user choices' digest separate from the proposed
    auto payload. Adoption is read-only reconciliation, not a second POST.
    """
    require(fingerprint(owner_version) == row["owner_version"], "official_receipt_version_mismatch")
    evidence = {"id": identifier(operation_id), "payload_digest": fingerprint(receipt_payload_digest)}
    fingerprint(receipt_digest)
    if row["state"] in {"confirmed", "continuation_pending", "continuation_unknown", "continuing"}:
        require(row.get("official_submission") == evidence and row["receipt_digest"] == receipt_digest, "receipt_changed")
        return copy.deepcopy(row)
    require(row["state"] in {"waiting_user", "submission_pending", "submission_unknown"}, "official_confirmation_not_permitted")
    return _changed(row, state="confirmed", due_at=None, receipt_digest=receipt_digest, official_submission=evidence)


def claim_continuation(row: dict, *, actor_id: str, expected_revision: int, original_turn: str, run_id: str) -> dict:
    """Reserve an exact run, never launch a second Agent beside a running one.

    waiting_user must be an official handoff with verified process cleanup,
    NOT inferred from the absence of artifacts or a completed Codex process.
    The durable run repository owns idempotent launch and cancellation races.
    """
    _actor(row, actor_id, expected_revision)
    require(row["state"] == "confirmed", "continuation_not_permitted")
    require(original_turn in {"running", "waiting_user"}, "original_turn_not_resumable")
    action = "observe_existing" if original_turn == "running" else "resume_same_session"
    return _changed(row, state="continuation_pending", continuation={"run_id": identifier(run_id), "action": action})


def record_continuation(row: dict, *, run_id: str) -> dict:
    require(row["state"] in {"continuation_pending", "continuation_unknown", "continuing"}, "no_continuation_claim")
    require(row["continuation"]["run_id"] == identifier(run_id), "continuation_identity_mismatch")
    return copy.deepcopy(row) if row["state"] == "continuing" else _changed(row, state="continuing")


def cancel(row: dict, *, actor_id: str) -> dict:
    """Revokes future effects; does not assert that an in-flight POST was undone."""
    _actor(row, actor_id)
    return _changed(row, state="cancelled", due_at=None, auto_block_reason="task_cancelled")
