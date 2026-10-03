"""Prevent final answers from claiming unpublished output files."""

from __future__ import annotations

import logging
import re
import threading
from collections.abc import Awaitable, Callable
from pathlib import Path, PurePosixPath
from typing import Any, override
from urllib.parse import unquote

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelCallResult, ModelRequest, ModelResponse, hook_config
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage
from langgraph.runtime import Runtime

from deerflow.agents.middlewares._bounded_dict import BoundedDict
from deerflow.run_timing import atime_middleware_after_agent, log_run_timing, time_middleware_after_agent

logger = logging.getLogger(__name__)

_OUTPUTS_PREFIX = "/mnt/user-data/outputs/"
_MARKDOWN_OUTPUT_RE = re.compile(r"\]\(\s*(/mnt/user-data/outputs/[^)\r\n]+)\s*\)")
_BACKTICK_OUTPUT_RE = re.compile(r"`(/mnt/user-data/outputs/[^`\r\n]+)`")
_BARE_OUTPUT_RE = re.compile(r"(?<![\w])(/mnt/user-data/outputs/[^\s<>{}\[\]()`\"']+)")
_ARTIFACT_API_OUTPUT_RE = re.compile(
    r"/api/threads/[^/\s)]+/artifacts/(mnt/user-data/outputs/[^\s<>{}\[\]()`\"']+)",
)

_RECOVERY_PROMPT = """<system_reminder>
文件交付校验失败：你刚才声明的输出文件不存在，或者尚未通过 present_files 发布。
- read_file 只会读取文件或脚本内容，不会执行脚本。
- 必须使用 bash 实际执行生成、转换和导出命令；更新 Todo 状态不等于任务已经执行。
- 用 bash 的 test -f、文件大小检查以及格式校验确认最终文件真实存在。
- 最终文件必须位于 /mnt/user-data/outputs 或当前线程 workspace，并调用 present_files 发布。
- 只有 present_files 成功后，才能在最终回答中给出文件链接。
- 如果生成被权限、依赖或输入阻塞，请如实说明，绝不能编造文件、页数、质检结果或下载链接。
请继续完成真实生成和发布流程。
</system_reminder>"""

_FALLBACK_CONTENT = "文件交付失败：系统未找到或未发布所声明的文件。本次任务没有被标记为已交付，请重新执行生成流程。"


def _message_text(message: AIMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") in {"text", "output_text"}:
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(parts)


def _has_tool_call_intent(message: AIMessage) -> bool:
    if message.tool_calls or getattr(message, "invalid_tool_calls", None):
        return True
    additional_kwargs = message.additional_kwargs or {}
    return bool(additional_kwargs.get("tool_calls") or additional_kwargs.get("function_call"))


def _normalize_virtual_output_path(raw_path: str) -> str | None:
    path = unquote(raw_path.strip()).split("?", 1)[0].split("#", 1)[0].rstrip(".,;:，。；：")
    if not path.startswith(_OUTPUTS_PREFIX) or "\\" in path:
        return None
    relative = PurePosixPath(path.removeprefix(_OUTPUTS_PREFIX))
    if not relative.parts or relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        return None
    return f"{_OUTPUTS_PREFIX}{relative.as_posix()}"


def _referenced_output_paths(message: AIMessage) -> set[str]:
    text = _message_text(message)
    raw_paths = {
        *(_MARKDOWN_OUTPUT_RE.findall(text)),
        *(_BACKTICK_OUTPUT_RE.findall(text)),
        *(_BARE_OUTPUT_RE.findall(text)),
        *(f"/{path}" for path in _ARTIFACT_API_OUTPUT_RE.findall(text)),
    }
    return {path for raw_path in raw_paths if (path := _normalize_virtual_output_path(raw_path))}


def _is_published_file(path: str, state: AgentState) -> bool:
    artifacts = {normalized for artifact in state.get("artifacts") or [] if isinstance(artifact, str) and (normalized := _normalize_virtual_output_path(artifact))}
    if path not in artifacts:
        return False

    thread_data = state.get("thread_data") or {}
    outputs_path = thread_data.get("outputs_path") if isinstance(thread_data, dict) else None
    if not outputs_path:
        return False
    outputs_dir = Path(outputs_path).resolve()
    relative = PurePosixPath(path.removeprefix(_OUTPUTS_PREFIX))
    candidate = (outputs_dir / Path(*relative.parts)).resolve()
    try:
        candidate.relative_to(outputs_dir)
    except ValueError:
        return False
    return candidate.is_file()


def has_invalid_artifact_claim(message: AIMessage, state: AgentState) -> bool:
    """Return whether an answer claims any output that was not really published.

    This helper is also used by the run worker when an in-flight response is
    interrupted after the provider callback but before normal middleware
    finalization. Keeping one validator prevents the cancellation path from
    accepting artifact API links that the ordinary after-model path rejects.
    """
    referenced_paths = _referenced_output_paths(message)
    return bool(referenced_paths) and not all(_is_published_file(path, state) for path in referenced_paths)


def build_artifact_delivery_fallback(message: AIMessage, *, reason: str = "Artifact delivery validation failed") -> AIMessage:
    """Replace a fabricated artifact claim while preserving message identity."""
    additional_kwargs = dict(message.additional_kwargs or {})
    additional_kwargs.update(
        {
            "deerflow_error_fallback": True,
            "error_reason": reason,
        }
    )
    return message.model_copy(
        update={
            "content": _FALLBACK_CONTENT,
            "additional_kwargs": additional_kwargs,
            "tool_calls": [],
            "invalid_tool_calls": [],
        }
    )


class ArtifactDeliveryMiddleware(AgentMiddleware[AgentState]):
    """Retry fabricated output links, then replace them with a visible error."""

    def __init__(self, *, max_retries: int = 1) -> None:
        super().__init__()
        self.max_retries = max(0, max_retries)
        self._lock = threading.Lock()
        self._retry_counts: BoundedDict[tuple[str, str], int] = BoundedDict(1000)
        self._pending_prompts: BoundedDict[tuple[str, str], bool] = BoundedDict(1000)

    @staticmethod
    def _key(runtime: Runtime) -> tuple[str, str]:
        context = getattr(runtime, "context", None)
        if isinstance(context, dict):
            thread_id = str(context.get("thread_id") or "unknown-thread")
            run_id = str(context.get("run_id") or context.get("run_attempt_id") or id(runtime))
            return thread_id, run_id
        return "unknown-thread", str(id(runtime))

    def _clear(self, runtime: Runtime) -> None:
        key = self._key(runtime)
        with self._lock:
            self._retry_counts.pop(key, None)
            self._pending_prompts.pop(key, None)

    def _clear_other_runs(self, runtime: Runtime) -> None:
        thread_id, run_id = self._key(runtime)
        with self._lock:
            stale = [key for key in self._retry_counts if key[0] == thread_id and key[1] != run_id]
            for key in stale:
                self._retry_counts.pop(key, None)
                self._pending_prompts.pop(key, None)

    def _apply(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        messages = list(state.get("messages") or [])
        if not messages or not isinstance(messages[-1], AIMessage):
            return None

        last = messages[-1]
        if _has_tool_call_intent(last):
            return None
        referenced_paths = _referenced_output_paths(last)
        if not referenced_paths:
            return None
        if not has_invalid_artifact_claim(last, state):
            self._clear(runtime)
            return None

        key = self._key(runtime)
        with self._lock:
            retry_count = self._retry_counts.get(key, 0)
            if retry_count < self.max_retries:
                self._retry_counts[key] = retry_count + 1
                self._pending_prompts[key] = True

        if retry_count < self.max_retries:
            log_run_timing(logger, "artifact_delivery_retry_queued", retry_count=retry_count + 1)
            message_updates = [RemoveMessage(id=last.id)] if last.id else []
            return {"messages": message_updates, "jump_to": "model"}

        fallback = build_artifact_delivery_fallback(last)
        # Keep the retry counter sticky until after_agent. Other middleware may
        # route through the model again after this replacement; clearing here
        # would grant another retry and produce duplicate fabricated answers.
        return {"messages": [fallback]}

    def _augment_request(self, request: ModelRequest) -> ModelRequest:
        key = self._key(request.runtime)
        with self._lock:
            pending = key in self._pending_prompts
            self._pending_prompts.pop(key, None)
        if not pending:
            return request
        log_run_timing(logger, "artifact_delivery_retry_injected")
        reminder = HumanMessage(
            content=_RECOVERY_PROMPT,
            name="artifact_delivery_recovery",
            additional_kwargs={"hide_from_ui": True},
        )
        return request.override(messages=[*request.messages, reminder])

    @override
    def before_agent(self, state: AgentState, runtime: Runtime) -> dict | None:
        self._clear_other_runs(runtime)
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
        with time_middleware_after_agent(logger, middleware="artifact_delivery"):
            self._clear(runtime)
            return None

    @override
    async def aafter_agent(self, state: AgentState, runtime: Runtime) -> dict | None:
        async with atime_middleware_after_agent(logger, middleware="artifact_delivery"):
            self._clear(runtime)
            return None
