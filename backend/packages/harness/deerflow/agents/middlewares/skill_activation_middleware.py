"""Middleware for skill activation: explicit slash + in-context secret binding."""

from __future__ import annotations

import asyncio
import hashlib
import html
import logging
import posixpath
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.runtime import Runtime

from deerflow.agents.thread_state import SkillWorkflowState, ThreadState
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

# Keep small and medium skill contracts visible throughout a long tool loop.
# The activation message is a per-call model override rather than a checkpointed
# conversation message, so a compact-only continuation can silently remove the
# skill's detailed phase rules after the first model call. Very large skills still
# use the compact continuation and can be re-read from their declared path.
_MAX_CONTINUATION_SKILL_CHARS = 32_000

_WORKFLOW_CONTINUATION_RE = re.compile(
    r"^(?:确认|继续|同意|可以|好的|好|开始|恢复|重试|再试一次|继续执行|继续任务|"
    r"confirm|confirmed|continue|resume|retry|yes|ok|okay)\b|"
    r"(?:继续|恢复|重试).{0,40}(?:任务|执行|流程)?$",
    re.IGNORECASE,
)

_PRESENTATION_CAPABILITY_PATTERNS: dict[str, re.Pattern[str]] = {
    "ai_images": re.compile(r"(?:AI|ai)\s*(?:生图|图片|图像)|文生图|image\s+generation", re.IGNORECASE),
    "speaker_notes": re.compile(r"演讲者?备注|备注|讲稿|speaker\s+notes?", re.IGNORECASE),
    "transitions": re.compile(r"页面?转场|切换效果|transitions?", re.IGNORECASE),
    "timed_advance": re.compile(r"自动(?:播放|翻页|切换)|auto[- ]?advance", re.IGNORECASE),
    "object_animations": re.compile(r"对象动画|元素动画|object\s+animations?", re.IGNORECASE),
    "narration_audio": re.compile(r"配音|旁白|音频|narration|audio", re.IGNORECASE),
    "native_charts": re.compile(r"原生图表|native\s+charts?", re.IGNORECASE),
    "template": re.compile(r"模板|template", re.IGNORECASE),
}
_TEMPLATE_WORKSPACE_REQUEST_RE = re.compile(
    r"(?:创建|制作|构建|生成).{0,20}(?:可复用)?(?:Deck\s*)?模板(?:工作区)?|"
    r"create\s+(?:a\s+)?(?:reusable\s+)?template",
    re.IGNORECASE,
)
_PRESENTATION_OUTPUT_REQUEST_RE = re.compile(
    r"(?:创建|制作|生成|输出).{0,30}(?:\d+\s*页.{0,20})?(?:PPTX?|演示文稿|幻灯片)|"
    r"(?:PPTX?|演示文稿|幻灯片).{0,20}(?:创建|制作|生成|输出)|"
    r"(?:create|generate|build).{0,30}(?:presentation|slides?|deck)",
    re.IGNORECASE,
)

_RUNTIME_EXECUTION_CONTRACT = """
<deerflow_execution_contract>
The activated skill's ordered workflow is an execution contract, not background reading.
Before entering a later phase, finish every earlier phase that the skill declares as
required. Treat any declared item marked Pending, Failed, missing, placeholder, or
otherwise unresolved as unfinished; a plan, design/specification, preview, or prose
claim that something "will be" created is not the artifact itself. Do not replace a
required acquisition/conversion/validation step with a downstream hand-authored
surrogate merely because the downstream file can be produced directly.

Use the exact command/reference and its documented success criterion for the current
phase. After a failure, inspect the owning input/output and retry that phase; do not
advance until the command succeeds or the skill explicitly records a terminal manual
block. Keep generated projects, derived inputs, temporary files, and final files under
the writable thread paths `/mnt/user-data/workspace` or `/mnt/user-data/outputs`.
`/mnt/skills` is read-only, and uploaded source paths may be protected from in-place
derived-file writes; use the command's documented output/base-directory option rather
than writing beside the source or into the skill package. A shell's working directory
does not change the persistence boundary.
</deerflow_execution_contract>
"""

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
# _SLASH_SKILL_ACTIVATION_CACHE_KEY: continuation context plus runtime contract
# state. Small and medium SKILL.md bodies remain available on later model calls;
# very large bodies use the compact contract so long workflows do not multiply an
# unbounded skill document into every tool iteration.
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


@dataclass(frozen=True, slots=True)
class _ActivationResolution:
    activation: _Activation | None = None
    failure_message: str | None = None


def is_slash_skill_activation_reminder(message: object) -> bool:
    """Return whether a message is hidden slash-skill activation context."""
    return isinstance(message, HumanMessage) and bool(message.additional_kwargs.get(_SLASH_SKILL_ACTIVATION_KEY))


def _is_user_activation_target(message: object) -> bool:
    return is_real_user_message(message)


class SkillActivationMiddleware(AgentMiddleware[ThreadState]):
    """Activate slash skills and enforce their declared execution contracts."""

    state_schema = ThreadState

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
            )
        )

    @staticmethod
    def _requested_capabilities(activation: _Activation) -> dict[str, bool]:
        if activation.skill_name != "ppt-master":
            return {}
        template_workspace = bool(_TEMPLATE_WORKSPACE_REQUEST_RE.search(activation.remaining_text))
        presentation_output = bool(_PRESENTATION_OUTPUT_REQUEST_RE.search(activation.remaining_text))
        capabilities = {
            "presentation": presentation_output or not template_workspace,
        }
        if template_workspace:
            capabilities["template_workspace"] = True
        for name, pattern in _PRESENTATION_CAPABILITY_PATTERNS.items():
            if pattern.search(activation.remaining_text):
                capabilities[name] = True
        return capabilities

    @classmethod
    def _workflow_state_from_activation(
        cls,
        activation: _Activation,
        target: HumanMessage,
    ) -> SkillWorkflowState:
        activation_message_id = target.id or cls._activation_run_key(target)
        activation_id = hashlib.sha256(f"{activation_message_id}\0{activation.content_hash}".encode()).hexdigest()
        capabilities = cls._requested_capabilities(activation)
        return {
            "activation_id": activation_id,
            "activation_message_id": activation_message_id,
            "skill_name": activation.skill_name,
            "skill_path": activation.container_file_path,
            "skill_sha256": activation.content_hash,
            "task_text": activation.remaining_text,
            "status": "active",
            "current_stage": "activated",
            "gate_status": "open",
            "resume_pointer": activation.container_file_path,
            "completion_contract": ("presentation_delivery" if capabilities.get("presentation") else "skill_declared"),
            "required_capabilities": capabilities,
            "evidence": [],
            "delivered_artifacts": [],
        }

    @staticmethod
    def _latest_real_user_message(messages: list) -> HumanMessage | None:
        return next(
            (message for message in reversed(messages) if isinstance(message, HumanMessage) and _is_user_activation_target(message)),
            None,
        )

    @staticmethod
    def _is_workflow_continuation(target: HumanMessage) -> bool:
        metadata = target.additional_kwargs or {}
        human_input = metadata.get("human_input_response")
        if isinstance(human_input, dict) and human_input.get("source") == "ask_clarification":
            return True
        text = get_original_user_content_text(target.content, metadata).strip()
        return bool(text and _WORKFLOW_CONTINUATION_RE.search(text))

    def _capture_workflow_activation(self, state: AgentState) -> dict | None:
        target = self._latest_real_user_message(list(state.get("messages") or []))
        if target is None:
            return None
        text = get_original_user_content_text(target.content, target.additional_kwargs)
        resolution = self._resolve_activation(text)
        if resolution is not None and resolution.activation is not None:
            return {"skill_workflow": self._workflow_state_from_activation(resolution.activation, target)}

        workflow = state.get("skill_workflow")
        if isinstance(workflow, dict) and workflow.get("status") in {"active", "waiting", "blocked"} and self._is_workflow_continuation(target):
            # A user continuation starts a new model run; it does not supersede
            # an unresolved owning gate.  The workflow middleware will decide
            # whether confirmation, repair, or an owner retry can advance it.
            if workflow.get("status") == "blocked" or workflow.get("gate_status") in {
                "failed",
                "managed-artifact-owner-required",
                "delivery-failed",
                "delivery-incomplete",
            }:
                return {
                    "skill_workflow": {
                        "activation_id": workflow.get("activation_id", ""),
                    }
                }
            return {
                "skill_workflow": {
                    "activation_id": workflow.get("activation_id", ""),
                    "status": "active",
                    "gate_status": "resumed",
                }
            }
        return None

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
{confirmation_contract}
{_RUNTIME_EXECUTION_CONTRACT}
Do not skip mandatory phase gates. A failed command or validation result does not advance the workflow; read the declared reference or exact `--help`, fix the owning source, and retry the same gate.

<skill name="{escaped_skill_name}" category="{escaped_category}" path="{escaped_path}" sha256="{escaped_content_hash}" editable="{editable_str}">
<skill_content encoding="xml-escaped">
{escaped_skill_content}
</skill_content>
</skill>
</slash_skill_activation>"""

    @staticmethod
    def _build_continuation_reminder(activation: _Activation) -> str:
        """Build the reminder used after the first model call.

        The activation message is injected as a model-request override and is
        not persisted as a normal conversation message. Keep reasonably sized
        skill bodies in the continuation so a long tool loop does not lose the
        detailed workflow contract. Large skill bodies fall back to the compact
        path reminder and remain available through read_file.
        """
        escaped_skill_name = html.escape(activation.skill_name, quote=True)
        escaped_path = html.escape(activation.container_file_path, quote=True)
        escaped_skill_dir = html.escape(posixpath.dirname(activation.container_file_path), quote=True)
        escaped_user_request = html.escape(activation.remaining_text, quote=False)
        confirmation_contract = ""
        if "wait for explicit user confirmation" in activation.skill_content.lower():
            confirmation_contract = """
<deerflow_confirmation_adapter>
For every BLOCKING chat-fallback confirmation, call the native `ask_clarification` tool with `clarification_type=\"suggestion\"`, the recommendation summary, and explicit confirmation options.
Do not merely print the recommendations and end the turn, and do not rely on generated quick-reply suggestions: only the structured clarification response preserves the active workflow and counts as user confirmation.
</deerflow_confirmation_adapter>
"""
        if len(activation.skill_content) <= _MAX_CONTINUATION_SKILL_CHARS:
            escaped_skill_content = html.escape(activation.skill_content, quote=False)
            skill_body = f"""
<skill_body encoding="xml-escaped">
{escaped_skill_content}
</skill_body>
"""
        else:
            skill_body = """
<skill_body>
The complete SKILL.md is larger than the safe continuation budget. Re-read the declared SKILL.md path before applying detailed instructions.
</skill_body>
"""
        return f"""<slash_skill_continuation name="{escaped_skill_name}" path="{escaped_path}">
Continue following the already activated skill. Do not substitute a general workflow or invent commands, schemas, parameters, or phase order.
<original_user_request>
{escaped_user_request}
</original_user_request>
For every independent shell command that uses `${{SKILL_DIR}}`, first declare:
export SKILL_DIR='{escaped_skill_dir}'
{_RUNTIME_EXECUTION_CONTRACT}
Failed commands and validation errors do not advance the workflow. Read the exact reference or command `--help`, fix the owning source, and retry the same gate.
{confirmation_contract}
{skill_body}
</slash_skill_continuation>"""

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

    def _activation_from_workflow(self, workflow: SkillWorkflowState) -> _Activation | None:
        skill_path = workflow.get("skill_path")
        skill_name = workflow.get("skill_name")
        if not skill_path or not skill_name:
            return None
        try:
            storage = self._storage()
            skills = storage.load_skills(enabled_only=False)
            container_root = storage.get_container_root()
            skill = next(
                (candidate for candidate in skills if posixpath.normpath(candidate.get_container_file_path(container_root)) == posixpath.normpath(skill_path)),
                None,
            )
            if skill is None or not skill.enabled:
                return None
            if self._available_skills is not None and skill.name not in self._available_skills:
                return None
            skill_content = self._read_skill_content(
                skill.skill_file,
                storage.get_skills_root_path(),
                storage=storage,
            )
        except (OSError, ValueError):
            logger.exception("Failed to restore durable Skill workflow %s", skill_name)
            return None

        content_hash = hashlib.sha256(skill_content.encode("utf-8")).hexdigest()
        previous_hash = workflow.get("skill_sha256")
        if previous_hash and previous_hash != content_hash:
            logger.info(
                "Durable Skill workflow %s resumed against updated content %s -> %s",
                skill_name,
                previous_hash,
                content_hash,
            )
        return _Activation(
            skill_name=skill.name,
            category=str(skill.category),
            container_file_path=skill.get_container_file_path(container_root),
            skill_content=skill_content,
            content_hash=content_hash,
            remaining_text=str(workflow.get("task_text") or ""),
            editable=skill.category == SkillCategory.CUSTOM,
            required_secrets=tuple(skill.required_secrets or ()),
        )

    def _prepare_persisted_activation_request(
        self,
        request: ModelRequest,
        *,
        run_context: dict | None,
    ) -> tuple[ModelRequest | None, _Activation | None]:
        state = request.state or {}
        workflow = state.get("skill_workflow")
        if not isinstance(workflow, dict) or workflow.get("status") not in {
            "active",
            "waiting",
            "blocked",
        }:
            return None, None

        messages = list(request.messages)
        target_index = next(
            (index for index in range(len(messages) - 1, -1, -1) if _is_user_activation_target(messages[index])),
            None,
        )
        if target_index is None:
            return None, None
        target = messages[target_index]
        if not isinstance(target, HumanMessage) or not self._is_workflow_continuation(target):
            return None, None
        target_text = get_original_user_content_text(target.content, target.additional_kwargs)
        if parse_slash_skill_reference(target_text) is not None:
            return None, None
        if self._has_existing_activation_for_target(messages, target_index, target):
            return None, None

        activation = self._activation_from_workflow(workflow)
        if activation is None:
            return None, None
        continuation_content = self._build_continuation_reminder(activation)
        messages.insert(
            target_index,
            self._make_activation_message(target, continuation_content),
        )
        run_key = self._activation_run_key(target)
        if run_context is not None:
            run_context[_SLASH_SKILL_ACTIVATION_RUN_KEY] = run_key
            run_context[_SLASH_SKILL_ACTIVATION_CACHE_KEY] = {
                "run_key": run_key,
                "continuation_content": continuation_content,
            }
        return request.override(messages=messages), activation

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
            cached = self._prepare_cached_activation_request(request, run_context=run_context)
            if cached is not None:
                return cached, None
            return self._prepare_persisted_activation_request(
                request,
                run_context=run_context,
            )

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
            run_context[_SLASH_SKILL_ACTIVATION_CACHE_KEY] = {
                "run_key": run_key,
                "continuation_content": self._build_continuation_reminder(activation),
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

    @override
    def before_agent(self, state: ThreadState, runtime: Runtime) -> dict | None:
        return self._capture_workflow_activation(state)

    @override
    async def abefore_agent(self, state: ThreadState, runtime: Runtime) -> dict | None:
        return await asyncio.to_thread(self._capture_workflow_activation, state)

    @override
    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse | AIMessage:
        prepared = self._handle_model_request(request, hook="wrap_model_call")
        if isinstance(prepared, AIMessage):
            return prepared
        return handler(prepared)

    @override
    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse | AIMessage:
        prepared = await asyncio.to_thread(
            self._handle_model_request,
            request,
            hook="awrap_model_call",
        )
        if isinstance(prepared, AIMessage):
            return prepared
        return await handler(prepared)
