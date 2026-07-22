"""Middleware for skill activation: explicit slash + in-context secret binding."""

from __future__ import annotations

import asyncio
import hashlib
import html
import logging
import posixpath
import re
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse, hook_config
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.runtime import Runtime
from langgraph.types import Command

from deerflow.agents.human_input import read_human_input_response
from deerflow.agents.middlewares.tool_result_meta import normalize_tool_result
from deerflow.runtime.secret_context import (
    _SECRETS_BINDING_AUDIT_KEY,
    _SLASH_SECRET_SOURCE_KEY,
    _SLASH_SKILL_ACTIVATION_CACHE_KEY,
    _SLASH_SKILL_ACTIVATION_RUN_KEY,
    ACTIVE_SECRETS_CONTEXT_KEY,
    extract_request_secrets,
)
from deerflow.skills.slash import parse_slash_skill_reference, resolve_slash_skill
from deerflow.skills.storage import get_or_new_skill_storage, get_or_new_user_skill_storage
from deerflow.skills.storage.skill_storage import SkillStorage
from deerflow.skills.types import SKILL_MD_FILE, SecretRequirement, Skill, SkillCategory
from deerflow.utils.messages import get_original_user_content_text, is_real_user_message

if TYPE_CHECKING:
    from deerflow.config.app_config import AppConfig

logger = logging.getLogger(__name__)

_SLASH_SKILL_ACTIVATION_KEY = "slash_skill_activation"
_SLASH_SKILL_ACTIVATION_TARGET_ID_KEY = "slash_skill_activation_target_id"

_PRESENTATION_COMPLETION_REMINDER = """<system_reminder>
PPT 工作流尚未完成，不能把当前回复作为最终答案。请从现有项目文件继续执行，不要重新开始，也不要修改技能包：
{requirements}
完成后必须确认真实 PPTX/PDF 文件存在，并调用 present_files 发布，再向用户返回结果。
</system_reminder>"""

_PRESENTATION_COMPLETION_FALLBACK = "PPT 生成流程未完成：自动续跑后仍缺少必要的质检、导出或文件发布步骤。本次运行已标记为失败，请重试；系统不会把未完成的 SVG 当作已交付的 PPT。"

# _SECRETS_BINDING_AUDIT_KEY: last audited binding (skill and secret names only,
# never values) so unchanged bindings are not re-recorded each call.
# _SLASH_SECRET_SOURCE_KEY: latest slash activation as a secret source, holding
# ONLY the activated skill's canonical container path (never its declared
# secrets — those are read from the live registry on each call, #3938). The
# injection set is recomputed every model call, but a slash-activated skill must
# stay bound for the rest of the run — the model's tool loop issues many model
# calls after the single activation call (#3861 semantics).
# _SLASH_SKILL_ACTIVATION_RUN_KEY: identity of the slash message already activated
# in this run, so the skill disk read + "activate" audit event fire once per user
# slash command instead of on every model call.
# _SLASH_SKILL_ACTIVATION_CACHE_KEY: compact continuation context plus runtime
# contract state. The full SKILL.md body is shown on the first model call only;
# later calls receive the compact contract so long workflows retain their gates
# without paying the full skill-body token cost on every tool iteration.
# These private keys live in secret_context so REDACTED_CONTEXT_KEYS covers them.


@dataclass(frozen=True, slots=True)
class _Activation:
    skill_name: str
    category: str
    container_file_path: str
    skill_content: str
    content_hash: str
    remaining_text: str
    editable: bool
    required_secrets: tuple[SecretRequirement, ...] = ()
    reference_requirements: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class _ActivationResolution:
    activation: _Activation | None = None
    failure_message: str | None = None


def is_slash_skill_activation_reminder(message: object) -> bool:
    """Return whether a message is hidden slash-skill activation context."""
    return isinstance(message, HumanMessage) and bool(message.additional_kwargs.get(_SLASH_SKILL_ACTIVATION_KEY))


def _is_user_activation_target(message: object) -> bool:
    return is_real_user_message(message)


class SkillActivationMiddleware(AgentMiddleware):
    """Activate slash skills and enforce their declared execution contracts."""

    def __init__(
        self,
        *,
        available_skills: set[str] | None = None,
        app_config: AppConfig | None = None,
        user_id: str | None = None,
    ) -> None:
        super().__init__()
        self._available_skills = set(available_skills) if available_skills is not None else None
        self._app_config = app_config
        self._user_id = user_id

    def _storage(self) -> SkillStorage:
        if self._user_id is not None:
            return get_or_new_user_skill_storage(self._user_id, app_config=self._app_config)
        if self._app_config is not None:
            return get_or_new_skill_storage(app_config=self._app_config)
        return get_or_new_skill_storage()

    @staticmethod
    def _read_skill_content(skill_file: Path, skills_root: Path, *, storage: SkillStorage | None = None) -> str:
        if skill_file.name != SKILL_MD_FILE:
            raise ValueError(f"Expected {SKILL_MD_FILE}, got {skill_file.name}")
        # Use the storage's path validation if available — UserScopedSkillStorage
        # stores custom skills in a per-user directory that is not a sub-path of
        # the global skills root, so the simple relative_to check would reject them.
        # Fall back to the relative_to check when the storage is a mock (e.g. tests)
        # that doesn't implement validate_skill_file_path.
        if storage is not None and hasattr(storage, "validate_skill_file_path"):
            resolved_file = storage.validate_skill_file_path(skill_file)
        else:
            resolved_file = skill_file.resolve()
            resolved_root = skills_root.resolve()
            try:
                resolved_file.relative_to(resolved_root)
            except ValueError as exc:
                raise ValueError("Resolved skill file must stay within the configured skills root.") from exc
        if not resolved_file.is_file():
            raise FileNotFoundError(resolved_file)
        return resolved_file.read_text(encoding="utf-8")

    @staticmethod
    def _extract_reference_requirements(skill_content: str, skill_dir: str) -> tuple[tuple[str, str], ...]:
        """Extract deterministic read-before-authoring contracts from a skill.

        Skills often declare a mandatory skeleton or reference for a generated
        control file. Preserve those declarations as a compact runtime gate so a
        model cannot invent the schema even when the relevant line is deep in a
        long skill. The gate is content-driven; it does not key on a skill name.
        """
        requirements: list[tuple[str, str]] = []

        def add(match_rule: str, reference: str) -> None:
            reference = reference.strip().removeprefix("./")
            if not reference or "<" in reference or ">" in reference:
                return
            absolute_reference = posixpath.normpath(posixpath.join(skill_dir, reference))
            candidate = (match_rule, absolute_reference)
            if candidate not in requirements:
                requirements.append(candidate)

        mandatory_pattern = re.compile(
            r"before\s+writing\s+`(?:<project_path>/)?(?P<target>[^`/]+)`[^\n]{0,320}?MUST\s+`read_file\s+(?P<reference>[^`]+)`",
            re.IGNORECASE,
        )
        for match in mandatory_pattern.finditer(skill_content):
            add(f"basename:{match.group('target')}", match.group("reference"))

        skeleton_pattern = re.compile(
            r"`<project_path>/(?P<target>[^`/]+)`[^\n]{0,240}?skeleton:\s*`(?P<reference>[^`]+)`",
            re.IGNORECASE,
        )
        for match in skeleton_pattern.finditer(skill_content):
            add(f"basename:{match.group('target')}", match.group("reference"))

        # The executor reference owns SVG authoring, quality gates, and speaker
        # note syntax in skills that explicitly declare it. Requiring one read
        # before either artifact phase keeps those later contracts visible.
        if "references/executor-base.md" in skill_content:
            add("contains:/svg_output/", "references/executor-base.md")
            add("suffix:/notes/total.md", "references/executor-base.md")

        return tuple(requirements)

    def _resolve_activation(self, text: str) -> _ActivationResolution | None:
        reference = parse_slash_skill_reference(text)
        if reference is None:
            return None

        storage = self._storage()
        skills = storage.load_skills(enabled_only=False)
        skill = next((candidate for candidate in skills if candidate.name == reference.name), None)
        if skill is None:
            return _ActivationResolution(failure_message=f"Skill `/{reference.name}` is not installed.")
        if not skill.enabled:
            return _ActivationResolution(failure_message=f"Skill `/{reference.name}` is installed but disabled. Enable it before using slash activation.")
        if self._available_skills is not None and reference.name not in self._available_skills:
            return _ActivationResolution(failure_message=f"Skill `/{reference.name}` is not available for this agent.")

        resolved = resolve_slash_skill(
            text,
            skills,
            available_skills=self._available_skills,
            container_base_path=storage.get_container_root(),
        )
        if resolved is None:
            return _ActivationResolution(failure_message=f"Skill `/{reference.name}` could not be resolved.")

        try:
            skill_content = self._read_skill_content(resolved.skill.skill_file, storage.get_skills_root_path(), storage=storage)
        except (OSError, ValueError):
            logger.exception("Failed to read slash-activated skill %s", resolved.skill.name)
            return _ActivationResolution(failure_message=f"Skill `/{reference.name}` could not be loaded safely. Please check the skill installation.")

        content_hash = hashlib.sha256(skill_content.encode("utf-8")).hexdigest()
        # CUSTOM skills are editable; PUBLIC and LEGACY are read-only
        editable = resolved.skill.category == SkillCategory.CUSTOM
        return _ActivationResolution(
            activation=_Activation(
                skill_name=resolved.skill.name,
                category=str(resolved.skill.category),
                container_file_path=resolved.container_file_path,
                skill_content=skill_content,
                content_hash=content_hash,
                remaining_text=resolved.remaining_text,
                editable=editable,
                required_secrets=tuple(resolved.skill.required_secrets or ()),
                reference_requirements=self._extract_reference_requirements(skill_content, posixpath.dirname(resolved.container_file_path)),
            )
        )

    @staticmethod
    def _build_activation_reminder(activation: _Activation) -> str:
        user_request = activation.remaining_text or ("No additional task text was provided after the slash skill command. Ask the user what they want to do with this skill if the next step is unclear.")
        escaped_user_request = html.escape(user_request, quote=False)
        escaped_skill_content = html.escape(activation.skill_content, quote=False)
        escaped_skill_name = html.escape(activation.skill_name, quote=True)
        escaped_category = html.escape(activation.category, quote=True)
        escaped_path = html.escape(activation.container_file_path, quote=True)
        escaped_skill_dir = html.escape(posixpath.dirname(activation.container_file_path), quote=True)
        escaped_content_hash = html.escape(activation.content_hash, quote=True)
        editable_str = "true" if activation.editable else "false"
        if activation.reference_requirements:
            requirement_lines = "\n".join(f'- target_match="{html.escape(match_rule, quote=True)}" read_first="{html.escape(reference, quote=True)}"' for match_rule, reference in activation.reference_requirements)
            reference_contract = f"""\n<required_reference_reads>
Before creating or modifying a matching target, call read_file on the exact read_first path and use that returned contract. The runtime blocks writes that skip this step.
{requirement_lines}
</required_reference_reads>\n"""
        else:
            reference_contract = ""
        confirmation_contract = ""
        if "wait for explicit user confirmation" in activation.skill_content.lower():
            confirmation_contract = """
<deerflow_confirmation_adapter>
For every BLOCKING chat-fallback confirmation, call the native `ask_clarification` tool with `clarification_type="suggestion"`, the recommendation summary, and explicit confirmation options.
Do not merely print the recommendations and end the turn, and do not rely on generated quick-reply suggestions: only the structured clarification response preserves the active workflow and counts as user confirmation.
</deerflow_confirmation_adapter>
"""
        return f"""<slash_skill_activation>
The user explicitly activated the `{escaped_skill_name}` skill for this turn.
Treat the task text as:
<user_request>
{escaped_user_request}
</user_request>

Follow this skill before choosing a general workflow. The complete SKILL.md body is already present below; do not call read_file on that same SKILL.md again. Load supporting resources from the same skill directory only when needed.
The runtime skill directory is discovered from the activated skill path.
For shell commands that use `${{SKILL_DIR}}`, prepend the following declaration in each new sandbox shell command (shell exports do not persist across independent tool calls):
export SKILL_DIR='{escaped_skill_dir}'
{reference_contract}
{confirmation_contract}
Do not skip mandatory phase gates. A failed command or validation result does not advance the workflow; read the declared reference or exact `--help`, fix the owning source, and retry the same gate.

<skill name="{escaped_skill_name}" category="{escaped_category}" path="{escaped_path}" sha256="{escaped_content_hash}" editable="{editable_str}">
<skill_content encoding="xml-escaped">
{escaped_skill_content}
</skill_content>
</skill>
</slash_skill_activation>"""

    @staticmethod
    def _build_continuation_reminder(activation: _Activation) -> str:
        """Build the small reminder used after the first model call.

        The complete skill body was already supplied on activation. Keeping it
        out of subsequent requests avoids multiplying a large SKILL.md by every
        tool iteration while preserving exact paths and non-negotiable gates.
        """
        escaped_skill_name = html.escape(activation.skill_name, quote=True)
        escaped_path = html.escape(activation.container_file_path, quote=True)
        escaped_skill_dir = html.escape(posixpath.dirname(activation.container_file_path), quote=True)
        if activation.reference_requirements:
            requirement_lines = "\n".join(f'- target_match="{html.escape(match_rule, quote=True)}" read_first="{html.escape(reference, quote=True)}"' for match_rule, reference in activation.reference_requirements)
            reference_contract = f"""\n<required_reference_reads>
{requirement_lines}
</required_reference_reads>\n"""
        else:
            reference_contract = ""
        phase_contract = ""
        if "--stage first-page" in activation.skill_content:
            phase_contract += "- Complete a successful `--stage first-page` quality gate before authoring a second distinct SVG page.\n"
        if "--stage final" in activation.skill_content:
            phase_contract += "- Complete a successful `--stage final` quality gate after the last SVG edit and before export/finalization.\n"
        if "wait for explicit user confirmation" in activation.skill_content.lower():
            phase_contract += "- A BLOCKING confirmation requires a real user response; never choose or confirm recommendations on the user's behalf.\n"
            phase_contract += '- In DeerFlow chat fallback, call `ask_clarification` with `clarification_type="suggestion"`; plain text plus quick replies is not the confirmation gate.\n'
        return f"""<slash_skill_continuation name="{escaped_skill_name}" path="{escaped_path}">
Continue following the already activated skill. Do not substitute a general workflow or invent commands, schemas, parameters, or phase order.
For every independent shell command that uses `${{SKILL_DIR}}`, first declare:
export SKILL_DIR='{escaped_skill_dir}'
{reference_contract}<mandatory_phase_gates>
{phase_contract or "- Preserve every mandatory gate declared by the activated skill.\n"}</mandatory_phase_gates>
Failed commands and validation errors do not advance the workflow. Read the exact reference or command `--help`, fix the owning source, and retry the same gate.
</slash_skill_continuation>"""

    @staticmethod
    def _requires_durable_full_context(skill_content: str) -> bool:
        """Keep strict, multi-phase workflow instructions visible throughout a run.

        A compact continuation is sufficient for ordinary reference skills. It is
        not sufficient for a serial workflow whose later phases define their own
        blocking UI, file schemas, and validation order. Those skills need the
        same effective model context that the legacy persisted ``read_file``
        result provided, without changing the skill package itself.
        """
        normalized = skill_content.casefold()
        return "strict serial pipeline" in normalized and "blocking = hard stop" in normalized and "wait for explicit user confirmation" in normalized

    @staticmethod
    def _has_existing_activation_for_target(messages: list, target_index: int, target: HumanMessage) -> bool:
        if target_index <= 0:
            return False

        if target.id:
            for previous in messages[:target_index]:
                if not is_slash_skill_activation_reminder(previous):
                    continue
                target_id = previous.additional_kwargs.get(_SLASH_SKILL_ACTIVATION_TARGET_ID_KEY)
                if target_id == target.id or previous.id == f"{target.id}__slash_activation":
                    return True

        previous = messages[target_index - 1]
        return is_slash_skill_activation_reminder(previous)

    @staticmethod
    def _activation_run_key(target: HumanMessage) -> str:
        """Stable identity for a user slash message, used to activate once per run.

        Prefers the message id (LangGraph assigns and preserves a stable id once a
        message is in graph state); falls back to a digest of the genuine user text
        so an id-less message still dedupes within a run. A new user slash message
        (new id / new text) yields a new key, so it is not suppressed.
        """
        if target.id:
            return target.id
        content = get_original_user_content_text(target.content, target.additional_kwargs)
        return "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()

    @staticmethod
    def _run_context(request: ModelRequest) -> dict | None:
        runtime = getattr(request, "runtime", None)
        context = getattr(runtime, "context", None)
        return context if isinstance(context, dict) else None

    @staticmethod
    def _already_activated(run_context: dict | None, run_key: str) -> bool:
        """Whether ``run_key`` was already recorded as activated earlier in this run.

        Sibling to ``_has_existing_activation_for_target``: that helper catches an
        activation reminder still present in the scanned ``messages`` window; this
        one catches a prior activation recorded on ``run_context`` whose reminder
        already fell out of that window (the tool-loop case — see
        ``_SLASH_SKILL_ACTIVATION_RUN_KEY``). ``run_key`` is computed once by the
        caller (``_find_activation_target``) and reused as-is at the write site in
        ``_prepare_model_request``, so the same key is always used to check and to
        record — this helper only ever checks membership, never computes the key.
        """
        return isinstance(run_context, dict) and run_context.get(_SLASH_SKILL_ACTIVATION_RUN_KEY) == run_key

    def _prepare_cached_activation_request(self, request: ModelRequest, *, run_context: dict | None) -> ModelRequest | None:
        """Restore the validated slash reminder on later model calls of a run.

        The cache is keyed to the latest real user message. A new non-slash
        message therefore cannot inherit an older activation, while a new slash
        command is resolved normally and replaces the cache.
        """
        if not isinstance(run_context, dict):
            return None
        cached = run_context.get(_SLASH_SKILL_ACTIVATION_CACHE_KEY)
        if not isinstance(cached, dict):
            return None

        messages = list(request.messages)
        target_index = next((idx for idx in range(len(messages) - 1, -1, -1) if _is_user_activation_target(messages[idx])), None)
        if target_index is None:
            return None
        target = messages[target_index]
        if not isinstance(target, HumanMessage):
            return None
        run_key = self._activation_run_key(target)
        if cached.get("run_key") != run_key:
            return None
        activation_content = cached.get("continuation_content")
        if not isinstance(activation_content, str) or not activation_content:
            return None
        if self._has_existing_activation_for_target(messages, target_index, target):
            return None

        messages.insert(target_index, self._make_activation_message(target, activation_content))
        return request.override(messages=messages)

    def _find_activation_target(self, messages: list, *, run_context: dict | None = None) -> tuple[int, HumanMessage, _ActivationResolution, str] | None:
        if not messages:
            return None

        target_index = next((idx for idx in range(len(messages) - 1, -1, -1) if _is_user_activation_target(messages[idx])), None)
        if target_index is None:
            return None

        target = messages[target_index]
        if target is None:
            return None
        if self._has_existing_activation_for_target(messages, target_index, target):
            return None
        # This exact slash message may have already activated earlier in the run.
        # The message scan above cannot catch it because the reminder lives only in
        # a per-call request override, never in state — the run context is the
        # durable signal (see _already_activated / _SLASH_SKILL_ACTIVATION_RUN_KEY).
        # Skipping here avoids the redundant skill disk read, reminder re-injection,
        # and duplicate "activate" audit. run_key is computed once here and threaded
        # through to the write site in _prepare_model_request.
        run_key = self._activation_run_key(target)
        if self._already_activated(run_context, run_key):
            return None

        content = get_original_user_content_text(target.content, target.additional_kwargs)
        resolution = self._resolve_activation(content)
        if resolution is None:
            return None
        return target_index, target, resolution, run_key

    @staticmethod
    def _record_activation(request: ModelRequest, activation: _Activation, *, hook: str) -> None:
        runtime = getattr(request, "runtime", None)
        context = getattr(runtime, "context", None)
        journal = context.get("__run_journal") if isinstance(context, dict) else None
        if journal is None:
            return
        try:
            journal.record_middleware(
                "skill_activation",
                name="SkillActivationMiddleware",
                hook=hook,
                action="activate",
                changes={
                    "skill_name": activation.skill_name,
                    "category": activation.category,
                    "path": activation.container_file_path,
                    "content_hash": activation.content_hash,
                },
            )
        except Exception:
            logger.debug("Failed to record slash skill activation audit event", exc_info=True)

    def _prepare_model_request(self, request: ModelRequest, *, hook: str) -> tuple[ModelRequest | AIMessage | None, _Activation | None]:
        run_context = self._run_context(request)
        target_and_resolution = self._find_activation_target(list(request.messages), run_context=run_context)
        if target_and_resolution is None:
            return self._prepare_cached_activation_request(request, run_context=run_context), None

        target_index, target, resolution, run_key = target_and_resolution
        if resolution.failure_message:
            if run_context is not None:
                run_context.pop(_SLASH_SKILL_ACTIVATION_CACHE_KEY, None)
            return AIMessage(content=resolution.failure_message), None

        activation = resolution.activation
        if activation is None:
            return None, None

        logger.info(
            "SkillActivationMiddleware: activating slash skill %s category=%s path=%s hash=%s",
            activation.skill_name,
            activation.category,
            activation.container_file_path,
            activation.content_hash,
        )
        self._record_activation(request, activation, hook=hook)
        # Mark this slash message as activated for the run so the tool loop's later
        # model calls skip the redundant re-activation (#3861: one activation call,
        # many follow-up model calls). A new user slash message keys differently and
        # still activates. Overwrite (`=`), not append/accumulate, is intentional:
        # _find_activation_target only ever considers the latest real user message as
        # an activation target, so there is nothing earlier in the run worth
        # remembering once a new activation replaces it — do not "fix" this into a
        # set. run_key is the same value already checked in _find_activation_target
        # (computed once there, threaded through here) rather than recomputed.
        if run_context is not None:
            run_context[_SLASH_SKILL_ACTIVATION_RUN_KEY] = run_key
        activation_content = self._build_activation_reminder(activation)
        if run_context is not None:
            durable_full_context = self._requires_durable_full_context(activation.skill_content)
            run_context[_SLASH_SKILL_ACTIVATION_CACHE_KEY] = {
                "run_key": run_key,
                "continuation_content": activation_content if durable_full_context else self._build_continuation_reminder(activation),
                "durable_full_context": durable_full_context,
                "reference_requirements": [list(item) for item in activation.reference_requirements],
                "read_paths": [],
                "required_markdown_sections": {"design_spec.md": ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X"]}
                if re.search(r"full\s+I\s*[\N{EN DASH}\N{EM DASH}-]\s*X\s+section\s+structure", activation.skill_content, re.IGNORECASE)
                else {},
                "requires_pptx_structure_mode": "pptx_structure.mode" in activation.skill_content or "pptx_structure is mandatory" in activation.skill_content.lower(),
                "requires_first_page_gate": "--stage first-page" in activation.skill_content,
                "requires_final_gate": "--stage final" in activation.skill_content,
                "requires_presentation_export": "svg_to_pptx.py" in activation.skill_content,
                "requires_presentation_delivery": "present_files" in activation.skill_content,
                "requires_human_confirmation": "wait for explicit user confirmation" in activation.skill_content.lower(),
                "human_confirmation_completed": False,
                "svg_paths": [],
                "first_page_validated": False,
                "final_validated": False,
                "presentation_export_completed": False,
                "presentation_delivery_completed": False,
                "presentation_completion_retry_count": 0,
            }
        activation_msg = self._make_activation_message(target, activation_content)
        messages = list(request.messages)
        messages.insert(target_index, activation_msg)
        return request.override(messages=messages), activation

    def _handle_model_request(self, request: ModelRequest, *, hook: str) -> ModelRequest | AIMessage:
        prepared, activation = self._prepare_model_request(request, hook=hook)
        if isinstance(prepared, AIMessage):
            return prepared
        effective = prepared if prepared is not None else request
        self._resolve_secret_bindings(effective, activation, hook=hook)
        return effective

    @staticmethod
    def _message_text(message: AIMessage) -> str:
        content = message.content
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, Mapping) and isinstance(item.get("text"), str):
                    parts.append(item["text"])
            return "\n".join(parts)
        return str(content)

    @staticmethod
    def _looks_like_design_confirmation(text: str) -> bool:
        """Identify a complete design proposal, not an earlier content question."""
        normalized = text.casefold()
        field_groups = (
            ("canvas", "画布"),
            ("visual style", "视觉风格", "风格目标"),
            ("color scheme", "配色", "颜色方案"),
            ("typography", "字体", "字号"),
            ("image usage", "图片使用", "图像使用"),
        )
        matched_fields = sum(any(term in normalized for term in group) for group in field_groups)
        asks_for_confirmation = any(term in normalized for term in ("确认", "confirm", "approve", "approval"))
        return matched_fields >= 4 and asks_for_confirmation

    def _ensure_structured_confirmation(self, request: ModelRequest, response: ModelResponse | AIMessage) -> ModelResponse | AIMessage:
        """Convert a plain-text blocking design proposal into a real DeerFlow wait."""
        run_context = self._run_context(request)
        cache = run_context.get(_SLASH_SKILL_ACTIVATION_CACHE_KEY) if isinstance(run_context, dict) else None
        if not isinstance(cache, dict) or not cache.get("requires_human_confirmation") or cache.get("human_confirmation_completed"):
            return response

        result_messages = response.result if isinstance(response, ModelResponse) else [response]
        if not result_messages or not isinstance(result_messages[-1], AIMessage):
            return response
        message = result_messages[-1]
        proposal_text = self._message_text(message).strip()
        if message.tool_calls or not self._looks_like_design_confirmation(proposal_text):
            return response

        confirmation_call = {
            "name": "ask_clarification",
            "args": {
                "question": "请确认以上设计方案是否采用；如需调整，请在其他回答中写明修改项。",
                "clarification_type": "suggestion",
                "context": proposal_text,
                "options": ["确认采用推荐方案并继续", "需要修改设计方案"],
            },
            "id": f"call_skill_confirmation_{uuid.uuid4().hex[:16]}",
            "type": "tool_call",
        }
        updated_message = message.model_copy(update={"tool_calls": [confirmation_call]})
        if isinstance(response, ModelResponse):
            return ModelResponse(result=[*result_messages[:-1], updated_message], structured_response=response.structured_response)
        return updated_message

    def _resolve_secret_bindings(self, request: ModelRequest, activation: _Activation | None, *, hook: str) -> None:
        """Recompute the per-run secret injection set (binding point A+, #3861/#3914).

        Sources, unioned on every model call:

        - the most recent slash activation of this run (persisted as a source on
          the run context so the whole tool loop after the activation call keeps
          the binding — a new slash activation replaces it). The slash source is
          validated once, at activation (enabled + allowlist checks in
          ``_resolve_activation``), and deliberately NOT re-validated per call:
          slash is a run-scoped commitment made by the user, and it dies with
          the run anyway;
        - skills the model loaded earlier in the thread (``ThreadState.skill_context``),
          re-validated against the live registry on each call: enabled,
          runtime-allowed for this agent, and not opted out via
          ``secrets-autonomous: false``. Slash activation is exempt from the
          opt-out — it is the explicit-ceremony path.

        The set is recomputed and REPLACED each call, so a skill evicted from
        skill_context, or a caller that stops supplying a value, loses its
        injection on the next call automatically. Injected values always come
        from the caller's request (``context.secrets``) — never the host
        environment, which ``env_policy.build_sandbox_env`` scrubs before
        injection — so a skill can never harvest a host platform credential.
        Secret *values* are never logged; the audit journal records names only.
        """
        runtime = getattr(request, "runtime", None)
        context = getattr(runtime, "context", None)
        if not isinstance(context, dict):
            return

        # The slash source records only the canonical container path of the
        # activated skill — never its declared secrets. Both sources resolve the
        # live registry skill by path on read, so a caller-forged source (the
        # context is caller-mergeable) can never inject secrets a real, enabled,
        # allowlisted skill did not declare (#3938).
        if activation is not None:
            context[_SLASH_SECRET_SOURCE_KEY] = {"path": activation.container_file_path}

        request_secrets = extract_request_secrets(context)
        sources: list[tuple[str, tuple[SecretRequirement, ...]]] = []
        if request_secrets:
            registry = self._load_skill_registry_by_path()
            if registry is not None:
                # Slash source: exempt from the ``secrets-autonomous`` opt-out
                # (explicit ceremony), but still enabled + allowlist checked.
                slash_source = context.get(_SLASH_SECRET_SOURCE_KEY)
                slash_path = slash_source.get("path") if isinstance(slash_source, dict) else None
                slash_skill = self._resolve_registry_skill(registry, slash_path, require_autonomous=False)
                if slash_skill is not None:
                    sources.append((slash_skill.name, tuple(slash_skill.required_secrets)))
                sources.extend(self._in_context_secret_sources(request, registry))

        injected: dict[str, str] = {}
        bound_skills: set[str] = set()
        missing: dict[str, list[str]] = {}
        for skill_name, requirements in sources:
            for req in requirements:
                if req.name in request_secrets:
                    injected[req.name] = request_secrets[req.name]
                    bound_skills.add(skill_name)
                elif not req.optional:
                    missing.setdefault(skill_name, []).append(req.name)

        if injected:
            context[ACTIVE_SECRETS_CONTEXT_KEY] = injected
        else:
            context.pop(ACTIVE_SECRETS_CONTEXT_KEY, None)

        audit_state = {
            "skills": sorted(bound_skills),
            "secrets": sorted(injected),
            "missing": {name: sorted(values) for name, values in sorted(missing.items())},
        }
        previous = context.get(_SECRETS_BINDING_AUDIT_KEY)
        if previous == audit_state:
            return
        if previous is None and not injected and not missing:
            return
        context[_SECRETS_BINDING_AUDIT_KEY] = audit_state
        for skill_name, names in sorted(missing.items()):
            logger.warning(
                "Skill %s is active but required secrets are missing from the request context: %s",
                skill_name,
                ", ".join(names),
            )
        self._record_secret_binding(context, audit_state, hook=hook)

    def _load_skill_registry_by_path(self) -> dict[str, Skill] | None:
        """Load the live skill registry keyed by normalized container file path.

        Reloaded every call on purpose (not cached): load_skills re-reads the
        enabled state from extensions_config so an operator disabling a skill
        revokes its secret binding on the very next model call. A cache keyed on
        file mtimes would miss enable/disable toggles (which do not touch
        SKILL.md) and keep injecting after a disable — trading the
        immediate-revocation security property for speed. The cost is gated: the
        only caller runs this only when the caller supplied secrets.

        Paths are normalized so a non-canonical ``container_path`` config (e.g. a
        trailing slash) still matches the canonical path captured in
        ``skill_context`` (#3938). Returns ``None`` if the registry can't load —
        both the slash and in-context sources then bind nothing for that call
        (fail closed). This is a deliberate availability-for-security trade-off:
        a transient registry read failure mid-run drops the injection for that
        call rather than trusting stale caller-supplied data.
        """
        try:
            storage = self._storage()
            skills = storage.load_skills(enabled_only=False)
            container_root = storage.get_container_root()
        except Exception:
            logger.exception("Failed to load skills while resolving secret bindings")
            return None
        return {posixpath.normpath(skill.get_container_file_path(container_root)): skill for skill in skills}

    def _resolve_registry_skill(self, registry: dict[str, Skill], path: object, *, require_autonomous: bool) -> Skill | None:
        """Resolve a container path to a live registry skill eligible for secret
        binding, or ``None``.

        Match strictly by normalized container file path — never by name. A
        by-name fallback would be a confused deputy: DeerFlow lets a custom skill
        shadow a same-named public/legacy one (load_skills de-dupes by name,
        custom wins), so a reference to public/foo could bind the custom foo's
        secrets. A path that does not resolve simply binds nothing (the safe
        direction), which also fails closed on a caller-forged path (#3938).

        Gates: the skill must be enabled, declare secrets, and be allowlisted for
        this agent. ``require_autonomous`` additionally enforces the
        ``secrets-autonomous`` opt-out for the in-context path; the slash path
        passes ``False`` because explicit activation is the ceremony that opt-out
        is meant to preserve.
        """
        if not isinstance(path, str) or not path:
            return None
        skill = registry.get(posixpath.normpath(path))
        if skill is None or not skill.enabled or not skill.required_secrets:
            return None
        if require_autonomous and not skill.secrets_autonomous:
            return None
        if self._available_skills is not None and skill.name not in self._available_skills:
            return None
        return skill

    def _in_context_secret_sources(self, request: ModelRequest, registry: dict[str, Skill]) -> list[tuple[str, tuple[SecretRequirement, ...]]]:
        """Map ``ThreadState.skill_context`` entries to declared-secret sources.

        Entries are references to skills the model actually loaded in this
        thread. Each is re-validated against the live registry so a skill that
        was disabled, uninstalled, opted out, or removed from the agent's
        allowlist after being read stops binding immediately.
        """
        state = getattr(request, "state", None) or {}
        try:
            entries = state.get("skill_context") or []
        except AttributeError:
            return []

        sources: list[tuple[str, tuple[SecretRequirement, ...]]] = []
        seen: set[str] = set()
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            skill = self._resolve_registry_skill(registry, entry.get("path"), require_autonomous=True)
            if skill is None or skill.name in seen:
                continue
            seen.add(skill.name)
            sources.append((skill.name, tuple(skill.required_secrets)))
        return sources

    @staticmethod
    def _record_secret_binding(context: dict, audit_state: dict, *, hook: str) -> None:
        journal = context.get("__run_journal")
        if journal is None:
            return
        try:
            journal.record_middleware(
                "skill_secrets",
                name="SkillActivationMiddleware",
                hook=hook,
                action="bind_secrets",
                changes=audit_state,
            )
        except Exception:
            logger.debug("Failed to record skill secret binding audit event", exc_info=True)

    @staticmethod
    def _make_activation_message(target: HumanMessage, activation_content: str) -> HumanMessage:
        stable_id = target.id or str(uuid.uuid4())
        additional_kwargs = {
            "hide_from_ui": True,
            _SLASH_SKILL_ACTIVATION_KEY: True,
        }
        if target.id:
            additional_kwargs[_SLASH_SKILL_ACTIVATION_TARGET_ID_KEY] = target.id
        return HumanMessage(
            content=activation_content,
            id=f"{stable_id}__slash_activation",
            additional_kwargs=additional_kwargs,
        )

    @staticmethod
    def _tool_path(request: ToolCallRequest) -> str | None:
        args = request.tool_call.get("args") or {}
        if not isinstance(args, dict):
            return None
        path = args.get("path")
        return posixpath.normpath(path.replace("\\", "/")) if isinstance(path, str) and path else None

    @staticmethod
    def _activation_cache_from_tool_request(request: ToolCallRequest) -> dict | None:
        context = getattr(request.runtime, "context", None)
        if not isinstance(context, dict):
            return None
        cache = context.get(_SLASH_SKILL_ACTIVATION_CACHE_KEY)
        return cache if isinstance(cache, dict) else None

    @staticmethod
    def _matching_reference(cache: dict, target_path: str) -> str | None:
        requirements = cache.get("reference_requirements")
        if not isinstance(requirements, list):
            return None
        for item in requirements:
            if not isinstance(item, (list, tuple)) or len(item) != 2:
                continue
            match_rule, reference = item
            if not isinstance(match_rule, str) or not isinstance(reference, str):
                continue
            if match_rule.startswith("basename:") and posixpath.basename(target_path) == match_rule.removeprefix("basename:"):
                return posixpath.normpath(reference)
            if match_rule.startswith("contains:"):
                needle = match_rule.removeprefix("contains:")
                if needle in target_path or needle.strip("/") in target_path.split("/"):
                    return posixpath.normpath(reference)
            if match_rule.startswith("suffix:"):
                suffix = match_rule.removeprefix("suffix:")
                if target_path.endswith(suffix) or target_path.endswith(suffix.lstrip("/")):
                    return posixpath.normpath(reference)
        return None

    @staticmethod
    def _successful_tool_message(result: ToolMessage | Command) -> bool:
        message: ToolMessage | None = result if isinstance(result, ToolMessage) else None
        if message is None and isinstance(result, Command) and isinstance(result.update, dict):
            candidates = [item for item in result.update.get("messages", []) if isinstance(item, ToolMessage)]
            message = candidates[-1] if candidates else None
        if message is None or message.status == "error":
            return False
        return not str(message.content).lstrip().startswith("Error:")

    @staticmethod
    def _record_reference_read(cache: dict, path: str) -> None:
        current = cache.get("read_paths")
        paths = [item for item in current if isinstance(item, str)] if isinstance(current, list) else []
        normalized = posixpath.normpath(path)
        if normalized not in paths:
            paths.append(normalized)
        cache["read_paths"] = paths[-64:]

    def _check_reference_gate(self, request: ToolCallRequest) -> ToolMessage | None:
        if request.tool_call.get("name") not in {"write_file", "str_replace"}:
            return None
        path = self._tool_path(request)
        cache = self._activation_cache_from_tool_request(request)
        if path is None or cache is None:
            return None
        reference = self._matching_reference(cache, path)
        if reference is None:
            return None
        read_paths = cache.get("read_paths")
        if isinstance(read_paths, list) and reference in {posixpath.normpath(item) for item in read_paths if isinstance(item, str)}:
            return None
        tool_name = str(request.tool_call.get("name") or "write_file")
        return ToolMessage(
            content=(
                f"Error: {tool_name} blocked for {path}. The active skill declares {reference} as the required schema/workflow reference for this target. "
                f"Call read_file with path={reference}, follow the returned contract exactly, then retry this write. Do not invent the file format from memory."
            ),
            tool_call_id=str(request.tool_call.get("id") or ""),
            name=tool_name,
            status="error",
        )

    @staticmethod
    def _tool_args(request: ToolCallRequest) -> dict:
        args = request.tool_call.get("args") or {}
        return args if isinstance(args, dict) else {}

    def _check_declared_content_contract(self, request: ToolCallRequest) -> ToolMessage | None:
        if request.tool_call.get("name") != "write_file":
            return None
        path = self._tool_path(request)
        cache = self._activation_cache_from_tool_request(request)
        if path is None or cache is None:
            return None
        content = self._tool_args(request).get("content")
        if not isinstance(content, str):
            return None
        tool_name = str(request.tool_call.get("name") or "write_file")
        contracts = cache.get("required_markdown_sections")
        required = contracts.get(posixpath.basename(path)) if isinstance(contracts, dict) else None
        if isinstance(required, list) and required:
            missing = [section for section in required if re.search(rf"(?m)^##\s+{re.escape(section)}\.\s+", content) is None]
            if missing:
                return ToolMessage(
                    content=(
                        f"Error: {tool_name} blocked for {path}. The active skill requires the full reference section structure; "
                        f"missing Markdown H2 sections: {', '.join(missing)}. Re-read the declared reference and write every required section, "
                        "including conditionally empty sections with an explicit 'not needed' value."
                    ),
                    tool_call_id=str(request.tool_call.get("id") or ""),
                    name=tool_name,
                    status="error",
                )
        if posixpath.basename(path) == "spec_lock.md" and cache.get("requires_pptx_structure_mode"):
            structure = re.search(r"(?ms)^##\s+pptx_structure\s*$\s*(.*?)(?=^##\s+|\Z)", content)
            mode = re.search(r"(?m)^-\s*mode:\s*(flat|structured)\s*$", structure.group(1)) if structure else None
            if mode is None:
                return ToolMessage(
                    content=(
                        f"Error: {tool_name} blocked for {path}. The active skill requires `## pptx_structure` with exactly one non-empty "
                        "`- mode: flat` or `- mode: structured` value. Select the route declared by the confirmed design before continuing."
                    ),
                    tool_call_id=str(request.tool_call.get("id") or ""),
                    name=tool_name,
                    status="error",
                )
        return None

    @staticmethod
    def _is_svg_output_path(path: str | None) -> bool:
        if not isinstance(path, str) or not path.endswith(".svg"):
            return False
        return "svg_output" in posixpath.normpath(path).split("/")

    @classmethod
    def _bash_svg_output_paths(cls, command: str) -> list[str]:
        """Extract SVG write targets from common shell/Python write forms.

        Bash is intentionally available to skills, so phase gates cannot rely
        only on the structured ``write_file`` tool. This parser is deliberately
        narrow: it recognizes redirection targets and Python ``open(..., write)``
        calls without interpreting SVG markup embedded inside quoted strings.
        """
        cwd: str | None = None
        for match in re.finditer(
            r"(?:^|&&|;|\|\|)\s*cd\s+(?P<quote>['\"]?)(?P<path>[^\s'\";&|]+)(?P=quote)",
            command,
        ):
            cwd = posixpath.normpath(match.group("path").replace("\\", "/"))

        candidates = [
            match.group("path")
            for match in re.finditer(
                r"(?:>>|>)\s*(?P<quote>['\"]?)(?P<path>[^\s'\";&|<>]+\.svg)(?P=quote)",
                command,
                re.IGNORECASE,
            )
        ]
        candidates.extend(
            match.group("path")
            for match in re.finditer(
                r"\bopen\(\s*['\"](?P<path>[^'\"]+\.svg)['\"]\s*,\s*['\"][wax+]",
                command,
                re.IGNORECASE,
            )
        )

        paths: list[str] = []
        for raw_path in candidates:
            normalized = posixpath.normpath(raw_path.replace("\\", "/"))
            if not normalized.startswith("/") and cwd:
                normalized = posixpath.normpath(posixpath.join(cwd, normalized))
            if cls._is_svg_output_path(normalized) and normalized not in paths:
                paths.append(normalized)
        return paths

    @staticmethod
    def _record_svg_paths(cache: dict, paths: list[str]) -> None:
        existing = [item for item in cache.get("svg_paths", []) if isinstance(item, str)]
        for path in paths:
            if path not in existing:
                existing.append(path)
        cache["svg_paths"] = existing[-256:]
        cache["final_validated"] = False
        cache["presentation_export_completed"] = False
        cache["presentation_delivery_completed"] = False
        if len(existing) == 1:
            cache["first_page_validated"] = False

    @staticmethod
    def _state_messages(request: ToolCallRequest) -> list:
        state = request.state
        if not isinstance(state, dict):
            return []
        messages = state.get("messages")
        return list(messages) if isinstance(messages, list) else []

    @staticmethod
    def _confirmation_request_ids(messages: list) -> set[str]:
        """Return structured clarification request IDs that asked for approval.

        A skill may ask for missing source material before its later blocking
        design confirmation. Both answers use the same hidden human-input message
        shape, so accepting any response would let the earlier content answer
        unlock the later design contract. Only suggestion/risk-confirmation cards
        represent explicit approval; their request IDs bind the response to the
        exact prompt the user saw.
        """
        request_ids: set[str] = set()
        for message in messages:
            if not isinstance(message, ToolMessage) or message.name != "ask_clarification":
                continue
            artifact = getattr(message, "artifact", None)
            human_input = artifact.get("human_input") if isinstance(artifact, Mapping) else None
            if not isinstance(human_input, Mapping):
                continue
            if human_input.get("source") != "ask_clarification" or human_input.get("clarification_type") not in {"suggestion", "risk_confirmation"}:
                continue
            request_id = human_input.get("request_id")
            if isinstance(request_id, str) and request_id:
                request_ids.add(request_id)
        return request_ids

    def _has_explicit_human_confirmation(self, request: ToolCallRequest, cache: dict) -> bool:
        if cache.get("human_confirmation_completed"):
            return True
        messages = self._state_messages(request)
        confirmation_request_ids = self._confirmation_request_ids(messages)
        for message in messages:
            if not isinstance(message, HumanMessage):
                continue
            response = read_human_input_response(message.additional_kwargs)
            if response is not None and response["source"] == "ask_clarification" and response["request_id"] in confirmation_request_ids:
                cache["human_confirmation_completed"] = True
                return True
        return False

    def _check_human_confirmation_gate(self, request: ToolCallRequest) -> ToolMessage | None:
        if request.tool_call.get("name") not in {"write_file", "str_replace"}:
            return None
        path = self._tool_path(request)
        cache = self._activation_cache_from_tool_request(request)
        if path is None or cache is None or not cache.get("requires_human_confirmation"):
            return None
        if posixpath.basename(path) not in {"design_spec.md", "spec_lock.md"}:
            return None
        if self._has_explicit_human_confirmation(request, cache):
            return None
        tool_name = str(request.tool_call.get("name") or "write_file")
        return ToolMessage(
            content=(
                f"Error: {tool_name} blocked for {path}. The active skill declares this Strategist confirmation as BLOCKING and "
                "requires an explicit user response. You may not confirm recommendations on the user's behalf. Use the skill's "
                "confirmation UI, or call ask_clarification with clarification_type='suggestion' to present the recommended defaults "
                "and wait. An earlier missing-information answer is not design approval. Retry only after the user responds to that confirmation request."
            ),
            tool_call_id=str(request.tool_call.get("id") or ""),
            name=tool_name,
            status="error",
        )

    def _check_phase_gate(self, request: ToolCallRequest) -> ToolMessage | None:
        cache = self._activation_cache_from_tool_request(request)
        if cache is None:
            return None
        name = request.tool_call.get("name")
        path = self._tool_path(request)
        if name in {"write_file", "str_replace"} and self._is_svg_output_path(path):
            existing = [item for item in cache.get("svg_paths", []) if isinstance(item, str)]
            if cache.get("requires_first_page_gate") and existing and path not in existing and not cache.get("first_page_validated"):
                tool_name = str(name or "write_file")
                return ToolMessage(
                    content=(
                        f"Error: {tool_name} blocked for {path}. The active skill requires the first-page quality gate to succeed "
                        "before a second distinct SVG page is authored. Run the declared checker with `--stage first-page`, fix any "
                        "reported errors, rerun it successfully, then retry this write."
                    ),
                    tool_call_id=str(request.tool_call.get("id") or ""),
                    name=tool_name,
                    status="error",
                )
        if name == "bash":
            command = self._tool_args(request).get("command")
            if not isinstance(command, str):
                return None
            bash_svg_paths = self._bash_svg_output_paths(command)
            if bash_svg_paths and cache.get("requires_first_page_gate") and not cache.get("first_page_validated"):
                existing = {item for item in cache.get("svg_paths", []) if isinstance(item, str)}
                if len(existing.union(bash_svg_paths)) > 1:
                    return ToolMessage(
                        content=(
                            "Error: bash blocked. The active skill requires the first-page quality gate to succeed before a second "
                            "distinct SVG page is authored. Run the declared checker with `--stage first-page`, fix any reported "
                            "errors, rerun it successfully, then retry this write."
                        ),
                        tool_call_id=str(request.tool_call.get("id") or ""),
                        name="bash",
                        status="error",
                    )
            is_final_check = "svg_quality_checker.py" in command and "--stage final" in command
            if is_final_check and cache.get("requires_first_page_gate") and not cache.get("first_page_validated"):
                return ToolMessage(
                    content="Error: final quality check blocked. Complete the declared `--stage first-page` gate successfully first.",
                    tool_call_id=str(request.tool_call.get("id") or ""),
                    name="bash",
                    status="error",
                )
            is_export = "finalize_svg.py" in command or "svg_to_pptx.py" in command
            if is_export and cache.get("requires_final_gate") and not cache.get("final_validated"):
                return ToolMessage(
                    content=("Error: export/finalization blocked. The active skill requires a successful `svg_quality_checker.py --stage final` after the most recent SVG edit. Run and pass that gate, then retry this command."),
                    tool_call_id=str(request.tool_call.get("id") or ""),
                    name="bash",
                    status="error",
                )
        return None

    def _record_phase_progress(self, request: ToolCallRequest, result: ToolMessage | Command) -> None:
        if not self._successful_tool_message(result):
            return
        cache = self._activation_cache_from_tool_request(request)
        if cache is None:
            return
        name = request.tool_call.get("name")
        path = self._tool_path(request)
        if name in {"write_file", "str_replace"} and self._is_svg_output_path(path):
            self._record_svg_paths(cache, [path])
            return
        if name == "present_files":
            cache["presentation_delivery_completed"] = True
            return
        if name != "bash":
            return
        command = self._tool_args(request).get("command")
        if not isinstance(command, str):
            return
        bash_svg_paths = self._bash_svg_output_paths(command)
        if bash_svg_paths:
            self._record_svg_paths(cache, bash_svg_paths)
        if "confirm_ui/server.py" in command and "--wait-only" in command and "--wait-stage" not in command:
            cache["human_confirmation_completed"] = True
        if "svg_quality_checker.py" in command:
            if "--stage first-page" in command:
                cache["first_page_validated"] = True
            if "--stage final" in command:
                cache["final_validated"] = True
        if "svg_to_pptx.py" in command:
            cache["presentation_export_completed"] = True

    @staticmethod
    def _presentation_incomplete_requirements(cache: dict) -> list[str]:
        if not cache.get("svg_paths"):
            return []
        requirements: list[str] = []
        if cache.get("requires_final_gate") and not cache.get("final_validated"):
            requirements.append("运行并通过 `svg_quality_checker.py --stage final` 最终质检")
        if cache.get("requires_presentation_export") and not cache.get("presentation_export_completed"):
            requirements.append("执行 `svg_to_pptx.py`，生成真实 PPTX 与预览 PDF")
        if cache.get("requires_presentation_delivery") and not cache.get("presentation_delivery_completed"):
            requirements.append("调用 `present_files` 发布 PPTX 和 PDF")
        return requirements

    @staticmethod
    def _has_tool_call_intent(message: AIMessage) -> bool:
        if message.tool_calls or getattr(message, "invalid_tool_calls", None):
            return True
        additional_kwargs = message.additional_kwargs or {}
        return bool(additional_kwargs.get("tool_calls") or additional_kwargs.get("function_call"))

    def _apply_presentation_completion_guard(self, state: AgentState, runtime: Runtime) -> dict | None:
        context = getattr(runtime, "context", None)
        cache = context.get(_SLASH_SKILL_ACTIVATION_CACHE_KEY) if isinstance(context, dict) else None
        if not isinstance(cache, dict):
            return None
        messages = list(state.get("messages") or [])
        if not messages or not isinstance(messages[-1], AIMessage):
            return None
        last = messages[-1]
        if self._has_tool_call_intent(last) or (last.additional_kwargs or {}).get("deerflow_error_fallback"):
            return None
        requirements = self._presentation_incomplete_requirements(cache)
        if not requirements:
            return None

        retry_count = int(cache.get("presentation_completion_retry_count") or 0)
        if retry_count == 0:
            cache["presentation_completion_retry_count"] = 1
            cache["presentation_completion_pending"] = requirements
            updates = [RemoveMessage(id=last.id)] if last.id else []
            return {"messages": updates, "jump_to": "model"}

        additional_kwargs = dict(last.additional_kwargs or {})
        additional_kwargs.update(
            {
                "deerflow_error_fallback": True,
                "error_reason": "PPT workflow ended before required quality, export, or delivery gates completed",
            }
        )
        fallback = last.model_copy(
            update={
                "content": _PRESENTATION_COMPLETION_FALLBACK,
                "additional_kwargs": additional_kwargs,
                "tool_calls": [],
                "invalid_tool_calls": [],
            }
        )
        return {"messages": [fallback]}

    def _augment_presentation_completion_request(self, request: ModelRequest) -> ModelRequest:
        context = getattr(request.runtime, "context", None)
        cache = context.get(_SLASH_SKILL_ACTIVATION_CACHE_KEY) if isinstance(context, dict) else None
        if not isinstance(cache, dict):
            return request
        requirements = cache.pop("presentation_completion_pending", None)
        if not isinstance(requirements, list) or not requirements:
            return request
        reminder = HumanMessage(
            content=_PRESENTATION_COMPLETION_REMINDER.format(requirements="\n".join(f"- {item}" for item in requirements)),
            name="presentation_completion_recovery",
            additional_kwargs={"hide_from_ui": True},
        )
        return request.override(messages=[*request.messages, reminder])

    @hook_config(can_jump_to=["model"])
    @override
    def after_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        return self._apply_presentation_completion_guard(state, runtime)

    @hook_config(can_jump_to=["model"])
    @override
    async def aafter_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        return self._apply_presentation_completion_guard(state, runtime)

    @override
    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        blocked = self._check_reference_gate(request) or self._check_human_confirmation_gate(request) or self._check_declared_content_contract(request) or self._check_phase_gate(request)
        if blocked is not None:
            return normalize_tool_result(blocked)
        result = handler(request)
        if request.tool_call.get("name") == "read_file":
            path = self._tool_path(request)
            cache = self._activation_cache_from_tool_request(request)
            if path is not None and cache is not None and self._successful_tool_message(result):
                self._record_reference_read(cache, path)
        self._record_phase_progress(request, result)
        return result

    @override
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        blocked = self._check_reference_gate(request) or self._check_human_confirmation_gate(request) or self._check_declared_content_contract(request) or self._check_phase_gate(request)
        if blocked is not None:
            return normalize_tool_result(blocked)
        result = await handler(request)
        if request.tool_call.get("name") == "read_file":
            path = self._tool_path(request)
            cache = self._activation_cache_from_tool_request(request)
            if path is not None and cache is not None and self._successful_tool_message(result):
                self._record_reference_read(cache, path)
        self._record_phase_progress(request, result)
        return result

    @override
    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse | AIMessage:
        prepared = self._handle_model_request(self._augment_presentation_completion_request(request), hook="wrap_model_call")
        if isinstance(prepared, AIMessage):
            return prepared
        return self._ensure_structured_confirmation(prepared, handler(prepared))

    @override
    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse | AIMessage:
        prepared = await asyncio.to_thread(
            self._handle_model_request,
            self._augment_presentation_completion_request(request),
            hook="awrap_model_call",
        )
        if isinstance(prepared, AIMessage):
            return prepared
        return self._ensure_structured_confirmation(prepared, await handler(prepared))
