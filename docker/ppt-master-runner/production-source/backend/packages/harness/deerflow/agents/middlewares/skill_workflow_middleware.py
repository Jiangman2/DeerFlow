"""Checkpoint and restore workflow progress for explicitly activated Skills.

Skill packages own their route semantics and validation scripts.  This middleware
does not invent stages; it records successful/failed tool receipts, selected
authority documents, project paths, and resume pointers in ``ThreadState`` so a
new run or context compaction does not turn durable files back into guesswork.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import posixpath
import re
import threading
from collections.abc import Awaitable, Callable
from typing import Any, override

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelCallResult, ModelRequest, ModelResponse, hook_config
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.runtime import Runtime
from langgraph.types import Command

from deerflow.agents.middlewares._bounded_dict import BoundedDict
from deerflow.agents.thread_state import SkillWorkflowEvidence, SkillWorkflowState, ThreadState
from deerflow.runtime.context_keys import CURRENT_RUN_PRE_EXISTING_MESSAGE_IDS_KEY

_PROJECT_PATH_RE = re.compile(r"(/mnt/user-data/workspace/projects/[^/\s'\";|&]+)")
_PROJECTS_ROOT = "/mnt/user-data/workspace/projects"
_PPT_MASTER_MODEL_TURN_TIMEOUT_SECONDS = 600.0
_PROJECT_INIT_COMMAND_RE = re.compile(r"(?:^|/)project_manager\.py[\"']?\s+init(?:\s|$)")
_PROJECT_INIT_RECEIPT_RE = re.compile(
    r"(?m)^(?:Project created:|\[OK\]\s*Project initialized:)\s*"
    r"(/mnt/user-data/workspace/projects/[^/\s'\";|&]+)\s*$"
)
_PROJECT_CONTRACT_NAMES = frozenset({"design_spec.md", "spec_lock.md"})
_PYTHON_SCRIPT_RE = re.compile(r"(?:^|/)([A-Za-z0-9_.-]+\.py)(?=\s|$)")
_STAGE_FLAG_RE = re.compile(r"--stage(?:=|\s+)([A-Za-z0-9_.-]+)")
_TOP_LEVEL_WORKFLOW_RE = re.compile(r"/workflows/([^/]+\.md)$")
_PROFILE_RE = re.compile(r"/workflows/profiles/([^/]+\.md)$")
_SKILL_DOCUMENT_RE = re.compile(r"^/mnt/skills/.+\.md$")
_ARTIFACT_AUTHORITY_DEPENDENCY_RE = re.compile(
    r"(?:author|write|create)[^\r\n]{0,160}?`(?P<artifact>[A-Za-z0-9_.-]+\.[A-Za-z0-9]+)`"
    r"[^\r\n]{0,160}?`?\$\{SKILL_DIR\}/(?P<reference>[A-Za-z0-9_./-]+\.md)`?",
    re.IGNORECASE,
)
_QUICK_PROFILE_SUFFIX = "/workflows/profiles/quick-generate.md"
_QUICK_GENERATE_FLAG_RE = re.compile(r"(?:^|\s)--quick-generate(?:\s|$)")
_PPT_MASTER_ATTRIBUTION_GUARD_RE = re.compile(
    r"^python(?:3(?:\.\d+)?)?\s+(?:['\"])?(?:/mnt/skills/[^\s'\"]+/)?scripts/attribution_guard\.py(?:['\"])?$",
    re.IGNORECASE,
)
_QUICK_INTENT_RE = re.compile(
    r"\bquick(?:ly)?\b|\bfast\b|快速(?:生成|制作|完成)?|快捷(?:生成|制作)?|"
    r"跳过(?:策略|规划|确认)|直接.{0,24}(?:SVG|svg).{0,24}(?:PPTX|pptx|导出)",
    re.IGNORECASE,
)
_QUICK_NEGATION_RE = re.compile(
    r"(?:不要|不使用|禁止|避免).{0,18}(?:快速|快捷|Quick|quick|Fast|fast)|"
    r"(?:do\s+not|don't|without)\s+(?:use\s+)?(?:quick|fast)",
    re.IGNORECASE,
)
_TEMPLATE_CONFIRM_PROMPT_RE = re.compile(
    r"模板选择确认|是否确认使用.{0,20}模板|请回复.{0,20}(?:确认|继续)|"
    r"confirm.{0,30}(?:template|deck)",
    re.IGNORECASE,
)
_TEMPLATE_CONFIRM_REPLY_RE = re.compile(
    r"^(?:确认(?:使用)?(?:此|该)?模板|确认|继续|使用.{0,30}模板|"
    r"confirm(?:\s+(?:this|the))?\s+(?:template|deck)|use\s+(?:this|the)\s+(?:template|deck))[。.!！\s]*$",
    re.IGNORECASE,
)
_TEMPLATE_RESET_INTENT_RE = re.compile(
    r"重新(?:选择|选|确认).{0,12}模板|重置.{0,12}模板|"
    r"(?:reselect|reset|change).{0,24}(?:template|deck)",
    re.IGNORECASE,
)
_CONFIRMATION_SURFACE_SWITCH_INTENT_RE = re.compile(
    r"(?:切换|改用|使用).{0,8}(?:聊天|文字).{0,8}(?:确认|模式)|"
    r"关闭.{0,8}(?:确认页面|确认界面)|"
    r"(?:switch|use).{0,24}(?:chat|text).{0,12}(?:confirmation|fallback)|"
    r"(?:close|shutdown).{0,16}(?:confirmation|confirm\s*ui)",
    re.IGNORECASE,
)
_CONFIRM_UI_SERVER_RE = re.compile(r"(?:^|/)confirm_ui/server\.py(?:\s|$)")
_CONFIRM_UI_URL_RE = re.compile(
    r"http://(?:127\.0\.0\.1|localhost|\[?::1\]?):(?P<port>\d+)(?P<path>/[^\s]*)?",
    re.IGNORECASE,
)
_RUNTIME_PACKAGE_INSTALL_RE = re.compile(
    r"(?:^|[;&|]\s*)(?:sudo\s+)?(?:"
    r"(?:python(?:3(?:\.\d+)?)?\s+-m\s+pip|pip3?|uv\s+pip)\s+install\b|"
    r"(?:apt(?:-get)?|apk|dnf|yum)\s+(?:[^;&|\r\n]+\s+)?install\b|"
    r"(?:npm|pnpm|yarn)\s+(?:add|install)\b"
    r")",
    re.IGNORECASE,
)
_CONFIRM_UI_WAIT_STAGE_RE = re.compile(r"--wait-stage(?:=|\s+)(template|stage1|stage2|final)(?:\s|$)")
_CONFIRM_UI_OPERATION_FLAGS = (
    "--complete-template-phase",
    "--reset-template-phase",
    "--wait-only",
    "--daemon",
    "--shutdown",
)
_SHELL_WRITE_RE = re.compile(r">|\btee\b|write_(?:text|bytes)\s*\(|json\.dump\s*\(|open\s*\([^)]*,\s*['\"]w", re.IGNORECASE)
_SAFE_REPLAY_ENV_ASSIGNMENT_RE = re.compile(
    r"^(?:export\s+)?[A-Za-z_][A-Za-z0-9_]*=(?:'[^']*'|\"[^\"]*\"|[^\s;&|]+)$"
)
_SAFE_REPLAY_PYTHON_SCRIPT_RE = re.compile(
    r"^python(?:3(?:\.\d+)?)?\s+(?:'[^']+\.py'|\"[^\"]+\.py\"|[^\s]+\.py)(?:\s|$)",
    re.IGNORECASE,
)
_SAFE_REPLAY_VALIDATION_VERB_RE = re.compile(r"(?:^|\s)(?:validate|check|verify|lint)(?:\s|$)", re.IGNORECASE)
_SAFE_REPLAY_VALIDATION_SCRIPT_RE = re.compile(
    r"(?:^|/)[^/\s'\"]*(?:validator|validate|checker|check|lint)[^/\s'\"]*\.py(?:['\"]?)(?:\s|$)",
    re.IGNORECASE,
)
_OWNER_OUTPUT_TRUNCATION_RE = re.compile(
    r"\s+(?:2>\s*&1\s*)?\|\s*(?:head|tail)(?:\s+(?:-n\s*)?-?\d+)?\s*$",
    re.IGNORECASE,
)
_TEMPLATE_INSTALL_MUTATION_RE = re.compile(
    r"(?:^|[\r\n;]|&&|\|\|)\s*(?:cp|rsync|mv|install)\b|shutil\.(?:copy|copy2|copytree)\s*\(",
    re.IGNORECASE,
)
_WAITING_RE = re.compile(r"[?？]|(?:请|需要).{0,30}(?:确认|选择|回复)|waiting\s+for", re.IGNORECASE)
_COMPLETION_CLAIM_RE = re.compile(
    r"(?:已完成|完成了|已生成|生成完成|已交付|交付完成|可以下载|"
    r"\bcompleted\b|\bdone\b|\bdelivered\b|\bgenerated\b)",
    re.IGNORECASE,
)

_CAPABILITY_ROW_PATTERNS: dict[str, re.Pattern[str]] = {
    "speaker_notes": re.compile(r"\|\s*Speaker Notes\s*\|\s*enabled\b", re.IGNORECASE),
    "object_animations": re.compile(r"\|\s*Custom Animations\s*\|\s*enabled\b", re.IGNORECASE),
    "narration_audio": re.compile(r"\|\s*Narration Audio\s*\|\s*enabled\b", re.IGNORECASE),
    "ai_images": re.compile(
        r"\|\s*AI Image Acquisition Path\s*\|\s*(?!none\b)[^|\r\n]+|"
        r"\|[^\r\n]*\|\s*ai\s*\|\s*Generated\s*\|",
        re.IGNORECASE,
    ),
    "native_charts": re.compile(r"Native-ready\*{0,2}\s*:\s*yes", re.IGNORECASE),
    "template": re.compile(
        r"Template Application\*{0,2}\s*:\s*(?![^\r\n]*\bfree design\b)[^\r\n]+",
        re.IGNORECASE,
    ),
}

_PPT_MASTER_SAFE_REFERENCE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_PPT_MASTER_ANIMATION_OWNER_RE = re.compile(
    r"(?:^|/)(?:pptx_animations|pptx_transitions|animation_config)\.py(?:\s|$)|/animations\.json(?:\s|$)",
    re.IGNORECASE,
)
_PPT_MASTER_AUDIO_OWNER_RE = re.compile(
    r"(?:^|/)(?:notes_to_audio|native_narration_pptx|narration_sync)\.py(?:\s|$)",
    re.IGNORECASE,
)

_MAX_SEQUENCE_GUARD_ATTEMPTS = 3
_MAX_RESUME_STAGE_DOCUMENTS = 24

_RECOVERY_PROMPT = """<system_reminder>
The active Skill workflow has not reached its checkpointed completion contract.
Do not claim completion. Resume from the persisted workflow state and existing
project files, run the exact owning validation/export step, and deliver only after
the required artifact passes its acceptance check and present_files succeeds.
</system_reminder>"""

_CONFIRMATION_MANAGED_ARTIFACTS = (
    "confirm_ui/template_selection.json",
    "confirm_ui/template_handoff.json",
    "confirm_ui/result.json",
)

_MANAGED_ARTIFACT_ERROR_MARKER = "owned by the active Skill's confirmation user/tool boundary"
_CONFIRM_UI_PREFLIGHT_PREFIX = "DEERFLOW_CONFIRM_UI_PREFLIGHT="


def _runtime_run_id(runtime: Runtime) -> str:
    context = getattr(runtime, "context", None)
    if isinstance(context, dict):
        return str(context.get("run_id") or context.get("run_attempt_id") or id(runtime))
    return str(id(runtime))


def _runtime_pre_existing_message_ids(runtime: Runtime) -> frozenset[str]:
    """Return message IDs checkpointed before the current run began."""

    context = getattr(runtime, "context", None)
    if not isinstance(context, dict):
        return frozenset()
    raw_ids = context.get(CURRENT_RUN_PRE_EXISTING_MESSAGE_IDS_KEY)
    if not isinstance(raw_ids, (frozenset, set, list, tuple)):
        return frozenset()
    return frozenset(str(message_id) for message_id in raw_ids if message_id)


def _message_id(message: object) -> str | None:
    if isinstance(message, dict):
        message_id = message.get("id")
    else:
        message_id = getattr(message, "id", None)
    return str(message_id) if message_id else None


def _required_authority_documents(workflow: SkillWorkflowState) -> list[str]:
    values = list(workflow.get("resume_documents") or [])
    for key in ("selected_authority", "selected_profile"):
        value = workflow.get(key)
        if isinstance(value, str) and value:
            values.append(value)
    skill_path = str(workflow.get("skill_path") or "")
    selected_profile = str(workflow.get("selected_profile") or "")
    managed_artifacts = workflow.get("managed_artifacts") or []
    if skill_path.endswith("/ppt-master/SKILL.md") and not selected_profile.endswith("/workflows/profiles/quick-generate.md") and any(str(value).startswith("confirm_ui/") for value in managed_artifacts):
        values.append(f"{posixpath.dirname(skill_path)}/scripts/docs/confirm_ui.md")
    # Project contracts become resume authorities only after a successful
    # write/read receipt records them in ``resume_documents``.  A freshly
    # initialized project deliberately has neither file yet: Default Generate
    # must complete template selection and Strategist confirmation first.
    return list(dict.fromkeys(posixpath.normpath(value) for value in values if value))


def _valid_project_root(candidate: str | None) -> str | None:
    """Return a normalized direct child of the persistent projects directory.

    Research supplements intentionally live beside project directories as
    ``projects/<slug>_research.md``.  They must never be promoted to a project
    root merely because a later action mentions their absolute path.
    """

    if not candidate:
        return None
    normalized = posixpath.normpath(str(candidate).rstrip("/.,:)"))
    if posixpath.dirname(normalized) != _PROJECTS_ROOT:
        return None
    basename = posixpath.basename(normalized)
    if not basename or basename in {".", ".."}:
        return None
    if posixpath.splitext(basename)[1].lower() in {
        ".md",
        ".json",
        ".ppt",
        ".pptx",
        ".pdf",
        ".svg",
        ".png",
        ".jpg",
        ".jpeg",
    }:
        return None
    return normalized


def _project_contract_path(path: str, project_path: str | None) -> bool:
    project = _valid_project_root(project_path)
    normalized = posixpath.normpath(path)
    return bool(project and posixpath.dirname(normalized) == project and posixpath.basename(normalized) in _PROJECT_CONTRACT_NAMES)


def _project_root_from_successful_receipt(
    tool: str,
    action: str,
    detail: str,
    existing: str | None,
) -> tuple[str | None, str | None]:
    """Resolve a project root only from a successful owning-tool receipt.

    The official initializer prints the canonical dated directory.  Other
    arbitrary actions (research files, images, exports, temporary previews)
    are not project-root authority and cannot overwrite a trusted root.
    """

    trusted_existing = _valid_project_root(existing)
    if trusted_existing:
        return trusted_existing, None

    if tool == "bash" and _PROJECT_INIT_COMMAND_RE.search(action):
        matches = _PROJECT_INIT_RECEIPT_RE.findall(detail)
        if matches:
            candidate = _valid_project_root(matches[-1])
            if candidate:
                return candidate, "project_manager_init_receipt"

    if tool in {"read_file", "write_file", "str_replace"} and posixpath.basename(action) in _PROJECT_CONTRACT_NAMES:
        candidate = _valid_project_root(posixpath.dirname(posixpath.normpath(action)))
        if candidate:
            return candidate, "project_contract_receipt"

    return None, None


def _is_terminal_safety_stop(message: AIMessage, runtime: Runtime) -> bool:
    """Keep a lower middleware's hard stop terminal across middleware layers."""

    context = getattr(runtime, "context", None)
    if isinstance(context, dict) and context.get("stop_reason") in {
        "loop_capped",
        "token_budget_exceeded",
    }:
        return True
    additional_kwargs = dict(message.additional_kwargs or {})
    if additional_kwargs.get("deerflow_terminal_stop"):
        return True
    content = message.content if isinstance(message.content, str) else str(message.content or "")
    return "[FORCED STOP]" in content


def _authority_reload_call_allowed(
    call: dict[str, Any],
    pending_documents: list[str],
) -> bool:
    """Allow the exact reload or bounded read-only diagnosis of a missing one."""

    tool = _tool_call_name(call)
    args = _tool_call_args(call)
    if tool == "read_file":
        # Exact reads close the barrier. Other reads remain available to inspect
        # an owning reference or diagnose a moved/missing persisted contract.
        return bool(str(args.get("path") or ""))
    if tool == "ls":
        return bool(str(args.get("path") or ""))
    if tool != "bash":
        return False
    command = str(args.get("command") or "").strip()
    if not command or _SHELL_WRITE_RE.search(command):
        return False
    return bool(
        re.search(
            r"(?:^|[;&|]\s*)(?:ls|find|rg|grep|head|tail|cat|test|stat|file|wc|sed)\b",
            command,
        )
    )


def _authority_reload_update(
    message: AIMessage,
    workflow: SkillWorkflowState,
    runtime: Runtime,
    pending_documents: list[str],
    summary_text: object = None,
) -> dict[str, Any]:
    """Replace a blocked action with real reads of the persisted authorities.

    A resumed run may begin with a user confirmation such as "continue".  Some
    models then prioritize the requested mutation over a hidden reminder to
    reload the Skill contract, repeatedly emitting the same blocked tool call.
    The host already knows the exact checkpointed authority paths, so perform
    those safe reads through the normal tool node.  Their real ToolMessages are
    then visible to the next model step and close the barrier without inventing
    or duplicating any Skill-owned state.
    """

    run_id = _runtime_run_id(runtime)
    # A path can legitimately require another read inside the same run after a
    # failed owner receipt or in-run context compaction invalidates the prior
    # authority load.  Scope synthetic tool-call ids to that recovery boundary;
    # reusing only ``run_id + path`` makes the receipt collector discard the
    # later read as already processed and leaves the barrier permanently open.
    reload_scope = "\0".join(
        (
            run_id,
            _authority_context_fingerprint(summary_text),
            str(workflow.get("gate_owner_receipt_id") or ""),
            str(workflow.get("failed_gate_attempt_mutation_receipt_id") or ""),
            str(workflow.get("failed_gate_repeat_count") or 0),
        )
    )
    tool_calls = []
    for index, path in enumerate(pending_documents):
        digest = hashlib.sha256(f"{reload_scope}\0{path}".encode()).hexdigest()[:16]
        tool_calls.append(
            {
                "id": f"skill-authority-reload-{index + 1}-{digest}",
                "name": "read_file",
                "args": {"path": path},
            }
        )

    additional_kwargs = dict(message.additional_kwargs or {})
    additional_kwargs.pop("tool_calls", None)
    additional_kwargs.pop("function_call", None)
    replacement = message.model_copy(
        update={
            "content": "Reloading the checkpointed Skill authority before resuming the requested action.",
            "tool_calls": tool_calls,
            "invalid_tool_calls": [],
            "additional_kwargs": additional_kwargs,
        }
    )
    workflow_update: SkillWorkflowState = {
        "activation_id": workflow.get("activation_id", ""),
    }
    if workflow.get("gate_status") == "failed":
        # Authority reload is a prerequisite inside the failed gate, not a new
        # gate that may supersede the failed owner and its resume pointer.
        workflow_update.update(
            {
                "status": "blocked",
                "gate_status": "failed",
                "gate_detail": str(workflow.get("gate_detail") or ""),
                "resume_pointer": str(workflow.get("resume_pointer") or ""),
            }
        )
    else:
        workflow_update.update(
            {
                "status": "active",
                "gate_status": "authority-reload-required",
                "resume_pointer": ", ".join(pending_documents),
            }
        )
    return {
        "messages": [replacement],
        "skill_workflow": workflow_update,
    }


def _route_selection_pending(workflow: SkillWorkflowState) -> bool:
    return workflow.get("skill_name") == "ppt-master" and workflow.get("completion_contract") == "presentation_delivery" and not workflow.get("selected_authority")


def _quick_profile_explicitly_requested(workflow: SkillWorkflowState) -> bool:
    """Return whether the immutable user task explicitly selects Quick Generate."""

    if workflow.get("skill_name") != "ppt-master":
        return False
    task_text = str(workflow.get("task_text") or "").strip()
    if not task_text or _QUICK_NEGATION_RE.search(task_text):
        return False
    return bool(_QUICK_INTENT_RE.search(task_text))


def _quick_generate_violation(call: dict[str, Any], workflow: SkillWorkflowState) -> str | None:
    if _tool_call_name(call) != "bash":
        return None
    action = _action_for_tool("bash", _tool_call_args(call))
    if not _QUICK_GENERATE_FLAG_RE.search(action):
        return None
    authority = str(workflow.get("selected_authority") or "")
    if not authority.endswith("/workflows/generate-pptx.md"):
        return None
    profile = str(workflow.get("selected_profile") or "")
    if _quick_profile_explicitly_requested(workflow) and profile.endswith(_QUICK_PROFILE_SUFFIX):
        return None
    return f"{posixpath.dirname(authority)}/profiles/quick-generate.md"


def _route_selection_call_allowed(call: dict[str, Any], workflow: SkillWorkflowState) -> bool:
    if _tool_call_name(call) != "read_file":
        return False
    path = posixpath.normpath(str(_tool_call_args(call).get("path") or ""))
    skill_path = str(workflow.get("skill_path") or "")
    skill_root = posixpath.dirname(skill_path) if skill_path else ""
    workflow_root = f"{skill_root}/workflows/" if skill_root else ""
    if not workflow_root or not path.startswith(workflow_root):
        return False
    relative = path[len(workflow_root) :]
    return "/" not in relative and relative.endswith(".md")


def _route_selection_document(
    call: dict[str, Any],
    workflow: SkillWorkflowState,
) -> str | None:
    """Return the top-level workflow filename named by a real read call."""

    if not _route_selection_call_allowed(call, workflow):
        return None
    path = posixpath.normpath(str(_tool_call_args(call).get("path") or ""))
    return posixpath.basename(path)


def _ppt_master_attribution_guard_action(
    action: str,
    workflow: SkillWorkflowState,
) -> bool:
    """Recognize only the Skill-owned, read-only integrity command.

    The official load order makes this command a prerequisite for routing.  A
    model may redundantly include it beside a later read, so route sequencing
    needs a semantic identity for the already successful receipt without
    accepting arbitrary shell companions.
    """

    action = action.strip()
    skill_path = posixpath.normpath(str(workflow.get("skill_path") or ""))
    skill_root = posixpath.dirname(skill_path).rstrip("/") if skill_path else ""
    if not action or not skill_root:
        return False
    if _SHELL_WRITE_RE.search(action) or any(marker in action for marker in ("\n", "\r", ";", "|", "`", "$(")):
        return False

    parts = [part.strip() for part in action.split("&&")]
    if not parts or any(not part for part in parts):
        return False
    invocation = parts[-1]
    for prefix in parts[:-1]:
        if prefix == f"cd {skill_root}" or _SAFE_REPLAY_ENV_ASSIGNMENT_RE.fullmatch(prefix):
            continue
        return False
    if not _PPT_MASTER_ATTRIBUTION_GUARD_RE.fullmatch(invocation):
        return False
    if f"{skill_root}/scripts/attribution_guard.py" in invocation:
        return True
    return bool(parts[:-1] and parts[-2] == f"cd {skill_root}")


def _ppt_master_attribution_guard_call(
    call: dict[str, Any],
    workflow: SkillWorkflowState,
) -> bool:
    if _tool_call_name(call) != "bash":
        return False
    return _ppt_master_attribution_guard_action(
        _action_for_tool("bash", _tool_call_args(call)),
        workflow,
    )


def _ppt_master_attribution_guard_succeeded(workflow: SkillWorkflowState) -> bool:
    for receipt in workflow.get("evidence") or []:
        if receipt.get("tool") != "bash" or receipt.get("status") != "passed":
            continue
        if _ppt_master_attribution_guard_action(str(receipt.get("action") or ""), workflow):
            return True
    return False


def _routing_authority_loaded(
    workflow: SkillWorkflowState,
    runtime: Runtime,
) -> bool:
    if workflow.get("loaded_authority_run_id") != _runtime_run_id(runtime):
        return False
    skill_root = posixpath.dirname(str(workflow.get("skill_path") or ""))
    routing_path = posixpath.normpath(f"{skill_root}/workflows/routing.md")
    loaded = {posixpath.normpath(str(value)) for value in workflow.get("loaded_authority_documents") or []}
    return routing_path in loaded


def _route_prerequisite_update(
    message: AIMessage,
    workflow: SkillWorkflowState,
    runtime: Runtime,
    *,
    tool_call: dict[str, Any],
    resume_pointer: str,
    detail: str,
) -> dict[str, Any]:
    """Execute one official route prerequisite instead of re-prompting it.

    This preserves a valid model-selected route read while removing redundant
    calls from the same batch.  For deterministic integrity/routing
    prerequisites the caller may supply a synthetic owning call.  In both
    cases the normal tool node produces the durable receipt consumed by the
    next model turn.
    """

    call = dict(tool_call)
    if not call.get("id"):
        digest = hashlib.sha256(
            f"{_runtime_run_id(runtime)}\0{resume_pointer}\0{detail}".encode()
        ).hexdigest()[:16]
        call["id"] = f"skill-route-prerequisite-{digest}"
    additional_kwargs = dict(message.additional_kwargs or {})
    additional_kwargs.pop("tool_calls", None)
    additional_kwargs.pop("function_call", None)
    replacement = message.model_copy(
        update={
            "content": detail,
            "tool_calls": [call],
            "invalid_tool_calls": [],
            "additional_kwargs": additional_kwargs,
        }
    )
    return {
        "messages": [replacement],
        "skill_workflow": {
            "activation_id": workflow.get("activation_id", ""),
            "status": "active",
            "gate_status": "route-selection-required",
            "gate_detail": detail,
            "guard_detail": "",
            "resume_pointer": resume_pointer,
        },
    }


def _top_level_route_switch_violation(
    call: dict[str, Any],
    workflow: SkillWorkflowState,
) -> str | None:
    """Return the persisted route when a run tries to load a competing route."""

    if _tool_call_name(call) != "read_file":
        return None
    path = posixpath.normpath(str(_tool_call_args(call).get("path") or ""))
    match = _TOP_LEVEL_WORKFLOW_RE.search(path)
    if not match or match.group(1) == "routing.md":
        return None
    selected = posixpath.normpath(str(workflow.get("selected_authority") or ""))
    if not selected or path == selected:
        return None
    return selected


def _authority_context_fingerprint(summary_text: object) -> str:
    if not summary_text:
        return ""
    return hashlib.sha256(str(summary_text).encode("utf-8")).hexdigest()


def _is_ppt_master_workflow(workflow: SkillWorkflowState) -> bool:
    skill_path = str(workflow.get("skill_path") or "").replace("\\", "/")
    return skill_path.endswith("/ppt-master/SKILL.md")


def _authority_context_changed(
    workflow: SkillWorkflowState,
    runtime: Runtime,
    summary_text: object = None,
) -> bool:
    """Return whether loaded authority is stale for the active execution scope.

    The generic workflow adapter treats an in-run summary rewrite as a new
    authority context.  ``ppt-master`` is deliberately run-scoped instead: its
    long Executor phase can compact on nearly every model/tool iteration, while
    the official contracts and generated project files stay durable.  Binding
    its authority receipt to the current run avoids turning healthy compaction
    into a full reference reload loop.  A new run still invalidates the receipt.
    """

    if workflow.get("loaded_authority_run_id") != _runtime_run_id(runtime):
        return True
    if _is_ppt_master_workflow(workflow):
        return False
    current_context = _authority_context_fingerprint(summary_text)
    return bool(current_context and workflow.get("loaded_authority_context_fingerprint") != current_context)


def _authority_reload_pending(
    workflow: SkillWorkflowState,
    runtime: Runtime,
    summary_text: object = None,
) -> list[str]:
    required = _required_authority_documents(workflow)
    if not required:
        return []
    if _authority_context_changed(workflow, runtime, summary_text):
        return required
    loaded = {posixpath.normpath(value) for value in workflow.get("loaded_authority_documents") or []}
    return [value for value in required if value not in loaded]


def _artifact_authority_reload_pending(
    calls: list[dict[str, Any]],
    workflow: SkillWorkflowState,
    runtime: Runtime,
    summary_text: object = None,
) -> list[str]:
    """Return declared authority files missing before artifact mutations."""

    dependencies = workflow.get("artifact_authority_dependencies") or {}
    if not isinstance(dependencies, dict) or not dependencies:
        return []
    if _authority_context_changed(workflow, runtime, summary_text):
        loaded: set[str] = set()
    else:
        loaded = {posixpath.normpath(value) for value in workflow.get("loaded_authority_documents") or []}

    pending: list[str] = []
    for call in calls:
        tool = _tool_call_name(call)
        if tool not in {"write_file", "str_replace"}:
            continue
        path = posixpath.normpath(str(_tool_call_args(call).get("path") or ""))
        reference = dependencies.get(posixpath.basename(path))
        if isinstance(reference, str):
            normalized = posixpath.normpath(reference)
            if normalized not in loaded and normalized not in pending:
                pending.append(normalized)
    return pending


def _managed_artifacts_from_authority(content: str) -> list[str]:
    """Discover confirmation receipts that the Skill assigns to users/tools.

    The host does not invent a second workflow schema. It activates this
    boundary only after the selected Skill authority actually names the
    standard managed confirmation artifacts.
    """

    if "confirm_ui" not in content and "template_selection.json" not in content:
        return []
    return [path for path in _CONFIRMATION_MANAGED_ARTIFACTS if path in content or posixpath.basename(path) in content]


def _artifact_authority_dependencies_from_authority(
    content: str,
    skill_path: str,
) -> dict[str, str]:
    """Discover artifact prerequisites explicitly declared by Skill prose.

    The host records only direct ``author/write/create <artifact> from
    ${SKILL_DIR}/<reference>`` declarations from an authority document that was
    actually read. It does not infer a stage graph, schema, or validator.
    """

    normalized_skill_path = posixpath.normpath(skill_path) if skill_path else ""
    skill_root = (
        posixpath.dirname(normalized_skill_path).rstrip("/")
        if posixpath.basename(normalized_skill_path).casefold() == "skill.md"
        else normalized_skill_path.rstrip("/")
    )
    if not skill_root:
        return {}
    dependencies: dict[str, str] = {}
    for match in _ARTIFACT_AUTHORITY_DEPENDENCY_RE.finditer(content):
        artifact = posixpath.basename(match.group("artifact"))
        reference = posixpath.normpath(f"{skill_root}/{match.group('reference')}")
        if artifact and reference.startswith(f"{skill_root}/"):
            dependencies[artifact] = reference
    return dependencies


def _managed_artifact_violation(
    tool: str,
    args: dict[str, Any],
    workflow: SkillWorkflowState,
) -> str | None:
    """Return a managed receipt path when a model tool would fabricate it."""

    managed = list(workflow.get("managed_artifacts") or [])
    if not managed:
        return None
    project = str(workflow.get("project_path") or "").rstrip("/")

    if tool in {"write_file", "str_replace"}:
        target = next(
            (str(args[key]) for key in ("path", "file_path", "filepath") if isinstance(args.get(key), str)),
            "",
        )
        normalized = posixpath.normpath(target) if target else ""
        for relative in managed:
            expected = posixpath.normpath(f"{project}/{relative}") if project else ""
            if (expected and normalized == expected) or normalized.endswith("/" + relative):
                return relative
        return None

    if tool != "bash":
        return None
    command = str(args.get("command") or "")
    if _CONFIRM_UI_SERVER_RE.search(command) or not _SHELL_WRITE_RE.search(command):
        return None
    for relative in managed:
        if relative in command or posixpath.basename(relative) in command:
            return relative
    return None


def _confirmation_wait_target(
    tool: str,
    args: dict[str, Any],
    workflow: SkillWorkflowState,
) -> tuple[str, str] | None:
    """Return the active project and receipt stage for a managed Confirm UI wait.

    The host does not validate recommendation fields itself. It only recognizes
    the official wait command so it can ask the already-running owner service to
    validate its own input before entering a long user wait.
    """

    if tool != "bash":
        return None
    skill_path = str(workflow.get("skill_path") or "")
    if not skill_path.endswith("/ppt-master/SKILL.md"):
        return None
    command = str(args.get("command") or "")
    if not _CONFIRM_UI_SERVER_RE.search(command) or "--wait-only" not in command:
        return None
    stage_match = _CONFIRM_UI_WAIT_STAGE_RE.search(command)
    target = stage_match.group(1) if stage_match else "final"
    if target == "template":
        return None
    project_match = _PROJECT_PATH_RE.search(command)
    if not project_match:
        return None
    return project_match.group(1).rstrip("/.,:)"), target


def _confirm_ui_preflight_command(project: str, target: str) -> str:
    """Build a bounded probe against the official Confirm UI owner endpoints."""

    project_literal = json.dumps(project, ensure_ascii=True)
    target_literal = json.dumps(target, ensure_ascii=True)
    prefix_literal = json.dumps(_CONFIRM_UI_PREFLIGHT_PREFIX, ensure_ascii=True)
    return f"""python3 - <<'PY'
import json
import pathlib
import time
import urllib.error
import urllib.request

project = {project_literal}
target = {target_literal}
prefix = {prefix_literal}

def emit(status, error=None):
    payload = {{"status": status}}
    if error:
        payload["error"] = str(error).replace("\\n", " ")[:800]
    print(prefix + json.dumps(payload, ensure_ascii=False, separators=(",", ":")))

def request_json(url):
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            raw = response.read().decode("utf-8", errors="replace")
            return response.status, json.loads(raw or "{{}}")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw or "{{}}")
        except json.JSONDecodeError:
            payload = {{"error": raw}}
        return exc.code, payload

def receipt_ready(session):
    stage = str(session.get("result_stage") or "")
    if target == "stage1":
        return stage in {{"stage1", "stage2", "final"}}
    if target == "stage2":
        return stage in {{"stage2", "final"}}
    return stage == "final"

deadline = time.monotonic() + 6
last_error = "owner service unavailable"
while time.monotonic() < deadline:
    try:
        lock = json.loads((pathlib.Path(project) / ".confirm_ui.lock").read_text(encoding="utf-8"))
        port = int(lock["port"])
        base = f"http://127.0.0.1:{{port}}"
        session_status, session = request_json(base + "/api/session")
        if session_status == 200 and receipt_ready(session):
            emit("receipt-ready")
            raise SystemExit(0)
        recommendation_status, recommendation = request_json(base + "/api/recommendations")
        if recommendation_status == 200:
            emit("valid")
            raise SystemExit(0)
        if recommendation_status == 409:
            emit("invalid", recommendation.get("error") or "owner rejected the active recommendation")
            raise SystemExit(0)
        last_error = recommendation.get("error") or f"owner returned HTTP {{recommendation_status}}"
    except SystemExit:
        raise
    except Exception as exc:
        last_error = exc
    time.sleep(0.25)

emit("unavailable", last_error)
PY"""


def _confirm_ui_preflight_error(output: str) -> str | None:
    """Extract an owner-declared recommendation error from preflight output."""

    for line in str(output or "").splitlines():
        if not line.startswith(_CONFIRM_UI_PREFLIGHT_PREFIX):
            continue
        try:
            payload = json.loads(line[len(_CONFIRM_UI_PREFLIGHT_PREFIX) :])
        except json.JSONDecodeError:
            return None
        if payload.get("status") == "invalid":
            return str(payload.get("error") or "owner rejected the active recommendation")[:800]
    return None


def _resolve_active_sandbox(request: ToolCallRequest) -> Any | None:
    """Resolve the already-acquired sandbox without creating a new one."""

    states = [request.state, getattr(getattr(request, "runtime", None), "state", None)]
    sandbox_id = None
    for state in states:
        if not isinstance(state, dict):
            continue
        sandbox_state = state.get("sandbox")
        if isinstance(sandbox_state, dict) and sandbox_state.get("sandbox_id"):
            sandbox_id = sandbox_state["sandbox_id"]
            break
    if not sandbox_id:
        return None
    try:
        from deerflow.sandbox.sandbox_provider import get_sandbox_provider

        return get_sandbox_provider().get(str(sandbox_id))
    except Exception:
        return None


def _failed_gate_call_allowed(call: dict[str, Any], workflow: SkillWorkflowState) -> bool:
    """Allow only diagnosis or the owning recovery action for a failed receipt.

    The host deliberately does not encode a Skill's stage graph here.  It only
    prevents a failed tool receipt from being silently superseded by an
    unrelated action.  The Skill's own command remains the recovery authority.
    """

    tool = _tool_call_name(call)
    args = _tool_call_args(call)
    action = _action_for_tool(tool, args)
    gate_status = str(workflow.get("gate_status") or "")
    resume_pointer = str(workflow.get("resume_pointer") or "")

    if tool in {"read_file", "ls", "write_file", "str_replace"}:
        return True
    if tool == "ask_clarification":
        return bool(workflow.get("confirmation_surface_switch_allowed"))

    failed_owner = _failed_gate_owner_tool(workflow)
    if gate_status == "failed" and failed_owner == "present_files":
        # A failed delivery receipt is an acceptance failure, not an owning
        # build-command failure.  The agent must be able to repair the active
        # project and retry delivery; otherwise every legitimate post-process
        # command is removed before the tool node and the run can spin forever.
        if tool == "present_files":
            return True
        if tool == "bash":
            project_path = str(workflow.get("project_path") or "").rstrip("/")
            return bool(project_path and project_path in action)
        return False
    if tool != "bash":
        return False

    if gate_status == "managed-artifact-owner-required":
        return bool(_CONFIRM_UI_SERVER_RE.search(action))
    if _safe_read_only_skill_validation_action(action, workflow):
        return True
    if _same_owning_recovery_action(tool, action, workflow):
        return not _unchanged_failed_owner_retry(workflow)
    if _project_scoped_repair_action(tool, action, workflow):
        return True
    if "--help" in action:
        return True
    diagnostic_roots: list[str] = []
    project = _PROJECT_PATH_RE.search(resume_pointer)
    if project:
        diagnostic_roots.append(project.group(1))
    project_path = str(workflow.get("project_path") or "").rstrip("/")
    if project_path:
        diagnostic_roots.append(project_path)
    skill_path = str(workflow.get("skill_path") or "")
    skill_root = posixpath.dirname(skill_path).rstrip("/") if skill_path else ""
    if skill_root:
        diagnostic_roots.append(skill_root)
    if any(root in action for root in diagnostic_roots):
        return bool(
            re.search(
                r"(?:^|[;&|]\s*)(?:ls|find|rg|grep|head|tail|cat|test|stat|file|wc|sed)\b",
                action,
            )
        )
    return False


def _unchanged_failed_owner_retry(workflow: SkillWorkflowState) -> bool:
    """Return whether an unchanged owner retry already failed once.

    One retry without a project mutation is retained for transient failures.
    After that retry fails with the same semantic owner identity, the project
    must change before the owner can run again. This prevents a validator from
    cycling through failure -> authority reload -> unchanged validator retry.
    """

    if str(workflow.get("gate_status") or "") != "failed":
        return False
    try:
        repeat_count = max(0, int(workflow.get("failed_gate_repeat_count") or 0))
    except (TypeError, ValueError):
        repeat_count = 0
    return repeat_count >= 2 and str(workflow.get("failed_gate_attempt_mutation_receipt_id") or "") == str(workflow.get("last_project_mutation_receipt_id") or "")


def _failed_gate_snapshot_stale(workflow: SkillWorkflowState) -> bool:
    """Return whether the failed owner receipt predates a durable repair.

    A Skill validator's failure detail and any report it emitted describe the
    project version that existed when the owning command ran.  Once the active
    project changes, that diagnostic snapshot is historical until the same
    owning action runs again.  This is a generic host transaction invariant;
    it does not inspect or duplicate any Skill-specific report schema.
    """

    if str(workflow.get("gate_status") or "") != "failed":
        return False
    latest_mutation = str(workflow.get("last_project_mutation_receipt_id") or "")
    if not latest_mutation:
        return False
    return latest_mutation != str(workflow.get("failed_gate_attempt_mutation_receipt_id") or "")


def _ppt_master_consolidated_quality_repair_calls_allowed(
    calls: list[dict[str, Any]],
    workflow: SkillWorkflowState,
) -> bool:
    """Allow the official SVG gate's consolidated multi-file repair pass.

    ppt-master explicitly requires one complete issue review followed by one
    consolidated edit pass and one verification rerun. The generic stale-report
    rule must not force a checker rerun after the first file edit. During this
    narrow batch, current project files/contracts may be read or changed, but an
    old validation report, unrelated action, or workflow advance remains blocked.
    """

    if not calls or not str(workflow.get("skill_path") or "").endswith("/ppt-master/SKILL.md"):
        return False
    if str(workflow.get("gate_owner_stage") or "") not in {
        "svg_quality_checker.py:first-page",
        "svg_quality_checker.py:final",
    }:
        return False
    project = str(workflow.get("project_path") or "").rstrip("/")
    if not project:
        return False
    for call in calls:
        tool = _tool_call_name(call)
        action = _action_for_tool(tool, _tool_call_args(call))
        if tool in {"write_file", "str_replace"} and posixpath.normpath(action).startswith(f"{project}/"):
            continue
        if tool == "read_file":
            normalized = posixpath.normpath(action)
            if normalized.startswith(f"{project}/") and not normalized.startswith(f"{project}/validation/"):
                continue
        if tool == "bash" and _project_scoped_repair_action(tool, action, workflow):
            continue
        return False
    return True


def _ppt_master_model_turn_timeout_response(request: ModelRequest) -> ModelResponse | None:
    """Build a recoverable stop for an overlong ppt-master model turn.

    Provider read/chunk-gap timeouts are idle timers.  A streamed provider may
    keep either timer alive with empty or hidden reasoning chunks while making
    no workflow progress.  ppt-master projects already persist their contracts,
    receipts, stage and resume pointer, so end that single turn at a generous
    wall-clock boundary and let a later ``继续`` resume from durable state.

    This is deliberately scoped to the selected ppt-master workflow.  It does
    not impose a new model deadline on unrelated DeerFlow conversations.
    """

    workflow = (request.state or {}).get("skill_workflow")
    if not isinstance(workflow, dict):
        return None
    skill_path = str(workflow.get("skill_path") or "").replace("\\", "/")
    if not skill_path.endswith("/ppt-master/SKILL.md"):
        return None
    if workflow.get("completion_contract") != "presentation_delivery" or workflow.get("status") == "completed":
        return None

    stage = str(workflow.get("current_stage") or "unknown")
    resume_pointer = str(workflow.get("resume_pointer") or "按当前持久化 Gate 继续")
    if len(resume_pointer) > 600:
        resume_pointer = f"{resume_pointer[:597]}..."
    message = AIMessage(
        content=(
            f"当前 PPT 任务的单次模型回合已达到 {int(_PPT_MASTER_MODEL_TURN_TIMEOUT_SECONDS)} 秒上限，"
            "且本回合尚未产生新的可验证持久化进展，已安全停止以避免无界消耗。"
            f"项目文件和工作流检查点均已保留；当前阶段：{stage}；恢复位置：{resume_pointer}。"
            "请回复“继续”，任务将从该持久化位置恢复。"
        ),
        additional_kwargs={
            "deerflow_waiting_for_user": True,
            "waiting_reason": "ppt-master model turn wall-clock limit reached",
            "deerflow_model_turn_timeout": True,
        },
        response_metadata={"finish_reason": "stop"},
    )
    return ModelResponse(result=[message])


def _safe_replayable_owner_validation_action(workflow: SkillWorkflowState) -> str | None:
    """Return a checkpointed owner action only when replay is provably narrow.

    A project mutation makes a prior validation receipt stale. Asking the model
    to choose the only legal next action can still loop when it keeps repairing
    from the old report. The host may deterministically replay the exact stored
    action only for one local, project-scoped Python validation command under
    the selected Skill root. Image generation, export, publication, network
    submission, and arbitrary compound shell commands remain model/user owned.
    """

    if str(workflow.get("gate_owner_tool") or "") != "bash":
        return None
    action = str(workflow.get("gate_owner_action") or "").strip()
    project_path = str(workflow.get("project_path") or "").rstrip("/")
    skill_path = str(workflow.get("skill_path") or "")
    skill_root = posixpath.dirname(skill_path).rstrip("/") if skill_path else ""
    if not action or not project_path or not skill_root:
        return None
    if project_path not in action or skill_root not in action:
        return None
    if _SHELL_WRITE_RE.search(action) or any(marker in action for marker in ("\n", "\r", ";", "|", "`", "$(")):
        return None

    parts = [part.strip() for part in action.split("&&")]
    if not parts or any(not part for part in parts):
        return None
    if any(not _SAFE_REPLAY_ENV_ASSIGNMENT_RE.fullmatch(part) for part in parts[:-1]):
        return None
    invocation = parts[-1]
    if not _SAFE_REPLAY_PYTHON_SCRIPT_RE.match(invocation):
        return None
    if not (_SAFE_REPLAY_VALIDATION_VERB_RE.search(invocation) or _SAFE_REPLAY_VALIDATION_SCRIPT_RE.search(invocation)):
        return None
    return action


def _safe_read_only_skill_validation_action(
    action: str,
    workflow: SkillWorkflowState,
) -> bool:
    """Allow a Skill-owned read-only preflight needed to repair a failed gate.

    A failed owner can reveal that an earlier official stage was skipped. That
    stage may require validating a selected read-only Skill workspace before one
    atomic project install. Treating this validator as an unrelated Bash action
    makes the recovery loop impossible even though ordinary file reads are
    allowed. Keep the exception as narrow as deterministic owner replay: one
    local Python validator, optional simple environment assignments, no shell
    writes or mutating flags, and a target under the active project or selected
    Skill root.
    """

    action = action.strip()
    project_path = str(workflow.get("project_path") or "").rstrip("/")
    skill_path = str(workflow.get("skill_path") or "")
    skill_root = posixpath.dirname(skill_path).rstrip("/") if skill_path else ""
    if not action or not project_path or not skill_root:
        return False
    if _SHELL_WRITE_RE.search(action) or any(marker in action for marker in ("\n", "\r", ";", "|", "`", "$(")):
        return False
    if re.search(r"(?:^|\s)--(?:fix|inplace|write|output|export)(?:\s|=|$)", action, re.IGNORECASE):
        return False

    parts = [part.strip() for part in action.split("&&")]
    if not parts or any(not part for part in parts):
        return False
    if any(not _SAFE_REPLAY_ENV_ASSIGNMENT_RE.fullmatch(part) for part in parts[:-1]):
        return False
    invocation = parts[-1]
    if not _SAFE_REPLAY_PYTHON_SCRIPT_RE.match(invocation):
        return False
    if not (_SAFE_REPLAY_VALIDATION_VERB_RE.search(invocation) or _SAFE_REPLAY_VALIDATION_SCRIPT_RE.search(invocation)):
        return False
    targets_allowed_root = project_path in invocation or skill_root in invocation or action.count(skill_root) >= 2
    return targets_allowed_root


def _owner_validation_replay_update(
    message: AIMessage,
    workflow: SkillWorkflowState,
    runtime: Runtime,
    action: str,
) -> dict[str, Any]:
    """Replace a stale-report diversion with the exact safe owner validator."""

    replay_scope = "\0".join(
        (
            _runtime_run_id(runtime),
            str(workflow.get("gate_owner_receipt_id") or ""),
            str(workflow.get("last_project_mutation_receipt_id") or ""),
            action,
        )
    )
    digest = hashlib.sha256(replay_scope.encode()).hexdigest()[:16]
    additional_kwargs = dict(message.additional_kwargs or {})
    additional_kwargs.pop("tool_calls", None)
    additional_kwargs.pop("function_call", None)
    replacement = message.model_copy(
        update={
            "content": "Re-running the checkpointed read-only Skill validation against the repaired project.",
            "tool_calls": [
                {
                    "id": f"skill-owner-validation-replay-{digest}",
                    "name": "bash",
                    "args": {"command": action},
                }
            ],
            "invalid_tool_calls": [],
            "additional_kwargs": additional_kwargs,
        }
    )
    return {
        "messages": [replacement],
        "skill_workflow": {
            "activation_id": workflow.get("activation_id", ""),
            "status": "blocked",
            "gate_status": "failed",
            "gate_detail": str(workflow.get("gate_detail") or ""),
            "resume_pointer": str(workflow.get("resume_pointer") or action),
        },
    }


def _untruncated_skill_owner_action(
    call: dict[str, Any],
    workflow: SkillWorkflowState,
) -> str | None:
    """Remove a terminal ``head``/``tail`` pipe from a Skill Python owner.

    The Bash transport already bounds observable output. A terminal output
    truncation pipe can instead terminate the owning process with SIGPIPE and
    report the truncator's zero exit code, turning a partial image/export/build
    into false success. Only the final display-only pipe is removed; arbitrary
    pipelines and non-Skill commands are left untouched.
    """

    if _tool_call_name(call) != "bash":
        return None
    action = _action_for_tool("bash", _tool_call_args(call)).strip()
    skill_path = str(workflow.get("skill_path") or "")
    skill_root = posixpath.dirname(skill_path).rstrip("/") if skill_path else ""
    if not action or not skill_root or skill_root not in action or not _PYTHON_SCRIPT_RE.search(action):
        return None
    match = _OWNER_OUTPUT_TRUNCATION_RE.search(action)
    if not match:
        return None
    untruncated = action[: match.start()].rstrip()
    return untruncated if untruncated and untruncated != action else None


def _owner_output_untruncation_update(
    message: AIMessage,
    workflow: SkillWorkflowState,
) -> dict[str, Any] | None:
    """Rewrite only affected Bash calls while preserving their tool-call ids."""

    rewritten: list[dict[str, Any]] = []
    changed = False
    for call in message.tool_calls or []:
        if not isinstance(call, dict):
            rewritten.append(call)
            continue
        action = _untruncated_skill_owner_action(call, workflow)
        if not action:
            rewritten.append(call)
            continue
        replacement = dict(call)
        replacement["args"] = {**_tool_call_args(call), "command": action}
        rewritten.append(replacement)
        changed = True
    if not changed:
        return None
    additional_kwargs = dict(message.additional_kwargs or {})
    additional_kwargs.pop("tool_calls", None)
    additional_kwargs.pop("function_call", None)
    replacement_message = message.model_copy(
        update={
            "content": "Running the Skill-owned command without a terminal head/tail pipe so its real exit status is preserved.",
            "tool_calls": rewritten,
            "invalid_tool_calls": [],
            "additional_kwargs": additional_kwargs,
        }
    )
    return {
        "messages": [replacement_message],
        "skill_workflow": {"activation_id": workflow.get("activation_id", "")},
    }


def _project_file_mutation(tool: str, action: str, project_path: str) -> bool:
    """Recognize a successful durable file mutation inside the active project."""

    if not project_path:
        return False
    if tool == "bash":
        return bool(
            project_path in action
            and "/templates/decks/" in action
            and _TEMPLATE_INSTALL_MUTATION_RE.search(action)
        )
    if tool not in {"write_file", "str_replace"}:
        return False
    project = posixpath.normpath(project_path).rstrip("/")
    target = posixpath.normpath(action)
    return target == project or target.startswith(project + "/")


def _project_scoped_repair_action(
    tool: str,
    action: str,
    workflow: SkillWorkflowState,
) -> bool:
    """Allow a failed Skill command to be repaired inside its active project.

    The repair does not clear the failed gate. Only a successful retry of the
    owning command can do that. This keeps the host generic while allowing the
    Skill's own documented preconditions to be restored after a validator or
    phase-completion command reports what is missing.
    """

    if str(workflow.get("gate_status") or "") != "failed":
        return False
    if _CONFIRM_UI_SERVER_RE.search(action):
        return False
    project_path = str(workflow.get("project_path") or "").rstrip("/")
    return bool(project_path and project_path in action)


def _failed_gate_owner_tool(workflow: SkillWorkflowState) -> str:
    """Return the tool that owns the persisted failed receipt, if known."""

    explicit_owner = str(workflow.get("gate_owner_tool") or "")
    if explicit_owner:
        return explicit_owner
    resume_pointer = str(workflow.get("resume_pointer") or "")
    if not resume_pointer:
        return ""
    for entry in reversed(list(workflow.get("evidence") or [])):
        if entry.get("status") != "failed" or str(entry.get("action") or "") != resume_pointer:
            continue
        return str(entry.get("tool") or "")
    return ""


def _failed_gate_owner_evidence(
    workflow: SkillWorkflowState,
) -> SkillWorkflowEvidence | None:
    """Resolve the canonical failed owner receipt, including legacy checkpoints."""

    evidence = list(workflow.get("evidence") or [])
    explicit_receipt_id = str(workflow.get("gate_owner_receipt_id") or "")
    if explicit_receipt_id:
        for entry in reversed(evidence):
            if entry.get("status") == "failed" and str(entry.get("id") or "") == explicit_receipt_id:
                return entry

    owner_action = str(workflow.get("gate_owner_action") or workflow.get("resume_pointer") or "")
    owner_tool = str(workflow.get("gate_owner_tool") or "")
    if not owner_action:
        return None
    for entry in reversed(evidence):
        if entry.get("status") != "failed":
            continue
        if str(entry.get("action") or "") != owner_action:
            continue
        if owner_tool and str(entry.get("tool") or "") != owner_tool:
            continue
        return entry
    return None


def _successful_delivery_recovery(
    tool: str,
    action: str,
    workflow: SkillWorkflowState,
) -> bool:
    """Recognize a successful presentation delivery retry after acceptance repair."""

    return (
        str(workflow.get("gate_status") or "") == "failed"
        and _failed_gate_owner_tool(workflow) == "present_files"
        and tool == "present_files"
        and workflow.get("completion_contract") == "presentation_delivery"
        and bool(re.search(r"\.pptx?(?:\b|$)", action, re.IGNORECASE))
    )


def _same_owning_recovery_action(tool: str, action: str, workflow: SkillWorkflowState) -> bool:
    """Match a failed Skill command to a semantically equivalent retry.

    Models routinely add comments, exports, or harmless whitespace while
    retrying the same owning script. Exact command-string equality leaves a
    successful retry blocked forever, but matching only the script basename is
    too broad. Keep the project and explicit ``--stage`` identity stable too.
    """
    if str(workflow.get("gate_status") or "") != "failed" or tool != "bash":
        return False
    owner_tool = str(workflow.get("gate_owner_tool") or "")
    if owner_tool and owner_tool != tool:
        return False
    resume_pointer = str(workflow.get("gate_owner_action") or workflow.get("resume_pointer") or "")
    if not resume_pointer:
        return False
    if tool == "present_files":
        return (not owner_tool or owner_tool == "present_files") and action == resume_pointer
    if tool != "bash":
        return False
    if action == resume_pointer:
        return True

    failed_script = _PYTHON_SCRIPT_RE.search(resume_pointer)
    retry_script = _PYTHON_SCRIPT_RE.search(action)
    if not failed_script or not retry_script or failed_script.group(1) != retry_script.group(1):
        return False

    failed_operation = _confirm_ui_operation(resume_pointer)
    retry_operation = _confirm_ui_operation(action)
    if (failed_operation or retry_operation) and failed_operation != retry_operation:
        return False

    failed_project = _PROJECT_PATH_RE.search(resume_pointer)
    if failed_project:
        retry_project = _PROJECT_PATH_RE.search(action)
        if not retry_project or posixpath.normpath(retry_project.group(1)) != posixpath.normpath(failed_project.group(1)):
            return False

    failed_stage = _STAGE_FLAG_RE.search(resume_pointer)
    if failed_stage:
        retry_stage = _STAGE_FLAG_RE.search(action)
        if not retry_stage or retry_stage.group(1) != failed_stage.group(1):
            return False
    return True


def _confirm_ui_operation(action: str) -> str:
    """Return the semantic Confirm UI server operation carried by a command."""

    if not _CONFIRM_UI_SERVER_RE.search(action):
        return ""
    for flag in _CONFIRM_UI_OPERATION_FLAGS:
        if flag not in action:
            continue
        if flag == "--wait-only":
            stage = _CONFIRM_UI_WAIT_STAGE_RE.search(action)
            return f"{flag}:{stage.group(1) if stage else 'unspecified'}"
        return flag
    return "serve"


def _confirm_ui_wait_timed_out(tool: str, action: str, detail: str) -> bool:
    """Recognize the official confirmation owner's bounded user-wait timeout.

    A long poll that exhausts its documented wait budget is not a failed build
    or validation stage.  It is a control-flow boundary: the owner remains live,
    the current receipt must be rechecked once, and the Skill may then use its
    documented chat fallback.  Other Confirm UI errors remain ordinary failed
    owner receipts.
    """

    if tool != "bash" or not _CONFIRM_UI_SERVER_RE.search(action) or "--wait-only" not in action:
        return False
    return bool(
        re.search(
            r"(?:timed out|timeout).{0,80}(?:waiting for|browser|confirmation|selection)|"
            r"(?:waiting for|browser|confirmation|selection).{0,80}(?:timed out|timeout)",
            detail,
            re.IGNORECASE | re.DOTALL,
        )
    )


def _confirmation_timeout_from_evidence(workflow: SkillWorkflowState) -> bool:
    """Recover a template-confirmation timeout from durable owner receipts.

    Older checkpoints may have had their explicit fallback flags cleared when
    the Confirm UI daemon was restarted.  The owner's receipts remain the
    durable source of truth: a daemon restart does not consume a previously
    earned chat-fallback boundary, while a later template wait or explicit
    template-phase reset does.
    """

    for receipt in reversed(workflow.get("evidence") or []):
        if str(receipt.get("tool") or "") != "bash":
            continue
        action = str(receipt.get("action") or "")
        if not _CONFIRM_UI_SERVER_RE.search(action):
            continue
        status = str(receipt.get("status") or "")
        if "--reset-template-phase" in action and status == "passed":
            return False
        if "--wait-only" in action and "--wait-stage template" in action:
            return status == "waiting"
    return False


def _confirmation_reset_requested(messages: list[Any]) -> bool:
    """Return whether the latest user turn explicitly asks to redo selection."""

    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            return bool(_TEMPLATE_RESET_INTENT_RE.search(_conversation_message_text(message)))
    return False


def _confirmation_surface_switch_requested(messages: list[Any]) -> bool:
    """Return whether the latest user turn explicitly changes the owner surface."""

    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            return bool(
                _CONFIRMATION_SURFACE_SWITCH_INTENT_RE.search(
                    _conversation_message_text(message)
                )
            )
    return False


def _failure_requires_convergence(
    tool: str,
    action: str,
    workflow: SkillWorkflowState,
) -> bool:
    """Return whether a failed receipt is an enforceable Skill workflow gate."""

    if tool == "present_files":
        return workflow.get("completion_contract") == "presentation_delivery"
    if tool != "bash" or not _PYTHON_SCRIPT_RE.search(action):
        return False
    skill_path = str(workflow.get("skill_path") or "")
    skill_root = posixpath.dirname(skill_path) if skill_path else ""
    return "/mnt/skills/" in action or bool(skill_root and skill_root in action)


def _failed_gate_is_enforceable(workflow: SkillWorkflowState) -> bool:
    gate_status = str(workflow.get("gate_status") or "")
    if gate_status == "managed-artifact-owner-required":
        return True
    if gate_status != "failed":
        return False
    resume_pointer = str(workflow.get("gate_owner_action") or workflow.get("resume_pointer") or "")
    owner = _failed_gate_owner_evidence(workflow)
    if owner is not None:
        return _failure_requires_convergence(
            str(owner.get("tool") or ""),
            resume_pointer,
            workflow,
        )
    return _failure_requires_convergence(
        str(workflow.get("gate_owner_tool") or "bash"),
        resume_pointer,
        workflow,
    )


def _template_selection_confirmed(workflow: SkillWorkflowState) -> bool:
    if workflow.get("template_selection_confirmed") or workflow.get("template_selection_chat_confirmed"):
        return True
    for entry in workflow.get("evidence") or []:
        action = str(entry.get("action") or "")
        if entry.get("status") == "passed" and entry.get("tool") == "bash" and _CONFIRM_UI_SERVER_RE.search(action) and "--wait-only" in action and "--wait-stage template" in action:
            return True
    return False


def _template_install_requires_confirmation(workflow: SkillWorkflowState) -> bool:
    authority = str(workflow.get("selected_authority") or "")
    profile = str(workflow.get("selected_profile") or "")
    managed = set(workflow.get("managed_artifacts") or [])
    return authority.endswith("/workflows/generate-pptx.md") and not profile.endswith("/workflows/profiles/quick-generate.md") and "confirm_ui/template_selection.json" in managed and not _template_selection_confirmed(workflow)


def _template_install_violation(
    call: dict[str, Any],
    workflow: SkillWorkflowState,
) -> str | None:
    if not _template_install_requires_confirmation(workflow):
        return None
    if _tool_call_name(call) != "bash":
        return None
    action = _action_for_tool("bash", _tool_call_args(call))
    project = str(workflow.get("project_path") or "").rstrip("/")
    if not project or project not in action:
        return None
    if "/templates/decks/" not in action or not _TEMPLATE_INSTALL_MUTATION_RE.search(action):
        return None
    return f"{project}/confirm_ui/template_selection.json"


def _page_confirmation_fallback_violation(
    message: AIMessage,
    workflow: SkillWorkflowState,
) -> bool:
    """Reject a silent switch from an active page owner to plain chat text."""

    if workflow.get("confirmation_surface") != "page" or workflow.get("confirmation_surface_switch_allowed") or not workflow.get("confirmation_ui_active"):
        return False
    if message.tool_calls or getattr(message, "invalid_tool_calls", None):
        return False
    content = message.content if isinstance(message.content, str) else str(message.content)
    return bool(_WAITING_RE.search(content) or _TEMPLATE_CONFIRM_PROMPT_RE.search(content))


def _resume_documents_after_read(
    workflow: SkillWorkflowState,
    document: str,
    selected_authority: str | None,
) -> list[str]:
    existing = [posixpath.normpath(value) for value in workflow.get("resume_documents") or []]
    authority = posixpath.normpath(selected_authority) if selected_authority else None
    normalized_document = posixpath.normpath(document)
    supporting = [value for value in existing if value != authority and value != normalized_document]
    contracts = [value for value in supporting if posixpath.basename(value) in _PROJECT_CONTRACT_NAMES]
    stage_documents = [value for value in supporting if posixpath.basename(value) not in _PROJECT_CONTRACT_NAMES]
    if normalized_document != authority:
        if posixpath.basename(normalized_document) in _PROJECT_CONTRACT_NAMES:
            contracts = [value for value in contracts if posixpath.basename(value) != posixpath.basename(normalized_document)]
            contracts.append(normalized_document)
        else:
            stage_documents.append(normalized_document)
    result = ([authority] if authority else []) + stage_documents[-_MAX_RESUME_STAGE_DOCUMENTS:] + contracts
    return list(dict.fromkeys(value for value in result if value))


def _message_text(message: ToolMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "\n".join(parts)
    return str(content or "")


def _conversation_message_text(message: AIMessage | HumanMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "\n".join(parts)
    return str(content or "")


def _chat_template_selection_confirmed(messages: list[Any]) -> bool:
    """Recognize a real user reply to an immediately preceding template question."""

    for index, message in enumerate(messages):
        if not isinstance(message, HumanMessage):
            continue
        reply = _conversation_message_text(message).strip()
        if not _TEMPLATE_CONFIRM_REPLY_RE.fullmatch(reply):
            continue
        # Tool-bearing progress and a host-generated bounded blocker may sit
        # between the visible template question and the user's short reply.
        # Search only within the current human-turn boundary instead of
        # requiring the immediately preceding AI message to be the question.
        for previous in reversed(messages[:index]):
            if isinstance(previous, HumanMessage):
                break
            if isinstance(previous, AIMessage) and _TEMPLATE_CONFIRM_PROMPT_RE.search(_conversation_message_text(previous)):
                return True
    return False


def _tool_call_name(call: dict[str, Any]) -> str:
    name = call.get("name")
    if isinstance(name, str):
        return name
    function = call.get("function")
    return str(function.get("name") or "") if isinstance(function, dict) else ""


def _tool_call_args(call: dict[str, Any]) -> dict[str, Any]:
    args = call.get("args")
    if isinstance(args, dict):
        return args
    function = call.get("function")
    if isinstance(function, dict) and isinstance(function.get("arguments"), dict):
        return function["arguments"]
    return {}


def _tool_call_id(call: dict[str, Any]) -> str:
    return str(call.get("id") or call.get("tool_call_id") or "")


def _result_succeeded(
    result: ToolMessage | Command,
    *,
    tool: str = "",
    action: str = "",
) -> bool:
    if isinstance(result, Command):
        update = getattr(result, "update", None)
        if not isinstance(update, dict):
            return True
        messages = update.get("messages")
        if not isinstance(messages, list):
            return True
        tool_messages = [message for message in messages if isinstance(message, ToolMessage)]
        if not tool_messages:
            return True
        return all(_result_succeeded(message, tool=tool, action=action) for message in tool_messages)
    message = result
    if getattr(message, "status", "success") == "error":
        return False
    detail = _message_text(message)
    if detail.lstrip().casefold().startswith("error:"):
        return False
    # Shell pipelines such as ``2>&1 | tail`` can make an owning script's
    # non-zero exit status look successful. The official Confirm UI emits a
    # timestamped ERROR record on every failed wait/start, so retain that
    # owner-declared failure instead of checkpointing a false receipt.
    if tool == "bash" and _CONFIRM_UI_SERVER_RE.search(action) and re.search(r"(?m)^\[[^\]\r\n]+\]\s+\[ERROR\]\s+", detail):
        return False
    return True


def _bundled_skill_install_violation(
    tool: str,
    args: dict[str, Any],
    workflow: SkillWorkflowState,
) -> bool:
    """Reject conversational dependency installation for the shipped PPT Skill.

    The release image is built from the vendored requirements and is the only
    supported dependency boundary. If an import is genuinely missing, the
    correct recovery is to rebuild/redeploy that image, not mutate one user's
    ephemeral Sandbox during a run.
    """

    if tool != "bash":
        return False
    skill_path = str(workflow.get("skill_path") or "")
    if not skill_path.endswith("/ppt-master/SKILL.md"):
        return False
    return bool(_RUNTIME_PACKAGE_INSTALL_RE.search(str(args.get("command") or "")))


def _action_for_tool(tool: str, args: dict[str, Any]) -> str:
    if tool == "bash":
        return str(args.get("command") or "").strip()[:1600]
    for key in ("path", "file_path", "filepath"):
        if isinstance(args.get(key), str):
            return str(args[key])[:1600]
    if tool == "present_files":
        values = args.get("filepaths")
        if isinstance(values, list):
            return ", ".join(str(value) for value in values)[:1600]
    if tool == "ask_clarification":
        return str(args.get("question") or "")[:1600]
    return str(args)[:1600]


def _stage_for(tool: str, action: str) -> str:
    if tool == "present_files":
        return "delivery"
    if tool == "ask_clarification":
        return "confirmation"
    if tool in {"write_file", "str_replace"}:
        return f"author:{posixpath.basename(action)}" if action else "author"
    if tool == "read_file":
        return f"read:{posixpath.basename(action)}" if action else "read"
    if tool == "bash":
        script = _PYTHON_SCRIPT_RE.search(action)
        stage = _STAGE_FLAG_RE.search(action)
        if script and stage:
            return f"{script.group(1)}:{stage.group(1)}"
        if script:
            return script.group(1)
        return "bash"
    return tool


def _bounded_receipt_detail(value: object, max_chars: int = 1200) -> str:
    """Keep failure context bounded without discarding its final blocker summary."""

    text = str(value or "")
    if len(text) <= max_chars:
        return text
    marker = "\n... [middle omitted by host checkpoint] ...\n"
    available = max_chars - len(marker)
    head_chars = available // 2
    tail_chars = available - head_chars
    return text[:head_chars] + marker + text[-tail_chars:]


def _requirements_from_design_spec(content: str) -> dict[str, bool]:
    requirements: dict[str, bool] = {}
    for name, pattern in _CAPABILITY_ROW_PATTERNS.items():
        if pattern.search(content):
            requirements[name] = True
    return requirements


def _ppt_master_nonterminal_resources(content: str) -> list[str]:
    """Return acquisition rows that the official Step 5 has not closed."""

    section_match = re.search(
        r"(?ms)^##\s+(?:VIII\.?\s+)?Image Resource List\s*$\n(?P<body>.*?)(?=^##\s+|\Z)",
        content,
        re.IGNORECASE,
    )
    if not section_match:
        return []
    lines = [line.strip() for line in section_match.group("body").splitlines() if line.strip().startswith("|")]
    if len(lines) < 2:
        return []
    headers = [value.strip().casefold() for value in lines[0].strip("|").split("|")]
    try:
        filename_index = headers.index("filename")
        acquire_index = headers.index("acquire via")
        status_index = headers.index("status")
    except ValueError:
        return []
    resources: list[str] = []
    for line in lines[2:]:
        values = [value.strip() for value in line.strip("|").split("|")]
        if max(filename_index, acquire_index, status_index) >= len(values):
            continue
        acquire_via = values[acquire_index].casefold()
        status = values[status_index].casefold()
        if acquire_via in {"ai", "web", "slice"} and status in {"pending", "failed"}:
            filename = values[filename_index]
            if filename and filename not in resources:
                resources.append(filename)
    return resources


def _markdown_section(content: str, name: str) -> str:
    match = re.search(
        rf"(?ms)^##\s+{re.escape(name)}\s*$\n(?P<body>.*?)(?=^##\s+|\Z)",
        content,
    )
    return match.group("body") if match else ""


def _ppt_master_requirements_from_spec_lock(content: str) -> dict[str, bool]:
    """Project only the ppt-master branch facts needed by its host adapter."""

    requirements: dict[str, bool] = {}
    images = _markdown_section(content, "images")
    if re.search(r"(?m)^-\s+[^:\r\n]+:\s*.+$", images):
        requirements["images"] = True
    if re.search(r"(?:source\s*=\s*web|status\s*=\s*sourced)", images, re.IGNORECASE):
        requirements["web_images"] = True
    charts = _markdown_section(content, "page_charts")
    if re.search(r"(?m)^-\s+P\d+\s*:\s*.+$", charts):
        requirements["charts"] = True
    structure = _markdown_section(content, "pptx_structure")
    if re.search(r"(?m)^-\s+mode\s*:\s*structured\s*$", structure, re.IGNORECASE):
        requirements["structured_pptx"] = True
    return requirements


def _ppt_master_dynamic_authority_documents(content: str, skill_path: str) -> list[str]:
    """Resolve locked mode/style references to their official authority files."""

    if not skill_path.endswith("/ppt-master/SKILL.md"):
        return []
    skill_root = posixpath.dirname(skill_path)
    documents: list[str] = []
    for section_name, key, references_key, directory in (
        ("mode", "mode", "mode_references", "modes"),
        ("visual_style", "visual_style", "visual_style_references", "visual-styles"),
    ):
        section = _markdown_section(content, section_name)
        selected = re.search(rf"(?m)^-\s+{re.escape(key)}\s*:\s*([^\r\n]+?)\s*$", section)
        references = re.search(rf"(?m)^-\s+{re.escape(references_key)}\s*:\s*([^\r\n]+?)\s*$", section)
        ids: list[str] = []
        if references:
            ids.extend(value.strip() for value in references.group(1).split(","))
        elif selected and selected.group(1).strip().casefold() != "custom":
            ids.append(selected.group(1).strip())
        for reference_id in ids:
            if not _PPT_MASTER_SAFE_REFERENCE_ID_RE.fullmatch(reference_id):
                continue
            path = posixpath.normpath(f"{skill_root}/references/{directory}/{reference_id}.md")
            if path not in documents:
                documents.append(path)
    return documents


def _ppt_master_svg_authoring_call(call: dict[str, Any], workflow: SkillWorkflowState) -> bool:
    project = str(workflow.get("project_path") or "").rstrip("/")
    if not project:
        return False
    tool = _tool_call_name(call)
    action = _action_for_tool(tool, _tool_call_args(call))
    svg_root = f"{project}/svg_output/"
    if tool in {"write_file", "str_replace"}:
        normalized = posixpath.normpath(action)
        return normalized.startswith(svg_root) and normalized.casefold().endswith(".svg")
    return tool == "bash" and svg_root in action and bool(_SHELL_WRITE_RE.search(action))


def _ppt_master_contract_authoring_name(
    call: dict[str, Any],
    workflow: SkillWorkflowState,
) -> str | None:
    project = str(workflow.get("project_path") or "").rstrip("/")
    if not project:
        return None
    tool = _tool_call_name(call)
    args = _tool_call_args(call)
    action = _action_for_tool(tool, args)
    if tool in {"write_file", "str_replace"} and _project_contract_path(action, project):
        return posixpath.basename(posixpath.normpath(action))
    if tool == "bash" and _SHELL_WRITE_RE.search(action):
        for name in _PROJECT_CONTRACT_NAMES:
            if f"{project}/{name}" in action:
                return name
    return None


def _ppt_master_prospective_file_text(
    sandbox: Any,
    tool: str,
    args: dict[str, Any],
    path: str,
) -> str | None:
    """Simulate one normal file-tool mutation without changing the sandbox."""

    if tool == "write_file":
        content = str(args.get("content") or "")
        if not args.get("append"):
            return content
        try:
            current = sandbox.read_file(path)
        except Exception:
            current = ""
        return f"{current or ''}{content}"
    if tool != "str_replace":
        return None
    try:
        current = sandbox.read_file(path)
    except Exception:
        return None
    if not isinstance(current, str):
        return None
    old = str(args.get("old_str") or "")
    if old not in current:
        return None
    new = str(args.get("new_str") or "")
    return current.replace(old, new) if args.get("replace_all") else current.replace(old, new, 1)


def _ppt_master_svg_targets(
    tool: str,
    args: dict[str, Any],
    workflow: SkillWorkflowState,
) -> list[str]:
    project = str(workflow.get("project_path") or "").rstrip("/")
    if not project:
        return []
    if tool in {"write_file", "str_replace"}:
        action = _action_for_tool(tool, args)
        normalized = posixpath.normpath(action)
        if normalized.startswith(f"{project}/svg_output/") and normalized.casefold().endswith(".svg"):
            return [normalized]
        return []
    if tool != "bash":
        return []
    command = str(args.get("command") or "")
    pattern = re.compile(rf"{re.escape(project)}/svg_output/[A-Za-z0-9_.-]+\.svg")
    return list(dict.fromkeys(posixpath.normpath(value) for value in pattern.findall(command)))


def _ppt_master_notes_authoring_call(call: dict[str, Any], workflow: SkillWorkflowState) -> bool:
    project = str(workflow.get("project_path") or "").rstrip("/")
    if not project:
        return False
    tool = _tool_call_name(call)
    action = _action_for_tool(tool, _tool_call_args(call))
    notes_root = f"{project}/notes/"
    if tool in {"write_file", "str_replace"}:
        return posixpath.normpath(action).startswith(notes_root)
    return tool == "bash" and (notes_root in action or "total_md_split.py" in action)


def _ppt_master_authority_closure_pending(
    calls: list[dict[str, Any]],
    workflow: SkillWorkflowState,
    runtime: Runtime,
    summary_text: object = None,
) -> list[str]:
    """Return missing official ppt-master references for the attempted action.

    This is intentionally scoped to the vendored ppt-master integration. The
    Skill still owns the reference names and branch semantics; this adapter
    makes the already-documented Executor/behavior load set durable across
    DeerFlow model/tool nodes, runs, and compaction.
    """

    skill_path = str(workflow.get("skill_path") or "")
    authority = str(workflow.get("selected_authority") or "")
    if not skill_path.endswith("/ppt-master/SKILL.md") or not authority.endswith("/workflows/generate-pptx.md"):
        return []
    if _authority_context_changed(workflow, runtime, summary_text):
        loaded: set[str] = set()
    else:
        loaded = {posixpath.normpath(value) for value in workflow.get("loaded_authority_documents") or []}

    skill_root = posixpath.dirname(skill_path)
    requirements = workflow.get("required_capabilities") or {}
    required: list[str] = []

    def extend(relative_paths: tuple[str, ...] | list[str]) -> None:
        for relative in relative_paths:
            path = relative if relative.startswith("/") else posixpath.normpath(f"{skill_root}/{relative}")
            if path not in required:
                required.append(path)

    contract_names = {
        name
        for call in calls
        if (name := _ppt_master_contract_authoring_name(call, workflow))
    }
    if requirements.get("template") and "design_spec.md" in contract_names:
        extend(("references/strategist-template.md", "templates/design_spec_reference.md"))
    if requirements.get("template") and "spec_lock.md" in contract_names:
        extend(("references/strategist-template.md", "templates/spec_lock_reference.md"))

    if any(_ppt_master_svg_authoring_call(call, workflow) for call in calls):
        extend(
            (
                "references/executor-base.md",
                "references/shared-standards-core.md",
                "references/svg-effects.md",
                "references/native-shape-authoring.md",
                "references/semantic-svg.md",
            )
        )
        extend(list(workflow.get("ppt_master_dynamic_authority_documents") or []))
        if requirements.get("images") or requirements.get("ai_images"):
            extend(
                (
                    "references/executor-image.md",
                    "references/image-layout-spec.md",
                    "references/image-layout-patterns.md",
                    "references/svg-image-embedding.md",
                )
            )
        if requirements.get("charts") or requirements.get("native_charts"):
            extend(("references/executor-chart.md", "references/native-data-interface.md"))
        if requirements.get("structured_pptx"):
            extend(("references/executor-structured.md", "references/pptx-structure-interface.md"))
        if requirements.get("web_images"):
            extend(("references/executor-web-image.md",))

    if requirements.get("speaker_notes") and any(_ppt_master_notes_authoring_call(call, workflow) for call in calls):
        extend(("references/executor-notes.md",))

    actions = [_action_for_tool(_tool_call_name(call), _tool_call_args(call)) for call in calls]
    if requirements.get("object_animations") and any(_PPT_MASTER_ANIMATION_OWNER_RE.search(action) for action in actions):
        extend(
            (
                "workflows/stages/customize-animations.md",
                "references/animations.md",
                "scripts/docs/pptx-animations.md",
                "scripts/docs/pptx-transitions.md",
                "scripts/docs/svg-pipeline.md",
            )
        )
    if requirements.get("narration_audio") and any(_PPT_MASTER_AUDIO_OWNER_RE.search(action) for action in actions):
        extend(("workflows/stages/generate-audio.md",))

    return [path for path in required if path not in loaded]


def _render_workflow_state(workflow: SkillWorkflowState | None) -> str:
    """Render a compact checkpoint for the model's durable context."""

    if not workflow:
        return ""
    lines = ["## Active Skill workflow checkpoint"]
    task_text = str(workflow.get("task_text") or "").strip()
    if task_text:
        lines.append(f"- original_task: {task_text[:1200]}")
    if workflow.get("template_selection_confirmed"):
        lines.append("- template_selection_confirmed: true")
    if workflow.get("confirmation_wait_timed_out"):
        lines.append("- confirmation_wait_timed_out: true (recheck the owner receipt once; preserve it and use the documented fallback if unresolved)")
    if _failed_gate_snapshot_stale(workflow):
        lines.append("- gate_failure_snapshot_stale: true (rerun gate_owner before using its prior report or making another repair)")
    for label, key in (
        ("skill", "skill_name"),
        ("status", "status"),
        ("authority", "selected_authority"),
        ("profile", "selected_profile"),
        ("confirmation_surface", "confirmation_surface"),
        ("project", "project_path"),
        ("current_stage", "current_stage"),
        ("gate_status", "gate_status"),
        ("gate_detail", "gate_detail"),
        ("gate_owner", "gate_owner_action"),
        ("last_guard", "guard_detail"),
        ("resume_pointer", "resume_pointer"),
        ("completion_contract", "completion_contract"),
    ):
        value = workflow.get(key)
        if value:
            lines.append(f"- {label}: {value}")
    required = sorted(name for name, enabled in (workflow.get("required_capabilities") or {}).items() if enabled)
    if required:
        lines.append("- required_capabilities: " + ", ".join(required))
    delivered = workflow.get("delivered_artifacts") or []
    if delivered:
        lines.append("- delivered_artifacts: " + ", ".join(delivered[-8:]))
    resume_documents = workflow.get("resume_documents") or []
    if resume_documents:
        lines.append("- resume_documents: " + ", ".join(resume_documents))
    managed_artifacts = workflow.get("managed_artifacts") or []
    if managed_artifacts:
        lines.append("- tool_or_user_managed_artifacts: " + ", ".join(managed_artifacts))
    artifact_dependencies = workflow.get("artifact_authority_dependencies") or {}
    if artifact_dependencies:
        lines.append(
            "- artifact_authority_dependencies: "
            + ", ".join(f"{artifact} <- {reference}" for artifact, reference in sorted(artifact_dependencies.items()))
        )
    ppt_master_dynamic = workflow.get("ppt_master_dynamic_authority_documents") or []
    if ppt_master_dynamic:
        lines.append("- ppt_master_dynamic_authority_documents: " + ", ".join(ppt_master_dynamic))
    ppt_master_nonterminal = workflow.get("ppt_master_nonterminal_resources") or []
    if ppt_master_nonterminal:
        lines.append("- ppt_master_nonterminal_resources: " + ", ".join(ppt_master_nonterminal))
    if workflow.get("ppt_master_design_spec_refresh_required"):
        lines.append("- ppt_master_design_spec_refresh_required: true")
    evidence = workflow.get("evidence") or []
    if evidence:
        lines.append("- recent_evidence:")
        for entry in evidence[-12:]:
            lines.append(f"  - {entry.get('status', 'unknown')} {entry.get('tool', '?')}: {entry.get('action', '')[:500]}")
            detail = str(entry.get("detail") or "").strip()
            if entry.get("status") == "failed" and detail:
                lines.append(f"    failure_detail: {detail[:1200]}")
    return "\n".join(lines)


class SkillWorkflowMiddleware(AgentMiddleware[ThreadState]):
    """Persist tool-backed Skill progress and reject unsupported completion claims."""

    state_schema = ThreadState

    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.Lock()
        self._pending_recovery: BoundedDict[tuple[str, str], bool] = BoundedDict(1000)
        self._retry_counts: BoundedDict[tuple[str, str], int] = BoundedDict(1000)
        self._sequence_retry_counts: BoundedDict[tuple[str, str, str], int] = BoundedDict(2000)

    @staticmethod
    def _key(runtime: Runtime) -> tuple[str, str]:
        context = getattr(runtime, "context", None)
        if isinstance(context, dict):
            return (
                str(context.get("thread_id") or "unknown-thread"),
                str(context.get("run_id") or context.get("run_attempt_id") or id(runtime)),
            )
        return "unknown-thread", str(id(runtime))

    def _collect_update(self, state: ThreadState, runtime: Runtime) -> SkillWorkflowState | None:
        workflow = state.get("skill_workflow")
        if not isinstance(workflow, dict):
            return None
        messages = list(state.get("messages") or [])
        activation_message_id = workflow.get("activation_message_id")
        if activation_message_id:
            for index, message in enumerate(messages):
                if getattr(message, "id", None) == activation_message_id:
                    messages = messages[index + 1 :]
                    break

        pre_existing_ids = _runtime_pre_existing_message_ids(runtime)
        run_messages = [message for message in messages if not (_message_id(message) and _message_id(message) in pre_existing_ids)]

        calls: dict[str, tuple[str, dict[str, Any]]] = {}
        for message in run_messages:
            if not isinstance(message, AIMessage):
                continue
            for call in message.tool_calls or []:
                if not isinstance(call, dict):
                    continue
                call_id = _tool_call_id(call)
                if call_id:
                    calls[call_id] = (_tool_call_name(call), _tool_call_args(call))

        existing_ids = {str(entry.get("id")) for entry in workflow.get("evidence") or [] if entry.get("id")}
        receipts: list[SkillWorkflowEvidence] = []
        update: SkillWorkflowState = {
            "activation_id": str(workflow.get("activation_id") or ""),
        }
        run_id = _runtime_run_id(runtime)
        current_context_fingerprint = _authority_context_fingerprint(state.get("summary_text"))
        context_changed = _authority_context_changed(workflow, runtime, state.get("summary_text"))
        loaded_documents = list(workflow.get("loaded_authority_documents") or []) if not context_changed else []
        authority_reload_invalidated = False
        resume_documents = list(workflow.get("resume_documents") or [])
        managed_artifacts = list(workflow.get("managed_artifacts") or [])
        artifact_authority_dependencies = dict(workflow.get("artifact_authority_dependencies") or {})
        ppt_master_dynamic_authority_documents = list(workflow.get("ppt_master_dynamic_authority_documents") or [])
        requirements: dict[str, bool] = {}
        state_changed = False
        project_path = _valid_project_root(str(workflow.get("project_path") or ""))
        current_gate = str(workflow.get("gate_status") or "")
        current_resume_pointer = str(workflow.get("resume_pointer") or "")
        confirmation_surface_switch_allowed = bool(workflow.get("confirmation_surface_switch_allowed"))
        confirmation_wait_timed_out = bool(workflow.get("confirmation_wait_timed_out"))
        gate_owner_receipt_id = str(workflow.get("gate_owner_receipt_id") or "")
        gate_owner_tool = str(workflow.get("gate_owner_tool") or "")
        gate_owner_action = str(workflow.get("gate_owner_action") or "")
        gate_owner_stage = str(workflow.get("gate_owner_stage") or "")
        last_project_mutation_receipt_id = str(workflow.get("last_project_mutation_receipt_id") or "")
        failed_gate_attempt_mutation_receipt_id = str(workflow.get("failed_gate_attempt_mutation_receipt_id") or "")
        try:
            failed_gate_repeat_count = max(0, int(workflow.get("failed_gate_repeat_count") or 0))
        except (TypeError, ValueError):
            failed_gate_repeat_count = 0
        if current_gate == "failed":
            # Older checkpoints inferred owner identity from the resume pointer
            # and evidence.  Rebuild the explicit owner bundle before applying
            # any new receipts, and repair checkpoints whose visible stage or
            # detail was overwritten by an auxiliary tool call.
            owner_receipt = _failed_gate_owner_evidence(workflow)
            if owner_receipt is not None:
                gate_owner_receipt_id = str(owner_receipt.get("id") or "")
                gate_owner_tool = str(owner_receipt.get("tool") or "")
                gate_owner_action = str(owner_receipt.get("action") or "")
                gate_owner_stage = _stage_for(gate_owner_tool, gate_owner_action)
                owner_detail = str(owner_receipt.get("detail") or "")[:1200]
                owner_bundle = {
                    "status": "blocked",
                    "gate_status": "failed",
                    "current_stage": gate_owner_stage,
                    "gate_detail": owner_detail,
                    "gate_owner_receipt_id": gate_owner_receipt_id,
                    "gate_owner_tool": gate_owner_tool,
                    "gate_owner_action": gate_owner_action,
                    "gate_owner_stage": gate_owner_stage,
                    "resume_pointer": gate_owner_action,
                }
                if any(workflow.get(key) != value for key, value in owner_bundle.items()):
                    update.update(owner_bundle)
                    current_resume_pointer = gate_owner_action
                    state_changed = True
        if current_gate == "failed" and not _failed_gate_is_enforceable(workflow):
            current_gate = "passed"
            current_resume_pointer = ""
            update["status"] = "active"
            update["gate_status"] = current_gate
            update["gate_detail"] = ""
            update["gate_owner_receipt_id"] = ""
            update["gate_owner_tool"] = ""
            update["gate_owner_action"] = ""
            update["gate_owner_stage"] = ""
            update["guard_detail"] = ""
            update["resume_pointer"] = current_resume_pointer
            state_changed = True
        if (
            not workflow.get("template_selection_confirmed")
            and not confirmation_wait_timed_out
            and current_gate in {"managed-artifact-owner-required", "waiting-for-user"}
            and _confirmation_timeout_from_evidence(workflow)
        ):
            # Compatibility repair for checkpoints written before daemon
            # restarts preserved the owner's bounded-wait boundary.
            confirmation_surface_switch_allowed = True
            confirmation_wait_timed_out = True
            update["confirmation_surface_switch_allowed"] = True
            update["confirmation_wait_timed_out"] = True
            state_changed = True
        chat_fallback_allowed = current_gate not in {"failed", "managed-artifact-owner-required"} or (
            current_gate == "managed-artifact-owner-required"
            and confirmation_wait_timed_out
            and confirmation_surface_switch_allowed
        )
        if chat_fallback_allowed and not workflow.get("template_selection_chat_confirmed") and _chat_template_selection_confirmed(messages):
            update["template_selection_confirmed"] = True
            update["template_selection_chat_confirmed"] = True
            update["confirmation_surface"] = "chat"
            update["confirmation_surface_switch_allowed"] = False
            update["confirmation_wait_timed_out"] = False
            update["status"] = "active"
            update["gate_status"] = "passed"
            update["gate_detail"] = ""
            update["gate_owner_receipt_id"] = ""
            update["gate_owner_tool"] = ""
            update["gate_owner_action"] = ""
            update["gate_owner_stage"] = ""
            update["guard_detail"] = ""
            update["resume_pointer"] = ""
            current_gate = "passed"
            current_resume_pointer = ""
            confirmation_surface_switch_allowed = False
            confirmation_wait_timed_out = False
            state_changed = True
        for message in run_messages:
            if not isinstance(message, ToolMessage):
                continue
            call_id = str(message.tool_call_id or "")
            if not call_id or call_id in existing_ids or call_id not in calls:
                continue
            tool, args = calls[call_id]
            action = _action_for_tool(tool, args)
            succeeded = _result_succeeded(message, tool=tool, action=action)
            message_detail = _message_text(message)
            receipt_detail = _bounded_receipt_detail(message_detail)
            wait_timed_out = not succeeded and _confirm_ui_wait_timed_out(
                tool,
                action,
                message_detail,
            )
            receipts.append(
                {
                    "id": call_id,
                    "tool": tool,
                    "status": "passed" if succeeded else ("waiting" if wait_timed_out else "failed"),
                    "action": action,
                    "detail": receipt_detail,
                }
            )
            receipt_stage = _stage_for(tool, action)

            if not succeeded:
                detail = message_detail
                if wait_timed_out:
                    # The owning server is still live after its bounded poll
                    # expires.  Do not turn an external-user wait into a sticky
                    # failed-script gate or consume sequence-guard retries.
                    current_gate = "waiting-for-user"
                    current_resume_pointer = current_resume_pointer or action
                    gate_owner_receipt_id = ""
                    gate_owner_tool = ""
                    gate_owner_action = ""
                    gate_owner_stage = ""
                    update["current_stage"] = receipt_stage
                    update["status"] = "waiting"
                    update["gate_status"] = current_gate
                    update["gate_detail"] = receipt_detail
                    update["guard_detail"] = ""
                    update["resume_pointer"] = current_resume_pointer
                    update["gate_owner_receipt_id"] = ""
                    update["gate_owner_tool"] = ""
                    update["gate_owner_action"] = ""
                    update["gate_owner_stage"] = ""
                    update["failed_gate_repeat_count"] = 0
                    update["confirmation_surface_switch_allowed"] = True
                    update["confirmation_wait_timed_out"] = True
                    confirmation_surface_switch_allowed = True
                    confirmation_wait_timed_out = True
                    continue
                blocking_gate_active = current_gate in {
                    "failed",
                    "managed-artifact-owner-required",
                }
                active_gate_view: SkillWorkflowState = {
                    **workflow,
                    "gate_status": current_gate,
                    "resume_pointer": current_resume_pointer,
                    "gate_owner_receipt_id": gate_owner_receipt_id,
                    "gate_owner_tool": gate_owner_tool,
                    "gate_owner_action": gate_owner_action,
                    "gate_owner_stage": gate_owner_stage,
                }
                same_active_owner = current_gate == "failed" and _same_owning_recovery_action(
                    tool,
                    action,
                    active_gate_view,
                )
                managed_owner_attempt = (
                    current_gate == "managed-artifact-owner-required"
                    and tool == "bash"
                    and bool(_CONFIRM_UI_SERVER_RE.search(action))
                )
                managed_owner_failure = (
                    managed_owner_attempt
                    and ("--wait-only" in action or "--complete-template-phase" in action)
                )
                new_enforced_failure = not blocking_gate_active and _failure_requires_convergence(tool, action, workflow)
                if blocking_gate_active and not (same_active_owner or managed_owner_attempt):
                    # Diagnostic and repair receipts remain useful evidence, but
                    # they cannot replace the active gate owner's stage, detail,
                    # or resume pointer.
                    continue
                if managed_owner_failure:
                    # A fabricated managed receipt first points recovery at the
                    # owning confirmation command.  If that official command
                    # then fails on a real project precondition, it becomes the
                    # enforceable owner of the recovery transaction.  Keeping
                    # the older managed-artifact gate here prevents the exact
                    # project-scoped repair named by the owner from running.
                    current_gate = "failed"
                    current_resume_pointer = action or tool
                    new_enforced_failure = True
                elif blocking_gate_active:
                    pass
                elif _MANAGED_ARTIFACT_ERROR_MARKER in detail:
                    current_gate = "managed-artifact-owner-required"
                    current_resume_pointer = action or tool
                elif new_enforced_failure:
                    current_gate = "failed"
                    current_resume_pointer = action or tool
                else:
                    continue
                update["current_stage"] = receipt_stage
                update["status"] = "blocked"
                update["gate_status"] = current_gate
                update["gate_detail"] = receipt_detail
                update["guard_detail"] = ""
                update["resume_pointer"] = current_resume_pointer
                if current_gate == "failed" and (new_enforced_failure or same_active_owner):
                    gate_owner_receipt_id = call_id
                    gate_owner_tool = tool
                    gate_owner_action = action or tool
                    gate_owner_stage = receipt_stage
                    current_resume_pointer = gate_owner_action
                    update["gate_owner_receipt_id"] = gate_owner_receipt_id
                    update["gate_owner_tool"] = gate_owner_tool
                    update["gate_owner_action"] = gate_owner_action
                    update["gate_owner_stage"] = gate_owner_stage
                    update["resume_pointer"] = current_resume_pointer
                    # A failed owning Skill script is a semantic boundary, not
                    # just another tool error. Reload checkpointed authority
                    # and project contracts before selecting a repair. A
                    # repeated failure without a project mutation does not
                    # invalidate that completed reload again.
                    unchanged_owner_failure = same_active_owner and failed_gate_attempt_mutation_receipt_id == last_project_mutation_receipt_id
                    if unchanged_owner_failure:
                        failed_gate_repeat_count += 1
                    else:
                        failed_gate_attempt_mutation_receipt_id = last_project_mutation_receipt_id
                        failed_gate_repeat_count = 1
                        loaded_documents = []
                        authority_reload_invalidated = True
                    update["failed_gate_attempt_mutation_receipt_id"] = failed_gate_attempt_mutation_receipt_id
                    update["failed_gate_repeat_count"] = failed_gate_repeat_count
                continue

            resolved_project, project_source = _project_root_from_successful_receipt(
                tool,
                action,
                _message_text(message),
                project_path,
            )
            if resolved_project and resolved_project != project_path:
                project_path = resolved_project
                update["project_path"] = resolved_project
                if project_source:
                    update["project_path_source"] = project_source

            if _project_file_mutation(tool, action, project_path):
                last_project_mutation_receipt_id = call_id
                update["last_project_mutation_receipt_id"] = call_id

            blocking_gate = current_gate in {"failed", "managed-artifact-owner-required"}
            active_gate_view: SkillWorkflowState = {
                **workflow,
                "gate_status": current_gate,
                "resume_pointer": current_resume_pointer,
                "gate_owner_receipt_id": gate_owner_receipt_id,
                "gate_owner_tool": gate_owner_tool,
                "gate_owner_action": gate_owner_action,
                "gate_owner_stage": gate_owner_stage,
            }
            recovered = (
                (current_gate == "failed" and _same_owning_recovery_action(tool, action, active_gate_view))
                or (current_gate == "failed" and _successful_delivery_recovery(tool, action, active_gate_view))
                or (current_gate == "managed-artifact-owner-required" and tool == "bash" and bool(_CONFIRM_UI_SERVER_RE.search(action)) and "--wait-only" in action)
            )
            if recovered:
                update["current_stage"] = receipt_stage
                current_gate = "passed"
                current_resume_pointer = ""
                gate_owner_receipt_id = ""
                gate_owner_tool = ""
                gate_owner_action = ""
                gate_owner_stage = ""
                update["status"] = "active"
                update["gate_status"] = current_gate
                update["gate_detail"] = ""
                update["gate_owner_receipt_id"] = ""
                update["gate_owner_tool"] = ""
                update["gate_owner_action"] = ""
                update["gate_owner_stage"] = ""
                update["guard_detail"] = ""
                update["resume_pointer"] = current_resume_pointer
                update["confirmation_surface_switch_allowed"] = False
                update["failed_gate_repeat_count"] = 0
                update["confirmation_wait_timed_out"] = False
                confirmation_surface_switch_allowed = False
                confirmation_wait_timed_out = False
            elif not blocking_gate:
                update["current_stage"] = receipt_stage
                if workflow.get("status") == "blocked" and current_gate in {
                    "route-selection-required",
                    "authority-reload-required",
                    "confirmation-surface-locked",
                }:
                    update["status"] = "active"
                current_gate = "passed"
                current_resume_pointer = ""
                update["gate_status"] = current_gate
                update["gate_detail"] = ""
                update["gate_owner_receipt_id"] = ""
                update["gate_owner_tool"] = ""
                update["gate_owner_action"] = ""
                update["gate_owner_stage"] = ""
                update["guard_detail"] = ""
                update["resume_pointer"] = current_resume_pointer

            if tool == "ask_clarification":
                if workflow.get("confirmation_surface") != "page" or workflow.get("confirmation_surface_switch_allowed"):
                    update["confirmation_surface"] = "chat"
                    update["confirmation_surface_switch_allowed"] = False
                    confirmation_surface_switch_allowed = False
                update["status"] = "waiting"
                if not blocking_gate:
                    update["gate_status"] = "waiting-for-user"
                    current_gate = "waiting-for-user"
            elif tool == "bash" and _CONFIRM_UI_SERVER_RE.search(action) and "--daemon" in action:
                update["confirmation_surface"] = "page"
                update["confirmation_surface_switch_allowed"] = confirmation_surface_switch_allowed
                confirmation_url = _CONFIRM_UI_URL_RE.search(_message_text(message))
                if confirmation_url:
                    update["confirmation_url"] = confirmation_url.group(0).rstrip(".,;)")
                update["confirmation_ui_active"] = True
                update["confirmation_wait_timed_out"] = confirmation_wait_timed_out
            elif tool == "bash" and _CONFIRM_UI_SERVER_RE.search(action) and "--wait-only" in action and "--wait-stage template" in action:
                update["template_selection_confirmed"] = True
                update["confirmation_surface"] = "page"
                update["confirmation_wait_timed_out"] = False
                update["confirmation_surface_switch_allowed"] = False
                confirmation_surface_switch_allowed = False
                confirmation_wait_timed_out = False
            elif tool == "bash" and _CONFIRM_UI_SERVER_RE.search(action) and "--reset-template-phase" in action:
                update["template_selection_confirmed"] = False
                update["template_selection_chat_confirmed"] = False
                update["confirmation_wait_timed_out"] = False
                update["confirmation_surface_switch_allowed"] = False
                confirmation_surface_switch_allowed = False
                confirmation_wait_timed_out = False
            elif tool == "bash" and _CONFIRM_UI_SERVER_RE.search(action) and "--shutdown" in action:
                update["confirmation_ui_active"] = False
            elif tool == "present_files" and workflow.get("completion_contract") == "presentation_delivery" and re.search(r"\.pptx?(?:\b|$)", action, re.IGNORECASE):
                update["status"] = "completed"

            normalized = posixpath.normpath(action)
            is_project_contract = _project_contract_path(normalized, project_path)
            if is_project_contract and tool in {"read_file", "write_file", "str_replace"}:
                if normalized not in loaded_documents:
                    loaded_documents.append(normalized)
                selected_authority = str(update.get("selected_authority") or workflow.get("selected_authority") or "") or None
                resume_documents = _resume_documents_after_read(
                    {**workflow, "resume_documents": resume_documents},
                    normalized,
                    selected_authority,
                )
                if posixpath.basename(normalized) == "design_spec.md":
                    if tool == "str_replace":
                        update["ppt_master_design_spec_refresh_required"] = True
                    else:
                        contract_content = str(args.get("content") or "") if tool == "write_file" else _message_text(message)
                        requirements.update(_requirements_from_design_spec(contract_content))
                        update["ppt_master_nonterminal_resources"] = _ppt_master_nonterminal_resources(contract_content)
                        update["ppt_master_design_spec_refresh_required"] = False
                elif posixpath.basename(normalized) == "spec_lock.md" and tool != "str_replace":
                    contract_content = str(args.get("content") or "") if tool == "write_file" else _message_text(message)
                    requirements.update(_ppt_master_requirements_from_spec_lock(contract_content))
                    ppt_master_dynamic_authority_documents = _ppt_master_dynamic_authority_documents(
                        contract_content,
                        str(workflow.get("skill_path") or ""),
                    )
                    update["ppt_master_dynamic_authority_documents"] = ppt_master_dynamic_authority_documents

            if tool == "read_file":
                workflow_match = _TOP_LEVEL_WORKFLOW_RE.search(action)
                profile_match = _PROFILE_RE.search(action)
                unauthorized_quick_profile = bool(profile_match and action.endswith(_QUICK_PROFILE_SUFFIX) and not _quick_profile_explicitly_requested(workflow))
                if profile_match and not unauthorized_quick_profile:
                    update["selected_profile"] = action
                elif workflow_match and workflow_match.group(1) != "routing.md":
                    update["selected_authority"] = action
                normalized = posixpath.normpath(action)
                if (_SKILL_DOCUMENT_RE.match(action) and not unauthorized_quick_profile) or is_project_contract:
                    if normalized not in loaded_documents:
                        loaded_documents.append(normalized)
                    selected_authority = str(update.get("selected_authority") or workflow.get("selected_authority") or "") or None
                    resume_documents = _resume_documents_after_read(
                        {**workflow, "resume_documents": resume_documents},
                        normalized,
                        selected_authority,
                    )
                    for managed in _managed_artifacts_from_authority(_message_text(message)):
                        if managed not in managed_artifacts:
                            managed_artifacts.append(managed)
                    discovered_dependencies = _artifact_authority_dependencies_from_authority(
                        _message_text(message),
                        str(workflow.get("skill_path") or ""),
                    )
                    for artifact, reference in discovered_dependencies.items():
                        if artifact_authority_dependencies.get(artifact) != reference:
                            artifact_authority_dependencies[artifact] = reference
                            state_changed = True
                if posixpath.basename(action) == "design_spec.md" and not is_project_contract:
                    requirements.update(_requirements_from_design_spec(_message_text(message)))

        if loaded_documents:
            update["loaded_authority_run_id"] = run_id
            update["loaded_authority_documents"] = loaded_documents
            update["loaded_authority_context_fingerprint"] = current_context_fingerprint
        elif authority_reload_invalidated:
            update["loaded_authority_run_id"] = ""
            update["loaded_authority_documents"] = []
            update["loaded_authority_context_fingerprint"] = ""
        if resume_documents:
            update["resume_documents"] = resume_documents
        if managed_artifacts:
            update["managed_artifacts"] = managed_artifacts
        if artifact_authority_dependencies:
            update["artifact_authority_dependencies"] = artifact_authority_dependencies
        if not receipts and not requirements and not state_changed:
            return None
        if receipts:
            update["evidence"] = receipts
        if requirements:
            update["required_capabilities"] = requirements
        return update

    @override
    def before_model(self, state: ThreadState, runtime: Runtime) -> dict | None:
        update = self._collect_update(state, runtime)
        return {"skill_workflow": update} if update else None

    @override
    async def abefore_model(self, state: ThreadState, runtime: Runtime) -> dict | None:
        return self.before_model(state, runtime)

    def _sequence_guard_update(
        self,
        last: AIMessage,
        workflow: SkillWorkflowState,
        runtime: Runtime,
        *,
        gate_status: str,
        resume_pointer: str,
        reason: str,
        status: str | None = None,
    ) -> dict[str, Any]:
        workflow_update: SkillWorkflowState = {
            "activation_id": workflow.get("activation_id", ""),
            "gate_status": gate_status,
            "resume_pointer": resume_pointer,
        }
        preserves_failed_owner = str(workflow.get("gate_status") or "") == "failed" and gate_status == "failed" and resume_pointer == str(workflow.get("resume_pointer") or "")
        if preserves_failed_owner:
            workflow_update["gate_detail"] = str(workflow.get("gate_detail") or "")
            workflow_update["current_stage"] = str(workflow.get("gate_owner_stage") or workflow.get("current_stage") or "")
            workflow_update["guard_detail"] = reason
        else:
            workflow_update["gate_detail"] = reason
            workflow_update["guard_detail"] = ""
        if status:
            workflow_update["status"] = status
        thread_id, run_id = self._key(runtime)
        # Sequence attempts are consecutive only inside one durable recovery
        # boundary.  A project mutation or a fresh owning failure receipt is
        # real progress even when the visible Gate/reason text is unchanged;
        # carrying earlier attempts across that boundary can terminate a valid
        # repair after its very first follow-up decision.
        recovery_boundary = "|".join(
            str(workflow.get(key) or "")
            for key in (
                "gate_owner_receipt_id",
                "last_project_mutation_receipt_id",
                "failed_gate_attempt_mutation_receipt_id",
                "failed_gate_repeat_count",
            )
        )
        retry_key = (
            thread_id,
            run_id,
            f"{gate_status}|{resume_pointer}|{reason}|boundary={recovery_boundary}",
        )
        with self._lock:
            attempt = self._sequence_retry_counts.get(retry_key, 0) + 1
            self._sequence_retry_counts[retry_key] = attempt
        if attempt >= _MAX_SEQUENCE_GUARD_ATTEMPTS:
            additional_kwargs = dict(last.additional_kwargs or {})
            additional_kwargs.pop("tool_calls", None)
            additional_kwargs.pop("function_call", None)
            additional_kwargs.update(
                {
                    "deerflow_error_fallback": True,
                    "error_reason": "Skill workflow sequence did not converge",
                }
            )
            fallback = last.model_copy(
                update={
                    "content": (f"Skill 工作流连续 3 次未遵循当前恢复契约，已停止自动续跑以避免无限循环。阻塞原因：{reason}；恢复位置：{resume_pointer or 'unknown'}。"),
                    "tool_calls": [],
                    "invalid_tool_calls": [],
                    "additional_kwargs": additional_kwargs,
                }
            )
            return {
                "messages": [fallback],
                "skill_workflow": workflow_update,
            }
        message_updates = [RemoveMessage(id=last.id)] if last.id else []
        return {
            "messages": message_updates,
            "jump_to": "model",
            "skill_workflow": workflow_update,
        }

    def _apply_sequence_guard(self, state: ThreadState, runtime: Runtime) -> dict[str, Any] | None:
        workflow = state.get("skill_workflow")
        if not isinstance(workflow, dict):
            return None
        messages = list(state.get("messages") or [])
        if not messages or not isinstance(messages[-1], AIMessage):
            return None
        last = messages[-1]
        if _is_terminal_safety_stop(last, runtime):
            return None

        if _route_selection_pending(workflow):
            calls = [call for call in last.tool_calls or [] if isinstance(call, dict)]
            skill_root = posixpath.dirname(str(workflow.get("skill_path") or ""))
            integrity_path = f"{skill_root}/scripts/attribution_guard.py"
            routing_path = f"{skill_root}/workflows/routing.md"
            integrity_calls = [call for call in calls if _ppt_master_attribution_guard_call(call, workflow)]

            if not _ppt_master_attribution_guard_succeeded(workflow):
                integrity_call = integrity_calls[0] if integrity_calls else {
                    "name": "bash",
                    "args": {
                        "command": f"cd {skill_root} && python3 scripts/attribution_guard.py",
                        "description": "Run the ppt-master attribution integrity gate",
                    },
                }
                return _route_prerequisite_update(
                    last,
                    workflow,
                    runtime,
                    tool_call=integrity_call,
                    resume_pointer=integrity_path,
                    detail="Running ppt-master's mandatory attribution integrity gate before route selection.",
                )

            route_reads = [
                (call, _route_selection_document(call, workflow))
                for call in calls
                if _route_selection_document(call, workflow)
            ]
            if not _routing_authority_loaded(workflow, runtime):
                routing_calls = [call for call, name in route_reads if name == "routing.md"]
                routing_call = routing_calls[0] if routing_calls else {
                    "name": "read_file",
                    "args": {"path": routing_path},
                }
                return _route_prerequisite_update(
                    last,
                    workflow,
                    runtime,
                    tool_call=routing_call,
                    resume_pointer=routing_path,
                    detail="Reading ppt-master's routing authority after the integrity gate passed.",
                )

            selected_route_calls = [call for call, name in route_reads if name != "routing.md"]
            selected_route_names = {
                name for _, name in route_reads if name != "routing.md"
            }
            if len(selected_route_calls) == 1 and len(selected_route_names) == 1:
                selected_call = selected_route_calls[0]
                selected_path = posixpath.normpath(str(_tool_call_args(selected_call).get("path") or ""))
                route_only = len(calls) == 1 and calls[0] is selected_call
                if route_only:
                    return None
                return _route_prerequisite_update(
                    last,
                    workflow,
                    runtime,
                    tool_call=selected_call,
                    resume_pointer=selected_path,
                    detail="Loading the single model-selected top-level ppt-master route authority.",
                )

            return self._sequence_guard_update(
                last,
                workflow,
                runtime,
                gate_status="route-selection-required",
                resume_pointer=routing_path,
                reason="routing.md 已读取，但尚未选择并读取唯一的官方顶层路线文档",
                status="blocked",
            )
            # Route selection is the prerequisite for deciding which persisted
            # stage/design documents are authoritative.  Do not apply the old
            # run's authority-reload barrier to the same response or the two
            # guards can alternately delete each other's valid read calls.
            return None

        for call in last.tool_calls or []:
            if not isinstance(call, dict):
                continue
            selected_route = _top_level_route_switch_violation(call, workflow)
            if selected_route:
                return self._sequence_guard_update(
                    last,
                    workflow,
                    runtime,
                    gate_status="top-level-route-locked",
                    resume_pointer=selected_route,
                    reason=(
                        "The Skill has already selected one official top-level route. Supporting documents may refine "
                        "that route, but another top-level route cannot be loaded without a new explicit Skill activation"
                    ),
                    status="blocked",
                )

        pending_documents = _authority_reload_pending(
            workflow,
            runtime,
            state.get("summary_text"),
        )
        if pending_documents:
            calls = [call for call in last.tool_calls or [] if isinstance(call, dict)]
            allowed = bool(calls) and len(calls) == len(last.tool_calls or []) and all(_authority_reload_call_allowed(call, pending_documents) for call in calls)
            if not allowed:
                return _authority_reload_update(
                    last,
                    workflow,
                    runtime,
                    pending_documents,
                    state.get("summary_text"),
                )

        calls = [call for call in last.tool_calls or [] if isinstance(call, dict)]
        ppt_master_svg_entry = any(_ppt_master_svg_authoring_call(call, workflow) for call in calls)
        if ppt_master_svg_entry and workflow.get("ppt_master_design_spec_refresh_required"):
            project = str(workflow.get("project_path") or "").rstrip("/")
            if project:
                return _authority_reload_update(
                    last,
                    workflow,
                    runtime,
                    [f"{project}/design_spec.md"],
                    state.get("summary_text"),
                )
        nonterminal_resources = list(workflow.get("ppt_master_nonterminal_resources") or [])
        if ppt_master_svg_entry and nonterminal_resources:
            project = str(workflow.get("project_path") or "").rstrip("/")
            return self._sequence_guard_update(
                last,
                workflow,
                runtime,
                gate_status="ppt-master-acquisition-incomplete",
                resume_pointer=f"{project}/design_spec.md" if project else "design_spec.md Image Resource List",
                reason=(
                    "ppt-master Step 5 is not closed: the following ai/web/slice resources still have Pending or "
                    f"Failed status in the persisted Design Spec: {', '.join(nonterminal_resources)}. Complete the "
                    "owning acquisition, update the existing rows to an official terminal status, reread the complete "
                    "Design Spec, and only then enter Executor"
                ),
                status="blocked",
            )
        artifact_dependencies = _artifact_authority_reload_pending(
            calls,
            workflow,
            runtime,
            state.get("summary_text"),
        )
        if artifact_dependencies:
            return _authority_reload_update(
                last,
                workflow,
                runtime,
                artifact_dependencies,
                state.get("summary_text"),
            )

        ppt_master_closure = _ppt_master_authority_closure_pending(
            calls,
            workflow,
            runtime,
            state.get("summary_text"),
        )
        if ppt_master_closure:
            return _authority_reload_update(
                last,
                workflow,
                runtime,
                ppt_master_closure,
                state.get("summary_text"),
            )

        untruncated_owner = _owner_output_untruncation_update(last, workflow)
        if untruncated_owner:
            return untruncated_owner

        for call in last.tool_calls or []:
            if not isinstance(call, dict):
                continue
            action = _action_for_tool(_tool_call_name(call), _tool_call_args(call))
            if (
                workflow.get("confirmation_surface") == "page"
                and workflow.get("confirmation_ui_active")
                and not workflow.get("confirmation_surface_switch_allowed")
                and _CONFIRM_UI_SERVER_RE.search(action)
                and "--shutdown" in action
                and not _confirmation_surface_switch_requested(messages)
            ):
                return self._sequence_guard_update(
                    last,
                    workflow,
                    runtime,
                    gate_status="confirmation-surface-locked",
                    resume_pointer="continue with the checkpointed page confirmation surface",
                    reason=(
                        "The official confirmation page is still the selected user/tool owner. Keep it running and "
                        "use its documented wait-only command; do not shut it down or switch to chat unless the "
                        "user explicitly requests that surface change or the bounded owner wait actually times out"
                    ),
                    status="blocked",
                )
            if workflow.get("confirmation_wait_timed_out") and _CONFIRM_UI_SERVER_RE.search(action) and "--reset-template-phase" in action and not _confirmation_reset_requested(messages):
                return self._sequence_guard_update(
                    last,
                    workflow,
                    runtime,
                    gate_status="waiting-for-user",
                    resume_pointer=str(workflow.get("resume_pointer") or "recheck the active confirmation receipt"),
                    reason=(
                        "The confirmation wait exhausted its bounded user-wait budget. Preserve and recheck the "
                        "owner-authored receipt once, then follow the selected Skill's documented fallback if it is "
                        "still unresolved. Do not reset a receipt unless the user explicitly requested reselection"
                    ),
                    status="waiting",
                )
            quick_profile = _quick_generate_violation(call, workflow)
            if quick_profile:
                explicitly_requested = _quick_profile_explicitly_requested(workflow)
                reason = (
                    "Quick Generate was explicitly requested, but its official profile has not been read and checkpointed for this run"
                    if explicitly_requested
                    else "Quick Generate requires explicit user intent; continue the default Generate pipeline"
                )
                return self._sequence_guard_update(
                    last,
                    workflow,
                    runtime,
                    gate_status="profile-selection-required",
                    resume_pointer=quick_profile if explicitly_requested else str(workflow.get("selected_authority") or ""),
                    reason=reason,
                    status="blocked",
                )
            receipt = _template_install_violation(call, workflow)
            if receipt:
                return self._sequence_guard_update(
                    last,
                    workflow,
                    runtime,
                    gate_status="managed-artifact-owner-required",
                    resume_pointer=receipt,
                    reason="模型试图绕过官方确认界面的托管产物边界",
                    status="blocked",
                )

        gate_status = str(workflow.get("gate_status") or "")
        if (
            workflow.get("status") == "blocked"
            and gate_status
            in {
                "failed",
                "managed-artifact-owner-required",
            }
            and _failed_gate_is_enforceable(workflow)
        ):
            calls = [call for call in last.tool_calls or [] if isinstance(call, dict)]
            if _failed_gate_snapshot_stale(workflow):
                owner_retry_only = (
                    bool(calls)
                    and len(calls) == len(last.tool_calls or [])
                    and all(
                        _same_owning_recovery_action(
                            _tool_call_name(call),
                            _action_for_tool(_tool_call_name(call), _tool_call_args(call)),
                            workflow,
                        )
                        for call in calls
                    )
                )
                consolidated_quality_repair = _ppt_master_consolidated_quality_repair_calls_allowed(
                    calls,
                    workflow,
                )
                if not owner_retry_only and not consolidated_quality_repair:
                    replay_action = _safe_replayable_owner_validation_action(workflow)
                    if replay_action:
                        return _owner_validation_replay_update(
                            last,
                            workflow,
                            runtime,
                            replay_action,
                        )
                    return self._sequence_guard_update(
                        last,
                        workflow,
                        runtime,
                        gate_status=gate_status,
                        resume_pointer=str(workflow.get("resume_pointer") or ""),
                        reason=(
                            "The active project changed after the failed owning receipt, so its failure detail and "
                            "generated reports are stale. Rerun the exact owning action now before reading the old "
                            "report, making another repair, or advancing the workflow"
                        ),
                        status="blocked",
                    )
            unchanged_owner_retry = any(
                _tool_call_name(call) == "bash"
                and _same_owning_recovery_action(
                    "bash",
                    _action_for_tool("bash", _tool_call_args(call)),
                    workflow,
                )
                and _unchanged_failed_owner_retry(workflow)
                for call in calls
            )
            if unchanged_owner_retry:
                return self._sequence_guard_update(
                    last,
                    workflow,
                    runtime,
                    gate_status=gate_status,
                    resume_pointer=str(workflow.get("resume_pointer") or ""),
                    reason=("The same owning command already failed again without a durable project mutation; repair the active project or report the exact blocker before retrying it"),
                    status="blocked",
                )
            if not calls or not all(_failed_gate_call_allowed(call, workflow) for call in calls):
                return self._sequence_guard_update(
                    last,
                    workflow,
                    runtime,
                    gate_status=gate_status,
                    resume_pointer=str(workflow.get("resume_pointer") or ""),
                    reason="模型未执行失败回执对应的诊断、项目内修复或拥有者重试动作",
                    status="blocked",
                )

        asks_in_chat = any(_tool_call_name(call) == "ask_clarification" for call in last.tool_calls or [] if isinstance(call, dict))
        if workflow.get("confirmation_surface") == "page" and not workflow.get("confirmation_surface_switch_allowed") and (asks_in_chat or _page_confirmation_fallback_violation(last, workflow)):
            return self._sequence_guard_update(
                last,
                workflow,
                runtime,
                gate_status="confirmation-surface-locked",
                resume_pointer="continue with the checkpointed page confirmation surface",
                reason="模型试图切换已锁定的官方确认界面",
            )
        return None

    def _apply_completion_guard(self, state: ThreadState, runtime: Runtime) -> dict[str, Any] | None:
        workflow = state.get("skill_workflow")
        if not isinstance(workflow, dict):
            return None
        if workflow.get("completion_contract") != "presentation_delivery":
            return None
        if workflow.get("status") in {"completed", "waiting"}:
            return None
        messages = list(state.get("messages") or [])
        if not messages or not isinstance(messages[-1], AIMessage):
            return None
        last = messages[-1]
        if _is_terminal_safety_stop(last, runtime):
            return None
        if last.tool_calls or getattr(last, "invalid_tool_calls", None):
            return None
        content = last.content if isinstance(last.content, str) else str(last.content)
        if _WAITING_RE.search(content) or not _COMPLETION_CLAIM_RE.search(content):
            return None

        key = self._key(runtime)
        with self._lock:
            count = self._retry_counts.get(key, 0)
            if count == 0:
                self._retry_counts[key] = 1
                self._pending_recovery[key] = True
        if count == 0:
            return {"jump_to": "model"}

        missing = sorted(name for name, required in (workflow.get("required_capabilities") or {}).items() if required)
        content = (
            "Skill 工作流尚未通过最终交付验收，不能标记为已完成。"
            f"当前阶段：{workflow.get('current_stage') or 'unknown'}；"
            f"恢复位置：{workflow.get('resume_pointer') or '按官方工作流检查下一未完成 Gate'}；"
            f"要求能力：{', '.join(missing) or '以 Skill 契约为准'}。"
        )
        return {
            "messages": [
                last.model_copy(
                    update={
                        "content": content,
                        "additional_kwargs": {
                            **(last.additional_kwargs or {}),
                            "deerflow_error_fallback": True,
                            "error_reason": "Skill workflow completion contract is not satisfied",
                        },
                    }
                )
            ],
            "skill_workflow": {
                "activation_id": workflow.get("activation_id", ""),
                "status": "blocked",
                "gate_status": "delivery-incomplete",
            },
        }

    def _augment_request(self, request: ModelRequest) -> ModelRequest:
        key = self._key(request.runtime)
        with self._lock:
            pending = self._pending_recovery.pop(key, None)
        reminders: list[HumanMessage] = []
        if pending:
            reminders.append(
                HumanMessage(
                    content=_RECOVERY_PROMPT,
                    name="skill_workflow_recovery",
                    additional_kwargs={"hide_from_ui": True},
                )
            )
        workflow = (request.state or {}).get("skill_workflow")
        if isinstance(workflow, dict):
            route_pending = _route_selection_pending(workflow)
            if route_pending:
                skill_root = posixpath.dirname(str(workflow.get("skill_path") or ""))
                reminders.append(
                    HumanMessage(
                        content=(
                            "<skill_route_selection>\n"
                            "The active Skill has no persisted top-level route. Before any build, repair, or "
                            "delivery action, read the exact routing document below, select the route using its "
                            "conditions, and then read that selected top-level workflow document so the host can "
                            "checkpoint it. Other read_file context calls may share that tool batch, but no "
                            "mutating action may run before the route is checkpointed. Stage documents are not "
                            "top-level route authority.\n"
                            f"- {skill_root}/workflows/routing.md\n"
                            "</skill_route_selection>"
                        ),
                        name="skill_route_selection",
                        additional_kwargs={"hide_from_ui": True},
                    )
                )
            else:
                authority = str(workflow.get("selected_authority") or "")
                if authority.endswith("/workflows/generate-pptx.md") and not _quick_profile_explicitly_requested(workflow):
                    reminders.append(
                        HumanMessage(
                            content=(
                                "<skill_profile_boundary>\n"
                                "The original user task does not explicitly request Quick Generate. Keep the "
                                "official default Generate pipeline and do not use --quick-generate, skip "
                                "Strategist/confirmation, or replace the lock-backed project.\n"
                                "</skill_profile_boundary>"
                            ),
                            name="skill_profile_boundary",
                            additional_kwargs={"hide_from_ui": True},
                        )
                    )
                pending_documents = _authority_reload_pending(
                    workflow,
                    request.runtime,
                    (request.state or {}).get("summary_text"),
                )
                if pending_documents:
                    surface = workflow.get("confirmation_surface") or "not-selected"
                    reminders.append(
                        HumanMessage(
                            content=(
                                "<skill_authority_reload>\n"
                                "This is a resumed Skill run. Before any other tool or conclusion, re-read every exact "
                                "authority document below with read_file; the host blocks other actions until this barrier closes.\n"
                                + "\n".join(f"- {path}" for path in pending_documents)
                                + f"\nThe checkpointed confirmation surface is {surface}; do not switch it unless its owning wait command failed. "
                                "Never fabricate a user- or tool-managed confirmation receipt.\n"
                                "</skill_authority_reload>"
                            ),
                            name="skill_authority_reload",
                            additional_kwargs={"hide_from_ui": True},
                        )
                    )
                gate_status = str(workflow.get("gate_status") or "")
                if (
                    workflow.get("status") == "blocked"
                    and gate_status
                    in {
                        "failed",
                        "managed-artifact-owner-required",
                    }
                    and _failed_gate_is_enforceable(workflow)
                ):
                    if gate_status == "managed-artifact-owner-required":
                        managed_instruction = "Use the owning confirmation server and complete its --wait-only receipt; direct file writes cannot satisfy this boundary."
                    elif _failed_gate_snapshot_stale(workflow):
                        managed_instruction = (
                            "A durable project mutation occurred after this failed receipt. Its failure detail and any "
                            "generated report describe an older project version. Rerun the exact resume_pointer now; "
                            "do not read the old report, make another repair, or advance until the owner returns a fresh receipt."
                        )
                    else:
                        managed_instruction = "Diagnose or repair the failed action, then rerun that owning action successfully."
                    reminders.append(
                        HumanMessage(
                            content=(
                                "<skill_failed_gate>\n"
                                "A persisted Skill tool receipt failed. Successful reads or unrelated tools do not clear "
                                "this gate, and the host blocks later-stage actions until the failure converges.\n"
                                f"gate_status: {gate_status}\n"
                                f"resume_pointer: {workflow.get('resume_pointer') or 'unknown'}\n"
                                f"{managed_instruction}\n"
                                "</skill_failed_gate>"
                            ),
                            name="skill_failed_gate",
                            additional_kwargs={"hide_from_ui": True},
                        )
                    )
                if workflow.get("confirmation_ui_active") and workflow.get("confirmation_url") and not workflow.get("confirmation_wait_timed_out"):
                    reminders.append(
                        HumanMessage(
                            content=(
                                "<skill_confirmation_page_handoff>\n"
                                "The official Skill confirmation owner is running. In the next visible assistant "
                                "progress message, include this exact URL as a Markdown link so the frontend can "
                                "rewrite the Sandbox loopback address to the authenticated thread proxy:\n"
                                f"- {workflow['confirmation_url']}\n"
                                "Keep the owner server alive and continue with its documented wait-only command. "
                                "Do not shorten the owning command's default wait timeout, do not pipe its result "
                                "through tail/head in a way that masks its exit status, and do not switch to chat "
                                "fallback unless the owning wait actually fails. Bundled Skill dependencies are "
                                "already installed in the release Sandbox image; never run a package installer.\n"
                                "</skill_confirmation_page_handoff>"
                            ),
                            name="skill_confirmation_page_handoff",
                            additional_kwargs={"hide_from_ui": True},
                        )
                    )
                if workflow.get("confirmation_wait_timed_out"):
                    reminders.append(
                        HumanMessage(
                            content=(
                                "<skill_confirmation_wait_boundary>\n"
                                "The official confirmation owner's bounded wait expired while the owner server "
                                "remains live. This is an external-user wait boundary, not a failed build or "
                                "validation stage. Recheck the active owner-authored receipt exactly once and "
                                "preserve any value already persisted. If it is still unresolved, follow the "
                                "selected Skill authority's documented page-to-chat fallback. Do not reset or "
                                "overwrite an existing receipt, do not relaunch the page repeatedly, and do not "
                                "retry the same long wait indefinitely.\n"
                                "</skill_confirmation_wait_boundary>"
                            ),
                            name="skill_confirmation_wait_boundary",
                            additional_kwargs={"hide_from_ui": True},
                        )
                    )
        if not reminders:
            return request
        return request.override(messages=[*request.messages, *reminders])

    @hook_config(can_jump_to=["model"])
    @override
    def after_model(self, state: ThreadState, runtime: Runtime) -> dict[str, Any] | None:
        return self._apply_sequence_guard(state, runtime) or self._apply_completion_guard(state, runtime)

    @hook_config(can_jump_to=["model"])
    @override
    async def aafter_model(self, state: ThreadState, runtime: Runtime) -> dict[str, Any] | None:
        return self.after_model(state, runtime)

    @staticmethod
    def _managed_artifact_error(request: ToolCallRequest, relative: str) -> ToolMessage:
        tool_call = request.tool_call
        return ToolMessage(
            content=(
                f"Error: {relative} is owned by the active Skill's confirmation user/tool boundary. "
                "Do not write or replace it with a file or shell tool. Use the owning Skill server/command; "
                "a chat fallback must retain equivalent state without fabricating the managed receipt."
            ),
            tool_call_id=str(tool_call.get("id") or ""),
            name=str(tool_call.get("name") or ""),
            status="error",
        )

    @staticmethod
    def _confirmation_owner_error(request: ToolCallRequest, error: str) -> ToolMessage:
        tool_call = request.tool_call
        return ToolMessage(
            content=(
                "Error: the official Confirm UI rejected the active recommendation before the managed wait began: "
                f"{error}. Re-read the owner contract, correct the active recommendations.stage*.json file, then "
                "rerun the same --wait-only command. The host did not duplicate or weaken the Skill schema."
            ),
            tool_call_id=str(tool_call.get("id") or ""),
            name=str(tool_call.get("name") or ""),
            status="error",
        )

    @staticmethod
    def _bundled_skill_install_error(request: ToolCallRequest) -> ToolMessage:
        tool_call = request.tool_call
        return ToolMessage(
            content=(
                "Error: runtime package installation is disabled for the bundled ppt-master Skill. "
                "Its declared dependencies are preinstalled in the release Sandbox image. Retry the owning "
                "Skill command without pip/apt/npm installation. If an import probe truly fails, report a "
                "Sandbox image defect so the image can be rebuilt and redeployed instead of mutating this run."
            ),
            tool_call_id=str(tool_call.get("id") or ""),
            name=str(tool_call.get("name") or ""),
            status="error",
        )

    @staticmethod
    def _template_contract_error(request: ToolCallRequest, issues: list[str]) -> ToolMessage:
        tool_call = request.tool_call
        detail = "；".join(dict.fromkeys(str(issue) for issue in issues if issue))
        return ToolMessage(
            content=(
                "Error: [PPT_TEMPLATE_CONTRACT] 已确认的 Deck/Layout 模板尚未形成可执行的一致契约："
                f"{detail}。请按官方 Generate 路线重新读取 strategist-template.md 和相应 reference，"
                "由 Strategist 修复 design_spec.md/spec_lock.md，运行官方 project_manager.py validate，"
                "再重试被阻止的动作。不得把结构模板降级成仿制风格，也不得跳过 page_layouts 原型。"
            ),
            tool_call_id=str(tool_call.get("id") or ""),
            name=str(tool_call.get("name") or ""),
            status="error",
        )

    @staticmethod
    def _template_contract_preflight(
        request: ToolCallRequest,
        workflow: SkillWorkflowState,
        tool: str,
        args: dict[str, Any],
    ) -> ToolMessage | None:
        skill_path = str(workflow.get("skill_path") or "")
        authority = str(workflow.get("selected_authority") or "")
        project = str(workflow.get("project_path") or "").rstrip("/")
        if not (
            skill_path.endswith("/ppt-master/SKILL.md")
            and authority.endswith("/workflows/generate-pptx.md")
            and project
        ):
            return None

        contract_name = _ppt_master_contract_authoring_name(request.tool_call, workflow)
        svg_authoring = _ppt_master_svg_authoring_call(request.tool_call, workflow)
        if not contract_name and not svg_authoring:
            return None
        if contract_name and tool == "bash":
            return SkillWorkflowMiddleware._template_contract_error(
                request,
                [f"{contract_name} must be authored through an inspectable file mutation, not shell redirection"],
            )

        sandbox = _resolve_active_sandbox(request)
        if sandbox is None:
            return None
        from deerflow.tools.builtins.presentation_acceptance import (
            ppt_master_template_contract_issues,
            ppt_master_template_svg_issues,
        )

        issues: list[str] = []
        if contract_name:
            target = f"{project}/{contract_name}"
            proposed = _ppt_master_prospective_file_text(sandbox, tool, args, target)
            if proposed is None:
                return None
            if contract_name == "design_spec.md":
                issues.extend(
                    ppt_master_template_contract_issues(
                        sandbox,
                        project,
                        workflow,
                        design_spec_text=proposed,
                        check_spec_lock=False,
                        require_current_run_authority=True,
                    )
                )
            else:
                issues.extend(
                    ppt_master_template_contract_issues(
                        sandbox,
                        project,
                        workflow,
                        spec_lock_text=proposed,
                        require_current_run_authority=True,
                        verify_prototype_files=True,
                    )
                )
        if svg_authoring:
            issues.extend(
                ppt_master_template_contract_issues(
                    sandbox,
                    project,
                    workflow,
                    require_current_run_authority=True,
                    verify_prototype_files=True,
                )
            )
            targets = _ppt_master_svg_targets(tool, args, workflow)
            if not issues and tool in {"write_file", "str_replace"} and targets:
                proposed = _ppt_master_prospective_file_text(sandbox, tool, args, targets[0])
                try:
                    spec_lock = sandbox.read_file(f"{project}/spec_lock.md")
                except Exception:
                    spec_lock = None
                if proposed is not None and isinstance(spec_lock, str):
                    issues.extend(
                        ppt_master_template_svg_issues(
                            sandbox,
                            project,
                            spec_lock,
                            targets[0],
                            proposed,
                        )
                    )
        if issues:
            return SkillWorkflowMiddleware._template_contract_error(request, issues)
        return None

    @staticmethod
    async def _atemplate_contract_preflight(
        request: ToolCallRequest,
        workflow: SkillWorkflowState,
        tool: str,
        args: dict[str, Any],
    ) -> ToolMessage | None:
        return await asyncio.to_thread(
            SkillWorkflowMiddleware._template_contract_preflight,
            request,
            workflow,
            tool,
            args,
        )

    @staticmethod
    def _template_svg_postflight(
        request: ToolCallRequest,
        workflow: SkillWorkflowState,
        tool: str,
        args: dict[str, Any],
        result: ToolMessage | Command,
    ) -> ToolMessage | None:
        targets = _ppt_master_svg_targets(tool, args, workflow)
        if not targets:
            return None
        if not _result_succeeded(result, tool=tool, action=_action_for_tool(tool, args)):
            return None
        project = str(workflow.get("project_path") or "").rstrip("/")
        sandbox = _resolve_active_sandbox(request)
        if not project or sandbox is None:
            return None
        try:
            spec_lock = sandbox.read_file(f"{project}/spec_lock.md")
        except Exception:
            return None
        if not isinstance(spec_lock, str):
            return None
        from deerflow.tools.builtins.presentation_acceptance import ppt_master_template_svg_issues

        issues: list[str] = []
        for target in targets:
            try:
                svg_text = sandbox.read_file(target)
            except Exception:
                continue
            if isinstance(svg_text, str):
                issues.extend(
                    ppt_master_template_svg_issues(
                        sandbox,
                        project,
                        spec_lock,
                        target,
                        svg_text,
                    )
                )
        if issues:
            return SkillWorkflowMiddleware._template_contract_error(request, issues)
        return None

    @staticmethod
    async def _atemplate_svg_postflight(
        request: ToolCallRequest,
        workflow: SkillWorkflowState,
        tool: str,
        args: dict[str, Any],
        result: ToolMessage | Command,
    ) -> ToolMessage | None:
        return await asyncio.to_thread(
            SkillWorkflowMiddleware._template_svg_postflight,
            request,
            workflow,
            tool,
            args,
            result,
        )

    @staticmethod
    def _template_install_preflight(
        request: ToolCallRequest,
        workflow: SkillWorkflowState,
        tool: str,
        args: dict[str, Any],
    ) -> ToolMessage | None:
        if not str(workflow.get("skill_path") or "").endswith("/ppt-master/SKILL.md"):
            return None
        command = str(args.get("command") or "") if tool == "bash" else ""
        confirm_owner = bool(command and _CONFIRM_UI_SERVER_RE.search(command))
        completes_template = confirm_owner and "--complete-template-phase" in command
        wait_stage = _CONFIRM_UI_WAIT_STAGE_RE.search(command) if confirm_owner else None
        enters_strategist = bool(wait_stage and wait_stage.group(1) != "template")
        authors_contract = bool(_ppt_master_contract_authoring_name(request.tool_call, workflow))
        authors_svg = _ppt_master_svg_authoring_call(request.tool_call, workflow)
        if not completes_template and not enters_strategist and not authors_contract and not authors_svg:
            return None
        project = str(workflow.get("project_path") or "").rstrip("/")
        sandbox = _resolve_active_sandbox(request)
        if not project or sandbox is None:
            return None
        from deerflow.tools.builtins.presentation_acceptance import ppt_master_template_install_issues

        issues = ppt_master_template_install_issues(sandbox, project)
        if not issues:
            return None
        return SkillWorkflowMiddleware._template_contract_error(
            request,
            [
                "the confirmed template workspace has not been atomically installed into project-local templates/images/icons",
                *issues,
            ],
        )

    @staticmethod
    def _template_install_postflight(
        request: ToolCallRequest,
        workflow: SkillWorkflowState,
        tool: str,
        args: dict[str, Any],
        result: ToolMessage | Command,
    ) -> ToolMessage | None:
        if tool != "bash" or not str(workflow.get("skill_path") or "").endswith("/ppt-master/SKILL.md"):
            return None
        action = str(args.get("command") or "")
        project = str(workflow.get("project_path") or "").rstrip("/")
        if not (
            project
            and project in action
            and "/templates/decks/" in action
            and _TEMPLATE_INSTALL_MUTATION_RE.search(action)
            and _result_succeeded(result, tool=tool, action=action)
        ):
            return None
        sandbox = _resolve_active_sandbox(request)
        if sandbox is None:
            return None
        from deerflow.tools.builtins.presentation_acceptance import ppt_master_template_install_issues

        issues = ppt_master_template_install_issues(sandbox, project)
        if not issues:
            return None
        return SkillWorkflowMiddleware._template_contract_error(
            request,
            [
                "the template copy changed the project but the selected workspace install is still incomplete",
                *issues,
            ],
        )

    @staticmethod
    async def _atemplate_install_postflight(
        request: ToolCallRequest,
        workflow: SkillWorkflowState,
        tool: str,
        args: dict[str, Any],
        result: ToolMessage | Command,
    ) -> ToolMessage | None:
        return await asyncio.to_thread(
            SkillWorkflowMiddleware._template_install_postflight,
            request,
            workflow,
            tool,
            args,
            result,
        )

    @staticmethod
    async def _atemplate_install_preflight(
        request: ToolCallRequest,
        workflow: SkillWorkflowState,
        tool: str,
        args: dict[str, Any],
    ) -> ToolMessage | None:
        return await asyncio.to_thread(
            SkillWorkflowMiddleware._template_install_preflight,
            request,
            workflow,
            tool,
            args,
        )

    @staticmethod
    def _confirmation_wait_preflight(
        request: ToolCallRequest,
        workflow: SkillWorkflowState,
        tool: str,
        args: dict[str, Any],
    ) -> ToolMessage | None:
        target = _confirmation_wait_target(tool, args, workflow)
        if target is None:
            return None
        sandbox = _resolve_active_sandbox(request)
        if sandbox is None:
            return None
        project, stage = target
        output = sandbox.execute_command(
            _confirm_ui_preflight_command(project, stage),
            timeout=10,
        )
        error = _confirm_ui_preflight_error(output)
        if error:
            return SkillWorkflowMiddleware._confirmation_owner_error(request, error)
        return None

    @staticmethod
    async def _aconfirmation_wait_preflight(
        request: ToolCallRequest,
        workflow: SkillWorkflowState,
        tool: str,
        args: dict[str, Any],
    ) -> ToolMessage | None:
        return await asyncio.to_thread(
            SkillWorkflowMiddleware._confirmation_wait_preflight,
            request,
            workflow,
            tool,
            args,
        )

    @override
    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        workflow = (request.state or {}).get("skill_workflow")
        if isinstance(workflow, dict):
            tool = _tool_call_name(request.tool_call)
            args = _tool_call_args(request.tool_call)
            relative = _managed_artifact_violation(tool, args, workflow)
            if relative:
                return self._managed_artifact_error(request, relative)
            if _bundled_skill_install_violation(tool, args, workflow):
                return self._bundled_skill_install_error(request)
            install_error = self._template_install_preflight(request, workflow, tool, args)
            if install_error:
                return install_error
            template_error = self._template_contract_preflight(request, workflow, tool, args)
            if template_error:
                return template_error
            preflight_error = self._confirmation_wait_preflight(request, workflow, tool, args)
            if preflight_error:
                return preflight_error
        result = handler(request)
        if isinstance(workflow, dict):
            install_postflight_error = self._template_install_postflight(
                request,
                workflow,
                tool,
                args,
                result,
            )
            if install_postflight_error:
                return install_postflight_error
            postflight_error = self._template_svg_postflight(request, workflow, tool, args, result)
            if postflight_error:
                return postflight_error
        return result

    @override
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        workflow = (request.state or {}).get("skill_workflow")
        if isinstance(workflow, dict):
            tool = _tool_call_name(request.tool_call)
            args = _tool_call_args(request.tool_call)
            relative = _managed_artifact_violation(tool, args, workflow)
            if relative:
                return self._managed_artifact_error(request, relative)
            if _bundled_skill_install_violation(tool, args, workflow):
                return self._bundled_skill_install_error(request)
            install_error = await self._atemplate_install_preflight(request, workflow, tool, args)
            if install_error:
                return install_error
            template_error = await self._atemplate_contract_preflight(request, workflow, tool, args)
            if template_error:
                return template_error
            preflight_error = await self._aconfirmation_wait_preflight(request, workflow, tool, args)
            if preflight_error:
                return preflight_error
        result = await handler(request)
        if isinstance(workflow, dict):
            install_postflight_error = await self._atemplate_install_postflight(
                request,
                workflow,
                tool,
                args,
                result,
            )
            if install_postflight_error:
                return install_postflight_error
            postflight_error = await self._atemplate_svg_postflight(request, workflow, tool, args, result)
            if postflight_error:
                return postflight_error
        return result

    @override
    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelCallResult:
        return handler(self._augment_request(request))

    @override
    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelCallResult:
        augmented = self._augment_request(request)
        timeout_response = _ppt_master_model_turn_timeout_response(request)
        if timeout_response is None:
            return await handler(augmented)
        try:
            return await asyncio.wait_for(
                handler(augmented),
                timeout=_PPT_MASTER_MODEL_TURN_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            return timeout_response

    @override
    def after_agent(self, state: ThreadState, runtime: Runtime) -> dict | None:
        key = self._key(runtime)
        with self._lock:
            self._pending_recovery.pop(key, None)
            self._retry_counts.pop(key, None)
        return None

    @override
    async def aafter_agent(self, state: ThreadState, runtime: Runtime) -> dict | None:
        return self.after_agent(state, runtime)


__all__ = ["SkillWorkflowMiddleware", "_managed_artifact_violation", "_render_workflow_state"]
