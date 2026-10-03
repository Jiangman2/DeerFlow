"""Ensure tool-using lead-agent turns end with a visible assistant response."""

from __future__ import annotations

import logging
import re
import threading
from collections.abc import Awaitable, Callable
from typing import Any, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelCallResult, ModelRequest, ModelResponse, hook_config
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, ToolMessage
from langgraph.runtime import Runtime

from deerflow.agents.middlewares._bounded_dict import BoundedDict
from deerflow.run_timing import atime_middleware_after_agent, log_run_timing, time_middleware_after_agent
from deerflow.runtime.context_keys import CURRENT_RUN_PRE_EXISTING_MESSAGE_IDS_KEY

logger = logging.getLogger(__name__)

_RECOVERY_PROMPT = "<system_reminder>\n你在工具执行后的上一条回复为空。请检查对话中已有的工具结果，并给出简洁、用户可见的简体中文最终回复。除非确有必要，否则不要再次调用工具。\n</system_reminder>"

_FALLBACK_CONTENT = "模型已完成工具调用，但在一次自动重试后仍未返回最终回复。请重试或改用其他模型。"

_TOOL_CALL_FINISH_REASONS = {"tool_calls", "function_call"}

_UNFINISHED_PLAN_RE = re.compile(
    r"\b(?:let me|i(?:'ll| will)|now let me|next,?|proceed(?: to)?|i need to|i am going to)\b|"
    r"(?:接下来|我将|现在我|让我|继续(?:处理|修复|生成))",
    re.IGNORECASE,
)
_PLAN_COMPLETION_RE = re.compile(
    r"\b(?:completed|complete|done|delivered|created|generated|attached|download|cannot|blocked|failed)\b|"
    r"(?:已完成|完成了|已生成|已交付|下载|无法|阻塞|失败)",
    re.IGNORECASE,
)
_WAITING_FOR_USER_RE = re.compile(
    r"(?:\?|？|\bblocking\b|⛔|请确认|是否继续|等待(?:用户|你的)?确认|需要(?:用户)?确认|请(?:选择|回复|批准)|需要你的(?:选择|回复|确认))",
    re.IGNORECASE,
)
_UNFINISHED_PLAN_PROMPT = (
    "<system_reminder>\nYour last visible response only described a next step and did not execute or deliver a result. "
    "Continue the activated skill's declared workflow and produce the actual result. Do not treat a future plan as "
    "completion; if blocked, perform the necessary final diagnosis and report the concrete blocker.\n</system_reminder>"
)
_UNFINISHED_PLAN_FALLBACK = "The model returned only an unfinished plan after one continuation attempt and did not deliver a verifiable result. Please retry; no final artifact can be confirmed."
_PPT_MASTER_SKILL_SUFFIX = "/ppt-master/SKILL.md"
_PPT_MASTER_RECOMMENDATION_RE = re.compile(r"(?:^|/)confirm_ui/recommendations\.stage[123]\.json$")


def _has_visible_content(message: AIMessage) -> bool:
    """Return whether an AI message contains user-visible text."""
    content = message.content
    if isinstance(content, str):
        return bool(content.strip())
    if isinstance(content, list):
        for block in content:
            if isinstance(block, str) and block.strip():
                return True
            if isinstance(block, dict) and block.get("type") in {"text", "output_text"}:
                text = block.get("text")
                if isinstance(text, str) and text.strip():
                    return True
    return False


def _has_tool_call_intent_or_error(message: AIMessage) -> bool:
    """Keep tool routing and malformed tool-call handling out of this guard."""
    if message.tool_calls or getattr(message, "invalid_tool_calls", None):
        return True
    additional_kwargs = message.additional_kwargs or {}
    if additional_kwargs.get("tool_calls") or additional_kwargs.get("function_call"):
        return True
    response_metadata = message.response_metadata or {}
    return response_metadata.get("finish_reason") in _TOOL_CALL_FINISH_REASONS


def _runtime_pre_existing_message_ids(runtime: Runtime | None) -> frozenset[str]:
    """Return message IDs that existed before the current Gateway run."""

    context = getattr(runtime, "context", None)
    if not isinstance(context, dict):
        return frozenset()
    raw_ids = context.get(CURRENT_RUN_PRE_EXISTING_MESSAGE_IDS_KEY)
    if not isinstance(raw_ids, (frozenset, set, list, tuple)):
        return frozenset()
    return frozenset(str(message_id) for message_id in raw_ids if message_id)


def _message_id(message: Any) -> str | None:
    message_id = getattr(message, "id", None)
    return str(message_id) if message_id else None


def _current_run_messages(messages: list[Any], runtime: Runtime | None) -> list[Any]:
    """Exclude durable history when the worker supplied a run boundary.

    Context compaction can leave a historical ``ToolMessage`` after the latest
    visible user message.  The terminal retry must never treat that old receipt
    as evidence that the current model response followed an executed tool.
    """

    pre_existing_ids = _runtime_pre_existing_message_ids(runtime)
    if not pre_existing_ids:
        return messages
    return [
        message
        for message in messages
        if _message_id(message) not in pre_existing_ids and _message_id(message) is not None
    ]


def _tool_result_in_current_turn(messages: list[Any], runtime: Runtime | None = None) -> bool:
    """Return whether a tool result follows the latest real user message."""
    messages = _current_run_messages(messages, runtime)
    latest_user_index = -1
    for index, message in enumerate(messages):
        if not isinstance(message, HumanMessage):
            continue
        if (message.additional_kwargs or {}).get("hide_from_ui"):
            continue
        latest_user_index = index
    # Scope: #4027 covers interactive post-tool turns. Scheduled/internal
    # invocations without a real HumanMessage need a separate terminal-success
    # invariant rather than being inferred from arbitrary historical tools.
    if latest_user_index == -1:
        return False
    return any(isinstance(message, ToolMessage) for message in messages[latest_user_index + 1 :])


def _latest_tool_result_in_current_turn(
    messages: list[Any], runtime: Runtime | None = None
) -> ToolMessage | None:
    """Return the latest tool receipt after the current visible user turn."""
    messages = _current_run_messages(messages, runtime)
    latest_user_index = -1
    for index, message in enumerate(messages):
        if isinstance(message, HumanMessage) and not (message.additional_kwargs or {}).get("hide_from_ui"):
            latest_user_index = index
    if latest_user_index == -1:
        return None
    for message in reversed(messages[latest_user_index + 1 :]):
        if isinstance(message, ToolMessage):
            return message
    return None


def _tool_call_for_result(messages: list[Any], result: ToolMessage) -> dict[str, Any] | None:
    """Resolve a tool receipt to its originating structured tool call."""
    tool_call_id = str(result.tool_call_id or "")
    if not tool_call_id:
        return None
    for message in reversed(messages):
        if not isinstance(message, AIMessage):
            continue
        for call in message.tool_calls or []:
            if str(call.get("id") or "") == tool_call_id:
                return call
    return None


def _ppt_master_confirmation_recommendation_ready(
    state: AgentState,
    messages: list[Any],
    runtime: Runtime | None = None,
) -> str:
    """Return the active Confirm UI URL when a recommendation write is the last durable action.

    The official ``ppt-master`` page becomes user-ready as soon as a valid
    ``recommendations.stageN.json`` write succeeds.  If the model then ends on
    future-plan prose instead of invoking the documented owner wait, that is a
    legitimate interactive boundary, not an unfinished artifact delivery.
    """
    workflow = state.get("skill_workflow")
    if not isinstance(workflow, dict):
        return ""
    skill_path = str(workflow.get("skill_path") or "").replace("\\", "/")
    confirmation_url = str(workflow.get("confirmation_url") or "")
    if (
        not skill_path.endswith(_PPT_MASTER_SKILL_SUFFIX)
        or not workflow.get("confirmation_ui_active")
        or not confirmation_url
    ):
        return ""

    current_messages = _current_run_messages(messages, runtime)
    result = _latest_tool_result_in_current_turn(current_messages)
    if result is None or str(getattr(result, "status", "success") or "success").casefold() == "error":
        return ""
    call = _tool_call_for_result(current_messages, result)
    if not call or str(call.get("name") or "") != "write_file":
        return ""
    args = call.get("args")
    if not isinstance(args, dict):
        return ""
    path = str(args.get("path") or args.get("file_path") or "").replace("\\", "/")
    if not _PPT_MASTER_RECOMMENDATION_RE.search(path):
        return ""
    return confirmation_url


def _is_unfinished_plan_response(message: AIMessage, messages: list[Any], runtime: Runtime | None = None) -> bool:
    """Detect a future-tense plan incorrectly ending a tool-using turn."""
    if not _has_visible_content(message) or _has_tool_call_intent_or_error(message):
        return False
    content = message.content if isinstance(message.content, str) else str(message.content)
    # A visible question or explicit blocking/confirmation request is a valid
    # terminal state: the skill is intentionally waiting for the user. Never
    # turn that state into an automatic continuation, or a workflow's own
    # confirmation gate can be bypassed by DeerFlow.
    if _WAITING_FOR_USER_RE.search(content):
        return False
    if not _UNFINISHED_PLAN_RE.search(content) or _PLAN_COMPLETION_RE.search(content):
        return False
    return _tool_result_in_current_turn(messages, runtime)


class TerminalResponseMiddleware(AgentMiddleware[AgentState]):
    """Retry one empty post-tool response, then persist a visible error fallback."""

    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.Lock()
        self._retry_counts: BoundedDict[tuple[str, str], int] = BoundedDict(1000)
        self._pending_prompts: BoundedDict[tuple[str, str], bool] = BoundedDict(1000)
        self._plan_retry_counts: BoundedDict[tuple[str, str], int] = BoundedDict(1000)

    @staticmethod
    def _key(runtime: Runtime) -> tuple[str, str]:
        context = getattr(runtime, "context", None)
        if isinstance(context, dict):
            thread_id = str(context.get("thread_id") or "unknown-thread")
            run_id = str(context.get("run_id") or context.get("run_attempt_id") or id(runtime))
            return thread_id, run_id
        # Defensive fallback for tests/custom embeddings. Production Gateway
        # runs always provide thread_id and run_id in Runtime.context.
        return "unknown-thread", str(id(runtime))

    def _clear(self, runtime: Runtime) -> None:
        key = self._key(runtime)
        with self._lock:
            self._retry_counts.pop(key, None)
            self._pending_prompts.pop(key, None)
            self._plan_retry_counts.pop(key, None)

    def _clear_other_runs(self, runtime: Runtime) -> None:
        thread_id, run_id = self._key(runtime)
        with self._lock:
            stale = [key for key in self._retry_counts if key[0] == thread_id and key[1] != run_id]
            for key in stale:
                self._retry_counts.pop(key, None)
                self._pending_prompts.pop(key, None)
                self._plan_retry_counts.pop(key, None)

    def _apply(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        messages = list(state.get("messages") or [])
        if not messages or not isinstance(messages[-1], AIMessage):
            return None

        last = messages[-1]
        confirmation_url = _ppt_master_confirmation_recommendation_ready(state, messages, runtime)
        if confirmation_url and not _has_tool_call_intent_or_error(last):
            self._clear(runtime)
            additional_kwargs = dict(last.additional_kwargs or {})
            additional_kwargs.update(
                {
                    "deerflow_waiting_for_user": True,
                    "waiting_reason": "ppt-master Confirm UI recommendation is ready",
                }
            )
            return {
                "messages": [
                    last.model_copy(
                        update={
                            "content": (
                                f"当前 PPT 设计确认页面已准备好，请在[确认页面]({confirmation_url})完成本阶段选择。"
                                "确认后回到对话输入“继续”，任务将从已持久化的检查点继续。"
                            ),
                            "additional_kwargs": additional_kwargs,
                        }
                    )
                ]
            }
        if _is_unfinished_plan_response(last, messages, runtime):
            key = self._key(runtime)
            with self._lock:
                retry_count = self._plan_retry_counts.get(key, 0)
                if retry_count == 0:
                    self._plan_retry_counts[key] = 1
                    self._pending_prompts[key] = True
            if retry_count == 0:
                log_run_timing(logger, "terminal_plan_retry_queued")
                return {"jump_to": "model"}

            additional_kwargs = dict(last.additional_kwargs or {})
            additional_kwargs.update(
                {
                    "deerflow_error_fallback": True,
                    "error_reason": "Model returned an unfinished plan after one retry",
                }
            )
            return {
                "messages": [
                    last.model_copy(
                        update={
                            "content": _UNFINISHED_PLAN_FALLBACK,
                            "additional_kwargs": additional_kwargs,
                        }
                    )
                ]
            }

        if _has_visible_content(last) or _has_tool_call_intent_or_error(last):
            return None
        if not _tool_result_in_current_turn(messages, runtime):
            return None

        key = self._key(runtime)
        with self._lock:
            # The recovery budget is once per run, not once per empty message.
            # A retry that calls another tool must not refresh the budget and
            # create an unbounded empty -> retry -> tool loop.
            retry_count = self._retry_counts.get(key, 0)
            if retry_count == 0:
                self._retry_counts[key] = 1
                self._pending_prompts[key] = True

        if retry_count == 0:
            # The next model call gets a new message id. Remove this empty
            # terminal message now so a successful recovery does not leave it
            # in checkpoint history or future model context.
            message_updates = [RemoveMessage(id=last.id)] if last.id else []
            log_run_timing(logger, "terminal_empty_retry_queued")
            return {"messages": message_updates, "jump_to": "model"}

        additional_kwargs = dict(last.additional_kwargs or {})
        additional_kwargs.update(
            {
                "deerflow_error_fallback": True,
                "error_reason": "Model returned an empty terminal response after one retry",
            }
        )
        fallback = last.model_copy(
            update={
                "content": _FALLBACK_CONTENT,
                "additional_kwargs": additional_kwargs,
            }
        )
        return {"messages": [fallback]}

    def _augment_request(self, request: ModelRequest) -> ModelRequest:
        key = self._key(request.runtime)
        with self._lock:
            pending = key in self._pending_prompts
            self._pending_prompts.pop(key, None)
        if not pending:
            return request
        with self._lock:
            is_plan_retry = self._plan_retry_counts.get(key, 0) == 1
        reminder = HumanMessage(
            content=_UNFINISHED_PLAN_PROMPT if is_plan_retry else _RECOVERY_PROMPT,
            name="unfinished_plan_recovery" if is_plan_retry else "terminal_response_recovery",
            additional_kwargs={"hide_from_ui": True},
        )
        log_run_timing(
            logger,
            "terminal_retry_injected",
            retry_kind="plan" if is_plan_retry else "empty",
        )
        return request.override(messages=[*request.messages, reminder])

    @override
    def before_agent(self, state: AgentState, runtime: Runtime) -> dict | None:
        self._clear_other_runs(runtime)
        # A prior invocation can bypass after_agent via Command(goto=END).
        # Reset the same run id here so resume starts with a fresh one-retry
        # budget; internal jump_to=model loops do not re-run before_agent.
        self._clear(runtime)
        return None

    @override
    async def abefore_agent(self, state: AgentState, runtime: Runtime) -> dict | None:
        self._clear_other_runs(runtime)
        self._clear(runtime)
        return None

    @hook_config(can_jump_to=["model"])
    @override
    def after_model(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        return self._apply(state, runtime)

    @hook_config(can_jump_to=["model"])
    @override
    async def aafter_model(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        return self._apply(state, runtime)

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
        return await handler(self._augment_request(request))

    @override
    def after_agent(self, state: AgentState, runtime: Runtime) -> dict | None:
        with time_middleware_after_agent(logger, middleware="terminal_response"):
            self._clear(runtime)
            return None

    @override
    async def aafter_agent(self, state: AgentState, runtime: Runtime) -> dict | None:
        async with atime_middleware_after_agent(logger, middleware="terminal_response"):
            self._clear(runtime)
            return None
