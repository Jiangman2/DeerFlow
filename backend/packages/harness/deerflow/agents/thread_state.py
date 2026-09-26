from collections.abc import Mapping
from typing import Annotated, NotRequired, TypedDict

from langchain.agents import AgentState

from deerflow.agents.goal_state import GoalState
from deerflow.agents.middlewares.receipt_verification import ReceiptVerdict
from deerflow.subagents.acceptance_checks import AcceptanceVerdict
from deerflow.subagents.status_contract import SUBAGENT_STATUS_VALUES


class SandboxState(TypedDict):
    sandbox_id: NotRequired[str | None]


class ThreadDataState(TypedDict):
    workspace_path: NotRequired[str | None]
    uploads_path: NotRequired[str | None]
    outputs_path: NotRequired[str | None]


class ViewedImageData(TypedDict):
    """Metadata for a viewed image file.

    Only lightweight metadata is persisted in checkpoint state; the actual
    image bytes are read on-demand from disk when the model needs them.
    This avoids duplicating large base64 payloads across every checkpoint
    (see #4138).
    """

    mime_type: str
    size: int
    actual_path: str


def merge_sandbox(existing: SandboxState | None, new: SandboxState | None) -> SandboxState | None:
    """Reducer for sandbox state - accepts idempotent writes only.

    Multiple sandbox tools can initialize lazily in the same graph step and
    emit the same sandbox_id via Command(update=...). LangGraph needs an
    explicit reducer for that shared state key. Different sandbox ids in the
    same thread indicate a lifecycle/isolation bug, so fail closed instead of
    choosing one silently.
    """
    if new is None:
        return existing
    if existing is None:
        return new

    existing_id = existing.get("sandbox_id")
    new_id = new.get("sandbox_id")
    if existing_id == new_id:
        return existing
    raise ValueError(f"Conflicting sandbox state updates: {existing_id!r} != {new_id!r}")


SandboxStateField = Annotated[NotRequired[SandboxState | None], merge_sandbox]


def merge_artifacts(existing: list[str] | None, new: list[str] | None) -> list[str]:
    """Reducer for artifacts list - merges and deduplicates artifacts."""
    if existing is None:
        return new or []
    if new is None:
        return existing
    # Use dict.fromkeys to deduplicate while preserving order
    return list(dict.fromkeys(existing + new))


def merge_viewed_images(existing: dict[str, ViewedImageData] | None, new: dict[str, ViewedImageData] | None) -> dict[str, ViewedImageData]:
    """Reducer for viewed_images dict - merges image dictionaries.

    Special case: If new is an empty dict {}, it clears the existing images.
    This allows middlewares to clear the viewed_images state after processing.
    """
    if existing is None:
        return new or {}
    if new is None:
        return existing
    # Special case: empty dict means clear all viewed images
    if len(new) == 0:
        return {}
    # Merge dictionaries, new values override existing ones for same keys
    return {**existing, **new}


def merge_todos(existing: list | None, new: list | None) -> list | None:
    """Reducer for todos list - keeps the last non-None value.

    Semantics:
    - If `new` is None (node didn't touch todos), preserve `existing`.
    - If `new` is provided (even empty list), it represents an explicit
      update and wins over `existing`.
    """
    if new is None:
        return existing
    return new


def merge_goal(existing: GoalState | None, new: GoalState | None) -> GoalState | None:
    """Reducer for goal state - preserves existing when a node does not touch it."""
    if new is None:
        return existing
    return new


class PromotedTools(TypedDict):
    catalog_hash: str
    names: list[str]


def merge_promoted(existing: PromotedTools | None, new: PromotedTools | None) -> PromotedTools | None:
    """Reducer for deferred-tool promotions, scoped by catalog hash.

    - new None/empty -> preserve existing (node didn't touch promotions).
    - catalog_hash changed -> replace wholesale, dropping stale names (prevents a
      persisted bare name from exposing a different tool after catalog drift).
    - same catalog_hash -> union names, dedupe, preserve order.
    """
    if not new:
        return existing
    if existing is None or existing.get("catalog_hash") != new["catalog_hash"]:
        return {
            "catalog_hash": new["catalog_hash"],
            "names": list(dict.fromkeys(new["names"])),
        }
    return {
        "catalog_hash": existing["catalog_hash"],
        "names": list(dict.fromkeys(existing["names"] + new["names"])),
    }


TERMINAL_STATUSES: frozenset[str] = frozenset(SUBAGENT_STATUS_VALUES)
_DELEGATION_LEDGER_MAX_ENTRIES = 50


class DelegationEntry(TypedDict):
    id: str
    run_id: NotRequired[str]
    description: str
    subagent_type: str
    status: str
    result_brief: NotRequired[str]
    result_sha256: NotRequired[str]
    result_ref: NotRequired[str]
    receipt_verdict: NotRequired[ReceiptVerdict]
    acceptance_verdict: NotRequired[AcceptanceVerdict]
    # Why a guardrail cap ended the run early (#3875 Phase 2): token_capped /
    # turn_capped / loop_capped. The status stays completed/failed; this field
    # is the additive signal that distinguishes a capped run from a clean one.
    stop_reason: NotRequired[str]
    created_at: str


def merge_delegations(existing: list[DelegationEntry] | None, new: list[DelegationEntry] | None) -> list[DelegationEntry]:
    """Reducer for the delegation ledger.

    - new None/empty -> preserve existing.
    - append entries, replacing same id with the latest version while preserving
      first-seen order.
    - terminal status is never overwritten by a non-terminal status.
    """
    if not new:
        return existing or []

    by_id: dict[str, DelegationEntry] = {}
    order: list[str] = []
    for entry in [*(existing or []), *new]:
        entry_id = entry["id"]
        previous = by_id.get(entry_id)
        if previous is not None and previous["status"] in TERMINAL_STATUSES and entry["status"] not in TERMINAL_STATUSES:
            continue
        if entry_id not in by_id:
            order.append(entry_id)
        elif previous.get("created_at"):
            entry = {**entry, "created_at": previous["created_at"]}
            if previous.get("run_id") and not entry.get("run_id"):
                entry["run_id"] = previous["run_id"]
        by_id[entry_id] = entry
    merged = [by_id[entry_id] for entry_id in order]
    if len(merged) > _DELEGATION_LEDGER_MAX_ENTRIES:
        merged = merged[-_DELEGATION_LEDGER_MAX_ENTRIES:]
    return merged


_SKILL_CONTEXT_MAX_ENTRIES = 8
_SKILL_DESCRIPTION_MAX_CHARS = 500
_SKILL_WORKFLOW_EVIDENCE_MAX_ENTRIES = 256


class SkillEntry(TypedDict):
    name: str
    path: str
    description: str
    loaded_at: int


def _normalize_skill_entry(entry: Mapping[str, object]) -> SkillEntry:
    """Drop legacy payload keys before storing skill_context back to state."""
    description = entry.get("description")
    loaded_at = entry.get("loaded_at")
    return {
        "name": str(entry.get("name") or ""),
        "path": str(entry["path"]),
        "description": " ".join(description.split())[:_SKILL_DESCRIPTION_MAX_CHARS] if isinstance(description, str) else "",
        "loaded_at": loaded_at if isinstance(loaded_at, int) else 0,
    }


def merge_skill_context(existing: list[SkillEntry] | None, new: list[SkillEntry] | None) -> list[SkillEntry]:
    """Reducer for the skill-context channel.

    - new None/empty -> preserve existing.
    - legacy entries are normalized to references; verbatim body keys are dropped.
    - dedup by ``path``; later reads refresh recency and replace the reference.
    - cap by keeping the most recently read entries. ``loaded_at`` is
      observational only because message indices reset after compaction.
    """
    normalized_existing = [_normalize_skill_entry(entry) for entry in existing or []]
    if not new:
        return normalized_existing

    by_path: dict[str, SkillEntry] = {}
    order: list[str] = []
    for entry in normalized_existing:
        path = entry["path"]
        if path not in by_path:
            order.append(path)
        by_path[path] = entry

    for entry in (_normalize_skill_entry(entry) for entry in new):
        path = entry["path"]
        if path in by_path:
            order.remove(path)
        order.append(path)
        by_path[path] = entry

    merged = [by_path[path] for path in order]
    if len(merged) > _SKILL_CONTEXT_MAX_ENTRIES:
        merged = merged[-_SKILL_CONTEXT_MAX_ENTRIES:]
    return merged


class SkillWorkflowEvidence(TypedDict):
    """A compact, checkpointed receipt for one workflow-relevant tool result."""

    id: str
    tool: str
    status: str
    action: str
    detail: NotRequired[str]


class SkillWorkflowState(TypedDict, total=False):
    """Durable execution state for an explicitly activated Skill workflow.

    The Skill package remains the authority for route semantics.  This channel
    stores only host/runtime facts needed to resume safely after a clarification,
    a new run, or context compaction.
    """

    activation_id: str
    activation_message_id: str
    skill_name: str
    skill_path: str
    skill_sha256: str
    task_text: str
    status: str
    selected_authority: str
    selected_profile: str
    resume_documents: list[str]
    loaded_authority_run_id: str
    loaded_authority_documents: list[str]
    loaded_authority_context_fingerprint: str
    managed_artifacts: list[str]
    artifact_authority_dependencies: dict[str, str]
    ppt_master_dynamic_authority_documents: list[str]
    ppt_master_nonterminal_resources: list[str]
    ppt_master_design_spec_refresh_required: bool
    confirmation_surface: str
    confirmation_surface_switch_allowed: bool
    confirmation_wait_timed_out: bool
    confirmation_ui_active: bool
    confirmation_url: str
    template_selection_confirmed: bool
    template_selection_chat_confirmed: bool
    project_path: str
    project_path_source: str
    current_stage: str
    gate_status: str
    gate_detail: str
    gate_owner_receipt_id: str
    gate_owner_tool: str
    gate_owner_action: str
    gate_owner_stage: str
    guard_detail: str
    resume_pointer: str
    last_project_mutation_receipt_id: str
    failed_gate_attempt_mutation_receipt_id: str
    failed_gate_repeat_count: int
    completion_contract: str
    required_capabilities: dict[str, bool]
    evidence: list[SkillWorkflowEvidence]
    delivered_artifacts: list[str]


def merge_skill_workflow(
    existing: SkillWorkflowState | None,
    new: SkillWorkflowState | None,
) -> SkillWorkflowState | None:
    """Merge durable Skill workflow updates without losing prior receipts.

    A different ``activation_id`` starts a new workflow and replaces the old
    execution state.  Updates for the same activation merge scalar fields,
    capability requirements, evidence receipts, and delivered artifacts.
    """

    if new is None:
        return existing
    if existing is None or (new.get("activation_id") and new.get("activation_id") != existing.get("activation_id")):
        replacement = dict(new)
        replacement["required_capabilities"] = dict(new.get("required_capabilities") or {})
        replacement["artifact_authority_dependencies"] = dict(new.get("artifact_authority_dependencies") or {})
        replacement["evidence"] = list(new.get("evidence") or [])[-_SKILL_WORKFLOW_EVIDENCE_MAX_ENTRIES:]
        replacement["delivered_artifacts"] = list(dict.fromkeys(new.get("delivered_artifacts") or []))
        return replacement  # type: ignore[return-value]

    merged: dict = {**existing, **new}
    merged["required_capabilities"] = {
        **(existing.get("required_capabilities") or {}),
        **(new.get("required_capabilities") or {}),
    }
    merged["artifact_authority_dependencies"] = {
        **(existing.get("artifact_authority_dependencies") or {}),
        **(new.get("artifact_authority_dependencies") or {}),
    }

    evidence_by_id: dict[str, SkillWorkflowEvidence] = {}
    evidence_order: list[str] = []
    for entry in [*(existing.get("evidence") or []), *(new.get("evidence") or [])]:
        entry_id = entry.get("id")
        if not entry_id:
            continue
        if entry_id not in evidence_by_id:
            evidence_order.append(entry_id)
        evidence_by_id[entry_id] = entry
    if len(evidence_order) > _SKILL_WORKFLOW_EVIDENCE_MAX_ENTRIES:
        evidence_order = evidence_order[-_SKILL_WORKFLOW_EVIDENCE_MAX_ENTRIES:]
    merged["evidence"] = [evidence_by_id[entry_id] for entry_id in evidence_order]
    merged["delivered_artifacts"] = list(
        dict.fromkeys(
            [
                *(existing.get("delivered_artifacts") or []),
                *(new.get("delivered_artifacts") or []),
            ]
        )
    )
    return merged  # type: ignore[return-value]


class ThreadState(AgentState):
    sandbox: SandboxStateField
    thread_data: NotRequired[ThreadDataState | None]
    title: NotRequired[str | None]
    artifacts: Annotated[list[str], merge_artifacts]
    todos: Annotated[list | None, merge_todos]
    goal: Annotated[GoalState | None, merge_goal]
    uploaded_files: NotRequired[list[dict] | None]
    viewed_images: Annotated[dict[str, ViewedImageData], merge_viewed_images]  # image_path -> metadata (no base64)
    promoted: Annotated[PromotedTools | None, merge_promoted]
    delegations: Annotated[list[DelegationEntry], merge_delegations]
    skill_context: Annotated[list[SkillEntry], merge_skill_context]
    skill_workflow: Annotated[SkillWorkflowState | None, merge_skill_workflow]
    summary_text: NotRequired[str | None]
